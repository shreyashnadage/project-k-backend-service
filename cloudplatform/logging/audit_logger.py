"""
Audit Logger for ELK Stack Integration (Phase 4)

Captures and logs all audit events:
- Authorization decisions (Phase 3)
- Authentication events (Phase 1)
- Device operations (Phase 2)

Events are formatted as JSON and sent to Logstash for processing.
"""

import logging
import json
from datetime import datetime
from typing import Dict, Any, Optional
from enum import Enum
import socket
import threading

logger = logging.getLogger(__name__)

# ============================================================================
# Event Types
# ============================================================================

class EventType(str, Enum):
    """Types of audit events"""
    AUTHORIZATION_CHECK = "authorization_check"
    AUTHENTICATION = "authentication"
    DEVICE_OPERATION = "device_operation"
    API_ACCESS = "api_access"


class AuthenticationAction(str, Enum):
    """Authentication actions"""
    LOGIN = "login"
    LOGOUT = "logout"
    REGISTER = "register"
    VERIFY_EMAIL = "verify_email"
    REFRESH_TOKEN = "refresh_token"


class DeviceOperation(str, Enum):
    """Device operations"""
    REGISTER = "register"
    ROTATE_KEY = "rotate_key"
    STATUS_CHECK = "status_check"


# ============================================================================
# Audit Event
# ============================================================================

class AuditEvent:
    """Represents a single audit event"""

    def __init__(
        self,
        event_type: EventType,
        timestamp: Optional[datetime] = None,
        request_id: Optional[str] = None,
        source_ip: Optional[str] = None,
        **kwargs
    ):
        """
        Create audit event

        Args:
            event_type: Type of event
            timestamp: Event timestamp (defaults to now)
            request_id: Request ID for tracing
            source_ip: Source IP address
            **kwargs: Event-specific fields
        """
        self.event_type = event_type
        self.timestamp = timestamp or datetime.utcnow()
        self.request_id = request_id or f"req_{self._generate_id()}"
        self.source_ip = source_ip or self._get_local_ip()
        self.fields = kwargs

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary"""
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "request_id": self.request_id,
            "source_ip": self.source_ip,
            **self.fields
        }

    def to_json(self) -> str:
        """Convert event to JSON"""
        return json.dumps(self.to_dict())

    @staticmethod
    def _generate_id() -> str:
        """Generate unique ID"""
        import uuid
        return str(uuid.uuid4())[:12]

    @staticmethod
    def _get_local_ip() -> str:
        """Get local IP address"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            sock.close()
            return ip
        except Exception:
            return "127.0.0.1"


# ============================================================================
# Logstash Client
# ============================================================================

class LogstashClient:
    """Client for sending events to Logstash"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5000,
        timeout: float = 2.0
    ):
        """
        Initialize Logstash client

        Args:
            host: Logstash host
            port: Logstash port
            timeout: Connection timeout in seconds
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.connected = False
        self._test_connection()

    def send_event(self, event: AuditEvent) -> bool:
        """
        Send event to Logstash

        Args:
            event: Event to send

        Returns:
            True if successful, False otherwise
        """
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)

            sock.connect((self.host, self.port))
            sock.sendall(event.to_json().encode() + b"\n")
            sock.close()

            logger.debug(f"Event sent to Logstash: {event.request_id}")
            return True

        except Exception as e:
            logger.warning(f"Failed to send event to Logstash: {str(e)}")
            return False

    def _test_connection(self):
        """Test Logstash connection"""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            result = sock.connect_ex((self.host, self.port))
            self.connected = result == 0
            sock.close()

            if self.connected:
                logger.info(f"Connected to Logstash at {self.host}:{self.port}")
            else:
                logger.warning(f"Cannot connect to Logstash at {self.host}:{self.port}")

        except Exception as e:
            logger.warning(f"Logstash connection test failed: {str(e)}")
            self.connected = False


# ============================================================================
# Audit Logger
# ============================================================================

class AuditLogger:
    """
    Audit logger for all system events

    Captures authorization, authentication, and device events,
    formats them as JSON, and sends to Logstash asynchronously.
    """

    def __init__(
        self,
        logstash_host: str = "localhost",
        logstash_port: int = 5000,
        use_async: bool = True
    ):
        """
        Initialize audit logger

        Args:
            logstash_host: Logstash host
            logstash_port: Logstash port
            use_async: Send events asynchronously
        """
        self.logstash_client = LogstashClient(logstash_host, logstash_port)
        self.use_async = use_async
        self.event_queue = []
        logger.info("Audit logger initialized")

    # ========================================================================
    # Authorization Events (Phase 3)
    # ========================================================================

    def log_authorization_check(
        self,
        principal_client_id: str,
        principal_role: str,
        resource_type: str,
        resource_id: str,
        action: str,
        allowed: bool,
        reason: str,
        request_id: Optional[str] = None
    ) -> str:
        """
        Log authorization check

        Args:
            principal_client_id: Client ID of user
            principal_role: Role of user
            resource_type: Type of resource
            resource_id: ID of resource
            action: Action attempted
            allowed: Whether action was allowed
            reason: Reason for decision
            request_id: Request ID for tracing

        Returns:
            Request ID of logged event
        """
        event = AuditEvent(
            EventType.AUTHORIZATION_CHECK,
            request_id=request_id,
            principal_client_id=principal_client_id,
            principal_role=principal_role,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            allowed=allowed,
            reason=reason
        )

        self._send_event(event)
        logger.info(
            f"Authorization: {principal_client_id} → {action} on {resource_type} "
            f"({'allowed' if allowed else 'denied'})"
        )
        return event.request_id

    # ========================================================================
    # Authentication Events (Phase 1)
    # ========================================================================

    def log_authentication(
        self,
        action: str,  # login, logout, register, verify_email, refresh_token
        client_id: str,
        status: str,  # success, failure
        reason: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> str:
        """
        Log authentication event

        Args:
            action: Authentication action
            client_id: Client ID
            status: success or failure
            reason: Reason if failure
            request_id: Request ID for tracing

        Returns:
            Request ID of logged event
        """
        event = AuditEvent(
            EventType.AUTHENTICATION,
            request_id=request_id,
            action=action,
            client_id=client_id,
            status=status,
            reason=reason
        )

        self._send_event(event)
        logger.info(f"Authentication: {action} for {client_id} - {status}")
        return event.request_id

    # ========================================================================
    # Device Events (Phase 2)
    # ========================================================================

    def log_device_operation(
        self,
        operation: str,  # register, rotate_key, status_check
        device_id: str,
        client_id: str,
        status: str,  # success, failure
        details: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None
    ) -> str:
        """
        Log device operation

        Args:
            operation: Device operation
            device_id: Device ID
            client_id: Client ID
            status: success or failure
            details: Additional details
            request_id: Request ID for tracing

        Returns:
            Request ID of logged event
        """
        event = AuditEvent(
            EventType.DEVICE_OPERATION,
            request_id=request_id,
            operation=operation,
            device_id=device_id,
            client_id=client_id,
            status=status,
            **(details or {})
        )

        self._send_event(event)
        logger.info(f"Device operation: {operation} on {device_id} - {status}")
        return event.request_id

    # ========================================================================
    # API Access Events
    # ========================================================================

    def log_api_access(
        self,
        method: str,
        endpoint: str,
        client_id: str,
        status_code: int,
        duration_ms: float,
        request_id: Optional[str] = None
    ) -> str:
        """
        Log API access

        Args:
            method: HTTP method
            endpoint: API endpoint
            client_id: Client ID
            status_code: HTTP status code
            duration_ms: Request duration in milliseconds
            request_id: Request ID for tracing

        Returns:
            Request ID of logged event
        """
        event = AuditEvent(
            EventType.API_ACCESS,
            request_id=request_id,
            method=method,
            endpoint=endpoint,
            client_id=client_id,
            status_code=status_code,
            duration_ms=duration_ms
        )

        self._send_event(event)
        logger.debug(
            f"API access: {method} {endpoint} - {status_code} ({duration_ms}ms)"
        )
        return event.request_id

    # ========================================================================
    # Private Methods
    # ========================================================================

    def _send_event(self, event: AuditEvent) -> None:
        """
        Send event to Logstash (async or sync)

        Args:
            event: Event to send
        """
        if self.use_async:
            threading.Thread(
                target=self._send_async,
                args=(event,),
                daemon=True
            ).start()
        else:
            self.logstash_client.send_event(event)

    def _send_async(self, event: AuditEvent) -> None:
        """Send event asynchronously"""
        if not self.logstash_client.send_event(event):
            # Fallback: store in queue (would be persisted to SQLite in production)
            self.event_queue.append(event)
            logger.debug(f"Event queued: {event.request_id}")

    def get_queued_events(self) -> list:
        """Get queued events for offline processing"""
        return self.event_queue.copy()

    def clear_queued_events(self) -> int:
        """Clear queued events and return count"""
        count = len(self.event_queue)
        self.event_queue.clear()
        return count


# ============================================================================
# Singleton Instance
# ============================================================================

# Global audit logger instance
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get or create global audit logger instance"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def init_audit_logger(
    logstash_host: str = "localhost",
    logstash_port: int = 5000
) -> AuditLogger:
    """Initialize global audit logger"""
    global _audit_logger
    _audit_logger = AuditLogger(logstash_host, logstash_port)
    return _audit_logger
