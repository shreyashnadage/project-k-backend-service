"""
Authorization Decorators for FastAPI Routes

Provides:
- @require_role - Check user has required role
- @require_permission - Check user has specific permission
- @require_client_ownership - Verify user owns resource
"""

import logging
from functools import wraps
from typing import Callable, Any, Optional
from fastapi import HTTPException, status, Request

from cloudplatform.authorization.cerbos_client import (
    cerbos_client,
    Role,
    Resource,
    Action,
)

logger = logging.getLogger(__name__)

# ============================================================================
# Decorator: Require Role
# ============================================================================

def require_role(required_role: Role) -> Callable:
    """
    Require user to have specific role

    Args:
        required_role: Required Role (Admin, Finance, Viewer, Device)

    Returns:
        Decorated function

    Example:
        @app.get("/admin/dashboard")
        @require_role(Role.ADMIN)
        async def admin_dashboard():
            return {"message": "Admin only"}
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            # Extract request from args
            request = kwargs.get("request")
            if not hasattr(request, "state") or not hasattr(request.state, "principal"):
                logger.warning("No principal found in request state")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required"
                )

            principal = request.state.principal

            # Check role
            if not cerbos_client.has_role(principal, required_role):
                logger.warning(
                    f"Access denied: {principal.client_id} lacks {required_role.value} role"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"This endpoint requires {required_role.value} role"
                )

            logger.info(f"Role check passed: {principal.client_id} has {required_role.value}")
            return await func(*args, **kwargs)

        return wrapper

    return decorator


# ============================================================================
# Decorator: Require Permission
# ============================================================================

def require_permission(resource: Resource, action: Action) -> Callable:
    """
    Require user to have specific permission on resource

    Args:
        resource: Resource type (Ledger, Voucher, Device, etc.)
        action: Action type (Read, Write, Delete, etc.)

    Returns:
        Decorated function

    Example:
        @app.delete("/v1/ledgers/{ledger_id}")
        @require_permission(Resource.LEDGER, Action.DELETE)
        async def delete_ledger(ledger_id: str):
            return {"deleted": ledger_id}
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            # Extract request from args or kwargs
            request: Optional[Request] = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break

            if not request:
                request = kwargs.get("request")

            if not request or not hasattr(request.state, "principal"):
                logger.warning("No principal found in request state")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required"
                )

            principal = request.state.principal
            resource_context = getattr(request.state, "resource", None)

            if not resource_context:
                logger.warning(f"No resource found for permission check")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Resource context required"
                )

            # Check permission
            result = cerbos_client.check_permission(principal, resource_context, action)

            if not result.allowed:
                logger.warning(
                    f"Permission denied: {principal.client_id} cannot {action.value} "
                    f"{resource.value} - {result.reason}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Permission denied: {result.reason}"
                )

            logger.info(
                f"Permission check passed: {principal.client_id} can "
                f"{action.value} {resource.value}"
            )
            return await func(*args, **kwargs)

        return wrapper

    return decorator


# ============================================================================
# Decorator: Require Client Ownership
# ============================================================================

def require_client_ownership(param_name: str) -> Callable:
    """
    Require user to own the resource (user's client_id matches resource owner)

    Args:
        param_name: Name of parameter containing client_id or resource_id

    Returns:
        Decorated function

    Example:
        @app.get("/v1/clients/{client_id}")
        @require_client_ownership("client_id")
        async def get_client(client_id: str, request: Request):
            return {"client_id": client_id}
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            # Extract request from kwargs
            request = kwargs.get("request")

            if not request or not hasattr(request.state, "principal"):
                logger.warning("No principal found in request state")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required"
                )

            principal = request.state.principal

            # Get resource owner from path parameter
            resource_owner_id = kwargs.get(param_name)

            if not resource_owner_id:
                logger.warning(f"Required parameter '{param_name}' not found")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Parameter '{param_name}' required"
                )

            # Check ownership
            if not cerbos_client.verify_client_ownership(principal, resource_owner_id):
                logger.warning(
                    f"Ownership check failed: {principal.client_id} "
                    f"does not own {resource_owner_id}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have access to this resource"
                )

            logger.info(
                f"Ownership check passed: {principal.client_id} owns {resource_owner_id}"
            )
            return await func(*args, **kwargs)

        return wrapper

    return decorator


# ============================================================================
# Utility Functions
# ============================================================================

def get_principal_from_request(request: Request):
    """
    Get authenticated principal from request

    Args:
        request: FastAPI Request object

    Returns:
        Principal object or raises 401

    Raises:
        HTTPException: If no principal found (401)
    """
    if not hasattr(request.state, "principal"):
        logger.warning("No principal in request state")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )

    return request.state.principal


def get_resource_from_request(request: Request):
    """
    Get resource context from request

    Args:
        request: FastAPI Request object

    Returns:
        ResourceContext object or raises 400

    Raises:
        HTTPException: If no resource found (400)
    """
    if not hasattr(request.state, "resource"):
        logger.warning("No resource in request state")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resource context required"
        )

    return request.state.resource
