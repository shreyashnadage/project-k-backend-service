# -*- coding: utf-8 -*-
"""
Command Channel API

Cloud → Agent command pipeline.

Agents long-poll GET /v1/commands/pending?wait=55 every cycle.
The server holds the connection up to `wait` seconds, returning as soon
as commands are available (or an empty list on timeout).

On discover_companies completion, a DeviceSyncSchedule is auto-created so
the backend scheduler will keep sending sync_all_companies going forward.
"""

import asyncio
import json
import logging
import time
from typing import List, Optional
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from cloudplatform.db.models import (
    SyncCommand, DeviceRegistration, Tenant, DeviceSyncSchedule,
)
from cloudplatform.db.database import get_db, SessionLocal
from cloudplatform.api.ingest import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["commands"])

COMMAND_TTL_HOURS = 24
DEFAULT_SYNC_INTERVAL_SECONDS = 1800  # 30 min

ALLOWED_COMMAND_TYPES = {
    "sync_ledgers",
    "sync_ledgers_by_group",
    "sync_ledger_one",
    "sync_groups",
    "sync_vouchers",
    "sync_vouchers_by_type",
    "sync_stock",
    "sync_stock_by_group",
    "sync_full",
    "sync_all_companies",
    "discover_companies",
    "health_check",
    "push_telemetry",
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
    status: str          # "completed" or "failed"
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


def _fetch_and_mark_pending(db: Session, device_id: str, tenant_id: str) -> List[SyncCommand]:
    """Return up to 5 pending commands for device, atomically marking them fetched."""
    now = datetime.now(timezone.utc)
    commands = (
        db.query(SyncCommand)
        .filter(
            SyncCommand.device_id == device_id,
            SyncCommand.tenant_id == tenant_id,
            SyncCommand.status == "pending",
            SyncCommand.expires_at > now,
        )
        .order_by(SyncCommand.created_at.asc())
        .limit(5)
        .all()
    )
    if commands:
        for cmd in commands:
            cmd.status = "fetched"
            cmd.fetched_at = now
        db.commit()
    return commands


def _touch_heartbeat(device_id: str) -> None:
    """Update DeviceRegistration.last_sync_at — used as online/offline signal."""
    db = SessionLocal()
    try:
        device = db.query(DeviceRegistration).filter(
            DeviceRegistration.device_id == device_id
        ).first()
        if device:
            device.last_sync_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as e:
        logger.warning(f"[Commands] Heartbeat update failed for {device_id}: {e}")
        db.rollback()
    finally:
        db.close()


def _upsert_sync_schedule(tenant_id: str, device_id: str) -> None:
    """
    Create or re-activate a DeviceSyncSchedule when discover_companies completes.
    Also immediately queues the first sync_all_companies so the agent syncs
    without waiting for the scheduler's next 60s tick.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        schedule = db.query(DeviceSyncSchedule).filter(
            DeviceSyncSchedule.tenant_id == tenant_id,
            DeviceSyncSchedule.device_id == device_id,
        ).first()

        if schedule:
            schedule.is_active = True
        else:
            schedule = DeviceSyncSchedule(
                tenant_id=tenant_id,
                device_id=device_id,
                interval_seconds=DEFAULT_SYNC_INTERVAL_SECONDS,
                is_active=True,
            )
            db.add(schedule)

        # Queue immediate first sync (don't wait for scheduler's next tick)
        already_queued = db.query(SyncCommand).filter(
            SyncCommand.device_id == device_id,
            SyncCommand.tenant_id == tenant_id,
            SyncCommand.command_type == "sync_all_companies",
            SyncCommand.status.in_(["pending", "fetched"]),
        ).first()

        if not already_queued:
            db.add(SyncCommand(
                tenant_id=tenant_id,
                device_id=device_id,
                command_type="sync_all_companies",
                params="{}",
                status="pending",
                created_by="auto:discover_companies",
                created_at=now,
                expires_at=now + timedelta(hours=2),
            ))
            logger.info(f"[Commands] Auto-queued sync_all_companies for device {device_id}")

        schedule.last_scheduled_at = now
        db.commit()
        logger.info(f"[Commands] DeviceSyncSchedule upserted for device {device_id}")

    except Exception as e:
        logger.error(f"[Commands] Failed to upsert sync schedule for {device_id}: {e}")
        db.rollback()
    finally:
        db.close()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/v1/commands", response_model=CommandResponse, status_code=201)
def create_command(
    body: CreateCommandRequest,
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Admin queues an on-demand command targeting a specific agent device."""
    if body.command_type not in ALLOWED_COMMAND_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown command_type '{body.command_type}'. "
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

    logger.info(f"[Commands] Created: id={cmd.id} type={cmd.command_type} device={cmd.device_id}")
    return _serialize(cmd)


@router.get("/v1/commands/pending", response_model=List[CommandResponse])
async def get_pending_commands(
    device_id: str = Query(..., description="Agent's registered device ID"),
    wait: int = Query(0, ge=0, le=60, description="Long-poll hold seconds (agent sends 55)"),
    tenant: Tenant = Depends(verify_api_key),
):
    """
    Agent polls this to receive queued commands.

    Supports long-polling: if wait>0, the server holds the connection until a
    command arrives or the timeout expires. The agent sends wait=55 with a 65s
    request timeout, so we hold for up to 55s and return immediately on first hit.

    Each poll also updates DeviceRegistration.last_sync_at as a heartbeat signal.
    """
    # Heartbeat — non-blocking, best-effort (fresh session in threadpool)
    await asyncio.get_event_loop().run_in_executor(None, _touch_heartbeat, device_id)

    tenant_id = tenant.id
    deadline = time.monotonic() + wait

    while True:
        # Each iteration gets a fresh short-lived session to avoid holding a
        # connection across asyncio.sleep() calls.
        with SessionLocal() as db:
            commands = _fetch_and_mark_pending(db, device_id, tenant_id)
            if commands:
                return [_serialize(cmd) for cmd in commands]

        remaining = deadline - time.monotonic()
        if remaining <= 1.5:
            return []

        await asyncio.sleep(2.0)


@router.patch("/v1/commands/{command_id}", response_model=CommandResponse)
def acknowledge_command(
    command_id: str,
    body: AcknowledgeCommandRequest,
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Agent reports a command result.

    Special side-effect: when discover_companies completes successfully, a
    DeviceSyncSchedule is upserted so the backend scheduler begins driving
    periodic sync_all_companies commands automatically.
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

    logger.info(f"[Commands] Acknowledged {command_id}: {body.status}")

    # Auto-activate recurring sync when device has discovered its companies
    if cmd.command_type == "discover_companies" and body.status == "completed":
        _upsert_sync_schedule(tenant_id=tenant.id, device_id=cmd.device_id)

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
