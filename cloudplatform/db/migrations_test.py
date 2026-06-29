"""
Test database migration script using SQLite.
This creates the tables locally for testing purposes.
"""

import logging
import sys
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Set UTF-8 encoding for Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Use SQLite for testing
TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "test_registration.db")
DATABASE_URL = f"sqlite:///{TEST_DB_PATH}"

print("\n[Migration Test] Using SQLite for local testing")
print(f"Database file: {TEST_DB_PATH}\n")

# Create engine
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

# Import models after engine is created
from cloudplatform.db.models import (
    Base, Client, InstallationKey, DeviceRegistration,
    SyncRecord, RegistrationAuditLog
)


def create_all_tables():
    """Create all tables in the database."""
    try:
        logger.info("Creating database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("✓ All tables created successfully")
        return True
    except Exception as e:
        logger.error(f"✗ Failed to create tables: {e}")
        return False


def verify_tables():
    """Verify all registration tables exist."""
    try:
        session = SessionLocal()

        tables_to_check = [
            ("clients", "SELECT COUNT(*) FROM clients"),
            ("installation_keys", "SELECT COUNT(*) FROM installation_keys"),
            ("device_registrations", "SELECT COUNT(*) FROM device_registrations"),
            ("sync_records", "SELECT COUNT(*) FROM sync_records"),
            ("registration_audit_log", "SELECT COUNT(*) FROM registration_audit_log"),
        ]

        all_exist = True
        for table_name, query in tables_to_check:
            try:
                result = session.execute(text(query))
                count = result.scalar()
                logger.info(f"✓ Table '{table_name}' exists (records: {count})")
            except Exception as e:
                logger.warning(f"✗ Table '{table_name}' does not exist: {e}")
                all_exist = False

        session.close()
        return all_exist

    except Exception as e:
        logger.error(f"✗ Error verifying tables: {e}")
        return False


def show_schema():
    """Display the schema of registration tables."""
    try:
        session = SessionLocal()

        print("\n" + "="*80)
        print("DATABASE SCHEMA - Registration Tables")
        print("="*80 + "\n")

        # Get all tables from Base.metadata
        for table in Base.metadata.sorted_tables:
            if table.name in ["clients", "installation_keys", "device_registrations",
                            "sync_records", "registration_audit_log"]:
                print(f"TABLE: {table.name.upper()}")
                print("   Columns:")
                for column in table.columns:
                    col_type = str(column.type)
                    nullable = "nullable" if column.nullable else "NOT NULL"
                    pk = " [PRIMARY KEY]" if column.primary_key else ""
                    unique = " [UNIQUE]" if column.unique else ""
                    indexed = " [INDEXED]" if column.index else ""
                    print(f"      - {column.name}: {col_type} ({nullable}){pk}{unique}{indexed}")
                print()

        session.close()

    except Exception as e:
        logger.error(f"✗ Error showing schema: {e}")


def insert_test_data():
    """Insert test data to verify the system works."""
    try:
        session = SessionLocal()

        print("\n" + "="*80)
        print("INSERTING TEST DATA")
        print("="*80 + "\n")

        # Create a test client (Sharma Traders)
        client = Client(
            company_name="Sharma Traders Pvt Ltd",
            email="shreya@sharma.com",
            phone="9876543210",
            gst_id="18AABCU12345K1Z5",
            status="active",
            plan="trial"
        )
        session.add(client)
        session.flush()  # Get the client_id
        client_id = client.client_id

        logger.info(f"✓ Created client: {client.company_name} (ID: {client_id})")

        # Create an installation key
        install_key = InstallationKey(
            client_id=client_id,
            installation_key="SHARMA-2026-ABC123",
            status="active"
        )
        session.add(install_key)
        logger.info(f"✓ Created installation key: {install_key.installation_key}")

        # Create a device registration
        device = DeviceRegistration(
            client_id=client_id,
            device_name="OFFICE-PC-01",
            os_version="Windows 11 Build 26200",
            agent_version="0.4.0",
            registration_token="reg_token_xyz123",
            api_key="api_key_xyz123",
            status="active"
        )
        session.add(device)
        session.flush()
        device_id = device.device_id
        logger.info(f"✓ Created device: {device.device_name} (ID: {device_id})")

        # Create a sync record
        from datetime import datetime, timezone
        sync = SyncRecord(
            client_id=client_id,
            device_id=device_id,
            tenant_id=client_id,  # Use client_id as tenant for this test
            records_count=650,
            extracted_ledgers=150,
            extracted_vouchers=500,
            sync_status="success",
            sync_timestamp=datetime.now(timezone.utc)
        )
        session.add(sync)
        logger.info(f"✓ Created sync record: 650 records")

        # Create audit log entry
        audit = RegistrationAuditLog(
            client_id=client_id,
            action="client_registered",
            details='{"company_name": "Sharma Traders"}',
            source_device="OFFICE-PC-01"
        )
        session.add(audit)
        logger.info(f"✓ Created audit log entry")

        session.commit()
        logger.info("\n✓ Test data inserted successfully!")
        session.close()

        return True

    except Exception as e:
        logger.error(f"✗ Error inserting test data: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_test_data():
    """Verify test data was inserted correctly."""
    try:
        session = SessionLocal()

        print("\n" + "="*80)
        print("VERIFYING TEST DATA")
        print("="*80 + "\n")

        # Verify client exists
        client_count = session.query(Client).count()
        logger.info(f"✓ Clients in database: {client_count}")

        # Verify installation key exists
        key_count = session.query(InstallationKey).count()
        logger.info(f"✓ Installation keys: {key_count}")

        # Verify device exists
        device_count = session.query(DeviceRegistration).count()
        logger.info(f"✓ Registered devices: {device_count}")

        # Verify sync record exists
        sync_count = session.query(SyncRecord).count()
        logger.info(f"✓ Sync records: {sync_count}")

        # Verify audit log exists
        audit_count = session.query(RegistrationAuditLog).count()
        logger.info(f"✓ Audit log entries: {audit_count}")

        # Show client details
        client = session.query(Client).first()
        if client:
            print(f"\n📊 Client Details:")
            print(f"   Name: {client.company_name}")
            print(f"   Email: {client.email}")
            print(f"   Status: {client.status}")
            print(f"   Created: {client.created_at}")

            # Show associated device
            device = session.query(DeviceRegistration).filter_by(client_id=client.client_id).first()
            if device:
                print(f"\n📱 Device Details:")
                print(f"   Name: {device.device_name}")
                print(f"   OS: {device.os_version}")
                print(f"   Agent Version: {device.agent_version}")
                print(f"   Status: {device.status}")

            # Show sync records
            syncs = session.query(SyncRecord).filter_by(client_id=client.client_id).all()
            if syncs:
                print(f"\n📈 Recent Syncs: {len(syncs)}")
                for sync in syncs[:3]:  # Show last 3
                    print(f"   - {sync.extracted_ledgers} ledgers + {sync.extracted_vouchers} vouchers")

        session.close()
        return True

    except Exception as e:
        logger.error(f"✗ Error verifying test data: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("\n" + "="*80)
    print("DATABASE MIGRATION TEST - Registration System")
    print("="*80)

    # Step 1: Create tables
    if not create_all_tables():
        print("\nMigration failed!")
        sys.exit(1)

    # Step 2: Verify tables
    if not verify_tables():
        print("\nTable verification failed!")
        sys.exit(1)

    # Step 3: Show schema
    show_schema()

    # Step 4: Insert test data
    if not insert_test_data():
        print("\nTest data insertion failed!")
        sys.exit(1)

    # Step 5: Verify test data
    if not verify_test_data():
        print("\nTest data verification failed!")
        sys.exit(1)

    print("\n" + "="*80)
    print("SUCCESS! All tests passed!")
    print("="*80)
    print(f"\nDatabase file: {TEST_DB_PATH}")
    print("You can now run the backend with this test database.")
    print("\n")
