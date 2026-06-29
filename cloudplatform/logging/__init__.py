"""
Audit logging module for ELK Stack integration (Phase 4)

Exports:
- AuditLogger: Main audit logging class
- AuditEvent: Individual audit event
- EventType: Types of audit events
- get_audit_logger: Get global audit logger instance
"""

from .audit_logger import (
    AuditLogger,
    AuditEvent,
    EventType,
    AuthenticationAction,
    DeviceOperation,
    LogstashClient,
    get_audit_logger,
    init_audit_logger,
)

__all__ = [
    "AuditLogger",
    "AuditEvent",
    "EventType",
    "AuthenticationAction",
    "DeviceOperation",
    "LogstashClient",
    "get_audit_logger",
    "init_audit_logger",
]
