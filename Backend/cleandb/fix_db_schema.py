"""
Rebuild the calls table with a strict schema to fix NULL id or AUTOINCREMENT issues.
Run with server STOPPED. Keeps calls_backup for safety.
"""
import sqlite3
import os
from pathlib import Path

# Use same DB path as main server (Backend/teams_dialer.db or TEAMS_DIALER_DB env)
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
_default_db = (BACKEND_DIR / "teams_dialer.db").resolve()
DB_PATH = Path(os.environ["TEAMS_DIALER_DB"]).resolve() if os.environ.get("TEAMS_DIALER_DB") else _default_db


def fix_schema():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    print(f"Opening database: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys=OFF")  # Disable constraints during migration
    cursor = conn.cursor()

    try:
        print("1. Checking current schema...")
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='calls'")
        schema = cursor.fetchone()
        if schema:
            print(f"   Current schema: {schema[0]}")

        print("\n2. Renaming 'calls' to 'calls_backup'...")
        cursor.execute("ALTER TABLE calls RENAME TO calls_backup")

        print("3. Creating new 'calls' table with correct AUTOINCREMENT...")
        cursor.execute("""
            CREATE TABLE calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                phone TEXT,
                contact_name TEXT,
                disposition TEXT,
                duration INTEGER,
                notes TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                campaign_mode TEXT,
                contact_data TEXT
            )
        """)

        print("4. Copying data from backup to new table...")
        cursor.execute("SELECT * FROM calls_backup")
        columns = [description[0] for description in cursor.description]

        cols_str = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(columns))

        # Rows with valid id: copy as-is (preserve history)
        cursor.execute(
            f"INSERT INTO calls ({cols_str}) SELECT {cols_str} FROM calls_backup WHERE id IS NOT NULL"
        )
        copied_with_id = cursor.rowcount

        # Rows with NULL id: insert without id so new table assigns one
        cols_no_id = [c for c in columns if c != "id"]
        if cols_no_id:
            cols_no_id_str = ", ".join(cols_no_id)
            cursor.execute(
                f"INSERT INTO calls ({cols_no_id_str}) SELECT {cols_no_id_str} FROM calls_backup WHERE id IS NULL"
            )
            copied_null_id = cursor.rowcount
        else:
            copied_null_id = 0

        print(f"   Copied {copied_with_id} rows with id, {copied_null_id} rows with NULL id (assigned new ids).")

        print("5. Recreating indexes...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_time ON calls(agent_id, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_disposition ON calls(disposition)")

        conn.execute("PRAGMA journal_mode=WAL;")
        conn.commit()
        print("\nSUCCESS! Table rebuilt. 'calls_backup' table kept for safety.")
        print("You can verify and then drop 'calls_backup' later with: DROP TABLE calls_backup;")

    except Exception as e:
        print(f"\nCRITICAL ERROR: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    fix_schema()
