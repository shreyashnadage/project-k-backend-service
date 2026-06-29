"""
Command Channel API

Allows admin to dispatch on-demand sync commands to registered agents.
Agents poll GET /v1/commands/pending every 60s and acknowledge via PATCH /v1/commands/{id}.
"""

import json
import logging
from typing import List, Optional
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from cloudplatform.db.models import SyncCommand, DeviceRegistration, Tenant
from cloudplatform.db.database import get_db
from cloudplatform.api.ingest import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["commands"])

COMMAND_TTL_HOURS = 24

ALLOWED_COMMAND_TYPES = {
    "sync_ledgers",             # Full ledger master for a company
    "sync_ledgers_by_group",    # Ledgers filtered by parent group
    "sync_ledger_one",          # Single ledger by name
    "sync_groups",              # Account group hierarchy
    "sync_vouchers",            # All vouchers in a date range
    "sync_vouchers_by_type",    # Specific voucher type + date range
    "sync_stock",               # All stock items
    "sync_stock_by_group",      # Stock items in a group
    "sync_full",                # Full sync cycle for one company (all data types)
    "sync_all_companies",       # Full sync for ALL mapped companies
    "discover_companies",       # Agent discovers and registers Tally companies
    "health_check",             # Agent responds with Tally reachability, no data extracted
    "push_telemetry",           # Agent pushes buffered telemetry events to cloud
}


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateCommandRequest(BaseModel):
    device_id: str
    command_type: str
    params: dict = {}
    created_by: Optional[str] = None


class CommandResponse(BaseModel):
    id: str
    device_id: str
    command_type: str
    params: dict
    status: str
    created_at: str
    fetched_at: Optional[str] = None
    completed_at: Optional[str] = None
    expires_at: Optional[str] = None
    result: Optional[dict] = None
    error_message: Optional[str] = None


class AcknowledgeCommandRequest(BaseModel):
    status: str                     # "completed" or "failed"
    result: Optional[dict] = None
    error_message: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize(cmd: SyncCommand) -> CommandResponse:
    return CommandResponse(
        id=cmd.id,
        device_id=cmd.device_id,
        command_type=cmd.command_type,
        params=json.loads(cmd.params),
        status=cmd.status,
        created_at=cmd.created_at.isoformat(),
        fetched_at=cmd.fetched_at.isoformat() if cmd.fetched_at else None,
        completed_at=cmd.completed_at.isoformat() if cmd.completed_at else None,
        expires_at=cmd.expires_at.isoformat() if cmd.expires_at else None,
        result=json.loads(cmd.result) if cmd.result else None,
        error_message=cmd.error_message,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/v1/commands", response_model=CommandResponse, status_code=201)
def create_command(
    body: CreateCommandRequest,
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Admin creates an on-demand sync command targeting a specific agent device.

    The agent will pick this up on its next poll (≤60s).
    Commands expire after 24 hours if not fetched.
    """
    if body.command_type not in ALLOWED_COMMAND_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown command type '{body.command_type}'. "
                   f"Allowed: {sorted(ALLOWED_COMMAND_TYPES)}",
        )

    device = db.query(DeviceRegistration).filter(
        DeviceRegistration.device_id == body.device_id,
    ).first()
    if not device:
        raise HTTPException(status_code=404, detail=f"Device '{body.device_id}' not found")

    now = datetime.now(timezone.utc)
    cmd = SyncCommand(
        tenant_id=tenant.id,
        device_id=body.device_id,
        command_type=body.command_type,
        params=json.dumps(body.params),
        status="pending",
        created_by=body.created_by,
        created_at=now,
        expires_at=now + timedelta(hours=COMMAND_TTL_HOURS),
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)

    logger.info(f"Command created: {cmd.id} type={cmd.command_type} device={cmd.device_id}")
    return _serialize(cmd)


@router.get("/v1/commands/pending", response_model=List[CommandResponse])
def get_pending_commands(
    device_id: str = Query(..., description="Agent's registered device ID"),
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Agent polls this every 60s to check for admin commands.

    Returns up to 5 pending commands (oldest first) scoped to this device.
    Atomically marks returned commands as 'fetched' so they aren't returned again.
    """
    now = datetime.now(timezone.utc)

    commands = (
        db.query(SyncCommand)
        .filter(
            SyncCommand.device_id == device_id,
            SyncCommand.tenant_id == tenant.id,
            SyncCommand.status == "pending",
            SyncCommand.expires_at > now,
        )
        .order_by(SyncCommand.created_at.asc())
        .limit(5)
        .all()
    )

    for cmd in commands:
        cmd.status = "fetched"
        cmd.fetched_at = now
    db.commit()

    if commands:
        logger.info(f"Dispatched {len(commands)} command(s) to device={device_id}")

    return [_serialize(cmd) for cmd in commands]


@router.patch("/v1/commands/{command_id}", response_model=CommandResponse)
def acknowledge_command(
    command_id: str,
    body: AcknowledgeCommandRequest,
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Agent calls this after executing a command to report the result.

    Accepted statuses: "completed" or "failed".
    """
    if body.status not in ("completed", "failed"):
        raise HTTPException(
            status_code=400,
            detail="status must be 'completed' or 'failed'",
        )

    cmd = db.query(SyncCommand).filter(
        SyncCommand.id == command_id,
        SyncCommand.tenant_id == tenant.id,
    ).first()

    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")

    cmd.status = body.status
    cmd.completed_at = datetime.now(timezone.utc)
    if body.result:
        cmd.result = json.dumps(body.result)
    if body.error_message:
        cmd.error_message = body.error_message

    db.commit()
    db.refresh(cmd)

    logger.info(f"Command {command_id} acknowledged: {body.status}")
    return _serialize(cmd)


@router.get("/v1/commands", response_model=List[CommandResponse])
def list_commands(
    device_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Admin: list all commands for tenant, newest first."""
    query = db.query(SyncCommand).filter(SyncCommand.tenant_id == tenant.id)
    if device_id:
        query = query.filter(SyncCommand.device_id == device_id)
    if status:
        query = query.filter(SyncCommand.status == status)

    commands = query.order_by(SyncCommand.created_at.desc()).limit(limit).all()
    return [_serialize(cmd) for cmd in commands]
