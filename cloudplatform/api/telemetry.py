# -*- coding: utf-8 -*-
"""
Cloud Telemetry API Endpoints

Receives telemetry event batches from agents (log ring-buffer dumps).
Authentication uses the same device API key as all other agent endpoints.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, DateTime, Text, desc
from sqlalchemy.orm import Session

from cloudplatform.db.database import get_db
from cloudplatform.db.models import Base, Tenant
from cloudplatform.api.ingest import verify_api_key

logger = logging.getLogger(__name__)


# ── DB model ──────────────────────────────────────────────────────────────────

class TelemetryEventModel(Base):
    __tablename__ = "telemetry_events"

    event_id     = Column(String, primary_key=True, index=True)
    event_type   = Column(String, index=True, nullable=False)
    timestamp    = Column(String, index=True, nullable=False)
    severity     = Column(String, nullable=False)
    source       = Column(String, nullable=False)
    agent_id     = Column(String, index=True, nullable=False)
    tenant_id    = Column(String, index=True, nullable=False)
    agent_version = Column(String)
    python_version = Column(String)
    platform     = Column(String)
    hostname     = Column(String)
    data         = Column(Text, nullable=False)
    error_message = Column(String)
    error_code   = Column(String)
    error_stack  = Column(Text)
    created_at   = Column(DateTime, default=datetime.utcnow, index=True)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TelemetryEventRequest(BaseModel):
    event_id:      str
    event_type:    str
    timestamp:     str
    severity:      str
    source:        str
    agent_id:      str
    tenant_id:     str
    agent_version: Optional[str] = None
    python_version: Optional[str] = None
    platform:      Optional[str] = None
    hostname:      Optional[str] = None
    data:          Dict[str, Any]
    error:         Optional[Dict[str, str]] = None


class TelemetryBatchRequest(BaseModel):
    events: List[TelemetryEventRequest]


class TelemetryEventResponse(BaseModel):
    event_id:      str
    event_type:    str
    timestamp:     str
    severity:      str
    data:          Dict[str, Any]
    agent_id:      str
    tenant_id:     str = ""
    agent_version: Optional[str] = None
    hostname:      Optional[str] = None
    source:        str = ""
    error_message: Optional[str] = None
    error_code:    Optional[str] = None
    error_stack:   Optional[str] = None


class TelemetryIngestionResponse(BaseModel):
    success:  bool
    ingested: int = 0
    skipped:  int = 0
    errors:   List[Dict[str, str]] = []


class TelemetryStatsResponse(BaseModel):
    total_events:   int = 0
    by_event_type:  Dict[str, int] = {}
    by_severity:    Dict[str, int] = {}
    by_agent_id:    Dict[str, int] = {}
    recent_errors:  List[TelemetryEventResponse] = []


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/v1/telemetry", tags=["telemetry"])


@router.post("/events", response_model=TelemetryIngestionResponse)
def ingest_events(
    request: TelemetryBatchRequest,
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Receive a batch of telemetry events from an agent. Idempotent by event_id."""
    ingested = skipped = 0
    errors: List[Dict[str, str]] = []

    for ev in request.events:
        try:
            if db.query(TelemetryEventModel).filter(
                TelemetryEventModel.event_id == ev.event_id
            ).first():
                skipped += 1
                continue

            db.add(TelemetryEventModel(
                event_id=ev.event_id,
                event_type=ev.event_type,
                timestamp=ev.timestamp,
                severity=ev.severity,
                source=ev.source,
                agent_id=ev.agent_id,
                tenant_id=ev.tenant_id,
                agent_version=ev.agent_version,
                python_version=ev.python_version,
                platform=ev.platform,
                hostname=ev.hostname,
                data=json.dumps(ev.data),
                error_message=ev.error.get("message") if ev.error else None,
                error_code=ev.error.get("code") if ev.error else None,
                error_stack=ev.error.get("stack_trace") if ev.error else None,
            ))
            ingested += 1
        except Exception as e:
            logger.error(f"[Telemetry] Error processing event {ev.event_id}: {e}")
            errors.append({"event_id": ev.event_id, "error": str(e)})

    db.commit()
    logger.info(f"[Telemetry] Ingested {ingested}, skipped {skipped} duplicate(s)")
    return TelemetryIngestionResponse(success=True, ingested=ingested, skipped=skipped, errors=errors)


@router.get("/events", response_model=List[TelemetryEventResponse])
def get_events(
    event_type: Optional[str] = Query(None),
    agent_id:   Optional[str] = Query(None),
    tenant_id:  Optional[str] = Query(None),
    severity:   Optional[str] = Query(None),
    limit:      int = Query(100, ge=1, le=1000),
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Query stored telemetry events (scoped to authenticated tenant)."""
    query = db.query(TelemetryEventModel).filter(
        TelemetryEventModel.tenant_id == tenant.id
    )
    if event_type:
        query = query.filter(TelemetryEventModel.event_type == event_type)
    if agent_id:
        query = query.filter(TelemetryEventModel.agent_id == agent_id)
    if severity:
        query = query.filter(TelemetryEventModel.severity == severity)

    events = query.order_by(desc(TelemetryEventModel.created_at)).limit(limit).all()

    return [
        TelemetryEventResponse(
            event_id=e.event_id,
            event_type=e.event_type,
            timestamp=e.timestamp,
            severity=e.severity,
            data=json.loads(e.data),
            agent_id=e.agent_id,
            tenant_id=e.tenant_id,
            agent_version=e.agent_version,
            hostname=e.hostname,
            source=e.source,
            error_message=e.error_message,
            error_code=e.error_code,
            error_stack=e.error_stack,
        )
        for e in events
    ]


@router.get("/stats", response_model=TelemetryStatsResponse)
def get_stats(
    agent_id: Optional[str] = Query(None),
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Telemetry summary: counts by event type / severity / agent, plus recent errors."""
    from sqlalchemy import func

    base = db.query(TelemetryEventModel).filter(
        TelemetryEventModel.tenant_id == tenant.id
    )
    if agent_id:
        base = base.filter(TelemetryEventModel.agent_id == agent_id)

    total = base.count()

    by_type = {k: v for k, v in db.query(
        TelemetryEventModel.event_type,
        func.count(TelemetryEventModel.event_id)
    ).filter(TelemetryEventModel.tenant_id == tenant.id).group_by(
        TelemetryEventModel.event_type
    ).all()}

    by_severity = {k: v for k, v in db.query(
        TelemetryEventModel.severity,
        func.count(TelemetryEventModel.event_id)
    ).filter(TelemetryEventModel.tenant_id == tenant.id).group_by(
        TelemetryEventModel.severity
    ).all()}

    by_agent: Dict[str, int] = {}
    if not agent_id:
        by_agent = {k: v for k, v in db.query(
            TelemetryEventModel.agent_id,
            func.count(TelemetryEventModel.event_id)
        ).filter(TelemetryEventModel.tenant_id == tenant.id).group_by(
            TelemetryEventModel.agent_id
        ).all()}

    recent_errors = base.filter(
        TelemetryEventModel.severity.in_(["warning", "error", "critical"])
    ).order_by(desc(TelemetryEventModel.created_at)).limit(10).all()

    return TelemetryStatsResponse(
        total_events=total,
        by_event_type=by_type,
        by_severity=by_severity,
        by_agent_id=by_agent,
        recent_errors=[
            TelemetryEventResponse(
                event_id=e.event_id,
                event_type=e.event_type,
                timestamp=e.timestamp,
                severity=e.severity,
                data=json.loads(e.data),
                agent_id=e.agent_id,
                tenant_id=e.tenant_id,
                agent_version=e.agent_version,
                hostname=e.hostname,
                source=e.source,
                error_message=e.error_message,
                error_code=e.error_code,
                error_stack=e.error_stack,
            )
            for e in recent_errors
        ],
    )


@router.get("/events/by-tenant/{tenant_id}", response_model=List[TelemetryEventResponse])
def get_events_by_tenant(
    tenant_id: str,
    event_type: Optional[str] = Query(None),
    severity:   Optional[str] = Query(None),
    limit:      int = Query(100, ge=1, le=1000),
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    query = db.query(TelemetryEventModel).filter(
        TelemetryEventModel.tenant_id == tenant_id
    )
    if event_type:
        query = query.filter(TelemetryEventModel.event_type == event_type)
    if severity:
        query = query.filter(TelemetryEventModel.severity == severity)

    events = query.order_by(desc(TelemetryEventModel.created_at)).limit(limit).all()
    return [
        TelemetryEventResponse(
            event_id=e.event_id,
            event_type=e.event_type,
            timestamp=e.timestamp,
            severity=e.severity,
            data=json.loads(e.data),
            agent_id=e.agent_id,
            tenant_id=e.tenant_id,
            agent_version=e.agent_version,
            hostname=e.hostname,
            source=e.source,
            error_message=e.error_message,
            error_code=e.error_code,
            error_stack=e.error_stack,
        )
        for e in events
    ]
