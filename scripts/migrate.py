#!/usr/bin/env python3
"""
Database Migration Runner
Runs SQL migrations in order from migrations/ directory
"""

import asyncio
import asyncpg
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment
load_dotenv()

async def run_migrations():
    """Run all SQL migrations in order"""

    # Get database URL
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("❌ ERROR: DATABASE_URL not set in environment")
        sys.exit(1)

    # Connect to database
    print("📡 Connecting to database...")
    try:
        conn = await asyncpg.connect(database_url)
        print("✅ Connected to database")
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        sys.exit(1)

    # Get migrations directory
    migrations_dir = Path(__file__).parent.parent / "migrations"
    if not migrations_dir.exists():
        print(f"❌ Migrations directory not found: {migrations_dir}")
        await conn.close()
        sys.exit(1)

    # Get all .sql files sorted
    sql_files = sorted(migrations_dir.glob("*.sql"))
    if not sql_files:
        print("⚠️  No migration files found")
        await conn.close()
        return

    print(f"\n🔍 Found {len(sql_files)} migration file(s)\n")

    # Run each migration
    success_count = 0
    for sql_file in sql_files:
        print(f"📄 Running {sql_file.name}...")

        try:
            # Read SQL file
            sql = sql_file.read_text()

            # Execute migration
            await conn.execute(sql)

            print(f"✅ {sql_file.name} completed successfully")
            success_count += 1

        except asyncpg.DuplicateTableError:
            print(f"⏭️  {sql_file.name} already applied (tables exist)")
            success_count += 1

        except asyncpg.DuplicateFunctionError:
            print(f"⏭️  {sql_file.name} already applied (functions exist)")
            success_count += 1

        except Exception as e:
            print(f"❌ {sql_file.name} failed: {e}")
            print(f"\n⚠️  Stopping migrations due to error")
            break

    # Close connection
    await conn.close()

    # Summary
    print(f"\n{'='*60}")
    print(f"📊 Migration Summary:")
    print(f"   Total files: {len(sql_files)}")
    print(f"   Successful:  {success_count}")
    print(f"   Failed:      {len(sql_files) - success_count}")
    print(f"{'='*60}\n")

    if success_count == len(sql_files):
        print("🎉 All migrations completed successfully!")
        return 0
    else:
        print("❌ Some migrations failed")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(run_migrations())
    sys.exit(exit_code)
