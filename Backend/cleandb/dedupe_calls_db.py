"""
Deduplicate calls table: one row per contact (by contact name), keeping the row with the most info.

"Same contact" = same agent_id + same contact name (normalized First + Last from contact_data, or
contact_name; if no name, fallback to phone so nameless rows don't all merge).

For each duplicate group we keep one row. Order of preference:
  1. Most contact info (phone, company, email, title, state, country, industry, notes) — the row
     with more filled fields is kept; the one with less info is deleted.
  2. Then disposition importance (higher = keep): No Response < ... < Booked Meeting.
  3. Then latest timestamp.

Usage (from Backend folder):
  python cleandb\\dedupe_calls_db.py         # dry-run
  python cleandb\\dedupe_calls_db.py --execute
"""

import sqlite3
import shutil
import argparse
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / "teams_dialer.db"
BACKUP_PATH = BACKEND_DIR / "teams_dialer_backup.db"

# Disposition importance: higher number = more important (tie-breaker after info score).
DISPOSITION_PRIORITY = {
    "No Response": 1,
    "Wrong Number": 2,
    "Invalid Contact": 3,
    "Call Back Later": 4,
    "Voicemail (Verified)": 5,
    "Sent Info": 6,
    "Not Interested": 7,
    "Booked Meeting": 8,
}


def _contact_name_key_expr():
    """SQL: normalized contact name for grouping duplicates (same name = same contact)."""
    name_from_data = (
        "TRIM(COALESCE(json_extract(contact_data, '$.First Name'), '')) || ' ' || "
        "TRIM(COALESCE(json_extract(contact_data, '$.Last Name'), ''))"
    )
    return f"""CASE
        WHEN contact_data IS NOT NULL AND TRIM(COALESCE(contact_data, '')) != '' AND TRIM({name_from_data}) != ''
        THEN LOWER(TRIM({name_from_data}))
        WHEN TRIM(COALESCE(contact_name, '')) != ''
        THEN LOWER(TRIM(contact_name))
        ELSE COALESCE(phone, '') || '_noname'
    END"""


def _disposition_priority_expr():
    """SQL expression: integer priority for disposition (0 = unknown)."""
    cases = " ".join(
        f"WHEN TRIM(COALESCE(disposition, '')) = '{d}' THEN {p}"
        for d, p in DISPOSITION_PRIORITY.items()
    )
    return f"CASE {cases} ELSE 0 END"


def _info_score_expr():
    """SQL: score how complete contact_data is (more filled fields = higher). Prefer keeping rows with more info."""
    # DB phone (valid)
    phone_score = """(CASE WHEN phone IS NOT NULL AND TRIM(COALESCE(phone, '')) != ''
        AND phone NOT IN ('Manual Entry', 'Researching') THEN 1 ELSE 0 END)"""
    # From contact_data JSON when present (COALESCE json_array_length so missing path => 0)
    cd = "contact_data"
    company = f"(CASE WHEN TRIM(COALESCE(json_extract({cd}, '$.Company Name'), json_extract({cd}, '$.Company'), '')) != '' THEN 1 ELSE 0 END)"
    email = f"(CASE WHEN COALESCE(json_array_length({cd}, '$.Emails'), 0) > 0 THEN 1 ELSE 0 END)"
    title = f"(CASE WHEN TRIM(COALESCE(json_extract({cd}, '$.Title'), json_extract({cd}, '$.Job Title'), '')) != '' THEN 1 ELSE 0 END)"
    phones_json = f"(CASE WHEN COALESCE(json_array_length({cd}, '$.Phones'), 0) > 0 THEN 1 ELSE 0 END)"
    state = f"(CASE WHEN TRIM(COALESCE(json_extract({cd}, '$.State'), '')) != '' THEN 1 ELSE 0 END)"
    country = f"(CASE WHEN TRIM(COALESCE(json_extract({cd}, '$.Country'), '')) != '' THEN 1 ELSE 0 END)"
    industry = f"(CASE WHEN TRIM(COALESCE(json_extract({cd}, '$.Industry'), '')) != '' THEN 1 ELSE 0 END)"
    notes = f"(CASE WHEN TRIM(COALESCE(json_extract({cd}, '$.Notes'), '')) != '' THEN 1 ELSE 0 END)"
    data_score = f"""(CASE WHEN {cd} IS NOT NULL AND TRIM(COALESCE({cd}, '')) != '' AND {cd} != '{{}}'
        THEN ({company} + {email} + {title} + {phones_json} + {state} + {country} + {industry} + {notes})
        ELSE 0 END)"""
    return f"({phone_score} + {data_score})"


def dedupe(dry_run: bool = True):
    db_path_str = str(DB_PATH)
    if not os.path.exists(db_path_str):
        print(f"Database not found: {db_path_str}")
        return
    name_key = _contact_name_key_expr()
    priority_expr = _disposition_priority_expr()
    info_score = _info_score_expr()
    conn = sqlite3.connect(db_path_str)
    cursor = conn.cursor()

    # One row per (agent_id, contact_name): keep the one with most info, then highest disposition, then latest
    cursor.execute(f"""
        WITH base AS (
            SELECT id, agent_id, ({name_key}) AS name_ckey, ({info_score}) AS info_score, ({priority_expr}) AS prio, timestamp
            FROM calls
        ),
        ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY agent_id, name_ckey
                       ORDER BY info_score DESC, prio DESC, timestamp DESC
                   ) AS rn
            FROM base
        )
        SELECT id FROM ranked WHERE rn > 1
    """)
    ids_to_delete = [row[0] for row in cursor.fetchall()]
    to_remove = len(ids_to_delete)

    if to_remove == 0:
        print("No duplicate rows (same contact name) found; each contact has at most one row.")
        conn.close()
        return

    if dry_run:
        print(f"Would delete {to_remove} duplicate row(s) (keeping row with most contact info, then highest disposition, per contact name). Run with --execute to apply.")
        conn.close()
        return

    print(f"Creating backup at {BACKUP_PATH}...")
    shutil.copy2(db_path_str, str(BACKUP_PATH))
    if ids_to_delete:
        placeholders = ",".join("?" for _ in ids_to_delete)
        cursor.execute(f"DELETE FROM calls WHERE id IN ({placeholders})", ids_to_delete)
        conn.commit()
    conn.close()
    print(f"Deleted {to_remove} duplicate row(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Dedupe calls by contact name; keep row with most info, then highest disposition."
    )
    parser.add_argument("--execute", action="store_true", help="Apply deletes. Default is dry-run.")
    args = parser.parse_args()
    dedupe(dry_run=not args.execute)
