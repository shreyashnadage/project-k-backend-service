# -*- coding: utf-8 -*-
"""
Company Mapping API — register and list Tally companies for a client.

An MSME client may run multiple Tally companies on one or more devices.
This endpoint lets the agent register discovered companies and lets
the admin see which companies are available for sync.
"""

import logging
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from cloudplatform.db.models import CompanyMapping, Tenant
from cloudplatform.db.database import get_db
from cloudplatform.api.ingest import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["companies"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class RegisterCompanyRequest(BaseModel):
    device_id: str
    company_name: str
    company_guid: Optional[str] = None
    formal_name: Optional[str] = None
    gst_number: Optional[str] = None
    state: Optional[str] = None


class RegisterCompaniesRequest(BaseModel):
    device_id: str
    companies: List[RegisterCompanyRequest]


class CompanyResponse(BaseModel):
    id: int
    client_id: str
    device_id: str
    company_name: str
    company_guid: Optional[str] = None
    formal_name: Optional[str] = None
    gst_number: Optional[str] = None
    state: Optional[str] = None
    is_active: bool
    last_synced_at: Optional[str] = None
    created_at: str


class CompanyListResponse(BaseModel):
    count: int
    companies: List[CompanyResponse]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _serialize(m: CompanyMapping) -> CompanyResponse:
    return CompanyResponse(
        id=m.id,
        client_id=m.client_id,
        device_id=m.device_id,
        company_name=m.company_name,
        company_guid=m.company_guid,
        formal_name=m.formal_name,
        gst_number=m.gst_number,
        state=m.state,
        is_active=m.is_active,
        last_synced_at=m.last_synced_at.isoformat() if m.last_synced_at else None,
        created_at=m.created_at.isoformat() if m.created_at else "",
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/v1/companies", response_model=CompanyListResponse, status_code=201)
def register_companies(
    body: RegisterCompaniesRequest,
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Agent registers discovered Tally companies.
    Idempotent: re-registering the same company updates it instead of duplicating.
    """
    results = []
    for comp in body.companies:
        existing = db.query(CompanyMapping).filter(
            CompanyMapping.client_id == tenant.id,
            CompanyMapping.device_id == body.device_id,
            CompanyMapping.company_name == comp.company_name,
        ).first()

        if existing:
            if comp.company_guid:
                existing.company_guid = comp.company_guid
            if comp.formal_name:
                existing.formal_name = comp.formal_name
            if comp.gst_number:
                existing.gst_number = comp.gst_number
            if comp.state:
                existing.state = comp.state
            existing.is_active = True
            results.append(existing)
        else:
            m = CompanyMapping(
                client_id=tenant.id,
                device_id=body.device_id,
                company_name=comp.company_name,
                company_guid=comp.company_guid,
                formal_name=comp.formal_name,
                gst_number=comp.gst_number,
                state=comp.state,
            )
            db.add(m)
            results.append(m)

    db.commit()
    for r in results:
        db.refresh(r)

    logger.info(f"Registered {len(results)} company(ies) for client {tenant.id}")
    return CompanyListResponse(
        count=len(results),
        companies=[_serialize(r) for r in results],
    )


@router.get("/v1/companies", response_model=CompanyListResponse)
def list_companies(
    device_id: Optional[str] = Query(None),
    active_only: bool = Query(True),
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """List all mapped companies for the tenant, optionally filtered by device."""
    query = db.query(CompanyMapping).filter(CompanyMapping.client_id == tenant.id)
    if device_id:
        query = query.filter(CompanyMapping.device_id == device_id)
    if active_only:
        query = query.filter(CompanyMapping.is_active == True)

    mappings = query.order_by(CompanyMapping.company_name).all()
    return CompanyListResponse(
        count=len(mappings),
        companies=[_serialize(m) for m in mappings],
    )


@router.patch("/v1/companies/{company_id}")
def update_company(
    company_id: int,
    is_active: Optional[bool] = Query(None),
    last_synced_at: Optional[str] = Query(None),
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Update a company mapping (activate/deactivate, update last sync time)."""
    m = db.query(CompanyMapping).filter(
        CompanyMapping.id == company_id,
        CompanyMapping.client_id == tenant.id,
    ).first()
    if not m:
        raise HTTPException(status_code=404, detail="Company mapping not found")

    if is_active is not None:
        m.is_active = is_active
    if last_synced_at:
        m.last_synced_at = datetime.fromisoformat(last_synced_at)

    db.commit()
    db.refresh(m)
    return _serialize(m)
