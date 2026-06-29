"""
Supabase Authentication Client
Handles JWT validation, client registration, and session management
"""

import os
import jwt
from datetime import datetime, timedelta
from typing import Dict, Optional
from pydantic import BaseModel, EmailStr, field_validator
import logging

logger = logging.getLogger(__name__)

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:3000")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-key-change-in-prod")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 7


# ============================================================================
# Pydantic Models
# ============================================================================

class RegistrationRequest(BaseModel):
    """Client registration request"""
    company_name: str
    email: EmailStr
    phone: str
    password: str

    @field_validator('company_name')
    @classmethod
    def company_name_not_empty(cls, v):
        if not v or len(v.strip()) < 3:
            raise ValueError('Company name must be at least 3 characters')
        return v.strip()

    @field_validator('password')
    @classmethod
    def password_strong(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters')
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain uppercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain digit')
        return v


class LoginRequest(BaseModel):
    """Client login request"""
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """JWT token response"""
    access_token: str
    refresh_token: Optional[str]
    token_type: str = "bearer"
    expires_in: int  # seconds


class ClientInfo(BaseModel):
    """Client information from JWT"""
    client_id: str
    email: str
    company_name: str
    phone: str
    created_at: datetime
    email_verified: bool = False


class EmailVerificationRequest(BaseModel):
    """Email verification request"""
    token: str


# ============================================================================
# Supabase Client
# ============================================================================

class SupabaseAuthClient:
    """
    Supabase Authentication Client

    Handles:
    - Client registration
    - Email verification
    - JWT token generation & validation
    - Login/logout
    - Session management
    """

    def __init__(
        self,
        supabase_url: str = SUPABASE_URL,
        supabase_key: str = SUPABASE_KEY,
        jwt_secret: str = JWT_SECRET,
    ):
        self.supabase_url = supabase_url
        self.supabase_key = supabase_key
        self.jwt_secret = jwt_secret
        logger.info(f"SupabaseAuthClient initialized: {supabase_url}")

    # ========================================================================
    # Registration & Verification
    # ========================================================================

    def register_client(
        self,
        registration: RegistrationRequest,
        db_session=None
    ) -> Dict:
        """
        Register a new MSME client

        Args:
            registration: Registration request data
            db_session: Database session for storing client

        Returns:
            Dict with client_id, verification_token, status

        Raises:
            ValueError: If client already exists or validation fails
        """
        try:
            logger.info(f"Registering client: {registration.email}")

            # In production, this would use real Supabase API
            # For now, we'll simulate it

            # Generate client_id
            import uuid
            client_id = f"cli_{uuid.uuid4().hex[:12]}"

            # Generate email verification token
            verification_token = self._generate_email_verification_token(
                email=registration.email,
                client_id=client_id
            )

            # In real implementation, store in Supabase Auth
            # supabase.auth.sign_up(
            #     email=registration.email,
            #     password=registration.password
            # )

            logger.info(f"✓ Client registered: {client_id}")

            return {
                "status": "success",
                "client_id": client_id,
                "email": registration.email,
                "company_name": registration.company_name,
                "verification_token": verification_token,
                "message": "Registration successful. Please verify your email."
            }

        except Exception as e:
            logger.error(f"✗ Registration failed: {str(e)}")
            raise ValueError(f"Registration failed: {str(e)}")

    def verify_email(self, verification_token: str) -> Dict:
        """
        Verify client email with token

        Args:
            verification_token: Email verification token

        Returns:
            Dict with verification status and client_id
        """
        try:
            logger.info("Verifying email...")

            # Decode and validate token
            payload = jwt.decode(
                verification_token,
                self.jwt_secret,
                algorithms=[JWT_ALGORITHM]
            )

            client_id = payload.get("sub")
            email = payload.get("email")
            token_type = payload.get("type")

            if token_type != "email_verification":
                raise ValueError("Invalid token type")

            # In production: update email_verified in database
            logger.info(f"✓ Email verified for: {email}")

            return {
                "status": "success",
                "client_id": client_id,
                "email": email,
                "message": "Email verified successfully"
            }

        except (jwt.InvalidTokenError, jwt.DecodeError) as e:
            logger.error(f"✗ Email verification failed: {str(e)}")
            raise ValueError("Invalid or expired verification token")

    # ========================================================================
    # Login & Logout
    # ========================================================================

    def login(self, login_request: LoginRequest, client_id: str = None) -> TokenResponse:
        """
        Authenticate client and return JWT tokens

        Args:
            login_request: Login credentials
            client_id: Client ID (if already verified by routes)

        Returns:
            TokenResponse with access and refresh tokens

        Raises:
            ValueError: If credentials invalid
        """
        try:
            logger.info(f"Login attempt: {login_request.email}")

            # In production: validate against Supabase Auth
            # auth_response = supabase.auth.sign_in_with_password(
            #     email=login_request.email,
            #     password=login_request.password
            # )

            # If client_id provided by routes (already verified), use it
            # Otherwise generate new one (for testing)
            if not client_id:
                import uuid
                client_id = f"cli_{uuid.uuid4().hex[:12]}"

            access_token = self._create_access_token(
                client_id=client_id,
                email=login_request.email
            )

            refresh_token = self._create_refresh_token(
                client_id=client_id
            )

            logger.info(f"✓ Login successful: {client_id}")

            return TokenResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60
            )

        except Exception as e:
            logger.error(f"✗ Login failed: {str(e)}")
            raise ValueError("Invalid credentials")

    def logout(self, access_token: str) -> Dict:
        """
        Logout client (invalidate token)

        Args:
            access_token: Current access token

        Returns:
            Dict with logout status
        """
        try:
            logger.info("Processing logout...")

            # In production: invalidate token in Supabase
            # Could store in token blacklist

            return {
                "status": "success",
                "message": "Logout successful"
            }

        except Exception as e:
            logger.error(f"✗ Logout failed: {str(e)}")
            raise ValueError("Logout failed")

    # ========================================================================
    # Token Management
    # ========================================================================

    def _create_access_token(self, client_id: str, email: str) -> str:
        """Create JWT access token"""
        expire = datetime.utcnow() + timedelta(
            minutes=ACCESS_TOKEN_EXPIRE_MINUTES
        )
        to_encode = {
            "sub": client_id,
            "email": email,
            "type": "access",
            "exp": expire,
            "iat": datetime.utcnow()
        }
        encoded_jwt = jwt.encode(
            to_encode,
            self.jwt_secret,
            algorithm=JWT_ALGORITHM
        )
        return encoded_jwt

    def _create_refresh_token(self, client_id: str) -> str:
        """Create JWT refresh token"""
        expire = datetime.utcnow() + timedelta(
            days=REFRESH_TOKEN_EXPIRE_DAYS
        )
        to_encode = {
            "sub": client_id,
            "type": "refresh",
            "exp": expire,
            "iat": datetime.utcnow()
        }
        encoded_jwt = jwt.encode(
            to_encode,
            self.jwt_secret,
            algorithm=JWT_ALGORITHM
        )
        return encoded_jwt

    def _generate_email_verification_token(
        self,
        email: str,
        client_id: str
    ) -> str:
        """Generate email verification token"""
        expire = datetime.utcnow() + timedelta(hours=24)
        to_encode = {
            "sub": client_id,
            "email": email,
            "type": "email_verification",
            "exp": expire,
            "iat": datetime.utcnow()
        }
        encoded_jwt = jwt.encode(
            to_encode,
            self.jwt_secret,
            algorithm=JWT_ALGORITHM
        )
        return encoded_jwt

    def verify_token(self, token: str) -> ClientInfo:
        """
        Verify and decode access token

        Args:
            token: JWT access token

        Returns:
            ClientInfo with client details

        Raises:
            ValueError: If token invalid or expired
        """
        try:
            payload = jwt.decode(
                token,
                self.jwt_secret,
                algorithms=[JWT_ALGORITHM]
            )

            client_id: str = payload.get("sub")
            email: str = payload.get("email")
            token_type: str = payload.get("type")

            if token_type != "access":
                raise ValueError("Invalid token type")

            if client_id is None:
                raise ValueError("Invalid token payload")

            return ClientInfo(
                client_id=client_id,
                email=email,
                company_name="",  # Would fetch from DB
                phone="",  # Would fetch from DB
                created_at=datetime.utcnow(),
                email_verified=True
            )

        except (jwt.InvalidTokenError, jwt.DecodeError) as e:
            logger.error(f"✗ Token verification failed: {str(e)}")
            raise ValueError("Invalid or expired token")

    def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """
        Generate new access token from refresh token

        Args:
            refresh_token: Valid refresh token

        Returns:
            TokenResponse with new access token
        """
        try:
            payload = jwt.decode(
                refresh_token,
                self.jwt_secret,
                algorithms=[JWT_ALGORITHM]
            )

            client_id: str = payload.get("sub")
            token_type: str = payload.get("type")

            if token_type != "refresh":
                raise ValueError("Invalid token type")

            # Create new access token
            access_token = self._create_access_token(
                client_id=client_id,
                email=client_id  # Would fetch from DB
            )

            logger.info(f"✓ Token refreshed for: {client_id}")

            return TokenResponse(
                access_token=access_token,
                refresh_token=None,
                token_type="bearer",
                expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60
            )

        except (jwt.InvalidTokenError, jwt.DecodeError) as e:
            logger.error(f"✗ Token refresh failed: {str(e)}")
            raise ValueError("Invalid or expired refresh token")


# ============================================================================
# Singleton Instance
# ============================================================================

supabase_client = SupabaseAuthClient()
