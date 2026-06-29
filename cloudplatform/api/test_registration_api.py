"""
Test script for registration API endpoints.

Tests:
1. POST /register - Portal registration
2. POST /v1/register-device - Agent registration
3. GET /v1/clients/{client_id}/stats - Get stats
4. POST /v1/sync-with-client - Send data with client_id
"""

import sys
import os
import json
from datetime import datetime

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Override DATABASE_URL to use test SQLite
os.environ['DATABASE_URL'] = 'sqlite:///cloudplatform/db/test_registration.db'

from sqlalchemy.orm import Session
from cloudplatform.db.database import SessionLocal, engine
from cloudplatform.db.models import Base, Client, InstallationKey, DeviceRegistration, SyncRecord, RegistrationAuditLog
from cloudplatform.api.registration import (
    register_client, register_device, get_client_stats, sync_data_with_client
)


class MockSession:
    """Mock session for testing."""
    def __init__(self):
        self.real_session = SessionLocal()

    def query(self, model):
        return self.real_session.query(model)

    def add(self, obj):
        return self.real_session.add(obj)

    def flush(self):
        return self.real_session.flush()

    def commit(self):
        return self.real_session.commit()

    def rollback(self):
        return self.real_session.rollback()

    def close(self):
        return self.real_session.close()


def test_registration_flow():
    """Test the complete registration flow."""
    print("\n" + "="*80)
    print("TESTING REGISTRATION API ENDPOINTS")
    print("="*80 + "\n")

    db = SessionLocal()

    try:
        # ====================================================================
        # TEST 1: Portal Registration
        # ====================================================================
        print("TEST 1: Portal Registration (POST /register)")
        print("-" * 80)

        try:
            result = register_client(
                company_name="Sharma Traders Pvt Ltd",
                email="test-portal@sharma.com",
                phone="9876543210",
                gst_id="18AABCU12345K1Z5",
                db=db
            )

            print("Request:")
            print(json.dumps({
                "company_name": "Sharma Traders Pvt Ltd",
                "email": "test-portal@sharma.com",
                "phone": "9876543210",
                "gst_id": "18AABCU12345K1Z5"
            }, indent=2))

            print("\nResponse:")
            print(json.dumps(result, indent=2, default=str))

            client_id = result['client_id']
            installation_key = result['installation_key']

            print(f"\n✓ TEST PASSED")
            print(f"  - Client ID: {client_id}")
            print(f"  - Installation Key: {installation_key}")

        except Exception as e:
            print(f"✗ TEST FAILED: {e}")
            return False

        # ====================================================================
        # TEST 2: Agent Registration
        # ====================================================================
        print("\n\nTEST 2: Agent Registration (POST /v1/register-device)")
        print("-" * 80)

        try:
            result = register_device(
                installation_key=installation_key,
                device_name="TEST-OFFICE-PC",
                os_version="Windows 11 Build 26200",
                agent_version="0.4.0",
                db=db
            )

            print("Request:")
            print(json.dumps({
                "installation_key": installation_key,
                "device_name": "TEST-OFFICE-PC",
                "os_version": "Windows 11 Build 26200",
                "agent_version": "0.4.0"
            }, indent=2))

            print("\nResponse:")
            print(json.dumps(result, indent=2, default=str))

            device_id = result['device_id']
            api_key = result['api_key']

            print(f"\n✓ TEST PASSED")
            print(f"  - Device ID: {device_id}")
            print(f"  - API Key: {api_key}")

        except Exception as e:
            print(f"✗ TEST FAILED: {e}")
            return False

        # ====================================================================
        # TEST 3: Get Client Stats (Before any syncs)
        # ====================================================================
        print("\n\nTEST 3: Get Client Stats - BEFORE SYNC (GET /v1/clients/{id}/stats)")
        print("-" * 80)

        try:
            result = get_client_stats(
                client_id=client_id,
                x_api_key=api_key,
                db=db
            )

            print("Request:")
            print(f"GET /v1/clients/{client_id}/stats")
            print(f"Headers: x-api-key: {api_key}")

            print("\nResponse:")
            print(json.dumps(result, indent=2, default=str))

            print(f"\n✓ TEST PASSED")
            print(f"  - Total Syncs: {result['total_syncs']}")
            print(f"  - Total Records: {result['total_records']}")

        except Exception as e:
            print(f"✗ TEST FAILED: {e}")
            return False

        # ====================================================================
        # TEST 4: Send Data with Client ID Tagging
        # ====================================================================
        print("\n\nTEST 4: Send Data with Client ID Tagging (POST /v1/sync-with-client)")
        print("-" * 80)

        try:
            result = sync_data_with_client(
                client_id=client_id,
                device_id=device_id,
                records_data={"sample": "data"},
                extracted_ledgers=150,
                extracted_vouchers=500,
                x_api_key=api_key,
                db=db
            )

            print("Request:")
            print(json.dumps({
                "client_id": client_id,
                "device_id": device_id,
                "extracted_ledgers": 150,
                "extracted_vouchers": 500
            }, indent=2))

            print("\nResponse:")
            print(json.dumps(result, indent=2, default=str))

            sync_id = result['sync_id']

            print(f"\n✓ TEST PASSED")
            print(f"  - Sync ID: {sync_id}")
            print(f"  - Records Received: {result['records_received']}")

        except Exception as e:
            print(f"✗ TEST FAILED: {e}")
            return False

        # ====================================================================
        # TEST 5: Get Client Stats (After sync)
        # ====================================================================
        print("\n\nTEST 5: Get Client Stats - AFTER SYNC (GET /v1/clients/{id}/stats)")
        print("-" * 80)

        try:
            result = get_client_stats(
                client_id=client_id,
                x_api_key=api_key,
                db=db
            )

            print("Request:")
            print(f"GET /v1/clients/{client_id}/stats")
            print(f"Headers: x-api-key: {api_key}")

            print("\nResponse:")
            print(json.dumps(result, indent=2, default=str))

            print(f"\n✓ TEST PASSED")
            print(f"  - Total Syncs: {result['total_syncs']}")
            print(f"  - Total Records: {result['total_records']}")
            print(f"  - Total Ledgers: {result['total_ledgers']}")
            print(f"  - Total Vouchers: {result['total_vouchers']}")

        except Exception as e:
            print(f"✗ TEST FAILED: {e}")
            return False

        # ====================================================================
        # TEST 6: Second Sync to verify tracking
        # ====================================================================
        print("\n\nTEST 6: Second Sync to verify tracking (POST /v1/sync-with-client)")
        print("-" * 80)

        try:
            result = sync_data_with_client(
                client_id=client_id,
                device_id=device_id,
                records_data={"sample": "data2"},
                extracted_ledgers=200,
                extracted_vouchers=600,
                x_api_key=api_key,
                db=db
            )

            print("Request:")
            print(json.dumps({
                "client_id": client_id,
                "device_id": device_id,
                "extracted_ledgers": 200,
                "extracted_vouchers": 600
            }, indent=2))

            print("\nResponse:")
            print(json.dumps(result, indent=2, default=str))

            print(f"\n✓ TEST PASSED")

        except Exception as e:
            print(f"✗ TEST FAILED: {e}")
            return False

        # ====================================================================
        # TEST 7: Final Stats Verification
        # ====================================================================
        print("\n\nTEST 7: Final Stats - Both Syncs Combined (GET /v1/clients/{id}/stats)")
        print("-" * 80)

        try:
            result = get_client_stats(
                client_id=client_id,
                x_api_key=api_key,
                db=db
            )

            print("Response:")
            print(json.dumps(result, indent=2, default=str))

            print(f"\n✓ TEST PASSED")
            print(f"  - Total Syncs: {result['total_syncs']} (expected 2)")
            print(f"  - Total Records: {result['total_records']} (expected 1450 = 650 + 800)")
            print(f"  - Total Ledgers: {result['total_ledgers']} (expected 350 = 150 + 200)")
            print(f"  - Total Vouchers: {result['total_vouchers']} (expected 1100 = 500 + 600)")

            # Verify counts
            assert result['total_syncs'] == 2, f"Expected 2 syncs, got {result['total_syncs']}"
            assert result['total_records'] == 1450, f"Expected 1450 records, got {result['total_records']}"
            assert result['total_ledgers'] == 350, f"Expected 350 ledgers, got {result['total_ledgers']}"
            assert result['total_vouchers'] == 1100, f"Expected 1100 vouchers, got {result['total_vouchers']}"

            print("\n✓ All counts verified!")

        except Exception as e:
            print(f"✗ TEST FAILED: {e}")
            return False

        return True

    finally:
        db.close()


def show_database_state():
    """Show the final state of the database."""
    print("\n" + "="*80)
    print("DATABASE STATE AFTER TESTS")
    print("="*80 + "\n")

    db = SessionLocal()

    try:
        # Show clients
        clients = db.query(Client).all()
        print(f"Clients: {len(clients)}")
        for client in clients:
            print(f"  - {client.company_name} ({client.email})")

        # Show devices
        devices = db.query(DeviceRegistration).all()
        print(f"\nDevices: {len(devices)}")
        for device in devices:
            client = db.query(Client).filter_by(client_id=device.client_id).first()
            print(f"  - {device.device_name} (for {client.company_name})")

        # Show syncs
        syncs = db.query(SyncRecord).all()
        print(f"\nSync Records: {len(syncs)}")
        total_records = 0
        for sync in syncs:
            print(f"  - {sync.extracted_ledgers} ledgers + {sync.extracted_vouchers} vouchers")
            total_records += sync.records_count
        print(f"  Total: {total_records} records")

        # Show audit logs
        audits = db.query(RegistrationAuditLog).all()
        print(f"\nAudit Log Entries: {len(audits)}")
        for audit in audits[:5]:  # Show first 5
            print(f"  - {audit.action} ({audit.created_at})")

    finally:
        db.close()


if __name__ == "__main__":
    success = test_registration_flow()

    if success:
        show_database_state()

        print("\n" + "="*80)
        print("ALL TESTS PASSED!")
        print("="*80 + "\n")
        sys.exit(0)
    else:
        print("\n" + "="*80)
        print("TESTS FAILED!")
        print("="*80 + "\n")
        sys.exit(1)
