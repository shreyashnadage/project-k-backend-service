"""
Watermark API — agents store and retrieve their sync progress.

Each watermark tracks the last successfully synced position for a
(device, company, resource_type) tuple. This enables incremental sync:
the agent only pulls data newer than its watermark.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from cloudplatform.db.models import SyncWatermark, Tenant
from cloudplatform.db.database import get_db
from cloudplatform.api.ingest import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["watermarks"])


class WatermarkResponse(BaseModel):
    device_id: str
    company_guid: str
    resource_type: str
    watermark_value: str
    records_synced: int
    updated_at: str


class UpdateWatermarkRequest(BaseModel):
    device_id: str
    company_guid: str
    resource_type: str
    watermark_value: str
    records_synced: int = 0


@router.get("/v1/watermarks", response_model=Optional[WatermarkResponse])
def get_watermark(
    device_id: str = Query(...),
    company_guid: str = Query(...),
    resource_type: str = Query(...),
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Get the current sync watermark for a specific (device, company, resource).
    Returns null if no watermark exists (first sync).
    """
    wm = db.query(SyncWatermark).filter(
        SyncWatermark.tenant_id == tenant.id,
        SyncWatermark.device_id == device_id,
        SyncWatermark.company_guid == company_guid,
        SyncWatermark.resource_type == resource_type,
    ).first()

    if not wm:
        return None

    return WatermarkResponse(
        device_id=wm.device_id,
        company_guid=wm.company_guid,
        resource_type=wm.resource_type,
        watermark_value=wm.watermark_value,
        records_synced=wm.records_synced,
        updated_at=wm.updated_at.isoformat() if wm.updated_at else "",
    )


@router.put("/v1/watermarks", response_model=WatermarkResponse)
def update_watermark(
    body: UpdateWatermarkRequest,
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Create or advance a sync watermark after a successful sync.
    Upserts: creates if new, updates if exists.
    """
    wm = db.query(SyncWatermark).filter(
        SyncWatermark.tenant_id == tenant.id,
        SyncWatermark.device_id == body.device_id,
        SyncWatermark.company_guid == body.company_guid,
        SyncWatermark.resource_type == body.resource_type,
    ).first()

    now = datetime.now(timezone.utc)

    if wm:
        wm.watermark_value = body.watermark_value
        wm.records_synced = body.records_synced
        wm.updated_at = now
    else:
        wm = SyncWatermark(
            tenant_id=tenant.id,
            device_id=body.device_id,
            company_guid=body.company_guid,
            resource_type=body.resource_type,
            watermark_value=body.watermark_value,
            records_synced=body.records_synced,
            updated_at=now,
        )
        db.add(wm)

    db.commit()
    db.refresh(wm)

    logger.info(
        f"Watermark updated: {body.resource_type} for {body.company_guid} "
        f"on device {body.device_id} → {body.watermark_value}"
    )

    return WatermarkResponse(
        device_id=wm.device_id,
        company_guid=wm.company_guid,
        resource_type=wm.resource_type,
        watermark_value=wm.watermark_value,
        records_synced=wm.records_synced,
        updated_at=wm.updated_at.isoformat(),
    )
