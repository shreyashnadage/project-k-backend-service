"""
Authentication Routes
FastAPI endpoints for client registration, login, and token management
"""

from fastapi import APIRouter, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging
from datetime import datetime
from sqlalchemy.orm import Session

from cloudplatform.auth.supabase_client import (
    supabase_client,
    RegistrationRequest,
    LoginRequest,
    EmailVerificationRequest,
    TokenResponse,
    ClientInfo
)
from cloudplatform.db.database import get_db
from cloudplatform.db.models import Client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["authentication"])
security = HTTPBearer()


# ============================================================================
# Helper Functions
# ============================================================================

async def get_current_client(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> ClientInfo:
    """
    Dependency: Extract and verify JWT token from Authorization header

    Args:
        credentials: HTTP Bearer token

    Returns:
        ClientInfo with verified client details

    Raises:
        HTTPException: If token invalid or expired
    """
    try:
        token = credentials.credentials
        client_info = supabase_client.verify_token(token)
        return client_info
    except ValueError as e:
        logger.warning(f"Token verification failed: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ============================================================================
# Registration Endpoints
# ============================================================================

@router.post("/register", response_model=dict)
async def register_client(
    registration: RegistrationRequest,
    db: Session = Depends(get_db)
):
    """
    Register a new MSME client

    Args:
        registration: Client registration details
        db: Database session

    Returns:
        Dict with registration status and verification token

    Example:
        POST /v1/auth/register
        {
            "company_name": "Sharma Traders",
            "email": "shreya@sharma.com",
            "phone": "+91-9876543210",
            "password": "SecurePass123"
        }
    """
    try:
        logger.info(f"📝 Registration request: {registration.email}")

        # Check if client already exists
        existing_client = db.query(Client).filter(
            Client.email == registration.email
        ).first()

        if existing_client:
            logger.warning(f"❌ Client already exists: {registration.email}")
            raise HTTPException(
                status_code=400,
                detail="Email already registered"
            )

        # Register with Supabase
        result = supabase_client.register_client(registration)
        client_id = result["client_id"]

        # Store client in database
        client = Client(
            client_id=client_id,
            company_name=registration.company_name,
            email=registration.email,
            phone=registration.phone,
            status="pending_verification",
            created_at=datetime.utcnow()
        )
        db.add(client)
        db.commit()
        db.refresh(client)

        logger.info(f"✅ Client registered: {client_id}")

        return {
            "status": "success",
            "client_id": client_id,
            "email": registration.email,
            "company_name": registration.company_name,
            "verification_token": result.get("verification_token"),
            "message": "Registration successful. Check your email to verify.",
            "verification_required": True
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Registration error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Registration failed"
        )


@router.post("/verify-email", response_model=dict)
async def verify_email(
    request: EmailVerificationRequest,
    db: Session = Depends(get_db)
):
    """
    Verify client email with verification token

    Args:
        request: Email verification request with token
        db: Database session

    Returns:
        Dict with verification status

    Example:
        POST /v1/auth/verify-email
        {
            "token": "eyJhbGciOiJIUzI1NiIs..."
        }
    """
    try:
        logger.info("✉️  Email verification request")

        # Verify token
        result = supabase_client.verify_email(request.token)
        client_id = result["client_id"]

        # Update client status
        client = db.query(Client).filter(
            Client.client_id == client_id
        ).first()

        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        client.email_verified = True
        client.status = "active"
        client.verified_at = datetime.utcnow()
        db.commit()

        logger.info(f"✅ Email verified: {client_id}")

        return {
            "status": "success",
            "client_id": client_id,
            "email": result["email"],
            "message": "Email verified successfully. You can now login."
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Verification error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Verification failed")


# ============================================================================
# Authentication Endpoints
# ============================================================================

@router.post("/login", response_model=TokenResponse)
async def login(
    login_request: LoginRequest,
    db: Session = Depends(get_db)
):
    """
    Authenticate client and return JWT tokens

    Args:
        login_request: Email and password
        db: Database session

    Returns:
        TokenResponse with access and refresh tokens

    Example:
        POST /v1/auth/login
        {
            "email": "shreya@sharma.com",
            "password": "SecurePass123"
        }
    """
    try:
        logger.info(f"🔐 Login request: {login_request.email}")

        # Check if client exists and is active
        client = db.query(Client).filter(
            Client.email == login_request.email
        ).first()

        if not client:
            logger.warning(f"❌ Client not found: {login_request.email}")
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials"
            )

        if not client.email_verified:
            logger.warning(f"❌ Email not verified: {login_request.email}")
            raise HTTPException(
                status_code=401,
                detail="Please verify your email first"
            )

        if client.status != "active":
            logger.warning(f"❌ Client inactive: {login_request.email}")
            raise HTTPException(
                status_code=401,
                detail="Account is not active"
            )

        # Authenticate with Supabase (pass client_id so JWT uses existing ID)
        token_response = supabase_client.login(login_request, client_id=client.client_id)

        # Update last login
        client.last_login_at = datetime.utcnow()
        db.commit()

        logger.info(f"✅ Login successful: {client.client_id}")

        return token_response

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Login failed")


@router.post("/logout", response_model=dict)
async def logout(
    current_client: ClientInfo = Depends(get_current_client),
    db: Session = Depends(get_db)
):
    """
    Logout client (invalidate session)

    Args:
        current_client: Current authenticated client
        db: Database session

    Returns:
        Dict with logout status

    Example:
        POST /v1/auth/logout
        Authorization: Bearer <access_token>
    """
    try:
        logger.info(f"👋 Logout request: {current_client.client_id}")

        result = supabase_client.logout("")

        logger.info(f"✅ Logout successful: {current_client.client_id}")

        return {
            "status": "success",
            "message": "Logout successful"
        }

    except Exception as e:
        logger.error(f"❌ Logout error: {str(e)}")
        raise HTTPException(status_code=500, detail="Logout failed")


# ============================================================================
# Token Management Endpoints
# ============================================================================

@router.post("/refresh", response_model=TokenResponse)
async def refresh_access_token(
    refresh_token: str,
):
    """
    Generate new access token from refresh token

    Args:
        refresh_token: Valid refresh token

    Returns:
        TokenResponse with new access token

    Example:
        POST /v1/auth/refresh
        {
            "refresh_token": "eyJhbGciOiJIUzI1NiIs..."
        }
    """
    try:
        logger.info("🔄 Token refresh request")

        token_response = supabase_client.refresh_access_token(refresh_token)

        logger.info("✅ Token refreshed successfully")

        return token_response

    except ValueError as e:
        logger.error(f"❌ Refresh error: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired refresh token"
        )
    except Exception as e:
        logger.error(f"❌ Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail="Token refresh failed")


@router.get("/me", response_model=dict)
async def get_current_user(
    current_client: ClientInfo = Depends(get_current_client),
    db: Session = Depends(get_db)
):
    """
    Get current authenticated client info

    Args:
        current_client: Current authenticated client
        db: Database session

    Returns:
        Dict with client information

    Example:
        GET /v1/auth/me
        Authorization: Bearer <access_token>
    """
    try:
        logger.info(f"👤 Get user info: {current_client.client_id}")

        # Fetch full client details from database
        client = db.query(Client).filter(
            Client.client_id == current_client.client_id
        ).first()

        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        return {
            "client_id": client.client_id,
            "company_name": client.company_name,
            "email": client.email,
            "phone": client.phone,
            "status": client.status,
            "email_verified": client.email_verified,
            "created_at": client.created_at.isoformat(),
            "last_login_at": client.last_login_at.isoformat() if client.last_login_at else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve user info")
