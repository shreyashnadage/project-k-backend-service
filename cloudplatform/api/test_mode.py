"""
Test Mode API — allows dashboard to view simulated data instead of live data.

Flow:
  1. GET  /v1/test/simulations   → list available simulations from simulator
  2. POST /v1/test/ingest        → pull sim data, tag with data_source='sim:<id>', store
  3. POST /v1/test/mode          → switch dashboard to 'test' or 'live' view
  4. GET  /v1/test/mode          → current mode + active sim info
  5. GET  /v1/test/datasets      → list ingested test datasets with record counts
  6. DELETE /v1/test/datasets/{sim_id} → purge all records for a simulation
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from cloudplatform.db.database import get_db, SIMULATOR_URL
from cloudplatform.db.models import (
    AccountGroup, Ledger, StockGroup, StockItem, SyncAuditLog,
    Tenant, TestModeState, Voucher,
)
from cloudplatform.api.ingest import verify_api_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/test", tags=["test-mode"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ModeResponse(BaseModel):
    mode: str
    simulation_id: Optional[int] = None
    simulation_name: Optional[str] = None
    simulator_url: Optional[str] = None
    simulator_available: bool = False


class SetModeRequest(BaseModel):
    mode: str  # 'live' | 'test'
    simulation_id: Optional[int] = None


class IngestRequest(BaseModel):
    simulation_id: int


class IngestResponse(BaseModel):
    simulation_id: int
    data_source: str
    ingested: Dict[str, int]


class DatasetSummary(BaseModel):
    simulation_id: int
    data_source: str
    simulation_name: Optional[str]
    ledgers: int
    vouchers: int
    stock_items: int
    account_groups: int
    stock_groups: int


class PurgeResponse(BaseModel):
    deleted: Dict[str, int]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _simulator_available() -> bool:
    if not SIMULATOR_URL:
        return False
    try:
        resp = http_requests.get(f"{SIMULATOR_URL}/api/health", timeout=3)
        return resp.status_code < 400
    except Exception:
        return False


def _require_simulator():
    if not SIMULATOR_URL:
        raise HTTPException(status_code=503, detail="SIMULATOR_URL not configured — test mode is disabled")


def _get_or_create_state(tenant_id: str, db: Session) -> TestModeState:
    state = db.query(TestModeState).filter(TestModeState.tenant_id == tenant_id).first()
    if not state:
        state = TestModeState(tenant_id=tenant_id, mode="live")
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def get_active_data_source(tenant_id: str, db: Session) -> str:
    """Returns 'live' or 'sim:<id>' based on current test mode state."""
    state = db.query(TestModeState).filter(TestModeState.tenant_id == tenant_id).first()
    if state and state.mode == "test" and state.active_simulation_id:
        return f"sim:{state.active_simulation_id}"
    return "live"


def _sim_fetch(path: str, timeout: int = 30) -> Any:
    url = f"{SIMULATOR_URL}{path}"
    try:
        resp = http_requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except http_requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail=f"Simulator unreachable at {SIMULATOR_URL}")
    except http_requests.exceptions.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Simulator returned error: {e}")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/mode", response_model=ModeResponse)
def get_mode(
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Get current test/live mode for this tenant."""
    state = _get_or_create_state(tenant.id, db)
    return ModeResponse(
        mode=state.mode,
        simulation_id=state.active_simulation_id,
        simulation_name=state.simulation_name,
        simulator_url=SIMULATOR_URL or None,
        simulator_available=_simulator_available(),
    )


@router.post("/mode", response_model=ModeResponse)
def set_mode(
    body: SetModeRequest,
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Switch dashboard between live and test mode."""
    if body.mode not in ("live", "test"):
        raise HTTPException(status_code=400, detail="mode must be 'live' or 'test'")

    if body.mode == "test":
        if not body.simulation_id:
            raise HTTPException(status_code=400, detail="simulation_id is required when switching to test mode")
        _require_simulator()

    state = _get_or_create_state(tenant.id, db)
    state.mode = body.mode
    state.updated_at = datetime.now(timezone.utc)

    if body.mode == "live":
        state.active_simulation_id = None
        state.simulation_name = None
    else:
        state.active_simulation_id = body.simulation_id
        # Try to fetch simulation name
        try:
            sim_info = _sim_fetch(f"/api/simulations/{body.simulation_id}")
            state.simulation_name = sim_info.get("name")
        except Exception:
            state.simulation_name = f"Simulation {body.simulation_id}"

    db.commit()
    db.refresh(state)

    return ModeResponse(
        mode=state.mode,
        simulation_id=state.active_simulation_id,
        simulation_name=state.simulation_name,
        simulator_url=SIMULATOR_URL or None,
        simulator_available=_simulator_available(),
    )


@router.get("/simulations")
def list_simulations(
    tenant: Tenant = Depends(verify_api_key),
):
    """Proxy to simulator: list available simulations."""
    _require_simulator()
    return _sim_fetch("/api/simulations")


@router.post("/ingest", response_model=IngestResponse)
def ingest_simulation(
    body: IngestRequest,
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """
    Pull all data for a simulation from the simulator and store it
    tagged as data_source='sim:<simulation_id>'.
    """
    _require_simulator()
    sim_id = body.simulation_id
    data_source = f"sim:{sim_id}"
    now = datetime.now(timezone.utc)

    counts: Dict[str, int] = {
        "ledgers": 0, "vouchers": 0, "stock_items": 0,
        "stock_groups": 0, "account_groups": 0,
    }

    # Get simulation info for company_guid
    sim_info = _sim_fetch(f"/api/simulations/{sim_id}")
    company_name = sim_info.get("company_name", f"sim-company-{sim_id}")
    company_guid = f"sim-{sim_id}-{company_name.lower().replace(' ', '-')[:40]}"

    # ── Ledgers ──────────────────────────────────────────────────────────────
    ledgers_raw = _sim_fetch(f"/api/data/{sim_id}/ledgers")
    for item in ledgers_raw:
        ledger_guid = f"sim{sim_id}-led-{item['id']}"
        existing = db.query(Ledger).filter(
            Ledger.tenant_id == tenant.id,
            Ledger.company_guid == company_guid,
            Ledger.ledger_guid == ledger_guid,
        ).first()
        if not existing:
            db.add(Ledger(
                tenant_id=tenant.id,
                company_guid=company_guid,
                ledger_guid=ledger_guid,
                name=item["name"],
                parent=item.get("group_name"),
                ledger_type=item.get("group_name"),
                opening_balance=str(item.get("opening_balance", 0)),
                closing_balance=str(item.get("closing_balance", 0)),
                data_source=data_source,
                received_at=now,
            ))
            counts["ledgers"] += 1
    db.flush()

    # ── Vouchers (paginated — simulator supports limit/offset) ───────────────
    offset = 0
    batch_size = 500
    while True:
        vouchers_raw = _sim_fetch(f"/api/data/{sim_id}/vouchers?limit={batch_size}&offset={offset}")
        if not vouchers_raw:
            break
        for item in vouchers_raw:
            voucher_guid = f"sim{sim_id}-vch-{item['id']}"
            existing = db.query(Voucher).filter(
                Voucher.tenant_id == tenant.id,
                Voucher.company_guid == company_guid,
                Voucher.voucher_guid == voucher_guid,
            ).first()
            if not existing:
                db.add(Voucher(
                    tenant_id=tenant.id,
                    company_guid=company_guid,
                    voucher_guid=voucher_guid,
                    voucher_type=item.get("voucher_type", "Journal"),
                    voucher_number=item.get("voucher_number"),
                    date=item.get("date", ""),
                    party=item.get("party_ledger_name"),
                    narration=item.get("narration"),
                    amount=str(item.get("amount", 0)),
                    data_source=data_source,
                    received_at=now,
                ))
                counts["vouchers"] += 1
        if len(vouchers_raw) < batch_size:
            break
        offset += batch_size
    db.flush()

    # ── Stock Items ───────────────────────────────────────────────────────────
    stock_items_raw = _sim_fetch(f"/api/data/{sim_id}/stock-items")
    stock_groups_seen: Dict[str, bool] = {}
    for item in stock_items_raw:
        item_guid = f"sim{sim_id}-stk-{item['id']}"
        existing = db.query(StockItem).filter(
            StockItem.tenant_id == tenant.id,
            StockItem.company_guid == company_guid,
            StockItem.item_guid == item_guid,
        ).first()
        if not existing:
            db.add(StockItem(
                tenant_id=tenant.id,
                company_guid=company_guid,
                item_guid=item_guid,
                name=item["name"],
                parent=item.get("group_name"),
                base_units=item.get("unit"),
                opening_balance=str(item.get("opening_value", 0)),
                hsn_code=item.get("hsn_code"),
                gst_rate=str(item.get("gst_rate", "")),
                data_source=data_source,
                received_at=now,
            ))
            counts["stock_items"] += 1

        # Derive stock groups from items (simulator has no dedicated endpoint)
        group_name = item.get("group_name")
        if group_name and group_name not in stock_groups_seen:
            stock_groups_seen[group_name] = True
            group_guid = f"sim{sim_id}-sgrp-{group_name[:40]}"
            existing_sg = db.query(StockGroup).filter(
                StockGroup.tenant_id == tenant.id,
                StockGroup.company_guid == company_guid,
                StockGroup.group_guid == group_guid,
            ).first()
            if not existing_sg:
                db.add(StockGroup(
                    tenant_id=tenant.id,
                    company_guid=company_guid,
                    group_guid=group_guid,
                    name=group_name,
                    data_source=data_source,
                    received_at=now,
                ))
                counts["stock_groups"] += 1
    db.flush()

    # ── Account Groups from Ledger group_names ────────────────────────────────
    groups_seen: Dict[str, bool] = {}
    for item in ledgers_raw:
        group_name = item.get("group_name")
        if group_name and group_name not in groups_seen:
            groups_seen[group_name] = True
            group_guid = f"sim{sim_id}-agrp-{group_name[:40]}"
            existing_ag = db.query(AccountGroup).filter(
                AccountGroup.tenant_id == tenant.id,
                AccountGroup.company_guid == company_guid,
                AccountGroup.group_guid == group_guid,
            ).first()
            if not existing_ag:
                db.add(AccountGroup(
                    tenant_id=tenant.id,
                    company_guid=company_guid,
                    group_guid=group_guid,
                    name=group_name,
                    data_source=data_source,
                    received_at=now,
                ))
                counts["account_groups"] += 1
    db.flush()

    db.commit()

    logger.info(
        f"[TestMode] Ingested sim:{sim_id} for tenant {tenant.id}: "
        f"ledgers={counts['ledgers']}, vouchers={counts['vouchers']}, "
        f"stock_items={counts['stock_items']}"
    )

    return IngestResponse(
        simulation_id=sim_id,
        data_source=data_source,
        ingested=counts,
    )


@router.get("/datasets", response_model=List[DatasetSummary])
def list_datasets(
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """List all ingested simulation datasets for this tenant."""
    # Find all distinct data_sources that look like sim:N
    from sqlalchemy import distinct, func
    sources = (
        db.query(distinct(Ledger.data_source))
        .filter(Ledger.tenant_id == tenant.id, Ledger.data_source.like("sim:%"))
        .all()
    )
    sources = [row[0] for row in sources]

    # Get current mode state for simulation name lookup
    state = db.query(TestModeState).filter(TestModeState.tenant_id == tenant.id).first()

    results = []
    for source in sources:
        sim_id = int(source.split(":")[1])

        ledger_count = db.query(func.count(Ledger.id)).filter(
            Ledger.tenant_id == tenant.id, Ledger.data_source == source
        ).scalar() or 0
        voucher_count = db.query(func.count(Voucher.id)).filter(
            Voucher.tenant_id == tenant.id, Voucher.data_source == source
        ).scalar() or 0
        stock_count = db.query(func.count(StockItem.id)).filter(
            StockItem.tenant_id == tenant.id, StockItem.data_source == source
        ).scalar() or 0
        agroup_count = db.query(func.count(AccountGroup.id)).filter(
            AccountGroup.tenant_id == tenant.id, AccountGroup.data_source == source
        ).scalar() or 0
        sgroup_count = db.query(func.count(StockGroup.id)).filter(
            StockGroup.tenant_id == tenant.id, StockGroup.data_source == source
        ).scalar() or 0

        sim_name = None
        if state and state.active_simulation_id == sim_id:
            sim_name = state.simulation_name
        if not sim_name and SIMULATOR_URL:
            try:
                sim_info = _sim_fetch(f"/api/simulations/{sim_id}")
                sim_name = sim_info.get("name")
            except Exception:
                pass

        results.append(DatasetSummary(
            simulation_id=sim_id,
            data_source=source,
            simulation_name=sim_name,
            ledgers=ledger_count,
            vouchers=voucher_count,
            stock_items=stock_count,
            account_groups=agroup_count,
            stock_groups=sgroup_count,
        ))

    return results


@router.delete("/datasets/{simulation_id}", response_model=PurgeResponse)
def purge_dataset(
    simulation_id: int,
    tenant: Tenant = Depends(verify_api_key),
    db: Session = Depends(get_db),
):
    """Delete all ingested records for a simulation and reset test mode if active."""
    data_source = f"sim:{simulation_id}"

    deleted: Dict[str, int] = {}

    def _delete(model, label: str):
        n = db.query(model).filter(
            model.tenant_id == tenant.id,
            model.data_source == data_source,
        ).delete(synchronize_session=False)
        deleted[label] = n

    _delete(Ledger, "ledgers")
    _delete(Voucher, "vouchers")
    _delete(AccountGroup, "account_groups")
    _delete(StockItem, "stock_items")
    _delete(StockGroup, "stock_groups")

    audit_deleted = db.query(SyncAuditLog).filter(
        SyncAuditLog.tenant_id == tenant.id,
        SyncAuditLog.data_source == data_source,
    ).delete(synchronize_session=False)
    deleted["audit_logs"] = audit_deleted

    # Reset test mode state if it points to this simulation
    state = db.query(TestModeState).filter(TestModeState.tenant_id == tenant.id).first()
    if state and state.active_simulation_id == simulation_id:
        state.mode = "live"
        state.active_simulation_id = None
        state.simulation_name = None
        state.updated_at = datetime.now(timezone.utc)

    db.commit()

    logger.info(f"[TestMode] Purged {data_source} for tenant {tenant.id}: {deleted}")
    return PurgeResponse(deleted=deleted)
