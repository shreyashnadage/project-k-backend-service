"""
SQLAlchemy Database Models for Tally Sync Platform

Schema includes:
- Tenants: Customer accounts
- Ledgers: Chart of accounts
- Vouchers: Transactions
- Heartbeats: Agent status tracking
- Audit logs: For debugging and compliance
"""

from sqlalchemy import Column, String, Integer, Text, DateTime, Boolean, Index, UniqueConstraint, Enum, DECIMAL
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime, timezone
import uuid
import enum


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class Tenant(Base):
    """Customer/tenant account."""
    __tablename__ = "tenants"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    api_key_hash = Column(String(64), nullable=False, unique=True)  # SHA-256 hash
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_active = Column(Boolean, default=True)

    def __repr__(self):
        return f"<Tenant {self.name}>"


class Ledger(Base):
    """Chart of accounts (ledger master)."""
    __tablename__ = "ledgers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    company_guid = Column(String(255), nullable=False)
    ledger_guid = Column(String(255), nullable=False)
    name = Column(String(500), nullable=False)
    parent = Column(String(500))
    ledger_type = Column(String(100))
    opening_balance = Column(String(30))
    closing_balance = Column(String(30))
    data_source = Column(String(50), nullable=False, default="live", index=True)
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Unique constraint: same ledger not inserted twice per company
    __table_args__ = (
        Index("ix_ledger_dedup", "tenant_id", "company_guid", "ledger_guid", unique=True),
    )

    def __repr__(self):
        return f"<Ledger {self.name}>"


class Voucher(Base):
    """Transactions (sales, purchases, receipts, payments, etc.)."""
    __tablename__ = "vouchers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    company_guid = Column(String(255), nullable=False)
    voucher_guid = Column(String(255), nullable=False)
    voucher_type = Column(String(50), nullable=False)  # Sales, Purchase, Receipt, Payment, Journal
    voucher_number = Column(String(100))
    date = Column(String(10), nullable=False)  # ISO 8601 YYYY-MM-DD
    party = Column(String(500))  # Customer/supplier name (may be Unicode)
    narration = Column(Text)  # Voucher notes
    amount = Column(String(30))  # String to preserve precision
    raw_data = Column(Text)  # Raw JSON for future expansion
    data_source = Column(String(50), nullable=False, default="live", index=True)
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    agent_version = Column(String(20))

    # Unique constraint: idempotent deduplication by (tenant, company, voucher_guid)
    __table_args__ = (
        Index("ix_voucher_dedup", "tenant_id", "company_guid", "voucher_guid", unique=True),
        Index("ix_voucher_date", "tenant_id", "date"),  # For range queries
    )

    def __repr__(self):
        return f"<Voucher {self.voucher_number}>"


class AgentHeartbeat(Base):
    """Agent status tracking for fleet monitoring."""
    __tablename__ = "agent_heartbeats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    agent_version = Column(String(20))
    company_guid = Column(String(255))
    last_sync_completed_at = Column(DateTime(timezone=True))
    last_extraction_count = Column(Integer)  # How many records extracted
    queue_depth = Column(Integer, default=0)  # Records pending transmission
    tally_reachable = Column(Boolean)  # Is Tally HTTP accessible?
    last_error_code = Column(String(50))  # Error code if last sync failed
    last_error_message = Column(Text)  # Error details
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Heartbeat {self.tenant_id}>"


class SyncAuditLog(Base):
    """Detailed audit trail of all data received."""
    __tablename__ = "sync_audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    company_guid = Column(String(255))
    record_type = Column(String(20))  # 'voucher' or 'ledger'
    record_guid = Column(String(255))  # voucher_guid or ledger_guid
    action = Column(String(20))  # 'inserted', 'updated', or 'duplicate'
    data_source = Column(String(50), nullable=False, default="live", index=True)
    transmitted_at = Column(DateTime(timezone=True))
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<AuditLog {self.action}>"


class AccountGroup(Base):
    """Account group hierarchy from Tally."""
    __tablename__ = "account_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    company_guid = Column(String(255), nullable=False)
    group_guid = Column(String(255), nullable=False)
    name = Column(String(500), nullable=False)
    parent = Column(String(500))
    is_revenue = Column(String(10))
    data_source = Column(String(50), nullable=False, default="live", index=True)
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_group_dedup", "tenant_id", "company_guid", "group_guid", unique=True),
    )

    def __repr__(self):
        return f"<AccountGroup {self.name}>"


class StockItem(Base):
    """Inventory stock items from Tally."""
    __tablename__ = "stock_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    company_guid = Column(String(255), nullable=False)
    item_guid = Column(String(255), nullable=False)
    name = Column(String(500), nullable=False)
    parent = Column(String(500))
    base_units = Column(String(100))
    opening_balance = Column(String(30))
    closing_balance = Column(String(30))
    hsn_code = Column(String(50))
    gst_rate = Column(String(20))
    data_source = Column(String(50), nullable=False, default="live", index=True)
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_stock_dedup", "tenant_id", "company_guid", "item_guid", unique=True),
    )

    def __repr__(self):
        return f"<StockItem {self.name}>"


class StockGroup(Base):
    """Inventory stock groups from Tally."""
    __tablename__ = "stock_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    company_guid = Column(String(255), nullable=False)
    group_guid = Column(String(255), nullable=False)
    name = Column(String(500), nullable=False)
    parent = Column(String(500))
    data_source = Column(String(50), nullable=False, default="live", index=True)
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_stockgroup_dedup", "tenant_id", "company_guid", "group_guid", unique=True),
    )

    def __repr__(self):
        return f"<StockGroup {self.name}>"


# ============================================================
# CLIENT REGISTRATION MODELS (Phase 4: Onboarding System)
# ============================================================

class Client(Base):
    """MSME Client - represents a business using Tally Sync."""
    __tablename__ = "clients"

    client_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, unique=True, index=True)
    phone = Column(String(20))
    gst_id = Column(String(50))

    # Authentication fields
    email_verified = Column(Boolean, default=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)

    status = Column(String(50), default="pending_verification", index=True)  # pending_verification, active, suspended, inactive
    plan = Column(String(50), default="trial")  # trial, basic, professional
    billing_monthly_usd = Column(DECIMAL(10, 2), default=0)

    # Tracking
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<Client {self.company_name}>"


class InstallationKey(Base):
    """One-time use installation keys for agent setup."""
    __tablename__ = "installation_keys"

    key_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_id = Column(String(36), nullable=False, index=True)
    installation_key = Column(String(100), nullable=False, unique=True, index=True)
    status = Column(String(50), default="active")  # active, used, expired
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True))
    used_at = Column(DateTime(timezone=True))
    device_id_used_by = Column(String(36))  # After first use

    def __repr__(self):
        return f"<InstallationKey {self.installation_key}>"


class DeviceRegistration(Base):
    """Registered devices (PCs) that run Tally Sync Agent."""
    __tablename__ = "device_registrations"

    device_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_id = Column(String(36), nullable=False, index=True)
    device_name = Column(String(255), nullable=False)
    os_version = Column(String(255))
    agent_version = Column(String(50))
    registration_token = Column(String(500), nullable=False, unique=True)
    api_key = Column(String(500), nullable=False, unique=True)
    status = Column(String(50), default="active")  # active, inactive, revoked
    registered_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_sync_at = Column(DateTime(timezone=True))
    last_ip_address = Column(String(50))

    __table_args__ = (
        Index("ix_device_client", "client_id"),
    )

    def __repr__(self):
        return f"<DeviceRegistration {self.device_name}>"


class SyncRecord(Base):
    """Track each sync operation with full metadata."""
    __tablename__ = "sync_records"

    sync_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_id = Column(String(36), nullable=False, index=True)
    device_id = Column(String(36), nullable=False, index=True)
    tenant_id = Column(String(36), nullable=False)
    records_count = Column(Integer, default=0)
    extracted_ledgers = Column(Integer, default=0)
    extracted_vouchers = Column(Integer, default=0)
    sync_status = Column(String(50), default="success")  # success, partial, failed
    sync_timestamp = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        Index("ix_sync_client_date", "client_id", "created_at"),
    )

    def __repr__(self):
        return f"<SyncRecord {self.client_id}>"


class RegistrationAuditLog(Base):
    """Audit trail for client registrations, device setup, etc."""
    __tablename__ = "registration_audit_log"

    audit_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_id = Column(String(36), nullable=False, index=True)
    action = Column(String(100), nullable=False)  # registered, device_registered, sync_received, key_used, etc.
    details = Column(Text)  # JSON details
    source_device = Column(String(255))
    ip_address = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    def __repr__(self):
        return f"<RegistrationAuditLog {self.action}>"


class CompanyMapping(Base):
    """Maps a client to one or more Tally company names on a specific device."""
    __tablename__ = "company_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String(36), nullable=False, index=True)
    device_id = Column(String(36), nullable=False, index=True)
    company_name = Column(String(500), nullable=False)
    company_guid = Column(String(255))
    formal_name = Column(String(500))
    gst_number = Column(String(50))
    state = Column(String(100))
    is_active = Column(Boolean, default=True)
    last_synced_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_company_mapping_dedup", "client_id", "device_id", "company_name", unique=True),
    )

    def __repr__(self):
        return f"<CompanyMapping {self.company_name}>"


class SyncCommand(Base):
    """
    Admin-triggered command for on-demand data extraction.

    Lifecycle: pending → fetched → completed | failed
    Commands that are never fetched expire after COMMAND_TTL_HOURS.
    """
    __tablename__ = "sync_commands"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), nullable=False, index=True)
    device_id = Column(String(36), nullable=False, index=True)

    command_type = Column(String(50), nullable=False)
    # JSON: { company_guid, company_name, voucher_type, from_date, to_date }
    params = Column(Text, nullable=False, default="{}")

    status = Column(String(20), nullable=False, default="pending", index=True)
    created_by = Column(String(255))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    fetched_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))

    # JSON: { records_synced, errors }
    result = Column(Text)
    error_message = Column(Text)

    __table_args__ = (
        Index("ix_commands_device_status", "device_id", "status"),
    )

    def __repr__(self):
        return f"<SyncCommand {self.command_type} {self.status}>"


class SyncWatermark(Base):
    """
    Tracks the last successful sync position per (tenant, device, company, resource).

    For vouchers: watermark_value is an ISO date (YYYY-MM-DD) — only fetch newer.
    For master data: watermark_value is a max ALTER ID or timestamp.
    """
    __tablename__ = "sync_watermarks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    device_id = Column(String(36), nullable=False)
    company_guid = Column(String(255), nullable=False)
    resource_type = Column(String(50), nullable=False)  # ledger, voucher, group, stock_item, stock_group
    watermark_value = Column(String(255), nullable=False)  # ISO date or ALTER ID
    records_synced = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index(
            "ix_watermark_lookup",
            "tenant_id", "device_id", "company_guid", "resource_type",
            unique=True,
        ),
    )


class TestModeState(Base):
    """Singleton per tenant: tracks whether the dashboard shows live or simulated data."""
    __tablename__ = "test_mode_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(36), nullable=False, unique=True, index=True)
    mode = Column(String(10), nullable=False, default="live")  # 'live' | 'test'
    active_simulation_id = Column(Integer, nullable=True)
    simulation_name = Column(String(255), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DeviceSyncSchedule(Base):
    """
    Per-device automatic sync schedule.

    Created automatically when a device completes discover_companies.
    The backend scheduler checks this table every minute and queues
    sync_all_companies commands for devices whose interval has elapsed.
    """
    __tablename__ = "device_sync_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    device_id = Column(String(36), nullable=False)
    interval_seconds = Column(Integer, nullable=False, default=1800)  # 30 min default
    is_active = Column(Boolean, default=True)
    last_scheduled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_device_schedule", "tenant_id", "device_id", unique=True),
    )

    def __repr__(self):
        return f"<DeviceSyncSchedule device={self.device_id} interval={self.interval_seconds}s>"
