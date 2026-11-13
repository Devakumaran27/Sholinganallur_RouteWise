import asyncio
import selectors
import sys
from app.db.connections import get_connection


async def test_database():
    print("==================================================")
    print("Testing Supabase PostgreSQL & pgvector Connection")
    print("==================================================")

    try:
        conn = await get_connection()
        print(" [SUCCESS] PostgreSQL connected successfully!")

        async with conn:
            # Check current database and user
            result = await (
                await conn.execute("SELECT current_database(), current_user, version();")
            ).fetchone()

            print(f" Database  : {result[0]}")
            print(f" User      : {result[1]}")
            print(f" Server    : {result[2].split(',')[0]}")

            # Check pgvector extension status
            ext_result = await (
                await conn.execute(
                    """
                    SELECT e.extname, e.extversion, n.nspname AS schema_name
                    FROM pg_extension e
                    JOIN pg_namespace n ON n.oid = e.extnamespace
                    WHERE e.extname = 'vector';
                    """
                )
            ).fetchone()

            if ext_result:
                print(f" [SUCCESS] pgvector extension: active (version {ext_result[1]}, schema: {ext_result[2]})")
            else:
                print(" [WARNING] pgvector extension not yet enabled.")

            # Check if application tables exist
            tables_result = await (
                await conn.execute(
                    """
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                      AND table_name IN ('documents', 'chunks');
                    """
                )
            ).fetchall()

            existing_tables = [t[0] for t in tables_result]
            print(f" Application Tables: {existing_tables if existing_tables else 'None found (manual/explicit migration only; auto-creation disabled)'}")

    except ValueError as ve:
        print(f" [CONFIG NOTICE] {ve}")
    except Exception as e:
        print(f" [ERROR] Connection failed: {e}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(
            test_database(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(test_database())

