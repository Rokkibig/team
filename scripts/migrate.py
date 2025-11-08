#!/usr/bin/env python3
"""
Database Migration Runner with schema_migrations tracking
Idempotent migrations with checksum validation
"""

import asyncio
import asyncpg
import os
import sys
import hashlib
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

# Load environment
load_dotenv()

def calculate_checksum(content: str) -> str:
    """Calculate SHA256 checksum of migration content"""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()

def extract_version(filename: str) -> str:
    """Extract version from filename (NNN_description.sql -> NNN)"""
    return filename.split('_')[0]

async def ensure_schema_migrations_table(conn: asyncpg.Connection):
    """Create schema_migrations table if it doesn't exist"""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            applied_at TIMESTAMPTZ DEFAULT NOW(),
            duration_ms INTEGER
        )
    """)
    print("✅ schema_migrations table ready")

async def run_migrations():
    """Run all SQL migrations in order with tracking"""

    # Get database URL
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("❌ ERROR: DATABASE_URL not set in environment")
        sys.exit(1)

    # Connect to database
    print("📡 Connecting to database...")
    try:
        conn = await asyncpg.connect(
            database_url,
            server_settings={'client_min_messages': 'warning'}
        )
        print("✅ Connected to database")
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        sys.exit(1)

    # Ensure schema_migrations table exists
    await ensure_schema_migrations_table(conn)

    # Get migrations directory
    migrations_dir = Path(__file__).parent.parent / "migrations"
    if not migrations_dir.exists():
        print(f"❌ Migrations directory not found: {migrations_dir}")
        await conn.close()
        sys.exit(1)

    # Get all .sql files sorted by version number
    sql_files = sorted(migrations_dir.glob("*.sql"))
    if not sql_files:
        print("⚠️  No migration files found")
        await conn.close()
        return 0

    print(f"\n🔍 Found {len(sql_files)} migration file(s)\n")

    # Get already applied migrations
    applied_migrations = await conn.fetch(
        "SELECT version, checksum FROM schema_migrations"
    )
    applied_dict = {row['version']: row['checksum'] for row in applied_migrations}

    # Run each migration
    success_count = 0
    skipped_count = 0
    failed_count = 0

    for sql_file in sql_files:
        version = extract_version(sql_file.name)
        print(f"📄 Migration {sql_file.name} (version: {version})...")

        # Read SQL content
        sql = sql_file.read_text()
        checksum = calculate_checksum(sql)

        # Check if already applied
        if version in applied_dict:
            stored_checksum = applied_dict[version]
            if stored_checksum == checksum:
                print(f"⏭️  Already applied (checksum matches)")
                skipped_count += 1
                success_count += 1
                continue
            else:
                print(f"❌ Checksum mismatch!")
                print(f"   Stored:  {stored_checksum}")
                print(f"   Current: {checksum}")
                print(f"   ⚠️  Migration file has been modified after application!")
                failed_count += 1
                break

        # Apply migration in transaction
        try:
            start_time = datetime.now()

            async with conn.transaction():
                # Execute migration SQL
                await conn.execute(sql)

                # Record in schema_migrations
                duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
                await conn.execute(
                    """
                    INSERT INTO schema_migrations (version, checksum, duration_ms)
                    VALUES ($1, $2, $3)
                    """,
                    version,
                    checksum,
                    duration_ms
                )

            print(f"✅ Applied successfully ({duration_ms}ms)")
            success_count += 1

        except Exception as e:
            print(f"❌ Failed: {e}")
            failed_count += 1
            print(f"\n⚠️  Stopping migrations due to error")
            break

    # Close connection
    await conn.close()

    # Summary
    print(f"\n{'='*60}")
    print(f"📊 Migration Summary:")
    print(f"   Total files:  {len(sql_files)}")
    print(f"   Applied:      {success_count - skipped_count}")
    print(f"   Skipped:      {skipped_count}")
    print(f"   Failed:       {failed_count}")
    print(f"{'='*60}\n")

    if failed_count == 0:
        print("🎉 All migrations completed successfully!")
        return 0
    else:
        print("❌ Some migrations failed")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(run_migrations())
    sys.exit(exit_code)
