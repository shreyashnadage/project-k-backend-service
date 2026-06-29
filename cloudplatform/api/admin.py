"""
Admin API endpoints for platform operators.
Provides client listing, detail, API key retrieval, and client onboarding.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
from typing import Optional
import logging
import secrets
import string

from sqlalchemy import Column, String, DateTime

from cloudplatform.db.database import get_db
from cloudplatform.db.models import (
    Base, Client, DeviceRegistration, CompanyMapping,
    InstallationKey, RegistrationAuditLog,
)
from cloudplatform.auth.routes import get_current_client
from cloudplatform.auth.supabase_client import ClientInfo


class TelemetryConfig(Base):
    __tablename__ = "telemetry_config"
    client_id = Column(String, primary_key=True, index=True)
    frequency = Column(String, default="manual")  # manual|1m|5m|15m|30m|1h
    updated_at = Column(DateTime, default=datetime.utcnow)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin", tags=["admin"])


class OnboardClientRequest(BaseModel):
    company_name: str
    email: str
    phone: Optional[str] = None
    gst_id: Optional[str] = None
    plan: str = "trial"


@router.get("/clients")
async def list_clients(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: str = Query(None),
    status: str = Query(None),
    current_user: ClientInfo = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    query = db.query(Client)

    if search:
        pattern = f"%{search}%"
        query = query.filter(
            (Client.company_name.ilike(pattern)) | (Client.email.ilike(pattern))
        )
    if status:
        query = query.filter(Client.status == status)

    total = query.count()
    clients = query.order_by(Client.created_at.desc()).offset(skip).limit(limit).all()

    result = []
    for c in clients:
        device_count = db.query(func.count(DeviceRegistration.device_id)).filter(
            DeviceRegistration.client_id == c.client_id,
            DeviceRegistration.status == "active",
        ).scalar() or 0

        last_device_sync = db.query(func.max(DeviceRegistration.last_sync_at)).filter(
            DeviceRegistration.client_id == c.client_id,
        ).scalar()

        result.append({
            "client_id": c.client_id,
            "company_name": c.company_name,
            "email": c.email,
            "phone": c.phone,
            "gst_id": c.gst_id,
            "status": c.status,
            "plan": c.plan,
            "device_count": device_count,
            "last_sync_at": last_device_sync.isoformat() if last_device_sync else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })

    return {"clients": result, "total": total}


@router.get("/clients/{client_id}")
async def get_client_detail(
    client_id: str,
    current_user: ClientInfo = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    client = db.query(Client).filter(Client.client_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    devices = db.query(DeviceRegistration).filter(
        DeviceRegistration.client_id == client_id,
    ).all()

    companies = db.query(CompanyMapping).filter(
        CompanyMapping.client_id == client_id,
    ).all()

    return {
        "client_id": client.client_id,
        "company_name": client.company_name,
        "email": client.email,
        "phone": client.phone,
        "gst_id": client.gst_id,
        "status": client.status,
        "plan": client.plan,
        "created_at": client.created_at.isoformat() if client.created_at else None,
        "devices": [
            {
                "device_id": d.device_id,
                "device_name": d.device_name,
                "status": d.status,
                "os_version": d.os_version,
                "agent_version": d.agent_version,
                "last_sync_at": d.last_sync_at.isoformat() if d.last_sync_at else None,
                "registered_at": d.registered_at.isoformat() if d.registered_at else None,
            }
            for d in devices
        ],
        "companies": [
            {
                "id": cm.id,
                "company_name": cm.company_name,
                "company_guid": cm.company_guid,
                "device_id": cm.device_id,
                "is_active": cm.is_active,
                "last_synced_at": cm.last_synced_at.isoformat() if cm.last_synced_at else None,
            }
            for cm in companies
        ],
    }


@router.get("/clients/{client_id}/api-key")
async def get_client_api_key(
    client_id: str,
    current_user: ClientInfo = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """Return the first active device's API key for a client.
    This lets the admin frontend call /api/dashboard/* endpoints scoped to this client."""
    client = db.query(Client).filter(Client.client_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    device = db.query(DeviceRegistration).filter(
        DeviceRegistration.client_id == client_id,
        DeviceRegistration.status == "active",
    ).first()

    if not device:
        raise HTTPException(
            status_code=404,
            detail="No active devices for this client",
        )

    return {
        "api_key": device.api_key,
        "device_id": device.device_id,
        "device_name": device.device_name,
    }


def _generate_installation_key() -> str:
    year = datetime.now().year
    random_part = "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5)
    )
    return f"TSA-{year}-{random_part}"


@router.post("/onboard-client")
async def onboard_client(
    req: OnboardClientRequest,
    current_user: ClientInfo = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    """Create a new client and generate an installation key in one step."""
    existing = db.query(Client).filter(Client.email == req.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    client = Client(
        company_name=req.company_name,
        email=req.email,
        phone=req.phone,
        gst_id=req.gst_id,
        status="active",
        email_verified=True,
        verified_at=datetime.now(timezone.utc),
        plan=req.plan,
    )
    db.add(client)
    db.flush()

    key_string = _generate_installation_key()
    install_key = InstallationKey(
        client_id=client.client_id,
        installation_key=key_string,
        status="active",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(install_key)

    audit = RegistrationAuditLog(
        client_id=client.client_id,
        action="admin_onboarded",
        details=f'{{"company_name": "{req.company_name}", "onboarded_by": "{current_user.email}"}}',
        source_device="ADMIN_DASHBOARD",
    )
    db.add(audit)

    db.commit()

    return {
        "client_id": client.client_id,
        "company_name": client.company_name,
        "email": client.email,
        "status": client.status,
        "plan": client.plan,
        "installation_key": key_string,
        "key_expires_at": install_key.expires_at.isoformat(),
    }


VALID_FREQUENCIES = {"manual", "1m", "5m", "15m", "30m", "1h"}


@router.get("/clients/{client_id}/telemetry-config")
async def get_telemetry_config(
    client_id: str,
    current_user: ClientInfo = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    cfg = db.query(TelemetryConfig).filter(TelemetryConfig.client_id == client_id).first()
    return {"client_id": client_id, "frequency": cfg.frequency if cfg else "manual"}


class TelemetryConfigUpdate(BaseModel):
    frequency: str


@router.put("/clients/{client_id}/telemetry-config")
async def update_telemetry_config(
    client_id: str,
    body: TelemetryConfigUpdate,
    current_user: ClientInfo = Depends(get_current_client),
    db: Session = Depends(get_db),
):
    if body.frequency not in VALID_FREQUENCIES:
        raise HTTPException(400, f"Invalid frequency. Allowed: {sorted(VALID_FREQUENCIES)}")

    cfg = db.query(TelemetryConfig).filter(TelemetryConfig.client_id == client_id).first()
    if cfg:
        cfg.frequency = body.frequency
        cfg.updated_at = datetime.now(timezone.utc)
    else:
        cfg = TelemetryConfig(
            client_id=client_id,
            frequency=body.frequency,
            updated_at=datetime.now(timezone.utc),
        )
        db.add(cfg)
    db.commit()
    return {"client_id": client_id, "frequency": cfg.frequency}
