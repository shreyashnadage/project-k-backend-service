"""
Database migration script to create registration tables.
Run this to set up the registration system.
"""

import logging
import sys
from sqlalchemy import text
from cloudplatform.db.database import engine, SessionLocal
from cloudplatform.db.models import (
    Base, Client, InstallationKey, DeviceRegistration,
    SyncRecord, RegistrationAuditLog
)

# Set UTF-8 encoding for Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

logger = logging.getLogger(__name__)


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

        # Check if tables exist by trying to query them
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
        session = get_session()

        print("\n" + "="*80)
        print("DATABASE SCHEMA - Registration Tables")
        print("="*80 + "\n")

        tables_info = {
            "clients": "MSME companies using Tally Sync",
            "installation_keys": "One-time keys for agent installation",
            "device_registrations": "Registered devices (PCs)",
            "sync_records": "Data sync operations",
            "registration_audit_log": "Audit trail",
        }

        for table_name, description in tables_info.items():
            print(f"📊 {table_name.upper()}")
            print(f"   Description: {description}")

            try:
                result = session.execute(text(f"""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_name = '{table_name}'
                    ORDER BY ordinal_position
                """))

                print("   Columns:")
                for col_name, col_type, is_nullable in result:
                    nullable_str = "nullable" if is_nullable == "YES" else "NOT NULL"
                    print(f"      - {col_name}: {col_type} ({nullable_str})")
            except:
                # If information_schema doesn't work, skip
                pass

            print()

        session.close()

    except Exception as e:
        logger.error(f"✗ Error showing schema: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("\n🔧 Database Migration Tool\n")

    # Create tables
    if create_all_tables():
        print("\n✓ Migration complete!\n")

        # Verify tables were created
        if verify_tables():
            print("\n✓ All tables verified!\n")
        else:
            print("\n⚠️  Some tables could not be verified\n")

        # Show schema
        show_schema()
    else:
        print("\n✗ Migration failed!\n")
