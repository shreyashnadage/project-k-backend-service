"""
Ingest API Endpoints

Receives extracted data from agents and stores in database.
Handles idempotent deduplication and request validation.
"""

import hashlib
import json
import logging
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session


from cloudplatform.db.models import (
    Tenant, Ledger, Voucher, SyncAuditLog, DeviceRegistration, Client,
    AccountGroup, StockItem, StockGroup,
)
from cloudplatform.db.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# ────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas (Request/Response)
# ────────────────────────────────────────────────────────────────────────────

class LedgerPayload(BaseModel):
    """Single ledger from agent."""
    company_guid: str
    company_name: str
    ledger_guid: str
    name: str
    parent: Optional[str] = None
    ledger_type: Optional[str] = None
    opening_balance: Optional[str] = None
    closing_balance: Optional[str] = None


class VoucherPayload(BaseModel):
    """Single voucher from agent."""
    company_guid: str
    company_name: str
    voucher_guid: str
    voucher_type: str
    voucher_number: Optional[str] = None
    date: str  # YYYY-MM-DD
    party: Optional[str] = None
    narration: Optional[str] = None
    amount: Optional[str] = None
    agent_version: Optional[str] = None

    @field_validator("voucher_type")
    @classmethod
    def validate_voucher_type(cls, v):
        """Validate voucher type is known."""
        allowed = {"Sales", "Purchase", "Receipt", "Payment", "Journal", "Debit Note", "Credit Note"}
        if v not in allowed:
            logger.warning(f"Uncommon voucher type: {v}")
        return v

    @field_validator("date")
    @classmethod
    def validate_date(cls, v):
        """Validate date format YYYY-MM-DD."""
        try:
            from datetime import datetime
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Invalid date format: {v}. Expected YYYY-MM-DD")
        return v


class LedgerBatch(BaseModel):
    """Batch of ledgers from agent."""
    tenant_id: str
    ledgers: List[LedgerPayload]


class VoucherBatch(BaseModel):
    """Batch of vouchers from agent."""
    tenant_id: str
    vouchers: List[VoucherPayload]


class IngestResponse(BaseModel):
    """Response from ingest endpoint."""
    accepted: int
    duplicates: int
    updated: int = 0
    errors: int
    message: str


# ────────────────────────────────────────────────────────────────────────────
# Authentication
# ────────────────────────────────────────────────────────────────────────────

def verify_api_key(x_api_key: str = Header(...), db: Session = Depends(get_db)) -> Tenant:
    """
    Verify API key. Accepts two key types:
    1. Tenant API key (SHA-256 hashed in Tenant.api_key_hash)
    2. Device API key (plain text in DeviceRegistration.api_key) — used by registered agents.
       For device keys, returns a synthetic Tenant-like object built from the Client record.
    """
    # Path 1: legacy tenant key (hashed)
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    tenant = db.query(Tenant).filter(
        Tenant.api_key_hash == key_hash,
        Tenant.is_active == True,
    ).first()
    if tenant:
        return tenant

    # Path 2: device registration key (plain text stored in DeviceRegistration)
    device = db.query(DeviceRegistration).filter(
        DeviceRegistration.api_key == x_api_key,
        DeviceRegistration.status == "active",
    ).first()
    if device:
        client = db.query(Client).filter(Client.client_id == device.client_id).first()
        # Auto-provision a Tenant so data storage is consistent with dashboard reads
        tenant = db.query(Tenant).filter(Tenant.id == device.client_id).first()
        if not tenant:
            tenant = Tenant(
                id=device.client_id,
                name=client.company_name if client else device.device_name,
                api_key_hash=key_hash,
                is_active=True,
            )
            db.add(tenant)
            db.commit()
        return tenant

    raise HTTPException(status_code=401, detail="Invalid or inactive API key")


# ────────────────────────────────────────────────────────────────────────────
# Endpoints
# ────────────────────────────────────────────────────────────────────────────

@router.post("/v1/ledgers", response_model=IngestResponse)
def ingest_ledgers(batch: LedgerBatch, tenant: Tenant = Depends(verify_api_key), db: Session = Depends(get_db)):
    """
    Ingest ledger master data.

    Idempotent: sending the same ledger twice results in 1 accepted, 1 duplicate.
    """
    if tenant.id != batch.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant ID mismatch")

    accepted = updated = duplicates = errors = 0

    for ledger in batch.ledgers:
        try:
            existing = db.query(Ledger).filter(
                Ledger.tenant_id == tenant.id,
                Ledger.company_guid == ledger.company_guid,
                Ledger.ledger_guid == ledger.ledger_guid,
            ).first()

            if existing:
                changed = False
                for field in ("name", "parent", "ledger_type", "opening_balance", "closing_balance"):
                    new_val = getattr(ledger, field)
                    if new_val is not None and getattr(existing, field) != new_val:
                        setattr(existing, field, new_val)
                        changed = True
                if changed:
                    db.flush()
                    updated += 1
                    action = "updated"
                else:
                    duplicates += 1
                    action = "duplicate"
            else:
                db.add(Ledger(
                    tenant_id=tenant.id,
                    company_guid=ledger.company_guid,
                    ledger_guid=ledger.ledger_guid,
                    name=ledger.name,
                    parent=ledger.parent,
                    ledger_type=ledger.ledger_type,
                    opening_balance=ledger.opening_balance,
                    closing_balance=ledger.closing_balance,
                ))
                db.flush()
                accepted += 1
                action = "inserted"

            db.add(SyncAuditLog(
                tenant_id=tenant.id,
                company_guid=ledger.company_guid,
                record_type="ledger",
                record_guid=ledger.ledger_guid,
                action=action,
                transmitted_at=datetime.now(timezone.utc),
            ))
        except Exception as e:
            db.rollback()
            errors += 1
            logger.error(f"Error ingesting ledger {ledger.ledger_guid}: {e}")

    db.commit()

    return IngestResponse(
        accepted=accepted,
        updated=updated,
        duplicates=duplicates,
        errors=errors,
        message=f"Ledgers: {accepted} new, {updated} updated, {duplicates} unchanged, {errors} errors"
    )


@router.post("/v1/vouchers", response_model=IngestResponse)
def ingest_vouchers(batch: VoucherBatch, tenant: Tenant = Depends(verify_api_key), db: Session = Depends(get_db)):
    """
    Ingest voucher (transaction) data.

    Idempotent: sending the same voucher twice results in 1 accepted, 1 duplicate.
    Same voucher_guid from same company = duplicate (no duplicate key errors).
    """
    if tenant.id != batch.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant ID mismatch")

    accepted = updated = duplicates = errors = 0

    for voucher in batch.vouchers:
        try:
            existing = db.query(Voucher).filter(
                Voucher.tenant_id == tenant.id,
                Voucher.company_guid == voucher.company_guid,
                Voucher.voucher_guid == voucher.voucher_guid,
            ).first()

            raw_data = {
                "voucher_type": voucher.voucher_type,
                "party": voucher.party,
                "narration": voucher.narration,
                "amount": voucher.amount,
            }

            if existing:
                changed = False
                for field in ("voucher_type", "voucher_number", "date", "party", "narration", "amount"):
                    new_val = getattr(voucher, field)
                    if new_val is not None and getattr(existing, field) != new_val:
                        setattr(existing, field, new_val)
                        changed = True
                if changed:
                    existing.raw_data = json.dumps(raw_data, ensure_ascii=False)
                    db.flush()
                    updated += 1
                    action = "updated"
                else:
                    duplicates += 1
                    action = "duplicate"
            else:
                db.add(Voucher(
                    tenant_id=tenant.id,
                    company_guid=voucher.company_guid,
                    voucher_guid=voucher.voucher_guid,
                    voucher_type=voucher.voucher_type,
                    voucher_number=voucher.voucher_number,
                    date=voucher.date,
                    party=voucher.party,
                    narration=voucher.narration,
                    amount=voucher.amount,
                    raw_data=json.dumps(raw_data, ensure_ascii=False),
                    agent_version=voucher.agent_version,
                ))
                db.flush()
                accepted += 1
                action = "inserted"

            db.add(SyncAuditLog(
                tenant_id=tenant.id,
                company_guid=voucher.company_guid,
                record_type="voucher",
                record_guid=voucher.voucher_guid,
                action=action,
                transmitted_at=datetime.now(timezone.utc),
            ))
        except Exception as e:
            db.rollback()
            errors += 1
            logger.error(f"Error ingesting voucher {voucher.voucher_guid}: {e}")

    db.commit()

    return IngestResponse(
        accepted=accepted,
        updated=updated,
        duplicates=duplicates,
        errors=errors,
        message=f"Vouchers: {accepted} new, {updated} updated, {duplicates} unchanged, {errors} errors"
    )


# ────────────────────────────────────────────────────────────────────────────
# Groups Ingest
# ────────────────────────────────────────────────────────────────────────────

class GroupPayload(BaseModel):
    company_guid: str
    company_name: str
    group_guid: str
    name: str
    parent: Optional[str] = None
    is_revenue: Optional[str] = None


class GroupBatch(BaseModel):
    tenant_id: str
    groups: List[GroupPayload]


@router.post("/v1/groups", response_model=IngestResponse)
def ingest_groups(batch: GroupBatch, tenant: Tenant = Depends(verify_api_key), db: Session = Depends(get_db)):
    if tenant.id != batch.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant ID mismatch")

    accepted = updated = duplicates = errors = 0
    for g in batch.groups:
        try:
            existing = db.query(AccountGroup).filter(
                AccountGroup.tenant_id == tenant.id,
                AccountGroup.company_guid == g.company_guid,
                AccountGroup.group_guid == g.group_guid,
            ).first()
            if existing:
                changed = False
                for field in ("name", "parent", "is_revenue"):
                    new_val = getattr(g, field)
                    if new_val is not None and getattr(existing, field) != new_val:
                        setattr(existing, field, new_val)
                        changed = True
                if changed:
                    db.flush()
                    updated += 1
                else:
                    duplicates += 1
            else:
                db.add(AccountGroup(
                    tenant_id=tenant.id, company_guid=g.company_guid,
                    group_guid=g.group_guid, name=g.name,
                    parent=g.parent, is_revenue=g.is_revenue,
                ))
                db.flush()
                accepted += 1
        except Exception as e:
            db.rollback()
            errors += 1
            logger.error(f"Error ingesting group {g.group_guid}: {e}")

    db.commit()
    return IngestResponse(accepted=accepted, updated=updated, duplicates=duplicates, errors=errors,
                          message=f"Groups: {accepted} new, {updated} updated, {duplicates} unchanged, {errors} errors")


# ────────────────────────────────────────────────────────────────────────────
# Stock Items Ingest
# ────────────────────────────────────────────────────────────────────────────

class StockItemPayload(BaseModel):
    company_guid: str
    company_name: str
    item_guid: str
    name: str
    parent: Optional[str] = None
    base_units: Optional[str] = None
    opening_balance: Optional[str] = None
    closing_balance: Optional[str] = None
    hsn_code: Optional[str] = None
    gst_rate: Optional[str] = None


class StockItemBatch(BaseModel):
    tenant_id: str
    stock_items: List[StockItemPayload]


@router.post("/v1/stock-items", response_model=IngestResponse)
def ingest_stock_items(batch: StockItemBatch, tenant: Tenant = Depends(verify_api_key), db: Session = Depends(get_db)):
    if tenant.id != batch.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant ID mismatch")

    accepted = updated = duplicates = errors = 0
    for s in batch.stock_items:
        try:
            existing = db.query(StockItem).filter(
                StockItem.tenant_id == tenant.id,
                StockItem.company_guid == s.company_guid,
                StockItem.item_guid == s.item_guid,
            ).first()
            if existing:
                changed = False
                for field in ("name", "parent", "base_units", "opening_balance", "closing_balance", "hsn_code", "gst_rate"):
                    new_val = getattr(s, field)
                    if new_val is not None and getattr(existing, field) != new_val:
                        setattr(existing, field, new_val)
                        changed = True
                if changed:
                    db.flush()
                    updated += 1
                else:
                    duplicates += 1
            else:
                db.add(StockItem(
                    tenant_id=tenant.id, company_guid=s.company_guid,
                    item_guid=s.item_guid, name=s.name, parent=s.parent,
                    base_units=s.base_units, opening_balance=s.opening_balance,
                    closing_balance=s.closing_balance, hsn_code=s.hsn_code,
                    gst_rate=s.gst_rate,
                ))
                db.flush()
                accepted += 1
        except Exception as e:
            db.rollback()
            errors += 1
            logger.error(f"Error ingesting stock item {s.item_guid}: {e}")

    db.commit()
    return IngestResponse(accepted=accepted, updated=updated, duplicates=duplicates, errors=errors,
                          message=f"Stock items: {accepted} new, {updated} updated, {duplicates} unchanged, {errors} errors")


# ────────────────────────────────────────────────────────────────────────────
# Stock Groups Ingest
# ────────────────────────────────────────────────────────────────────────────

class StockGroupPayload(BaseModel):
    company_guid: str
    company_name: str
    group_guid: str
    name: str
    parent: Optional[str] = None


class StockGroupBatch(BaseModel):
    tenant_id: str
    stock_groups: List[StockGroupPayload]


@router.post("/v1/stock-groups", response_model=IngestResponse)
def ingest_stock_groups(batch: StockGroupBatch, tenant: Tenant = Depends(verify_api_key), db: Session = Depends(get_db)):
    if tenant.id != batch.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant ID mismatch")

    accepted = updated = duplicates = errors = 0
    for g in batch.stock_groups:
        try:
            existing = db.query(StockGroup).filter(
                StockGroup.tenant_id == tenant.id,
                StockGroup.company_guid == g.company_guid,
                StockGroup.group_guid == g.group_guid,
            ).first()
            if existing:
                changed = False
                for field in ("name", "parent"):
                    new_val = getattr(g, field)
                    if new_val is not None and getattr(existing, field) != new_val:
                        setattr(existing, field, new_val)
                        changed = True
                if changed:
                    db.flush()
                    updated += 1
                else:
                    duplicates += 1
            else:
                db.add(StockGroup(
                    tenant_id=tenant.id, company_guid=g.company_guid,
                    group_guid=g.group_guid, name=g.name, parent=g.parent,
                ))
                db.flush()
                accepted += 1
        except Exception as e:
            db.rollback()
            errors += 1
            logger.error(f"Error ingesting stock group {g.group_guid}: {e}")

    db.commit()
    return IngestResponse(accepted=accepted, updated=updated, duplicates=duplicates, errors=errors,
                          message=f"Stock groups: {accepted} new, {updated} updated, {duplicates} unchanged, {errors} errors")


@router.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "tally-sync-ingest"}


@router.get("/v1/stats")
def get_stats(tenant: Tenant = Depends(verify_api_key), db: Session = Depends(get_db)):
    """Get ingest statistics for tenant."""
    return {
        "tenant_id": tenant.id,
        "total_ledgers":      db.query(Ledger).filter(Ledger.tenant_id == tenant.id).count(),
        "total_vouchers":     db.query(Voucher).filter(Voucher.tenant_id == tenant.id).count(),
        "total_groups":       db.query(AccountGroup).filter(AccountGroup.tenant_id == tenant.id).count(),
        "total_stock_items":  db.query(StockItem).filter(StockItem.tenant_id == tenant.id).count(),
        "total_stock_groups": db.query(StockGroup).filter(StockGroup.tenant_id == tenant.id).count(),
    }


@router.get("/v1/data/{resource}")
def query_data(
    resource: str,
    limit: int = 50,
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Query stored data by resource type."""
    model_map = {
        "ledgers": Ledger,
        "vouchers": Voucher,
        "groups": AccountGroup,
        "stock-items": StockItem,
        "stock-groups": StockGroup,
    }
    model = model_map.get(resource)
    if not model:
        raise HTTPException(status_code=404, detail=f"Unknown resource: {resource}")

    rows = db.query(model).filter(
        model.tenant_id == tenant.id
    ).limit(limit).all()

    results = []
    for row in rows:
        d = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        results.append(d)

    return {"resource": resource, "count": len(results), "data": results}
