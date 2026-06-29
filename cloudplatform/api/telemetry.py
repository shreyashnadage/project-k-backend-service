"""
Cloud Telemetry API Endpoints

Receives telemetry events from agents and stores them in PostgreSQL.
Provides endpoints for querying events and metrics.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, DateTime, Text, Integer, desc
from sqlalchemy.orm import Session

from cloudplatform.db.database import get_db
from cloudplatform.db.models import Base

logger = logging.getLogger(__name__)

# ============================================================================
# Database Models
# ============================================================================


class TelemetryEventModel(Base):
    """Telemetry event database model."""
    __tablename__ = "telemetry_events"

    event_id = Column(String, primary_key=True, index=True)
    event_type = Column(String, index=True, nullable=False)
    timestamp = Column(String, index=True, nullable=False)
    severity = Column(String, nullable=False)
    source = Column(String, nullable=False)
    agent_id = Column(String, index=True, nullable=False)
    tenant_id = Column(String, index=True, nullable=False)
    agent_version = Column(String)
    python_version = Column(String)
    platform = Column(String)
    hostname = Column(String)
    data = Column(Text, nullable=False)  # JSON string
    error_message = Column(String)
    error_code = Column(String)
    error_stack = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ============================================================================
# Pydantic Models
# ============================================================================


class TelemetryEventRequest(BaseModel):
    """Single telemetry event in request."""
    event_id: str
    event_type: str
    timestamp: str
    severity: str
    source: str
    agent_id: str
    tenant_id: str
    agent_version: str = None
    python_version: str = None
    platform: str = None
    hostname: str = None
    data: Dict[str, Any]
    error: Dict[str, str] = None


class TelemetryBatchRequest(BaseModel):
    """Batch of telemetry events."""
    events: List[TelemetryEventRequest]


class TelemetryEventResponse(BaseModel):
    """Single telemetry event in response."""
    event_id: str
    event_type: str
    timestamp: str
    severity: str
    data: Dict[str, Any]
    agent_id: str
    tenant_id: str = ""
    agent_version: Optional[str] = None
    hostname: Optional[str] = None
    source: str = ""
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    error_stack: Optional[str] = None


class TelemetryIngestionResponse(BaseModel):
    """Response from telemetry ingestion."""
    success: bool
    ingested: int = 0
    skipped: int = 0
    errors: List[Dict[str, str]] = []


class TelemetryStatsResponse(BaseModel):
    """Telemetry statistics response."""
    total_events: int = 0
    by_event_type: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    by_agent_id: Dict[str, int] = {}
    recent_errors: List[TelemetryEventResponse] = []


# ============================================================================
# Router
# ============================================================================

router = APIRouter(prefix="/v1/telemetry", tags=["telemetry"])


@router.post(
    "/events",
    response_model=TelemetryIngestionResponse,
    summary="Ingest telemetry events from agents",
)
def ingest_events(
    request: TelemetryBatchRequest,
    x_api_key: str = Header(None),
    db: Session = Depends(get_db),
):
    """
    Receive batch of telemetry events from agents.

    Features:
    - Idempotent (deduplicates by event_id)
    - Validates API key
    - Stores in PostgreSQL
    - Returns ingestion status

    Args:
        request: Batch of events
        x_api_key: API key for authentication
        db: Database session

    Returns:
        TelemetryIngestionResponse with status
    """
    # Validate API key
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing x-api-key header")

    # TODO: Validate API key against database or config

    ingested = 0
    skipped = 0
    errors = []


    try:
        for event_req in request.events:
            try:
                # Check if event already exists (idempotency)
                existing = db.query(TelemetryEventModel).filter(
                    TelemetryEventModel.event_id == event_req.event_id
                ).first()

                if existing:
                    skipped += 1
                    continue

                # Create event record
                import json
                event = TelemetryEventModel(
                    event_id=event_req.event_id,
                    event_type=event_req.event_type,
                    timestamp=event_req.timestamp,
                    severity=event_req.severity,
                    source=event_req.source,
                    agent_id=event_req.agent_id,
                    tenant_id=event_req.tenant_id,
                    agent_version=event_req.agent_version,
                    python_version=event_req.python_version,
                    platform=event_req.platform,
                    hostname=event_req.hostname,
                    data=json.dumps(event_req.data),
                    error_message=event_req.error.get("message") if event_req.error else None,
                    error_code=event_req.error.get("code") if event_req.error else None,
                    error_stack=event_req.error.get("stack_trace") if event_req.error else None,
                )

                db.add(event)
                ingested += 1

            except Exception as e:
                logger.error(f"Error processing event {event_req.event_id}: {e}")
                errors.append({
                    "event_id": event_req.event_id,
                    "error": str(e),
                })

        db.commit()
        logger.info(f"Ingested {ingested} telemetry events, skipped {skipped}")

        return TelemetryIngestionResponse(
            success=True,
            ingested=ingested,
            skipped=skipped,
            errors=errors,
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Error ingesting telemetry batch: {e}")
        raise HTTPException(status_code=500, detail="Failed to ingest events")

    finally:
        db.close()


@router.get(
    "/events",
    response_model=List[TelemetryEventResponse],
    summary="Query telemetry events",
)
async def get_events(
    event_type: str = Query(None, description="Filter by event type"),
    agent_id: str = Query(None, description="Filter by agent ID"),
    tenant_id: str = Query(None, description="Filter by tenant/client ID"),
    severity: str = Query(None, description="Filter by severity"),
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
    db: Session = Depends(get_db),
):
    """
    Query telemetry events.

    Supports filtering and limits results for dashboard/debugging.

    Args:
        event_type: Optional event type filter
        agent_id: Optional agent ID filter
        severity: Optional severity filter
        limit: Max results
        db: Database session

    Returns:
        List of telemetry events
    """

    try:
        query = db.query(TelemetryEventModel)

        if event_type:
            query = query.filter(TelemetryEventModel.event_type == event_type)

        if agent_id:
            query = query.filter(TelemetryEventModel.agent_id == agent_id)

        if tenant_id:
            query = query.filter(TelemetryEventModel.tenant_id == tenant_id)

        if severity:
            query = query.filter(TelemetryEventModel.severity == severity)

        events = query.order_by(desc(TelemetryEventModel.created_at)).limit(limit).all()

        import json
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

    finally:
        db.close()


@router.get(
    "/stats",
    response_model=TelemetryStatsResponse,
    summary="Get telemetry statistics",
)
async def get_stats(
    agent_id: str = Query(None, description="Filter by agent ID"),
    db: Session = Depends(get_db),
):
    """
    Get telemetry statistics.

    Shows event counts, distributions, and recent errors.

    Args:
        agent_id: Optional filter to specific agent
        db: Database session

    Returns:
        TelemetryStatsResponse with statistics
    """
    db = next(get_db())

    try:
        # Total events
        query = db.query(TelemetryEventModel)
        if agent_id:
            query = query.filter(TelemetryEventModel.agent_id == agent_id)

        total = query.count()

        # By event type
        from sqlalchemy import func
        by_type = {}
        for event_type, count in db.query(
            TelemetryEventModel.event_type,
            func.count(TelemetryEventModel.event_id)
        ).filter(
            TelemetryEventModel.agent_id == agent_id if agent_id else True
        ).group_by(TelemetryEventModel.event_type).all():
            by_type[event_type] = count

        # By severity
        by_severity = {}
        for severity, count in db.query(
            TelemetryEventModel.severity,
            func.count(TelemetryEventModel.event_id)
        ).filter(
            TelemetryEventModel.agent_id == agent_id if agent_id else True
        ).group_by(TelemetryEventModel.severity).all():
            by_severity[severity] = count

        # By agent
        by_agent = {}
        if not agent_id:
            for aid, count in db.query(
                TelemetryEventModel.agent_id,
                func.count(TelemetryEventModel.event_id)
            ).group_by(TelemetryEventModel.agent_id).all():
                by_agent[aid] = count

        # Recent errors
        import json
        recent_errors = db.query(TelemetryEventModel).filter(
            TelemetryEventModel.severity.in_(["warning", "error", "critical"])
        ).filter(
            TelemetryEventModel.agent_id == agent_id if agent_id else True
        ).order_by(
            desc(TelemetryEventModel.created_at)
        ).limit(10).all()

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
                )
                for e in recent_errors
            ],
        )

    finally:
        db.close()


@router.get(
    "/events/by-tenant/{tenant_id}",
    response_model=List[TelemetryEventResponse],
    summary="Get telemetry events for a specific tenant/client",
)
async def get_events_by_tenant(
    tenant_id: str,
    event_type: str = Query(None),
    severity: str = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    import json
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
