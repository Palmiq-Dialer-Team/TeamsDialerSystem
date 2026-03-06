"""
Delete call rows that do not have proper contact_data.

"Proper" = contact_data is present and contains at least one of the export columns:
Title, Company Name, Email 1, Contact Phone 1–5, State, Country, Industry, Company Revenue, Notes.
Rows with contact_data missing, empty, or with none of those fields filled are deleted so they
no longer appear in dashboard exports.

Optional: --older-than-days N  only deletes such rows if timestamp is older than N days
(default 0 = delete all rows without proper contact_data, no age filter).

Usage (from Backend folder):
  python cleandb\\delete_calls_without_contact_data.py              # dry-run, all such rows
  python cleandb\\delete_calls_without_contact_data.py --execute    # apply
  python cleandb\\delete_calls_without_contact_data.py --older-than-days 30 --execute  # only older than 30 days
"""

import sqlite3
import shutil
import argparse
import os
from pathlib import Path
from datetime import datetime, timedelta

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / "teams_dialer.db"
BACKUP_PATH = BACKEND_DIR / "teams_dialer_backup.db"


def _where_no_proper_contact_data():
    """
    SQL fragment: true when row has no proper contact_data (missing, empty, or no export columns filled).
    Export uses only the contact_data JSON for Title, Company Name, Email 1, Contact Phone 1-5, etc.,
    so we check only keys inside that JSON (same format: "First Name", "Last Name", "Company Name", ...).
    """
    empty_cd = "(contact_data IS NULL OR TRIM(COALESCE(contact_data, '')) = '' OR contact_data = '{}')"
    # Check only contact_data JSON content (export reads these keys; DB phone column is not used for Contact Phone 1-5)
    has_company = "TRIM(COALESCE(json_extract(contact_data, '$.Company Name'), json_extract(contact_data, '$.Company'), '')) != ''"
    has_email = "COALESCE(json_array_length(contact_data, '$.Emails'), 0) > 0"
    has_title = "TRIM(COALESCE(json_extract(contact_data, '$.Title'), json_extract(contact_data, '$.Job Title'), '')) != ''"
    has_phones = "COALESCE(json_array_length(contact_data, '$.Phones'), 0) > 0"
    has_state = "TRIM(COALESCE(json_extract(contact_data, '$.State'), '')) != ''"
    has_country = "TRIM(COALESCE(json_extract(contact_data, '$.Country'), '')) != ''"
    has_industry = "TRIM(COALESCE(json_extract(contact_data, '$.Industry'), '')) != ''"
    has_revenue = "TRIM(COALESCE(json_extract(contact_data, '$.Company Revenue'), json_extract(contact_data, '$.Revenue'), '')) != ''"
    has_notes = "TRIM(COALESCE(json_extract(contact_data, '$.Notes'), '')) != ''"
    cd_present = "(contact_data IS NOT NULL AND TRIM(COALESCE(contact_data, '')) != '' AND contact_data != '{}')"
    no_export_fields = f"""(
        (NOT ({has_company}))
        AND (NOT ({has_email}))
        AND (NOT ({has_title}))
        AND (NOT ({has_phones}))
        AND (NOT ({has_state}))
        AND (NOT ({has_country}))
        AND (NOT ({has_industry}))
        AND (NOT ({has_revenue}))
        AND (NOT ({has_notes}))
    )"""
    # Delete when: no contact_data at all, OR contact_data present but none of the export columns filled in the JSON
    return f"({empty_cd} OR ({cd_present} AND {no_export_fields}))"


def delete_calls_without_contact_data(dry_run: bool = True, older_than_days: int = 0):
    """Delete rows where contact_data is missing, empty, or has none of the export columns filled."""
    db_path_str = str(DB_PATH)
    if not os.path.exists(db_path_str):
        print(f"Database not found: {db_path_str}")
        return

    where = _where_no_proper_contact_data()
    conn = sqlite3.connect(db_path_str)
    cursor = conn.cursor()

    if older_than_days > 0:
        cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()
        cursor.execute(
            f"SELECT COUNT(*) FROM calls WHERE {where} AND timestamp < ?",
            (cutoff,),
        )
    else:
        cursor.execute(f"SELECT COUNT(*) FROM calls WHERE {where}")
    count = cursor.fetchone()[0] or 0

    if count == 0:
        print("No rows without proper contact_data found.")
        conn.close()
        return

    age_note = f" (older than {older_than_days} days)" if older_than_days > 0 else " (all such rows)"
    print(f"Rows without proper contact_data to delete{age_note}: {count}")
    print("(Missing/empty contact_data or none of: Title, Company Name, Email 1, Contact Phones, State, Country, Industry, Company Revenue, Notes)")

    if dry_run:
        print("Dry-run only. Run with --execute to apply deletions.")
        conn.close()
        return

    print(f"Creating backup at {BACKUP_PATH}...")
    shutil.copy2(db_path_str, str(BACKUP_PATH))

    if older_than_days > 0:
        cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()
        cursor.execute(
            f"DELETE FROM calls WHERE {where} AND timestamp < ?",
            (cutoff,),
        )
    else:
        cursor.execute(f"DELETE FROM calls WHERE {where}")
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"Deleted {deleted} row(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Delete call rows without proper contact_data (missing or no export columns filled)."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply deletions. Default is dry-run.",
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=0,
        metavar="N",
        help="Only delete rows older than N days (default 0 = delete all such rows).",
    )
    args = parser.parse_args()
    delete_calls_without_contact_data(
        dry_run=not args.execute,
        older_than_days=args.older_than_days,
    )
