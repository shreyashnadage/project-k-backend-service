"""
Cerbos Authorization Client

Provides role-based access control (RBAC) using Cerbos.
Features:
- Permission checking (allow/deny)
- Resource-level access control
- Role-based policies
- Audit logging of decisions
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)

# ============================================================================
# Enums
# ============================================================================

class Role(str, Enum):
    """User roles in the system"""
    ADMIN = "admin"
    FINANCE = "finance"
    VIEWER = "viewer"
    DEVICE = "device"


class Action(str, Enum):
    """Actions that can be performed"""
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    TRANSMIT_SYNC = "transmit_sync"
    REGISTER_DEVICE = "register_device"
    ROTATE_KEY = "rotate_key"


class Resource(str, Enum):
    """Resources in the system"""
    LEDGER = "ledger"
    VOUCHER = "voucher"
    DEVICE = "device"
    CLIENT = "client"
    INSTALLATION_KEY = "installation_key"
    SYNC_RECORD = "sync_record"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class Principal:
    """User principal for authorization checks"""
    client_id: str
    role: Role
    email: str = ""


@dataclass
class ResourceContext:
    """Resource context for authorization checks"""
    resource_type: Resource
    resource_id: str
    owner_client_id: str


@dataclass
class AuthorizationCheck:
    """Single authorization check"""
    principal: Principal
    resource: ResourceContext
    action: Action


@dataclass
class AuthorizationResult:
    """Result of authorization check"""
    allowed: bool
    reason: str = ""
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


# ============================================================================
# Policy Definitions
# ============================================================================

class Policies:
    """RBAC policy definitions"""

    # Admin role - full access
    ADMIN_PERMISSIONS = {
        Resource.LEDGER: [Action.READ, Action.WRITE, Action.DELETE],
        Resource.VOUCHER: [Action.READ, Action.WRITE, Action.DELETE],
        Resource.DEVICE: [Action.READ, Action.WRITE, Action.DELETE],
        Resource.CLIENT: [Action.READ, Action.WRITE, Action.DELETE],
        Resource.INSTALLATION_KEY: [Action.READ, Action.WRITE, Action.DELETE],
        Resource.SYNC_RECORD: [Action.READ, Action.WRITE, Action.DELETE],
    }

    # Finance role - read/write accounting data, no delete
    FINANCE_PERMISSIONS = {
        Resource.LEDGER: [Action.READ, Action.WRITE],
        Resource.VOUCHER: [Action.READ, Action.WRITE],
        Resource.DEVICE: [],
        Resource.CLIENT: [Action.READ],
        Resource.INSTALLATION_KEY: [],
        Resource.SYNC_RECORD: [Action.READ],
    }

    # Viewer role - read-only access
    VIEWER_PERMISSIONS = {
        Resource.LEDGER: [Action.READ],
        Resource.VOUCHER: [Action.READ],
        Resource.DEVICE: [Action.READ],
        Resource.CLIENT: [Action.READ],
        Resource.INSTALLATION_KEY: [],
        Resource.SYNC_RECORD: [Action.READ],
    }

    # Device role - sync and registration only
    DEVICE_PERMISSIONS = {
        Resource.LEDGER: [],
        Resource.VOUCHER: [],
        Resource.DEVICE: [Action.REGISTER_DEVICE, Action.ROTATE_KEY],
        Resource.CLIENT: [],
        Resource.INSTALLATION_KEY: [Action.READ],
        Resource.SYNC_RECORD: [Action.TRANSMIT_SYNC],
    }

    @classmethod
    def get_permissions(cls, role: Role) -> Dict[Resource, List[Action]]:
        """Get all permissions for a role"""
        permissions_map = {
            Role.ADMIN: cls.ADMIN_PERMISSIONS,
            Role.FINANCE: cls.FINANCE_PERMISSIONS,
            Role.VIEWER: cls.VIEWER_PERMISSIONS,
            Role.DEVICE: cls.DEVICE_PERMISSIONS,
        }
        return permissions_map.get(role, {})


# ============================================================================
# Cerbos Client
# ============================================================================

class CerbosClient:
    """
    Cerbos authorization client

    Provides:
    - Permission checking (allow/deny)
    - Resource-level access control
    - Role-based policies
    - Audit trail
    """

    def __init__(self):
        """Initialize Cerbos client"""
        logger.info("Initializing Cerbos authorization client")
        self.policies = Policies()
        self.decisions = []  # Audit trail

    def check_permission(
        self,
        principal: Principal,
        resource: ResourceContext,
        action: Action
    ) -> AuthorizationResult:
        """
        Check if principal is authorized to perform action on resource

        Args:
            principal: User principal (client_id, role, email)
            resource: Resource being accessed (type, id, owner)
            action: Action to perform (read, write, delete, etc.)

        Returns:
            AuthorizationResult with allow/deny decision

        Raises:
            ValueError: If inputs are invalid
        """
        try:
            # Validate inputs
            if not principal or not principal.client_id:
                raise ValueError("Principal client_id is required")
            if not resource or not resource.resource_type:
                raise ValueError("Resource type is required")
            if not action:
                raise ValueError("Action is required")

            logger.info(
                f"Checking permission: {principal.client_id} "
                f"[{principal.role.value}] → {action.value} on {resource.resource_type.value}"
            )

            # Step 1: Check cross-client isolation
            # Non-admin users cannot access other clients' resources
            if principal.role != Role.ADMIN:
                if resource.owner_client_id != principal.client_id:
                    result = AuthorizationResult(
                        allowed=False,
                        reason=f"Cross-client access denied: "
                        f"Client {principal.client_id} cannot access "
                        f"resource owned by {resource.owner_client_id}"
                    )
                    self._log_decision(principal, resource, action, result)
                    logger.warning(f"❌ Permission denied: {result.reason}")
                    return result

            # Step 2: Check role permissions
            permissions = self.policies.get_permissions(principal.role)

            if resource.resource_type not in permissions:
                result = AuthorizationResult(
                    allowed=False,
                    reason=f"Resource type {resource.resource_type.value} "
                    f"not recognized for role {principal.role.value}"
                )
                self._log_decision(principal, resource, action, result)
                logger.warning(f"❌ Permission denied: {result.reason}")
                return result

            # Step 3: Check action is allowed for this role+resource
            allowed_actions = permissions[resource.resource_type]

            if action not in allowed_actions:
                result = AuthorizationResult(
                    allowed=False,
                    reason=f"Role {principal.role.value} cannot perform "
                    f"{action.value} on {resource.resource_type.value}"
                )
                self._log_decision(principal, resource, action, result)
                logger.warning(f"❌ Permission denied: {result.reason}")
                return result

            # Step 4: Permission granted
            result = AuthorizationResult(
                allowed=True,
                reason=f"Allowed: {principal.role.value} can "
                f"{action.value} {resource.resource_type.value}"
            )
            self._log_decision(principal, resource, action, result)
            logger.info(f"✓ Permission granted: {result.reason}")
            return result

        except ValueError as e:
            logger.error(f"Invalid check parameters: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Authorization check error: {str(e)}")
            raise

    def check_resource_access(
        self,
        principal: Principal,
        resource: ResourceContext
    ) -> Dict[Action, bool]:
        """
        Check all actions available for principal on resource

        Args:
            principal: User principal
            resource: Resource being accessed

        Returns:
            Dict mapping Action → bool (allowed/denied)
        """
        try:
            logger.info(
                f"Checking resource access: {principal.client_id} → "
                f"{resource.resource_type.value}/{resource.resource_id}"
            )

            access_map = {}
            permissions = self.policies.get_permissions(principal.role)
            allowed_actions = permissions.get(resource.resource_type, [])

            for action in Action:
                # Skip device-specific actions for non-device resources
                if action in [Action.TRANSMIT_SYNC, Action.REGISTER_DEVICE, Action.ROTATE_KEY]:
                    if resource.resource_type != Resource.DEVICE:
                        continue

                # Check permission
                result = self.check_permission(principal, resource, action)
                access_map[action] = result.allowed

            return access_map

        except Exception as e:
            logger.error(f"Resource access check error: {str(e)}")
            raise

    def check_multiple(
        self,
        checks: List[AuthorizationCheck]
    ) -> Dict[int, AuthorizationResult]:
        """
        Batch check multiple authorizations

        Args:
            checks: List of authorization checks

        Returns:
            Dict mapping check index → result
        """
        results = {}
        for idx, check in enumerate(checks):
            try:
                result = self.check_permission(
                    check.principal,
                    check.resource,
                    check.action
                )
                results[idx] = result
            except Exception as e:
                logger.error(f"Error checking authorization {idx}: {str(e)}")
                results[idx] = AuthorizationResult(
                    allowed=False,
                    reason=f"Check failed: {str(e)}"
                )
        return results

    def verify_client_ownership(
        self,
        principal: Principal,
        owner_client_id: str
    ) -> bool:
        """
        Verify that principal owns or can access resource owned by another client

        Args:
            principal: User principal
            owner_client_id: Client ID that owns the resource

        Returns:
            True if principal can access, False otherwise
        """
        # Admin can access all
        if principal.role == Role.ADMIN:
            return True

        # Non-admin can only access own
        return principal.client_id == owner_client_id

    def has_role(self, principal: Principal, required_role: Role) -> bool:
        """
        Check if principal has required role (or higher privilege)

        Args:
            principal: User principal
            required_role: Required role

        Returns:
            True if principal has required role
        """
        if principal.role == Role.ADMIN:
            return True  # Admin has all roles

        return principal.role == required_role

    # ========================================================================
    # Private Methods
    # ========================================================================

    def _log_decision(
        self,
        principal: Principal,
        resource: ResourceContext,
        action: Action,
        result: AuthorizationResult
    ) -> None:
        """Log authorization decision for audit trail"""
        self.decisions.append({
            "timestamp": result.timestamp,
            "principal_client_id": principal.client_id,
            "principal_role": principal.role.value,
            "resource_type": resource.resource_type.value,
            "resource_id": resource.resource_id,
            "action": action.value,
            "allowed": result.allowed,
            "reason": result.reason
        })

    def get_audit_trail(self) -> List[Dict]:
        """
        Get audit trail of authorization decisions

        Returns:
            List of authorization decisions
        """
        return self.decisions.copy()

    def clear_audit_trail(self) -> None:
        """Clear audit trail (for testing)"""
        self.decisions.clear()


# ============================================================================
# Singleton Instance
# ============================================================================

cerbos_client = CerbosClient()
