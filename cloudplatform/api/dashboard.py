"""
Dashboard API Endpoints

Provides read-only dashboard data for frontend visualization.
Includes KPI metrics, paginated vouchers, and cash flow analysis.
"""

import logging
from typing import List, Optional, Tuple
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
import json
from sqlalchemy import func, and_, or_

from cloudplatform.db.models import (
    Tenant, Ledger, Voucher, AgentHeartbeat, SyncAuditLog,
    AccountGroup, StockItem, StockGroup, SyncCommand,
)
from cloudplatform.db.database import get_db
from cloudplatform.api.ingest import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# ────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas
# ────────────────────────────────────────────────────────────────────────────

class KPIData(BaseModel):
    """Dashboard KPI metrics."""
    total_ledgers: int
    total_vouchers: int
    last_sync: Optional[str] = None
    sync_health: str  # 'healthy', 'warning', 'error'
    recent_syncs: int = 0  # Syncs in last 24 hours


class VoucherDTO(BaseModel):
    """Voucher data for dashboard."""
    id: int
    voucher_number: Optional[str]
    date: str
    party: Optional[str]
    amount: Optional[str]
    type: str  # voucher_type


class VouchersResponse(BaseModel):
    """Paginated vouchers response."""
    data: List[VoucherDTO]
    total: int
    skip: int
    limit: int


class CashFlowData(BaseModel):
    """Cash flow trend data point."""
    month: str
    amount: float


class TenantConfig(BaseModel):
    """Tenant configuration."""
    id: str
    name: str
    logo: Optional[str] = None
    primaryColor: Optional[str] = None
    accentColor: Optional[str] = None


class LedgerDTO(BaseModel):
    id: int
    name: str
    parent: Optional[str] = None
    ledger_type: Optional[str] = None
    opening_balance: Optional[str] = None
    closing_balance: Optional[str] = None


class LedgersResponse(BaseModel):
    data: List[LedgerDTO]
    total: int
    skip: int
    limit: int


class SyncCommandDTO(BaseModel):
    id: str
    command_type: str
    status: str
    params: Optional[dict] = None
    created_at: str
    fetched_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[dict] = None
    error_message: Optional[str] = None


class SyncHistoryResponse(BaseModel):
    data: List[SyncCommandDTO]
    total: int
    skip: int
    limit: int


# ────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ────────────────────────────────────────────────────────────────────────────

def get_sync_health(db: Session, tenant_id: str) -> Tuple[str, Optional[str]]:
    """
    Determine sync health status.

    Returns: (status, last_sync_time)
    - 'healthy': Last sync within 24 hours
    - 'warning': Last sync within 7 days
    - 'error': No sync or older than 7 days
    """
    # Check most recent audit log
    latest_sync = db.query(SyncAuditLog).filter(
        SyncAuditLog.tenant_id == tenant_id,
        SyncAuditLog.action == "inserted"
    ).order_by(SyncAuditLog.received_at.desc()).first()

    if not latest_sync:
        return ("error", None)

    now = datetime.now(timezone.utc)
    received = latest_sync.received_at
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    hours_ago = (now - received).total_seconds() / 3600

    if hours_ago <= 24:
        return ("healthy", received.isoformat())
    elif hours_ago <= 168:  # 7 days
        return ("warning", received.isoformat())
    else:
        return ("error", received.isoformat())


# ────────────────────────────────────────────────────────────────────────────
# Dashboard Endpoints
# ────────────────────────────────────────────────────────────────────────────

@router.get("/kpis", response_model=KPIData)
def get_kpis(
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Get KPI metrics for dashboard.

    Returns:
    - total_ledgers: Total ledgers synced
    - total_vouchers: Total vouchers synced
    - last_sync: ISO timestamp of last successful sync
    - sync_health: Status (healthy/warning/error)
    - recent_syncs: Number of syncs in last 24 hours
    """
    # Count ledgers and vouchers
    ledger_count = db.query(func.count(Ledger.id)).filter(
        Ledger.tenant_id == tenant.id
    ).scalar() or 0

    voucher_count = db.query(func.count(Voucher.id)).filter(
        Voucher.tenant_id == tenant.id
    ).scalar() or 0

    # Get sync health
    health, last_sync = get_sync_health(db, tenant.id)

    # Count recent syncs (last 24 hours)
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    recent_syncs = db.query(func.count(SyncAuditLog.id)).filter(
        and_(
            SyncAuditLog.tenant_id == tenant.id,
            SyncAuditLog.action == "inserted",
            SyncAuditLog.received_at >= day_ago
        )
    ).scalar() or 0

    return KPIData(
        total_ledgers=ledger_count,
        total_vouchers=voucher_count,
        last_sync=last_sync,
        sync_health=health,
        recent_syncs=recent_syncs,
    )


@router.get("/vouchers", response_model=VouchersResponse)
def get_vouchers(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Get paginated vouchers for dashboard.

    Query Parameters:
    - skip: Number of records to skip (default 0)
    - limit: Number of records to return (default 50, max 100)

    Returns: Paginated list of vouchers, most recent first.
    """
    # Get total count
    total = db.query(func.count(Voucher.id)).filter(
        Voucher.tenant_id == tenant.id
    ).scalar() or 0

    # Get paginated vouchers (most recent first)
    vouchers = db.query(Voucher).filter(
        Voucher.tenant_id == tenant.id
    ).order_by(
        Voucher.date.desc(),
        Voucher.id.desc()
    ).offset(skip).limit(limit).all()

    return VouchersResponse(
        data=[
            VoucherDTO(
                id=v.id,
                voucher_number=v.voucher_number,
                date=v.date,
                party=v.party,
                amount=v.amount,
                type=v.voucher_type,
            )
            for v in vouchers
        ],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/cash-flow", response_model=List[CashFlowData])
def get_cash_flow(
    period: str = Query("monthly", pattern="^(daily|weekly|monthly)$"),
    months: int = Query(6, ge=1, le=12),
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Get cash flow trend analysis.

    Query Parameters:
    - period: Aggregation period (daily, weekly, monthly) - default: monthly
    - months: Number of periods to return (default 6, max 12)

    Returns: List of cash flow data points with inflow/outflow aggregated.
    """
    # Calculate date range
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=30 * months)

    # Query vouchers in date range, grouped by month
    vouchers = db.query(
        func.substr(Voucher.date, 1, 7).label("period"),  # YYYY-MM
        func.sum(
            func.cast(
                func.replace(Voucher.amount, ",", ""),
                db.types.Numeric
            )
        ).label("total_amount")
    ).filter(
        and_(
            Voucher.tenant_id == tenant.id,
            Voucher.date >= start_date.strftime("%Y-%m-%d"),
            Voucher.date <= now.strftime("%Y-%m-%d")
        )
    ).group_by(
        func.substr(Voucher.date, 1, 7)
    ).order_by(
        func.substr(Voucher.date, 1, 7)
    ).all()

    # Format response
    result = [
        CashFlowData(
            month=v.period,
            amount=float(v.total_amount or 0),
        )
        for v in vouchers
    ]

    # Fill missing months with zero
    if result:
        first_month = datetime.strptime(result[0].month, "%Y-%m")
        current = first_month
        filled_result = []

        for _ in range(len(result)):
            month_str = current.strftime("%Y-%m")
            existing = next((r for r in result if r.month == month_str), None)
            filled_result.append(existing or CashFlowData(month=month_str, amount=0.0))
            current += timedelta(days=32)  # Roughly 1 month

        return filled_result

    return result


@router.get("/tenant-config", response_model=TenantConfig)
def get_tenant_config(
    tenant: Tenant = Depends(verify_api_key),
):
    """
    Get tenant configuration for dashboard branding.

    Returns: Tenant configuration including name and colors.
    """
    return TenantConfig(
        id=tenant.id,
        name=tenant.name,
        logo=None,
        primaryColor="#0D9488",  # Teal
        accentColor="#D97706",  # Amber
    )


@router.get("/ledgers", response_model=LedgersResponse)
def get_ledgers(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    parent: Optional[str] = Query(None),
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    base = db.query(Ledger).filter(Ledger.tenant_id == tenant.id)

    if search:
        base = base.filter(
            or_(
                Ledger.name.ilike(f"%{search}%"),
                Ledger.parent.ilike(f"%{search}%"),
            )
        )
    if parent:
        base = base.filter(Ledger.parent == parent)

    total = base.count()
    rows = base.order_by(Ledger.name).offset(skip).limit(limit).all()

    return LedgersResponse(
        data=[
            LedgerDTO(
                id=r.id, name=r.name, parent=r.parent,
                ledger_type=r.ledger_type,
                opening_balance=r.opening_balance,
                closing_balance=r.closing_balance,
            )
            for r in rows
        ],
        total=total, skip=skip, limit=limit,
    )


@router.get("/ledgers/groups")
def get_ledger_groups(
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    parents = (
        db.query(Ledger.parent)
        .filter(Ledger.tenant_id == tenant.id, Ledger.parent.isnot(None))
        .distinct()
        .order_by(Ledger.parent)
        .all()
    )
    return [p[0] for p in parents]


@router.get("/sync-history", response_model=SyncHistoryResponse)
def get_sync_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    base = db.query(SyncCommand).filter(SyncCommand.tenant_id == tenant.id)
    if status:
        base = base.filter(SyncCommand.status == status)

    total = base.count()
    rows = base.order_by(SyncCommand.created_at.desc()).offset(skip).limit(limit).all()

    return SyncHistoryResponse(
        data=[
            SyncCommandDTO(
                id=c.id,
                command_type=c.command_type,
                status=c.status,
                params=json.loads(c.params) if c.params else None,
                created_at=c.created_at.isoformat() if c.created_at else "",
                fetched_at=c.fetched_at.isoformat() if c.fetched_at else None,
                completed_at=c.completed_at.isoformat() if c.completed_at else None,
                result=json.loads(c.result) if c.result else None,
                error_message=c.error_message,
            )
            for c in rows
        ],
        total=total, skip=skip, limit=limit,
    )


@router.get("/health")
def dashboard_health():
    """Health check for dashboard API."""
    return {"status": "ok", "service": "tally-sync-dashboard"}
