"""
Diagnose why disposition file rows don't match database call rows.

Uses the same file loading and matching logic as repair_dispositions_from_files.py.
Produces a report: DB vs file counts, why rows didn't match, and file entries
that have no DB counterpart (or vice versa). Run from Backend folder.

Usage:
  python cleandb\\diagnose_disposition_match.py [--files DIR] [--out report.txt] [--samples N]
"""

import json
import re
import sqlite3
import argparse
import sys
from pathlib import Path
from collections import defaultdict

# Reuse repair script's paths and helpers
BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BACKEND_DIR / "teams_dialer.db"
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
        if val != val:
            return ""
        return str(int(val)) if val == int(val) else str(val)
    return str(val).strip()


def _disposition_columns():
    return ("_DISPO_STAMP", "Disposition Stamp", "disposition stamp", "_dispo_stamp")


def _date_columns():
    return ("Date", "Timestamp", "Disposition Date", "date", "timestamp")


def _extract_disposition(val) -> str:
    s = _str_val(val)
    if not s:
        return ""
    try:
        obj = json.loads(s)
        if isinstance(obj, dict) and "disposition" in obj:
            return _str_val(obj.get("disposition"))
    except Exception:
        pass
    if "|" in s:
        tail = s.split("|")[-1].strip()
        if tail:
            return tail
    return s


def load_rows_from_path(path: Path, file_mtime: float):
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
    disp_col = None
    for h in _disposition_columns():
        for c in df.columns:
            if c.strip().lower() == h.strip().lower():
                disp_col = c
                break
        if disp_col is not None:
            break
    if disp_col is None:
        return
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
            yield (name_norm, ph_norm, disp, sort_key)


def load_all_file_entries(files_dir: Path):
    """Same as repair script: (name_norm, phone_norm) -> (disposition, notes, sort_key). Returns dict and also set of all file phone_norms."""
    if not files_dir.is_dir():
        return {}, set()
    candidates = []
    for path in sorted(files_dir.rglob("*")):
        if path.suffix.lower() not in (".xlsx", ".csv"):
            continue
        if path.name.startswith("~"):
            continue
        mtime = path.stat().st_mtime
        try:
            for name_norm, phone_norm, disp, sort_key in load_rows_from_path(path, mtime):
                candidates.append(((name_norm, phone_norm), (disp, "", sort_key)))
        except Exception as e:
            print(f"  Skip {path.name}: {e}", file=sys.stderr)
    latest = {}
    for key, value in candidates:
        disp, notes, sort_key = value
        if key not in latest or sort_key > latest[key][2]:
            latest[key] = (disp, notes, sort_key)
    lookup = {k: (v[0], v[1]) for k, v in latest.items()}
    all_file_phones = set()
    for (name_norm, phone_norm) in lookup:
        all_file_phones.add(phone_norm)
    return lookup, all_file_phones


def run_diagnosis(files_dir: Path, out_path: Path = None, sample_size: int = 20):
    db_path_str = str(DB_PATH)
    if not Path(db_path_str).exists():
        print(f"Database not found: {db_path_str}", file=sys.stderr)
        return
    lookup, all_file_phones = load_all_file_entries(files_dir)
    lines = []
    def log(s=""):
        lines.append(s)
        print(s)

    log("=" * 60)
    log("DISPOSITION FILE vs DATABASE MATCH DIAGNOSIS")
    log("=" * 60)
    log()
    log(f"Files dir: {files_dir}")
    log(f"Database:  {db_path_str}")
    log()
    log("--- FILE SIDE ---")
    log(f"  Unique (name, phone) keys in files: {len(lookup)}")
    log(f"  Unique phone numbers in files:     {len(all_file_phones)}")
    log()

    conn = sqlite3.connect(db_path_str)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, agent_id, phone, contact_name, contact_data FROM calls"
    )
    db_rows = cursor.fetchall()
    conn.close()

    log("--- DATABASE SIDE ---")
    log(f"  Total call rows in DB: {len(db_rows)}")
    log()

    # Categories for DB rows
    matched_name_phone = 0
    no_phone = 0
    no_phone_has_name = 0  # name present but phone empty (saved without number)
    no_name = 0
    phone_not_in_file = 0
    phone_in_file_name_mismatch = 0
    name_only_match = 0  # DB has name+phone, didn't match (name+phone); but name_norm appears in file (different phone in file)
    samples_no_phone = []
    samples_no_phone_has_name = []
    samples_no_name = []
    samples_phone_not_in_file = []
    samples_name_mismatch = []
    samples_name_only_match = []
    file_keys_matched_by_db = set()

    # Names that appear in at least one file key (for name-only check)
    file_name_norms = set(n for n, p in lookup.keys())

    for call_id, agent_id, phone, contact_name, contact_data_str in db_rows:
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

        if not phone or not str(phone).strip():
            no_phone += 1
            if name_norm:
                no_phone_has_name += 1
                if len(samples_no_phone_has_name) < sample_size:
                    samples_no_phone_has_name.append((call_id, agent_id, name_norm[:40]))
            if len(samples_no_phone) < sample_size:
                samples_no_phone.append((call_id, agent_id, str(phone)[:30], (contact_name or "")[:30]))
            continue
        phone_norm = normalize_phone(str(phone))
        if not name_norm:
            no_name += 1
            if len(samples_no_name) < sample_size:
                samples_no_name.append((call_id, agent_id, phone_norm[:20], (contact_name or "")[:30]))
            continue
        key = (name_norm, phone_norm)
        if key in lookup:
            matched_name_phone += 1
            file_keys_matched_by_db.add(key)
        else:
            if phone_norm in all_file_phones:
                phone_in_file_name_mismatch += 1
                if len(samples_name_mismatch) < sample_size:
                    samples_name_mismatch.append((call_id, agent_id, phone_norm[:20], name_norm[:40]))
            else:
                phone_not_in_file += 1
                if len(samples_phone_not_in_file) < sample_size:
                    samples_phone_not_in_file.append((call_id, agent_id, phone_norm[:20], name_norm[:40]))
                # Name-only: this DB row's name appears in file but with different phone(s)
                if name_norm in file_name_norms:
                    name_only_match += 1
                    if len(samples_name_only_match) < sample_size:
                        samples_name_only_match.append((call_id, agent_id, phone_norm[:20], name_norm[:40]))

    log("--- WHY DB ROWS DID NOT MATCH A FILE KEY ---")
    log("  (Repair script matches on (name_norm, phone_norm); both must be present.)")
    log()
    log(f"  DB rows that MATCHED a file (name+phone):  {matched_name_phone}")
    log(f"  DB rows with no / empty phone:             {no_phone}")
    if no_phone > 0:
        log(f"    -> of those, have name (saved without number):  {no_phone_has_name}")
    log(f"  DB rows with no name (no contact_data or contact_name): {no_name}")
    log(f"  DB rows with phone NOT in any file:        {phone_not_in_file}")
    log(f"  DB rows with phone IN file but NAME mismatch: {phone_in_file_name_mismatch}")
    log()
    total_accounted = matched_name_phone + no_phone + no_name + phone_not_in_file + phone_in_file_name_mismatch
    log(f"  Total accounted: {total_accounted} (should equal {len(db_rows)})")
    log()

    log("--- NAME-ONLY CHECK (same person, different number?) ---")
    log("  DB stores contact name as First Name + Last Name (from contact_data or contact_name).")
    log(f"  Unique names in files (name_norm): {len(file_name_norms)}")
    log(f"  DB rows that did NOT match (name+phone) but their NAME appears in file: {name_only_match}")
    log("  (Same person in file but with different phone - e.g. file has Contact Phone 2, DB has Contact Phone 1)")
    log()

    file_keys_never_matched = set(lookup.keys()) - file_keys_matched_by_db
    log("--- FILE SIDE: KEYS THAT NEVER MATCHED A DB ROW ---")
    log(f"  File (name, phone) keys that at least one DB row matched: {len(file_keys_matched_by_db)}")
    log(f"  File (name, phone) keys that NO DB row had:             {len(file_keys_never_matched)}")
    log()

    log("--- SAMPLE DB ROWS WITH NO PHONE ---")
    for t in samples_no_phone[:sample_size]:
        log(f"  id={t[0]} agent={t[1]} phone={t[2]!r} contact_name={t[3]!r}")
    if no_phone_has_name > 0:
        log()
        log("--- SAMPLE DB ROWS: NO PHONE BUT HAVE NAME (saved without number?) ---")
        for t in samples_no_phone_has_name[:sample_size]:
            log(f"  id={t[0]} agent={t[1]} name_norm={t[2]!r}")
    log()
    log("--- SAMPLE DB ROWS WITH NO NAME ---")
    for t in samples_no_name[:sample_size]:
        log(f"  id={t[0]} agent={t[1]} phone_norm={t[2]!r} contact_name={t[3]!r}")
    log()
    log("--- SAMPLE DB ROWS: PHONE NOT IN ANY FILE ---")
    for t in samples_phone_not_in_file[:sample_size]:
        log(f"  id={t[0]} agent={t[1]} phone_norm={t[2]!r} name_norm={t[3]!r}")
    log()
    log("--- SAMPLE DB ROWS: PHONE IN FILE BUT NAME MISMATCH ---")
    for t in samples_name_mismatch[:sample_size]:
        log(f"  id={t[0]} agent={t[1]} phone_norm={t[2]!r} name_norm={t[3]!r}")
    if name_only_match > 0:
        log()
        log("--- SAMPLE DB ROWS: NAME IN FILE BUT DIFFERENT PHONE (name-only match) ---")
        for t in samples_name_only_match[:sample_size]:
            log(f"  id={t[0]} agent={t[1]} phone_norm={t[2]!r} name_norm={t[3]!r}")
    log()

    sample_file_unmatched = list(file_keys_never_matched)[:sample_size]
    log("--- SAMPLE FILE (name, phone) KEYS THAT NO DB ROW HAD ---")
    for (name_norm, phone_norm) in sample_file_unmatched:
        log(f"  name_norm={name_norm!r} phone_norm={phone_norm!r}")
    log()

    log("--- POSSIBLE REASONS FOR MISMATCH ---")
    log("  1. No phone in DB: row was created without a phone (e.g. Manual Entry).")
    log("  2. No name in DB: contact_data and contact_name are empty; repair script requires a name to match.")
    log("  3. Phone not in file: that number was never in your disposition files (different lead list or column).")
    log("  4. Phone in file but name mismatch: same number, different spelling/format of First/Last name in DB vs file.")
    log("  5. File key never matched: that (name, phone) in the file was never called (no DB row with that combo).")
    log("  6. Normalization: phone uses digits-only; name is lowercased and space-collapsed. Different formats can still match.")
    log()
    log("--- RECOMMENDATIONS ---")
    if no_name > 0:
        log(f"  - Run backfill (backfill_calls_from_excel.py) to fill contact_data so {no_name} rows get names.")
    if no_phone_has_name > 0:
        log(f"  - {no_phone_has_name} rows have a name but no phone; repair cannot match without a number. Fix source data or leave as-is.")
    if phone_in_file_name_mismatch > 0:
        log("  - Consider matching by phone-only for repair (same number, different name) or normalize names in files.")
    if name_only_match > 0:
        log(f"  - {name_only_match} rows: name appears in file but phone in DB is not in file (e.g. different number for same person). Phone-only repair would not fix these; file would need that exact number.")
    if len(file_keys_never_matched) > len(db_rows):
        log("  - Many file keys have no DB row: files may include contacts that were never called, or DB was cleared.")
    log("=" * 60)

    if out_path:
        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"Report written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Diagnose why disposition file rows don't match DB rows."
    )
    parser.add_argument(
        "--files",
        type=Path,
        default=None,
        help=f"Path to folder with disposition CSV/XLSX (default: {DEFAULT_FILES_DIR})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write report to this file as well as stdout",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=20,
        help="Max sample rows per category (default 20)",
    )
    args = parser.parse_args()
    run_diagnosis(
        files_dir=args.files or DEFAULT_FILES_DIR,
        out_path=args.out,
        sample_size=args.samples,
    )
