"""
Fill calls.contact_data for rows that are missing it, using data from XLSX/CSV disposition files.

Reads the same disposition_files folder (or --files DIR) as the repair script. For each DB row
where contact_data is empty or missing, tries to match by (name+phone), then phone-only, then
name-only. When a file row matches, builds contact_data JSON in the same format used when a
disposition is logged (so dashboard export gets Title, Company Name, Email 1, Contact Phone 1-5,
State, Country, Industry, Company Revenue, Notes).

Usage (from Backend folder):
  python cleandb\\fill_contact_data_from_files.py         # dry-run
  python cleandb\\fill_contact_data_from_files.py --execute
"""

import json
import re
import sqlite3
import shutil
import argparse
import os
from pathlib import Path

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


def _sanitize_contact(contact: dict) -> dict:
    """Ensure contact values are JSON-serializable (no numpy/pandas types)."""
    out = {}
    for k, v in contact.items():
        if v is None:
            out[k] = ""
        elif isinstance(v, list):
            out[k] = [_str_val(x) for x in v if _str_val(x)]
        elif isinstance(v, (str, int)):
            out[k] = v
        elif isinstance(v, float):
            if v != v:
                out[k] = ""
            else:
                out[k] = int(v) if v == int(v) else v
        else:
            out[k] = _str_val(v)
    return out


def _contact_from_row(row, columns):
    """
    Build a contact dict in DB/export format from one file row.
    Matches column names used in disposition/lead files: First Name, Last Name, Title,
    Company Name, Email 1 / Email, Contact Phone 1-5, Contact Mobile Phone 1-3, State,
    Country, Industry, Company Revenue, Notes.
    """
    first = last = ""
    title = ""
    company = ""
    emails = []
    phones = []
    state = ""
    country = ""
    industry = ""
    revenue = ""
    notes = ""
    for c in columns:
        cc = str(c).strip().lower()
        val = _str_val(row.get(c, ""))
        if not val:
            continue
        if cc in ("first name", "firstname"):
            first = val
        elif cc in ("last name", "lastname"):
            last = val
        elif "title" in cc or "job title" in cc:
            title = val
        elif "company" in cc and "phone" not in cc and "state" not in cc and "country" not in cc and "revenue" not in cc and "industry" not in cc:
            company = val
        elif "email" in cc:
            emails.append(val)
        elif ("phone" in cc or "mobile" in cc or "cell" in cc) and "company" not in cc:
            if val and val not in phones:
                phones.append(val)
        elif ("state" in cc and "country" not in cc) or cc == "company state" or cc == "company state abbr":
            state = val
        elif "country" in cc:
            country = val
        elif "industry" in cc or "company industry" in cc or "company description" in cc:
            industry = val
        elif "revenue" in cc or "annual revenue" in cc or "company revenue" in cc:
            revenue = val
        elif cc == "notes":
            notes = val
    # Build dict in same shape as DB/export expect (see database._row_to_export_columns)
    contact = {
        "First Name": first or "",
        "Last Name": last or "",
        "Title": title,
        "Company": company,
        "Company Name": company,
        "Emails": emails,
        "Phones": phones,
        "State": state,
        "Country": country,
        "Industry": industry,
        "Revenue": revenue,
        "Company Revenue": revenue,
        "Notes": notes,
    }
    return contact


def _load_contact_rows_from_path(path: Path, file_mtime: float):
    """
    Load rows from one CSV/XLSX; yield (name_norm, phone_norm, sort_key, contact_dict) per phone.
    Contact dict is in DB format. Requires First/Last name and at least one phone.
    """
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

    def first_last(r):
        f, l = "", ""
        for c in df.columns:
            cc = str(c).lower()
            if cc in ("first name", "firstname"):
                f = _str_val(r.get(c, ""))
            elif cc in ("last name", "lastname"):
                l = _str_val(r.get(c, ""))
        return f, l

    def phones_from_row(r):
        out = []
        for c in df.columns:
            cc = str(c).lower()
            if "phone" in cc and "company" not in cc and pd.notna(r.get(c)) and _str_val(r.get(c)):
                out.append(_str_val(r.get(c)))
        return out

    for idx, row in df.iterrows():
        first, last = first_last(row)
        if not first and not last:
            continue
        phones = phones_from_row(row)
        if not phones:
            continue
        contact = _contact_from_row(row, df.columns)
        # Ensure contact has first/last and phones from our extraction
        contact["First Name"] = first or contact.get("First Name", "")
        contact["Last Name"] = last or contact.get("Last Name", "")
        contact["Phones"] = phones
        name_norm = normalize_name(f"{first} {last}")
        try:
            # Use row index for ordering if no date column
            sort_key = (file_mtime, idx)
        except Exception:
            sort_key = (file_mtime, idx)
        for ph in phones:
            ph_norm = normalize_phone(ph)
            if ph_norm and len(ph_norm) >= 6:
                yield (name_norm, ph_norm, sort_key, contact)


def load_all_contacts_from_files(files_dir: Path):
    """
    Build three lookups: (name,phone)->contact, phone->contact, name->contact.
    Latest file row wins (by file mtime, then row index). Value is the full contact dict (DB format).
    """
    if not files_dir.is_dir():
        print(f"Directory not found: {files_dir}")
        return {}, {}, {}
    candidates = []  # ( (name_norm, phone_norm), (sort_key, contact) )
    for path in sorted(files_dir.rglob("*")):
        if path.suffix.lower() not in (".xlsx", ".csv"):
            continue
        if path.name.startswith("~"):
            continue
        mtime = path.stat().st_mtime
        try:
            for name_norm, phone_norm, sort_key, contact in _load_contact_rows_from_path(path, mtime):
                candidates.append(((name_norm, phone_norm), (sort_key, contact)))
        except Exception as e:
            print(f"  Skip {path.name}: {e}")

    # Latest wins per (name_norm, phone_norm)
    main_lookup = {}
    for key, (sort_key, contact) in candidates:
        if key not in main_lookup or sort_key > main_lookup[key][0]:
            main_lookup[key] = (sort_key, contact)
    main_lookup = {k: v[1] for k, v in main_lookup.items()}

    # Phone-only
    by_phone = {}
    for (name_norm, phone_norm), (sort_key, contact) in candidates:
        if phone_norm not in by_phone or sort_key > by_phone[phone_norm][0]:
            by_phone[phone_norm] = (sort_key, contact)
    phone_lookup = {p: v[1] for p, v in by_phone.items()}

    # Name-only
    by_name = {}
    for (name_norm, phone_norm), (sort_key, contact) in candidates:
        if name_norm not in by_name or sort_key > by_name[name_norm][0]:
            by_name[name_norm] = (sort_key, contact)
    name_lookup = {n: v[1] for n, v in by_name.items()}

    return main_lookup, phone_lookup, name_lookup


def fill_contact_data(dry_run: bool = True, files_dir: Path = None):
    files_dir = files_dir or DEFAULT_FILES_DIR
    db_path_str = str(DB_PATH)
    if not os.path.exists(db_path_str):
        print(f"Database not found: {db_path_str}")
        return
    print(f"Loading contact rows from {files_dir}...")
    main_lookup, phone_lookup, name_lookup = load_all_contacts_from_files(files_dir)
    print(f"Lookup: {len(main_lookup)} (name,phone), {len(phone_lookup)} phone, {len(name_lookup)} name.")
    if not main_lookup and not phone_lookup and not name_lookup:
        print("No file data to apply.")
        return

    if dry_run:
        print("Dry-run: no changes will be written.")
    else:
        print(f"Creating backup at {BACKUP_PATH}...")
        shutil.copy2(db_path_str, str(BACKUP_PATH))

    conn = sqlite3.connect(db_path_str)
    cursor = conn.cursor()
    # Only rows with missing or empty contact_data
    cursor.execute(
        """SELECT id, agent_id, phone, contact_name, contact_data FROM calls
           WHERE (contact_data IS NULL OR TRIM(COALESCE(contact_data, '')) = '' OR contact_data = '{}')"""
    )
    rows = cursor.fetchall()
    updated = 0
    skipped_no_match = 0
    match_name_phone = 0
    match_phone_only = 0
    match_name_only = 0
    errors = 0

    for call_id, agent_id, phone, contact_name, contact_data_str in rows:
        phone_norm = normalize_phone(str(phone).strip()) if phone else ""
        name_norm = ""
        if contact_data_str and contact_data_str.strip() and contact_data_str.strip() != "{}":
            try:
                cd = json.loads(contact_data_str)
                first = (cd.get("First Name") or "").strip()
                last = (cd.get("Last Name") or "").strip()
                name_norm = normalize_name(f"{first} {last}")
            except (json.JSONDecodeError, TypeError):
                pass
        if not name_norm and contact_name:
            name_norm = normalize_name(str(contact_name))

        if not name_norm and not phone_norm:
            skipped_no_match += 1
            continue

        contact = None
        match_type = None
        if name_norm and phone_norm and len(phone_norm) >= 6:
            contact = main_lookup.get((name_norm, phone_norm))
            if contact:
                match_type = "name+phone"
        if not contact and phone_norm and len(phone_norm) >= 6:
            contact = phone_lookup.get(phone_norm)
            if contact:
                match_type = "phone-only"
        if not contact and name_norm:
            contact = name_lookup.get(name_norm)
            if contact:
                match_type = "name-only"

        if not contact:
            skipped_no_match += 1
            continue

        contact = _sanitize_contact(contact)
        try:
            contact_json = json.dumps(contact)
        except (TypeError, ValueError) as e:
            errors += 1
            continue

        if match_type == "name+phone":
            match_name_phone += 1
        elif match_type == "phone-only":
            match_phone_only += 1
        else:
            match_name_only += 1

        if dry_run:
            updated += 1
            continue
        cursor.execute("UPDATE calls SET contact_data = ? WHERE id = ?", (contact_json, call_id))
        updated += 1

    conn.commit()
    conn.close()

    print(f"Rows with missing contact_data considered: {len(rows)}")
    print(f"  Matched (name+phone): {match_name_phone}, phone-only: {match_phone_only}, name-only: {match_name_only}")
    print(f"  Updated: {updated}  No match: {skipped_no_match}  Errors: {errors}")
    if dry_run and updated:
        print("Run with --execute to apply updates.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fill calls.contact_data from XLSX/CSV disposition files for rows missing it."
    )
    parser.add_argument("--files", type=str, default=None, help="Path to folder with disposition XLSX/CSV files")
    parser.add_argument("--execute", action="store_true", help="Apply updates. Default is dry-run.")
    args = parser.parse_args()
    files_dir = Path(args.files) if args.files else DEFAULT_FILES_DIR
    fill_contact_data(dry_run=not args.execute, files_dir=files_dir)
