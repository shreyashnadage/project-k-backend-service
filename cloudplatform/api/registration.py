"""
Registration API endpoints for client onboarding.

Endpoints:
- POST /register                - Portal: Register new MSME client
- POST /v1/register            - Agent: Register device after installation
- GET /v1/clients/{id}/stats   - Get client sync statistics
- POST /v1/sync                - Receive data with client_id tagging
"""

from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
from typing import Optional
import uuid
import secrets
import string

from cloudplatform.db.database import SessionLocal
from cloudplatform.db.models import (
    Client, InstallationKey, DeviceRegistration,
    SyncRecord, RegistrationAuditLog
)

router = APIRouter(prefix="/v1", tags=["registration"])


def get_db() -> Session:
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def generate_installation_key() -> str:
    """Generate a unique installation key."""
    # Format: PREFIX-YEAR-RANDOM (e.g., TSA-2026-XXXXX)
    year = datetime.now().year
    random_part = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5))
    return f"TSA-{year}-{random_part}"


# ============================================================
# PORTAL ENDPOINTS (For web UI)
# ============================================================

@router.post("/register")
def register_client(
    company_name: str,
    email: str,
    phone: Optional[str] = None,
    gst_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Register a new MSME client on the portal.

    Request:
        POST /register
        {
            "company_name": "Sharma Traders Pvt Ltd",
            "email": "shreya@sharma.com",
            "phone": "9876543210",
            "gst_id": "18AABCU12345K1Z5"
        }

    Response:
        {
            "status": "registered",
            "client_id": "cli_abc123",
            "company_name": "Sharma Traders",
            "installation_key": "TSA-2026-XXXXX",
            "expires_in_days": 30,
            "next_steps": "Download agent installer and enter this key"
        }
    """
    try:
        # Check if client already exists
        existing = db.query(Client).filter_by(email=email).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        # Create client
        client = Client(
            company_name=company_name,
            email=email,
            phone=phone,
            gst_id=gst_id,
            status="active",
            plan="trial"
        )
        db.add(client)
        db.flush()  # Get client_id
        client_id = client.client_id

        # Generate installation key (valid for 30 days)
        key_string = generate_installation_key()
        install_key = InstallationKey(
            client_id=client_id,
            installation_key=key_string,
            status="active",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30)
        )
        db.add(install_key)

        # Create audit log entry
        audit = RegistrationAuditLog(
            client_id=client_id,
            action="client_registered",
            details=f'{{"company_name": "{company_name}", "email": "{email}"}}',
            source_device="WEB_PORTAL"
        )
        db.add(audit)

        db.commit()

        return {
            "status": "registered",
            "client_id": client_id,
            "company_name": company_name,
            "installation_key": key_string,
            "expires_in_days": 30,
            "message": "Registration successful! Download the agent and enter this key during installation."
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# AGENT ENDPOINTS (For Windows Agent)
# ============================================================

@router.post("/register-device")
def register_device(
    installation_key: str,
    device_name: str,
    os_version: str,
    agent_version: str,
    db: Session = Depends(get_db)
):
    """
    Register an agent device (called during installation).

    Request:
        POST /v1/register-device
        {
            "installation_key": "TSA-2026-XXXXX",
            "device_name": "OFFICE-PC-01",
            "os_version": "Windows 11 Build 26200",
            "agent_version": "0.4.0"
        }

    Response:
        {
            "status": "registered",
            "client_id": "cli_abc123",
            "device_id": "dev_xyz789",
            "registration_token": "reg_token_xxx",
            "api_key": "api_key_xxx",
            "message": "Agent registered successfully. Store credentials securely."
        }
    """
    try:
        # Find and validate installation key
        install_key = db.query(InstallationKey).filter_by(
            installation_key=installation_key
        ).first()

        if not install_key:
            raise HTTPException(status_code=404, detail="Installation key not found")

        if install_key.status != "active":
            raise HTTPException(status_code=400, detail="Installation key is not active")

        if install_key.expires_at:
            now = datetime.now(timezone.utc)
            # Handle both naive and aware datetimes
            expires = install_key.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < now:
                raise HTTPException(status_code=400, detail="Installation key has expired")

        # Get client
        client = db.query(Client).filter_by(client_id=install_key.client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        if client.status != "active":
            raise HTTPException(status_code=400, detail="Client account is not active")

        # Create device registration
        device_id = str(uuid.uuid4())
        registration_token = secrets.token_urlsafe(32)
        api_key = secrets.token_urlsafe(32)

        device = DeviceRegistration(
            device_id=device_id,
            client_id=client.client_id,
            device_name=device_name,
            os_version=os_version,
            agent_version=agent_version,
            registration_token=registration_token,
            api_key=api_key,
            status="active"
        )
        db.add(device)
        db.flush()

        # Mark installation key as used
        install_key.status = "used"
        install_key.used_at = datetime.now(timezone.utc)
        install_key.device_id_used_by = device_id

        # Create audit log entry
        audit = RegistrationAuditLog(
            client_id=client.client_id,
            action="device_registered",
            details=f'{{"device_name": "{device_name}", "os": "{os_version}"}}',
            source_device=device_name
        )
        db.add(audit)

        db.commit()

        return {
            "status": "registered",
            "client_id": client.client_id,
            "device_id": device_id,
            "registration_token": registration_token,
            "api_key": api_key,
            "message": "Device registered successfully. Store credentials securely in Windows Credential Manager."
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/clients/{client_id}/stats")
def get_client_stats(
    client_id: str,
    x_api_key: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """
    Get sync statistics for a client.

    Request:
        GET /v1/clients/{client_id}/stats
        Headers:
            x-api-key: api_key_xxx

    Response:
        {
            "client_id": "cli_abc123",
            "company_name": "Sharma Traders",
            "total_syncs": 5,
            "total_records": 3250,
            "total_ledgers": 750,
            "total_vouchers": 2500,
            "last_sync": "2026-06-28T06:00:00Z",
            "devices": [
                {
                    "device_id": "dev_xyz",
                    "device_name": "OFFICE-PC-01",
                    "last_sync": "2026-06-28T06:00:00Z"
                }
            ]
        }
    """
    try:
        # Get client
        client = db.query(Client).filter_by(client_id=client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        # Get sync statistics
        syncs = db.query(SyncRecord).filter_by(client_id=client_id).all()
        total_syncs = len(syncs)
        total_records = sum(s.records_count for s in syncs)
        total_ledgers = sum(s.extracted_ledgers for s in syncs)
        total_vouchers = sum(s.extracted_vouchers for s in syncs)

        last_sync = max([s.sync_timestamp for s in syncs]) if syncs else None

        # Get devices
        devices = db.query(DeviceRegistration).filter_by(client_id=client_id).all()
        devices_list = [
            {
                "device_id": d.device_id,
                "device_name": d.device_name,
                "status": d.status,
                "last_sync": d.last_sync_at.isoformat() if d.last_sync_at else None
            }
            for d in devices
        ]

        return {
            "client_id": client_id,
            "company_name": client.company_name,
            "status": client.status,
            "plan": client.plan,
            "total_syncs": total_syncs,
            "total_records": total_records,
            "total_ledgers": total_ledgers,
            "total_vouchers": total_vouchers,
            "last_sync": last_sync.isoformat() if last_sync else None,
            "devices": devices_list
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync-with-client")
def sync_data_with_client(
    client_id: str,
    device_id: str,
    records_data: dict,
    extracted_ledgers: int = 0,
    extracted_vouchers: int = 0,
    x_api_key: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """
    Receive data from agent with client tagging.

    Request:
        POST /v1/sync-with-client
        Headers:
            x-api-key: api_key_xxx
            x-client-id: cli_abc123
            x-device-id: dev_xyz789

        Body:
        {
            "extracted_ledgers": 150,
            "extracted_vouchers": 500,
            "records": [...]
        }

    Response:
        {
            "status": "success",
            "sync_id": "sync_abc123",
            "records_received": 650,
            "message": "Data received and stored"
        }
    """
    try:
        # Validate client exists
        client = db.query(Client).filter_by(client_id=client_id).first()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        # Validate device is registered to client
        device = db.query(DeviceRegistration).filter_by(
            device_id=device_id,
            client_id=client_id
        ).first()
        if not device:
            raise HTTPException(status_code=403, detail="Device not registered for this client")

        if device.status != "active":
            raise HTTPException(status_code=403, detail="Device is not active")

        # Validate API key
        if not x_api_key or x_api_key != device.api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Create sync record
        sync = SyncRecord(
            client_id=client_id,
            device_id=device_id,
            tenant_id=client_id,  # Use client_id as tenant for data isolation
            records_count=extracted_ledgers + extracted_vouchers,
            extracted_ledgers=extracted_ledgers,
            extracted_vouchers=extracted_vouchers,
            sync_status="success",
            sync_timestamp=datetime.now(timezone.utc)
        )
        db.add(sync)

        # Update device last sync time
        device.last_sync_at = datetime.now(timezone.utc)

        # Create audit log entry
        audit = RegistrationAuditLog(
            client_id=client_id,
            action="sync_received",
            details=f'{{"records": {extracted_ledgers + extracted_vouchers}, "ledgers": {extracted_ledgers}, "vouchers": {extracted_vouchers}}}',
            source_device=device.device_name
        )
        db.add(audit)

        db.commit()

        return {
            "status": "success",
            "sync_id": sync.sync_id,
            "records_received": sync.records_count,
            "message": "Data received and tagged with client_id"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
