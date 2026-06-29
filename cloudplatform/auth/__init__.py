"""
Authentication Module

Provides:
- Client registration
- Email verification
- JWT authentication
- Token management
- Access control
"""

from cloudplatform.auth.supabase_client import (
    supabase_client,
    SupabaseAuthClient,
    RegistrationRequest,
    LoginRequest,
    EmailVerificationRequest,
    TokenResponse,
    ClientInfo,
)
from cloudplatform.auth.routes import router

__all__ = [
    "supabase_client",
    "SupabaseAuthClient",
    "RegistrationRequest",
    "LoginRequest",
    "EmailVerificationRequest",
    "TokenResponse",
    "ClientInfo",
    "router",
]
