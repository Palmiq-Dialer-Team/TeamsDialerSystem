"""
Delete all call records (dispositions) for a given agent in a given year.
Example: delete Christian's 2026 dispositions.
Run from Backend folder. Use --dry-run first, then --execute to apply.
"""

import sqlite3
import shutil
import argparse
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
DB_PATH = BACKEND_DIR / "teams_dialer.db"
BACKUP_PATH = BACKEND_DIR / "teams_dialer_backup.db"


def delete_agent_year(agent_id: str, year: int, dry_run: bool = True):
    """Delete all calls for agent_id where timestamp is in the given year."""
    db_path_str = str(DB_PATH)
    if not os.path.exists(db_path_str):
        print(f"Database not found at {db_path_str}")
        return

    print(f"Creating backup at {BACKUP_PATH}...")
    shutil.copy2(db_path_str, str(BACKUP_PATH))

    conn = sqlite3.connect(db_path_str)
    cursor = conn.cursor()

    # timestamp format is ISO like 2026-01-15T12:30:00
    pattern = f"{year}-%"
    cursor.execute(
        "SELECT COUNT(*) FROM calls WHERE agent_id = ? AND timestamp LIKE ?",
        (agent_id, pattern),
    )
    count = cursor.fetchone()[0] or 0
    print(f"Agent '{agent_id}' year {year}: {count} row(s) to delete.")

    if dry_run:
        print("Dry-run only. No changes applied. Run with --execute to delete.")
        conn.close()
        return

    cursor.execute(
        "DELETE FROM calls WHERE agent_id = ? AND timestamp LIKE ?",
        (agent_id, pattern),
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"Deleted {deleted} row(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Delete all dispositions for an agent in a given year."
    )
    parser.add_argument("--agent", default="Christian", help="Agent id (default: Christian)")
    parser.add_argument("--year", type=int, default=2026, help="Year (default: 2026)")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete. If not provided, dry-run only.",
    )
    args = parser.parse_args()
    delete_agent_year(args.agent, args.year, dry_run=not args.execute)
