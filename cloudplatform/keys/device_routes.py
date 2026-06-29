"""
Device Registration Routes

FastAPI endpoints for:
- Device registration (using installation key)
- Device management
- API key operations
"""

from fastapi import APIRouter, HTTPException, Depends, Security
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import logging
import uuid
import secrets

from cloudplatform.keys.key_service import installation_key_service, api_key_service
from cloudplatform.auth.routes import get_current_client
from cloudplatform.auth.supabase_client import ClientInfo
from cloudplatform.db.database import get_db
from cloudplatform.db.models import DeviceRegistration, InstallationKey

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/devices", tags=["device-registration"])


# ============================================================================
# Pydantic Models
# ============================================================================

class DeviceRegistrationRequest(BaseModel):
    """Device registration request"""
    installation_key: str = Field(..., description="Installation key from email")
    device_name: str = Field(..., description="Human-readable device name (e.g., OFFICE-PC-01)")
    os_version: str = Field(None, description="Operating system version")
    agent_version: str = Field(None, description="Agent software version")


class DeviceRegistrationResponse(BaseModel):
    """Device registration response"""
    device_id: str
    device_name: str
    api_key: str
    registration_token: str
    status: str = "active"
    registered_at: str
    message: str


class APIKeyRotationResponse(BaseModel):
    """API key rotation response"""
    new_api_key: str
    old_key_revoked_at: str
    message: str


class DeviceStatusResponse(BaseModel):
    """Device status response"""
    device_id: str
    device_name: str
    status: str
    registered_at: str
    last_sync_at: str = None
    last_ip: str = None


# ============================================================================
# Device Registration Endpoints
# ============================================================================

@router.post("/register", response_model=DeviceRegistrationResponse)
async def register_device(
    request: DeviceRegistrationRequest,
    current_client: ClientInfo = Depends(get_current_client),
    db: Session = Depends(get_db)
):
    """
    Register a new device (agent PC)

    The device registration flow:
    1. MSME receives installation key via email during client registration
    2. Agent installer prompts for key
    3. Agent sends key to this endpoint
    4. Platform validates key, generates device_id and api_key
    5. Agent stores credentials securely in Windows Credential Manager
    6. Future syncs use device_id + api_key for authentication

    Args:
        request: Device registration details with installation key
        current_client: Authenticated client
        db: Database session

    Returns:
        DeviceRegistrationResponse with device_id and api_key

    Example:
        POST /v1/devices/register
        Authorization: Bearer <access_token>
        {
            "installation_key": "TSA-ABC123-DEF456-GHI789",
            "device_name": "OFFICE-PC-01",
            "os_version": "Windows 11",
            "agent_version": "0.4.0"
        }

        Response:
        {
            "device_id": "device_abc123...",
            "device_name": "OFFICE-PC-01",
            "api_key": "sk_live_xyz789...",
            "registration_token": "reg_token_abc123...",
            "status": "active",
            "registered_at": "2026-06-28T12:34:56Z",
            "message": "Device registered successfully"
        }
    """
    try:
        logger.info(f"[Device] Registration request: {request.device_name}")

        # Step 1: Validate installation key
        logger.info(f"[Device] Validating installation key...")

        # Lookup key in database
        key_record = db.query(InstallationKey).filter(
            InstallationKey.installation_key == request.installation_key
        ).first()

        if not key_record:
            logger.warning(f"[Device] ✗ Key not found: {request.installation_key}")
            raise HTTPException(status_code=400, detail="Invalid installation key")

        # Validate key
        is_valid, error_msg = installation_key_service.validate_installation_key(
            request.installation_key,
            {
                "installation_key": key_record.installation_key,
                "status": key_record.status,
                "expires_at": key_record.expires_at,
                "client_id": key_record.client_id
            }
        )

        if not is_valid:
            logger.warning(f"[Device] ✗ Key validation failed: {error_msg}")
            raise HTTPException(status_code=400, detail=error_msg)

        # Verify key belongs to this client
        if key_record.client_id != current_client.client_id:
            logger.warning(f"[Device] ✗ Key doesn't belong to client: {current_client.client_id}")
            raise HTTPException(status_code=403, detail="Key not authorized for this client")

        logger.info(f"[Device] ✓ Key validated")

        # Step 2: Generate device credentials
        logger.info(f"[Device] Generating credentials...")

        device_id = f"device_{uuid.uuid4().hex[:12]}"
        registration_token = f"reg_{secrets.token_urlsafe(32)}"

        # Generate API key
        api_key_data = api_key_service.generate_api_key(
            device_id=device_id,
            client_id=current_client.client_id
        )
        api_key = api_key_data["api_key"]

        logger.info(f"[Device] ✓ Credentials generated")

        # Step 3: Store device in database
        logger.info(f"[Device] Storing device registration...")

        device = DeviceRegistration(
            device_id=device_id,
            client_id=current_client.client_id,
            device_name=request.device_name,
            os_version=request.os_version,
            agent_version=request.agent_version,
            api_key=api_key,
            registration_token=registration_token,
            status="active",
            registered_at=datetime.now(timezone.utc)
        )
        db.add(device)

        # Mark installation key as used
        key_record.status = "used"
        key_record.device_id_used_by = device_id
        key_record.used_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(device)

        logger.info(f"[Device] ✓ Device registered: {device_id}")

        return DeviceRegistrationResponse(
            device_id=device_id,
            device_name=request.device_name,
            api_key=api_key,
            registration_token=registration_token,
            status="active",
            registered_at=device.registered_at.isoformat(),
            message="Device registered successfully. Store API key securely."
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Device] ✗ Registration error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Device registration failed")


@router.get("/list", response_model=list)
async def list_devices(
    current_client: ClientInfo = Depends(get_current_client),
    db: Session = Depends(get_db)
):
    """
    List all registered devices for current client

    Args:
        current_client: Authenticated client
        db: Database session

    Returns:
        List of device details

    Example:
        GET /v1/devices/list
        Authorization: Bearer <access_token>

        Response:
        [
            {
                "device_id": "device_abc123...",
                "device_name": "OFFICE-PC-01",
                "status": "active",
                "registered_at": "2026-06-28T12:34:56Z",
                "last_sync_at": "2026-06-28T13:00:00Z",
                "last_ip": "192.168.1.100"
            },
            ...
        ]
    """
    try:
        logger.info(f"[Device] Listing devices for client: {current_client.client_id}")

        devices = db.query(DeviceRegistration).filter(
            DeviceRegistration.client_id == current_client.client_id,
            DeviceRegistration.status == "active"
        ).all()

        return [
            {
                "device_id": d.device_id,
                "device_name": d.device_name,
                "status": d.status,
                "registered_at": d.registered_at.isoformat(),
                "last_sync_at": d.last_sync_at.isoformat() if d.last_sync_at else None,
                "last_ip": d.last_ip,
                "os_version": d.os_version,
                "agent_version": d.agent_version
            }
            for d in devices
        ]

    except Exception as e:
        logger.error(f"[Device] ✗ Error listing devices: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to list devices")


@router.post("/rotate-key", response_model=APIKeyRotationResponse)
async def rotate_api_key(
    device_id: str,
    current_client: ClientInfo = Depends(get_current_client),
    db: Session = Depends(get_db)
):
    """
    Rotate API key for a device

    Revokes old key and issues a new one. This allows key rotation without
    disrupting the agent if a key is compromised.

    Args:
        device_id: Device to rotate key for
        current_client: Authenticated client
        db: Database session

    Returns:
        APIKeyRotationResponse with new key and revocation timestamp

    Example:
        POST /v1/devices/rotate-key?device_id=device_abc123
        Authorization: Bearer <access_token>

        Response:
        {
            "new_api_key": "sk_live_new_xyz789...",
            "old_key_revoked_at": "2026-06-28T13:15:00Z",
            "message": "API key rotated successfully"
        }
    """
    try:
        logger.info(f"[Device] Rotating key for device: {device_id}")

        # Verify device belongs to client
        device = db.query(DeviceRegistration).filter(
            DeviceRegistration.device_id == device_id,
            DeviceRegistration.client_id == current_client.client_id
        ).first()

        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        # Generate new key
        new_key_data = api_key_service.generate_api_key(device_id, current_client.client_id)
        new_api_key = new_key_data["api_key"]

        # Update device with new key
        device.api_key = new_api_key
        device.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(device)

        logger.info(f"[Device] ✓ Key rotated for device: {device_id}")

        return APIKeyRotationResponse(
            new_api_key=new_api_key,
            old_key_revoked_at=datetime.now(timezone.utc).isoformat(),
            message="API key rotated successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Device] ✗ Key rotation error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Key rotation failed")


@router.get("/status/{device_id}", response_model=DeviceStatusResponse)
async def get_device_status(
    device_id: str,
    current_client: ClientInfo = Depends(get_current_client),
    db: Session = Depends(get_db)
):
    """
    Get status of a specific device

    Args:
        device_id: Device to check
        current_client: Authenticated client
        db: Database session

    Returns:
        DeviceStatusResponse with device details

    Example:
        GET /v1/devices/status/device_abc123
        Authorization: Bearer <access_token>

        Response:
        {
            "device_id": "device_abc123",
            "device_name": "OFFICE-PC-01",
            "status": "active",
            "registered_at": "2026-06-28T12:34:56Z",
            "last_sync_at": "2026-06-28T14:00:00Z",
            "last_ip": "192.168.1.100"
        }
    """
    try:
        device = db.query(DeviceRegistration).filter(
            DeviceRegistration.device_id == device_id,
            DeviceRegistration.client_id == current_client.client_id
        ).first()

        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        return DeviceStatusResponse(
            device_id=device.device_id,
            device_name=device.device_name,
            status=device.status,
            registered_at=device.registered_at.isoformat(),
            last_sync_at=device.last_sync_at.isoformat() if device.last_sync_at else None,
            last_ip=device.last_ip
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Device] ✗ Status check error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get device status")
