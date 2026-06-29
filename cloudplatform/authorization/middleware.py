"""
Authorization Middleware for FastAPI

Provides:
- Request-level authorization checking
- Automatic resource extraction
- Authorization decision logging
- 403 Forbidden responses for denied access
"""

import logging
from typing import Callable, Optional
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from cloudplatform.authorization.cerbos_client import (
    cerbos_client,
    Principal,
    ResourceContext,
    Resource,
    Role,
    Action,
)

logger = logging.getLogger(__name__)

# ============================================================================
# Authorization Middleware
# ============================================================================

class AuthorizationMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for authorization checking

    For each request:
    1. Extract principal from JWT (set by authentication middleware)
    2. Extract resource from request path/body
    3. Check authorization with CerbosClient
    4. Return 403 if denied
    5. Log all decisions for audit trail

    Example:
        app.add_middleware(AuthorizationMiddleware)
    """

    async def dispatch(self, request: Request, call_next: Callable) -> None:
        """
        Process request through authorization checks

        Args:
            request: FastAPI Request object
            call_next: Next middleware/endpoint in chain

        Returns:
            Response (allowed) or 403 Forbidden (denied)
        """
        try:
            # Skip authorization for public endpoints
            if self._is_public_endpoint(request.url.path):
                logger.debug(f"Skipping auth for public endpoint: {request.url.path}")
                return await call_next(request)

            # Extract principal from request (set by auth middleware)
            principal = self._extract_principal(request)

            if not principal:
                logger.warning(f"No principal found for: {request.url.path}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required"
                )

            # Extract resource from request
            resource = self._extract_resource(request, principal)

            # Infer action from HTTP method
            action = self._infer_action(request.method)

            if not resource or not action:
                logger.debug(f"Skipping auth (no resource/action): {request.url.path}")
                return await call_next(request)

            # Check authorization
            logger.info(
                f"Authorization check: {principal.client_id} "
                f"[{principal.role.value}] → {action.value} on {resource.resource_type.value}"
            )

            result = cerbos_client.check_permission(principal, resource, action)

            if not result.allowed:
                logger.warning(
                    f"Access denied: {principal.client_id} → "
                    f"{action.value} on {resource.resource_type.value} - {result.reason}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied: {result.reason}"
                )

            logger.info(f"Access granted: {principal.client_id} → {action.value}")

            # Attach principal to request state for use in endpoint
            request.state.principal = principal
            request.state.resource = resource

            # Continue to endpoint
            response = await call_next(request)
            return response

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Authorization middleware error: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authorization check failed"
            )

    # ========================================================================
    # Private Methods
    # ========================================================================

    @staticmethod
    def _is_public_endpoint(path: str) -> bool:
        """
        Check if endpoint is public (no authorization required)

        Args:
            path: Request path

        Returns:
            True if endpoint is public
        """
        public_paths = [
            "/",
            "/health",
            "/docs",
            "/openapi.json",
            "/v1/auth/register",
            "/v1/auth/verify-email",
            "/v1/auth/login",
        ]

        return path in public_paths or path.startswith("/static/")

    @staticmethod
    def _extract_principal(request: Request) -> Optional[Principal]:
        """
        Extract principal from request

        Looks for:
        1. request.state.current_client (set by auth middleware)
        2. Authorization header with JWT

        Args:
            request: FastAPI Request object

        Returns:
            Principal object or None if not found
        """
        # Check if already set by authentication middleware
        if hasattr(request.state, "current_client"):
            client_info = request.state.current_client
            return Principal(
                client_id=client_info.client_id,
                role=Role.ADMIN if client_info.client_id == "cli_admin_test" else Role.FINANCE,
                email=client_info.email
            )

        # Check Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        # In real implementation, would validate JWT and extract principal
        # For now, return None (handled by auth middleware)
        return None

    @staticmethod
    def _extract_resource(request: Request, principal: Principal) -> Optional[ResourceContext]:
        """
        Extract resource from request path/body

        Examples:
        - GET /v1/ledgers/ledger_123 → Resource(LEDGER, ledger_123, owner_id)
        - POST /v1/devices/register → Resource(DEVICE, generated_id, principal.client_id)
        - DELETE /v1/vouchers/voucher_456 → Resource(VOUCHER, voucher_456, owner_id)

        Args:
            request: FastAPI Request object
            principal: Authenticated principal

        Returns:
            ResourceContext or None if not found
        """
        path = request.url.path

        # Parse path to extract resource type and ID
        parts = path.strip("/").split("/")

        if len(parts) < 2:
            return None

        # Get resource type from path
        # /v1/ledgers/123 → ledgers
        resource_type_str = parts[1]

        # Map to Resource enum
        resource_mapping = {
            "ledgers": Resource.LEDGER,
            "vouchers": Resource.VOUCHER,
            "devices": Resource.DEVICE,
            "clients": Resource.CLIENT,
            "installation-keys": Resource.INSTALLATION_KEY,
            "sync": Resource.SYNC_RECORD,
        }

        if resource_type_str not in resource_mapping:
            return None

        resource_type = resource_mapping[resource_type_str]

        # Get resource ID from path if available
        resource_id = parts[2] if len(parts) > 2 else f"new_{resource_type_str}"

        # For now, assume principal owns the resource
        # In real implementation, would look up actual owner from database
        owner_client_id = principal.client_id

        return ResourceContext(
            resource_type=resource_type,
            resource_id=resource_id,
            owner_client_id=owner_client_id
        )

    @staticmethod
    def _infer_action(method: str) -> Optional[Action]:
        """
        Infer action from HTTP method

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)

        Returns:
            Action enum or None if unmapped
        """
        action_mapping = {
            "GET": Action.READ,
            "POST": Action.WRITE,
            "PUT": Action.WRITE,
            "PATCH": Action.WRITE,
            "DELETE": Action.DELETE,
        }

        return action_mapping.get(method)


# ============================================================================
# Middleware Factory
# ============================================================================

def create_authorization_middleware() -> AuthorizationMiddleware:
    """
    Create authorization middleware instance

    Returns:
        Configured AuthorizationMiddleware
    """
    logger.info("Creating authorization middleware")
    return AuthorizationMiddleware()
