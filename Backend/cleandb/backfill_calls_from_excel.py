"""
Backfill calls.contact_data from Excel/CSV lead files in cleandb/leads/.
Matches existing call rows by phone + contact name; updates contact_data with full contact from leads.
Run from Backend folder. Use --dry-run first, then --execute to apply.
"""

import json
import re
import sqlite3
import shutil
import argparse
import os
from pathlib import Path

# Make contact dicts JSON-serializable (pandas can leave numpy/float nan in values)
def _sanitize_for_json(obj):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(x) for x in obj]
    if isinstance(obj, (str, int)):
        return obj
    if isinstance(obj, float):
        if obj != obj:  # nan
            return None
        return obj
    try:
        import numpy as np
        if isinstance(obj, (np.integer, np.int64, np.int32)):
            return int(obj)
        if isinstance(obj, (np.floating, np.float64, np.float32)):
            if np.isnan(obj):
                return None
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    return str(obj) if obj is not None else None

BACKEND_DIR = Path(__file__).parent.parent
DB_PATH = BACKEND_DIR / "teams_dialer.db"
BACKUP_PATH = BACKEND_DIR / "teams_dialer_backup.db"
LEADS_DIR = Path(__file__).parent / "leads"


def normalize_phone(phone: str) -> str:
    """Strip and remove spaces, dashes, dots, parens for matching."""
    if not phone or not isinstance(phone, str):
        return ""
    s = re.sub(r"[\s\-\.\(\)]", "", str(phone).strip())
    return s


def normalize_name(name: str) -> str:
    """Lowercase, collapse spaces, for matching."""
    if not name or not isinstance(name, str):
        return ""
    return " ".join(str(name).lower().split())


def load_contacts_from_excel(path: Path, sheet_name=None):
    """Load contact rows from one Excel file. Returns list of contact dicts (app-style)."""
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("pandas is required. Run: pip install pandas openpyxl")
    try:
        if path.suffix.lower() == ".csv":
            df = pd.read_csv(path)
        else:
            df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
    except Exception as e:
        print(f"  Skip {path.name}: {e}")
        return []
    if df is None or df.empty:
        return []
    df.columns = [str(c).strip() for c in df.columns]
    contacts = []
    for idx, row in df.iterrows():
        try:
            first = ""
            last = ""
            for col in df.columns:
                c = str(col).lower()
                if c in ("first name", "firstname"):
                    first = str(row[col]).strip() if pd.notna(row[col]) else ""
                elif c in ("last name", "lastname"):
                    last = str(row[col]).strip() if pd.notna(row[col]) else ""
            if not first and not last:
                continue
            contact = {
                "First Name": first or "-",
                "Last Name": last or "-",
                "Title": "-",
                "Company": "-",
                "Company Name": "-",
                "Website": "-",
                "Industry": "-",
                "Revenue": "-",
                "State": "-",
                "Country": "-",
                "Emails": [],
                "Phones": [],
                "Phone_Objects": [],
            }
            for col in df.columns:
                c = str(col).lower()
                if "title" in c or "job title" in c:
                    val = str(row[col]).strip() if pd.notna(row[col]) else "-"
                    contact["Title"] = val
                elif "company" in c and "phone" not in c and "state" not in c and "country" not in c and "revenue" not in c and "industry" not in c and "description" not in c:
                    val = str(row[col]).strip() if pd.notna(row[col]) else "-"
                    contact["Company"] = contact["Company Name"] = val
                elif "website" in c:
                    contact["Website"] = str(row[col]).strip() if pd.notna(row[col]) else "-"
                elif "industry" in c or "company industry" in c:
                    contact["Industry"] = str(row[col]).strip() if pd.notna(row[col]) else "-"
                elif "revenue" in c or "company revenue" in c or "annual revenue" in c:
                    contact["Revenue"] = str(row[col]).strip() if pd.notna(row[col]) else "-"
                elif ("state" in c or "company state" in c) and "country" not in c:
                    contact["State"] = str(row[col]).strip() if pd.notna(row[col]) else "-"
                elif "country" in c:
                    contact["Country"] = str(row[col]).strip() if pd.notna(row[col]) else "-"
                elif "notes" in c:
                    contact["Notes"] = str(row[col]).strip() if pd.notna(row[col]) else "-"
                elif "email" in c and pd.notna(row[col]) and str(row[col]).strip():
                    contact["Emails"].append(str(row[col]).strip())
                elif ("phone" in c or "mobile" in c or "cell" in c) and pd.notna(row[col]):
                    raw = row[col]
                    if isinstance(raw, float) and raw == raw:  # not nan
                        num = str(int(raw)) if raw == int(raw) else str(raw)
                    else:
                        num = str(raw).strip()
                    if num and num not in contact["Phones"]:
                        contact["Phones"].append(num)
                        contact["Phone_Objects"].append({"number": num, "type": "Direct", "original_col": str(col)})
            if not contact["Phones"]:
                continue
            contacts.append(contact)
        except Exception:
            continue
    return contacts


def load_all_contacts_from_leads(leads_dir: Path):
    """Walk leads_dir for .xlsx and .csv; load all contacts. First match wins per (phone, name)."""
    all_contacts = []
    if not leads_dir.is_dir():
        print(f"Leads folder not found: {leads_dir}")
        return all_contacts
    for path in sorted(leads_dir.rglob("*")):
        if path.suffix.lower() not in (".xlsx", ".csv"):
            continue
        if path.name.startswith("~"):
            continue
        if path.suffix.lower() == ".xlsx":
            try:
                import pandas as pd
                xl = pd.ExcelFile(path, engine="openpyxl")
                for sn in xl.sheet_names:
                    contacts = load_contacts_from_excel(path, sheet_name=sn)
                    all_contacts.extend(contacts)
            except Exception as e:
                print(f"  Skip {path.name}: {e}")
        else:
            all_contacts.extend(load_contacts_from_excel(path))
    print(f"Loaded {len(all_contacts)} contact rows from leads.")
    return all_contacts


def build_lookup(contacts: list):
    """Build (normalized_phone, normalized_name) -> contact. First occurrence wins."""
    lookup = {}
    for c in contacts:
        name = f"{c.get('First Name', '')} {c.get('Last Name', '')}".strip()
        name_norm = normalize_name(name)
        for p in c.get("Phones", []) or []:
            key = (normalize_phone(p), name_norm)
            if key not in lookup:
                lookup[key] = c
    return lookup


def ensure_contact_data_column(cursor):
    """Ensure calls.contact_data exists (in case init_db hasn't been run on this DB)."""
    cursor.execute("PRAGMA table_info(calls)")
    columns = [row[1] for row in cursor.fetchall()]
    if "contact_data" not in columns:
        print("Adding missing column: calls.contact_data")
        cursor.execute("ALTER TABLE calls ADD COLUMN contact_data TEXT")


def backfill(dry_run: bool = True, leads_dir: Path = None):
    leads_dir = leads_dir or LEADS_DIR
    db_path_str = str(DB_PATH)
    if not os.path.exists(db_path_str):
        print(f"Database not found at {db_path_str}")
        return
    print(f"Creating backup at {BACKUP_PATH}...")
    shutil.copy2(db_path_str, str(BACKUP_PATH))
    contacts = load_all_contacts_from_leads(leads_dir)
    lookup = build_lookup(contacts)
    print(f"Lookup has {len(lookup)} (phone, name) keys.")
    conn = sqlite3.connect(db_path_str)
    cursor = conn.cursor()
    ensure_contact_data_column(cursor)
    conn.commit()
    cursor.execute("SELECT id, phone, contact_name FROM calls")
    rows = cursor.fetchall()
    updated = 0
    no_match = 0
    errors = 0
    error_samples = []  # first few errors to show
    for call_id, phone, contact_name in rows:
        if not phone or not str(phone).strip():
            no_match += 1
            continue
        phone_norm = normalize_phone(str(phone))
        name_norm = normalize_name(str(contact_name or ""))
        key = (phone_norm, name_norm)
        contact = lookup.get(key)
        if not contact:
            no_match += 1
            continue
        try:
            clean_contact = _sanitize_for_json(contact)
            contact_json = json.dumps(clean_contact)
            if not dry_run:
                cursor.execute("UPDATE calls SET contact_data = ? WHERE id = ?", (contact_json, call_id))
            updated += 1
        except Exception as e:
            errors += 1
            if len(error_samples) < 5:
                error_samples.append((call_id, str(e)))
    if not dry_run:
        conn.commit()
    conn.close()
    print(f"Would update: {updated}" if dry_run else f"Updated: {updated}")
    print(f"No match: {no_match}")
    print(f"Errors: {errors}")
    if error_samples:
        print("Sample errors (first few):")
        for call_id, err in error_samples:
            print(f"  call id={call_id}: {err}")
    if dry_run:
        print("Dry-run only. Run with --execute to apply.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill calls.contact_data from Excel/CSV in cleandb/leads/.")
    parser.add_argument("--execute", action="store_true", help="Apply updates. Default is dry-run.")
    parser.add_argument("--leads", type=Path, default=None, help="Path to leads folder (default: cleandb/leads)")
    args = parser.parse_args()
    backfill(dry_run=not args.execute, leads_dir=args.leads)
