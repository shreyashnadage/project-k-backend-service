"""
Installation Key & API Key Service

Handles:
- Installation key generation (one-time use for device setup)
- Installation key validation
- API key generation (for authenticated requests)
- API key rotation
- Key tracking and expiry
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# Configuration
INSTALLATION_KEY_LENGTH = 32  # 32 random bytes → 64 hex chars
INSTALLATION_KEY_EXPIRY_DAYS = 30
API_KEY_LENGTH = 32


class InstallationKeyService:
    """
    Service for managing installation keys

    Installation key = One-time setup token for device registration
    Characteristics:
    - One-time use (marks as 'used' after first device registration)
    - 30-day expiry
    - Unique per client
    - Returned via email during registration
    """

    @staticmethod
    def generate_installation_key(client_id: str) -> Dict:
        """
        Generate a new installation key for a client

        Args:
            client_id: The client this key is for

        Returns:
            Dict with key, expires_at, key_id

        Example:
            {
                "key_id": "key_abc123...",
                "installation_key": "TSA-ABC123-DEF456-GHI789",
                "expires_at": "2026-07-28T12:34:56Z",
                "status": "active"
            }
        """
        try:
            logger.info(f"Generating installation key for client: {client_id}")

            # Generate unique key ID
            key_id = str(uuid.uuid4())

            # Generate random key (64 hex characters)
            raw_key = secrets.token_hex(INSTALLATION_KEY_LENGTH)

            # Format as readable key (TSA-prefix for "Tally Sync Agent")
            # Example: TSA-ABC123-DEF456-GHI789-JKL012
            formatted_key = "TSA-" + "-".join([
                raw_key[i:i+6].upper()
                for i in range(0, len(raw_key), 6)
            ])

            # Calculate expiry (30 days from now)
            expires_at = datetime.now(timezone.utc) + timedelta(
                days=INSTALLATION_KEY_EXPIRY_DAYS
            )

            logger.info(f"✓ Installation key generated: {formatted_key[:20]}...")

            return {
                "key_id": key_id,
                "installation_key": formatted_key,
                "raw_key": raw_key,  # Store this for validation
                "client_id": client_id,
                "expires_at": expires_at.isoformat(),
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            logger.error(f"✗ Key generation failed: {str(e)}")
            raise ValueError(f"Failed to generate installation key: {str(e)}")

    @staticmethod
    def validate_installation_key(
        installation_key: str,
        stored_key_data: Dict
    ) -> Tuple[bool, str]:
        """
        Validate an installation key

        Args:
            installation_key: Key provided by user
            stored_key_data: Key data from database

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            logger.info(f"Validating installation key: {installation_key[:20]}...")

            # Check if key exists
            if not stored_key_data:
                return False, "Installation key not found"

            # Check if already used
            if stored_key_data.get("status") == "used":
                return False, "Installation key already used"

            # Check if expired
            expires_at = stored_key_data.get("expires_at")
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)
            if isinstance(expires_at, datetime) and expires_at < datetime.now(timezone.utc):
                return False, "Installation key expired"

            # Check if matches stored key
            if stored_key_data.get("installation_key") != installation_key:
                return False, "Invalid installation key"

            logger.info("✓ Installation key validated successfully")
            return True, ""

        except Exception as e:
            logger.error(f"✗ Validation error: {str(e)}")
            return False, f"Validation error: {str(e)}"

    @staticmethod
    def mark_key_as_used(key_data: Dict, device_id: str) -> Dict:
        """
        Mark installation key as used (after device registration)

        Args:
            key_data: Original key data
            device_id: Device that used this key

        Returns:
            Updated key data
        """
        try:
            logger.info(f"Marking key as used by device: {device_id}")

            key_data["status"] = "used"
            key_data["used_at"] = datetime.now(timezone.utc).isoformat()
            key_data["device_id_used_by"] = device_id

            logger.info("✓ Key marked as used")
            return key_data

        except Exception as e:
            logger.error(f"✗ Error marking key as used: {str(e)}")
            raise ValueError(f"Failed to mark key as used: {str(e)}")


class APIKeyService:
    """
    Service for managing API keys

    API key = Long-lived credential for authenticated API requests
    Characteristics:
    - Unique per device
    - Can be rotated/revoked
    - Stored securely (hashed or in Infisical)
    - Sent in Authorization header
    """

    @staticmethod
    def generate_api_key(device_id: str, client_id: str) -> Dict:
        """
        Generate a new API key for a device

        Args:
            device_id: The device this key is for
            client_id: The client/tenant

        Returns:
            Dict with api_key, key_id, created_at

        Example:
            {
                "key_id": "apikey_abc123...",
                "api_key": "sk_live_abc123...xyz789",
                "prefix": "sk_live",
                "created_at": "2026-06-28T12:34:56Z",
                "status": "active",
                "last_used_at": null
            }
        """
        try:
            logger.info(f"Generating API key for device: {device_id}")

            # Generate unique key ID
            key_id = str(uuid.uuid4())

            # Generate random API key (64 random bytes)
            raw_key = secrets.token_urlsafe(API_KEY_LENGTH)

            # Format with prefix: sk_live_<random>
            api_key = f"sk_live_{raw_key}"

            logger.info(f"✓ API key generated: {api_key[:20]}...")

            return {
                "key_id": key_id,
                "api_key": api_key,
                "prefix": "sk_live",
                "device_id": device_id,
                "client_id": client_id,
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_used_at": None,
                "rotated_from": None  # For tracking key rotation
            }

        except Exception as e:
            logger.error(f"✗ API key generation failed: {str(e)}")
            raise ValueError(f"Failed to generate API key: {str(e)}")

    @staticmethod
    def validate_api_key(api_key: str, stored_key_data: Dict) -> Tuple[bool, Dict]:
        """
        Validate an API key

        Args:
            api_key: Key provided in request
            stored_key_data: Key data from database

        Returns:
            Tuple of (is_valid, key_data)
        """
        try:
            logger.info(f"Validating API key: {api_key[:20]}...")

            # Check if key exists
            if not stored_key_data:
                return False, {}

            # Check if active
            if stored_key_data.get("status") != "active":
                return False, {}

            # Check if matches
            if stored_key_data.get("api_key") != api_key:
                return False, {}

            logger.info("✓ API key validated successfully")
            return True, stored_key_data

        except Exception as e:
            logger.error(f"✗ Validation error: {str(e)}")
            return False, {}

    @staticmethod
    def rotate_api_key(
        old_key_data: Dict,
        new_key_id: str,
        new_api_key: str
    ) -> Tuple[Dict, Dict]:
        """
        Rotate an API key (revoke old, create new)

        Args:
            old_key_data: Old key to revoke
            new_key_id: ID for new key
            new_api_key: New API key value

        Returns:
            Tuple of (revoked_old_key, new_key_data)
        """
        try:
            logger.info("Rotating API key...")

            # Revoke old key
            old_key_data["status"] = "revoked"
            old_key_data["revoked_at"] = datetime.now(timezone.utc).isoformat()

            # Create new key data
            new_key_data = {
                "key_id": new_key_id,
                "api_key": new_api_key,
                "device_id": old_key_data.get("device_id"),
                "client_id": old_key_data.get("client_id"),
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_used_at": None,
                "rotated_from": old_key_data.get("key_id")
            }

            logger.info("✓ API key rotated successfully")
            return old_key_data, new_key_data

        except Exception as e:
            logger.error(f"✗ Key rotation failed: {str(e)}")
            raise ValueError(f"Failed to rotate key: {str(e)}")

    @staticmethod
    def revoke_api_key(key_data: Dict) -> Dict:
        """
        Revoke an API key

        Args:
            key_data: Key to revoke

        Returns:
            Updated key data
        """
        try:
            logger.info(f"Revoking API key: {key_data.get('key_id')}")

            key_data["status"] = "revoked"
            key_data["revoked_at"] = datetime.now(timezone.utc).isoformat()

            logger.info("✓ API key revoked")
            return key_data

        except Exception as e:
            logger.error(f"✗ Revocation failed: {str(e)}")
            raise ValueError(f"Failed to revoke key: {str(e)}")

    @staticmethod
    def update_last_used(key_data: Dict) -> Dict:
        """
        Update last_used_at timestamp (for tracking)

        Args:
            key_data: Key that was used

        Returns:
            Updated key data
        """
        key_data["last_used_at"] = datetime.now(timezone.utc).isoformat()
        return key_data


# Singleton instances
installation_key_service = InstallationKeyService()
api_key_service = APIKeyService()
