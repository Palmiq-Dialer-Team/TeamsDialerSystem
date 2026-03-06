"""
Repair server call dispositions from local CSV/XLSX disposition files.

- Only updates DB rows whose disposition is "No Response" or "No Disposition".
  Rows that already have a final disposition (e.g. Booked Meeting, Not Interested)
  are left unchanged.

- Matching order: (1) name+phone, (2) phone-only (same number, different name),
  (3) name-only (same person, different number in file). Latest file row wins.

- When a match is found and DB contact_data is empty, fills First Name / Last Name
  from the file so the row has minimal contact info.

Reads files with headers: First Name, Last Name, ..., Contact Phone 1-5,
Contact Mobile Phone 1-3, Notes, _DISPO_STAMP. Uses "Date" or "Timestamp"
column if present for ordering.

Usage (from Backend folder):
  python cleandb\\repair_dispositions_from_files.py [--files DIR] [--execute]
  Dry-run by default; use --execute to apply updates (after backup).
"""

import json
import re
import sqlite3
import shutil
import argparse
import os
from pathlib import Path
from datetime import datetime

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / "teams_dialer.db"
BACKUP_PATH = BACKEND_DIR / "teams_dialer_backup.db"
DEFAULT_FILES_DIR = Path(__file__).resolve().parent / "disposition_files"


def normalize_phone(phone: str) -> str:
    if not phone or not isinstance(phone, str):
        return ""
    return re.sub(r"[\s\-\.\(\)]", "", str(phone).strip())


def normalize_name(name: str) -> str:
    if not name or not isinstance(name, str):
        return ""
    return " ".join(str(name).lower().split())


def _str_val(val) -> str:
    if val is None:
        return ""
    if isinstance(val, float):
        if val != val:  # nan
            return ""
        return str(int(val)) if val == int(val) else str(val)
    return str(val).strip()


def _disposition_columns():
    """Accepted header names for disposition (first match per row)."""
    return ("_DISPO_STAMP", "Disposition Stamp", "disposition stamp", "_dispo_stamp")


def _date_columns():
    """Optional column names for ordering by date (first found)."""
    return ("Date", "Timestamp", "Disposition Date", "date", "timestamp")


def _extract_disposition(val) -> str:
    """
    Normalize a disposition stamp value.
    - If it's a JSON/dict string with a 'disposition' field, return that.
    - Otherwise, if it contains a pipe ('|'), return the part after the last pipe.
    - Otherwise, return the plain string.
    """
    s = _str_val(val)
    if not s:
        return ""
    # Try JSON / dict style: {"disposition": "No Response", "timestamp": "...", "notes": "..."}
    try:
        obj = json.loads(s)
        if isinstance(obj, dict) and "disposition" in obj:
            return _str_val(obj.get("disposition"))
    except Exception:
        pass
    # Try pipe-separated "timestamp | Disposition"
    if "|" in s:
        tail = s.split("|")[-1].strip()
        if tail:
            return tail
    return s


def load_rows_from_path(path: Path, file_mtime: float):
    """Load rows from one CSV or XLSX; yield (row_dict, row_index, sort_key)."""
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("pandas required. pip install pandas openpyxl")
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path, engine="openpyxl")
    if df is None or df.empty:
        return
    df.columns = [str(c).strip() for c in df.columns]
    # Find disposition column
    disp_col = None
    for h in _disposition_columns():
        for c in df.columns:
            if c.strip().lower() == h.strip().lower():
                disp_col = c
                break
        if disp_col is not None:
            break
    if disp_col is None:
        print(f"  Skip {path.name}: no _DISPO_STAMP / Disposition Stamp column")
        return
    # Optional date column for ordering
    date_col = None
    for h in _date_columns():
        for c in df.columns:
            if c.strip().lower() == h.strip().lower():
                date_col = c
                break
        if date_col is not None:
            break

    def first_last(row):
        first, last = "", ""
        for c in df.columns:
            cc = str(c).lower()
            if cc in ("first name", "firstname"):
                first = _str_val(row.get(c, ""))
            elif cc in ("last name", "lastname"):
                last = _str_val(row.get(c, ""))
        return first, last

    def phones_from_row(row):
        out = []
        for c in df.columns:
            cc = str(c).lower()
            if "phone" in cc and "company" not in cc and pd.notna(row.get(c)) and _str_val(row.get(c)):
                out.append(_str_val(row.get(c)))
        return out

    def notes_from_row(row):
        for c in df.columns:
            if str(c).strip().lower() == "notes":
                return _str_val(row.get(c, ""))
        return ""

    for idx, row in df.iterrows():
        disp = _extract_disposition(row.get(disp_col, ""))
        if not disp:
            continue
        first, last = first_last(row)
        if not first and not last:
            continue
        name_norm = normalize_name(f"{first} {last}")
        phones = phones_from_row(row)
        if not phones:
            continue
        notes = notes_from_row(row)
        # Sort key (tuple for comparable "latest wins"): (primary_ts, row_index)
        try:
            if date_col and pd.notna(row.get(date_col)):
                raw = row[date_col]
                if hasattr(raw, "timestamp"):
                    ts = raw.timestamp()
                else:
                    ts = float(pd.to_datetime(raw).timestamp())
                sort_key = (ts, idx)
            else:
                sort_key = (file_mtime, idx)
        except Exception:
            sort_key = (file_mtime, idx)

        for ph in phones:
            ph_norm = normalize_phone(ph)
            if not ph_norm or len(ph_norm) < 6:
                continue
            yield (name_norm, ph_norm, disp, notes, sort_key, first, last)


def load_all_file_entries(files_dir: Path):
    """
    Build three lookups (latest wins): (name,phone)->(disp,notes,first,last),
    phone->(disp,notes,first,last), name->(disp,notes,first,last).
    """
    if not files_dir.is_dir():
        print(f"Directory not found: {files_dir}")
        return {}, {}, {}
    candidates = []
    for path in sorted(files_dir.rglob("*")):
        if path.suffix.lower() not in (".xlsx", ".csv"):
            continue
        if path.name.startswith("~"):
            continue
        mtime = path.stat().st_mtime
        try:
            for name_norm, phone_norm, disp, notes, sort_key, first, last in load_rows_from_path(path, mtime):
                candidates.append(( (name_norm, phone_norm), (disp, notes, sort_key, first, last) ))
        except Exception as e:
            print(f"  Skip {path.name}: {e}")

    # Latest wins per (name_norm, phone_norm)
    latest_key = {}
    for key, value in candidates:
        disp, notes, sort_key, first, last = value
        if key not in latest_key or sort_key > latest_key[key][2]:
            latest_key[key] = (disp, notes, sort_key, first, last)
    lookup = {k: (v[0], v[1], v[3], v[4]) for k, v in latest_key.items()}

    # Phone-only: phone_norm -> (disp, notes, first, last), latest wins
    by_phone = {}
    for (name_norm, phone_norm), (disp, notes, sort_key, first, last) in candidates:
        if phone_norm not in by_phone or sort_key > by_phone[phone_norm][2]:
            by_phone[phone_norm] = (disp, notes, sort_key, first, last)
    phone_lookup = {p: (v[0], v[1], v[3], v[4]) for p, v in by_phone.items()}

    # Name-only: name_norm -> (disp, notes, first, last), latest wins
    by_name = {}
    for (name_norm, phone_norm), (disp, notes, sort_key, first, last) in candidates:
        if name_norm not in by_name or sort_key > by_name[name_norm][2]:
            by_name[name_norm] = (disp, notes, sort_key, first, last)
    name_lookup = {n: (v[0], v[1], v[3], v[4]) for n, v in by_name.items()}

    return lookup, phone_lookup, name_lookup


# Only update DB rows that still have these placeholder dispositions.
REPAIR_TARGET_DISPOSITIONS = ("No Response", "No Disposition")


def repair(dry_run: bool = True, files_dir: Path = None):
    files_dir = files_dir or DEFAULT_FILES_DIR
    db_path_str = str(DB_PATH)
    if not os.path.exists(db_path_str):
        print(f"Database not found: {db_path_str}")
        return
    print(f"Loading disposition rows from {files_dir} (name+phone, phone-only, name-only lookups)...")
    lookup, phone_lookup, name_lookup = load_all_file_entries(files_dir)
    print(f"Lookup: {len(lookup)} (name,phone) keys, {len(phone_lookup)} phone keys, {len(name_lookup)} name keys.")
    if not lookup:
        print("Nothing to apply.")
        return

    if dry_run:
        print("Creating backup (dry-run still backs up for safety)...")
    else:
        print(f"Creating backup at {BACKUP_PATH}...")
    shutil.copy2(db_path_str, str(BACKUP_PATH))

    conn = sqlite3.connect(db_path_str)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, agent_id, phone, contact_name, contact_data, disposition, notes FROM calls"
    )
    rows = cursor.fetchall()
    updated = 0
    skipped_no_match = 0
    skipped_same = 0
    skipped_not_target = 0  # DB already has a final disposition
    updated_by_phone_only = 0
    updated_by_name_only = 0
    filled_contact_data = 0
    errors = 0

    for call_id, agent_id, phone, contact_name, contact_data_str, db_disp, db_notes in rows:
        db_disp = (db_disp or "").strip()
        db_notes = db_notes or ""

        # Only update rows that are still No Response or No Disposition
        if db_disp not in REPAIR_TARGET_DISPOSITIONS:
            skipped_not_target += 1
            continue

        phone_norm = normalize_phone(str(phone).strip()) if phone else ""
        name_norm = ""
        if contact_data_str and contact_data_str.strip():
            try:
                cd = json.loads(contact_data_str)
                first = (cd.get("First Name") or "").strip()
                last = (cd.get("Last Name") or "").strip()
                name_norm = normalize_name(f"{first} {last}")
            except (json.JSONDecodeError, TypeError):
                pass
        if not name_norm and contact_name:
            name_norm = normalize_name(str(contact_name))

        # Need at least name or phone to try any match
        if not name_norm and not phone_norm:
            skipped_no_match += 1
            continue

        file_disp = file_notes = file_first = file_last = None
        match_type = None

        if name_norm and phone_norm and len(phone_norm) >= 6:
            key = (name_norm, phone_norm)
            val = lookup.get(key)
            if val:
                file_disp, file_notes, file_first, file_last = val
                match_type = "name+phone"
        if not file_disp and phone_norm and len(phone_norm) >= 6:
            val = phone_lookup.get(phone_norm)
            if val:
                file_disp, file_notes, file_first, file_last = val
                match_type = "phone-only"
        if not file_disp and name_norm:
            val = name_lookup.get(name_norm)
            if val:
                file_disp, file_notes, file_first, file_last = val
                match_type = "name-only"

        if not file_disp:
            skipped_no_match += 1
            continue
        if db_disp == file_disp and (file_notes or "") == db_notes:
            skipped_same += 1
            continue

        # Optionally fill empty contact_data from file (First Name, Last Name)
        new_contact_data = None
        if (not contact_data_str or not contact_data_str.strip()) and (file_first or file_last):
            new_contact_data = json.dumps({
                "First Name": file_first or "",
                "Last Name": file_last or "",
            })

        if dry_run:
            updated += 1
            if match_type == "phone-only":
                updated_by_phone_only += 1
            elif match_type == "name-only":
                updated_by_name_only += 1
            print(f"  Would update call id={call_id} agent={agent_id} [{match_type}] {db_disp!r} -> {file_disp!r}")
            if new_contact_data:
                print(f"    + fill contact_data (First/Last from file)")
            continue

        try:
            if new_contact_data:
                cursor.execute(
                    "UPDATE calls SET disposition = ?, notes = ?, contact_data = ? WHERE id = ?",
                    (file_disp, file_notes or db_notes, new_contact_data, call_id),
                )
                filled_contact_data += 1
            else:
                cursor.execute(
                    "UPDATE calls SET disposition = ?, notes = ? WHERE id = ?",
                    (file_disp, file_notes or db_notes, call_id),
                )
            updated += 1
            if match_type == "phone-only":
                updated_by_phone_only += 1
            elif match_type == "name-only":
                updated_by_name_only += 1
        except Exception as e:
            errors += 1
            print(f"  Error updating call id={call_id}: {e}")

    if not dry_run:
        conn.commit()
    conn.close()
    print(f"{'Would update' if dry_run else 'Updated'}: {updated}")
    if updated_by_phone_only or updated_by_name_only:
        print(f"  (by name+phone: {updated - updated_by_phone_only - updated_by_name_only}, by phone-only: {updated_by_phone_only}, by name-only: {updated_by_name_only})")
    if filled_contact_data:
        print(f"  Rows with contact_data filled from file: {filled_contact_data}")
    print(f"Skipped (DB not No Response/No Disposition): {skipped_not_target}")
    print(f"No match: {skipped_no_match}")
    print(f"Same disposition/notes: {skipped_same}")
    if errors:
        print(f"Errors: {errors}")
    if dry_run:
        print("Dry-run only. Run with --execute to apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Repair call dispositions from local CSV/XLSX (_DISPO_STAMP). Latest date wins."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply updates. Default is dry-run.",
    )
    parser.add_argument(
        "--files",
        type=Path,
        default=None,
        help=f"Path to folder with disposition CSV/XLSX (default: {DEFAULT_FILES_DIR})",
    )
    args = parser.parse_args()
    repair(dry_run=not args.execute, files_dir=args.files)
