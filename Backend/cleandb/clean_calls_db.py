import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import argparse
import os

# Resolve DB paths relative to Backend/ (same convention as database.py / clean_db.py)
BACKEND_DIR = Path(__file__).parent.parent
DB_PATH = BACKEND_DIR / "teams_dialer.db"
BACKUP_PATH = BACKEND_DIR / "teams_dialer_backup.db"


def clean_calls_db(dry_run: bool = True):
    """Cleanup script for calls table: remove manual entries and old no-contact-info rows."""
    db_path_str = str(DB_PATH)
    if not os.path.exists(db_path_str):
        print(f"Database not found at {db_path_str}")
        return

    # Always create a fresh backup before any run (dry-run or execute)
    backup_path_str = str(BACKUP_PATH)
    print(f"Creating backup at {backup_path_str}...")
    shutil.copy2(db_path_str, backup_path_str)

    conn = sqlite3.connect(db_path_str)
    cursor = conn.cursor()

    # Count manual-entry rows
    cursor.execute(
        """
        SELECT COUNT(*) FROM calls
        WHERE phone = 'Manual Entry'
          AND contact_name = 'Manual Entry'
          AND disposition = 'Manual Entry'
        """
    )
    manual_count = cursor.fetchone()[0] or 0

    # Count no-contact-info rows older than 1 month
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    cursor.execute(
        """
        SELECT COUNT(*) FROM calls
        WHERE (contact_data IS NULL OR contact_data = '')
          AND timestamp < ?
        """,
        (cutoff,),
    )
    no_contact_old_count = cursor.fetchone()[0] or 0

    print(f"Manual 'Manual Entry' rows to delete: {manual_count}")
    print(f"No-contact-info rows older than 30 days to delete: {no_contact_old_count}")

    if dry_run:
        print("Dry-run only. No changes applied.")
        conn.close()
        return

    # Delete manual-entry rows
    cursor.execute(
        """
        DELETE FROM calls
        WHERE phone = 'Manual Entry'
          AND contact_name = 'Manual Entry'
          AND disposition = 'Manual Entry'
        """
    )

    # Delete no-contact-info rows older than 1 month
    cursor.execute(
        """
        DELETE FROM calls
        WHERE (contact_data IS NULL OR contact_data = '')
          AND timestamp < ?
        """,
        (cutoff,),
    )

    deleted_manual = cursor.rowcount  # last statement; manual deletions counted separately above
    conn.commit()
    conn.close()

    print("Cleanup complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean calls DB: remove manual entries and old no-contact-info rows.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually apply deletions. If not provided, runs in dry-run mode.",
    )
    args = parser.parse_args()

    clean_calls_db(dry_run=not args.execute)

