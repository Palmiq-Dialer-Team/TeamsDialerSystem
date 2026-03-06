# Database Cleaning & Fix Scripts (cleandb)

Run these from the **Backend** folder (or use the `.bat` files which do that for you).  
The database file is `Backend\teams_dialer.db`. A backup is always created as `teams_dialer_backup.db` before any destructive step.

---

## Order of operations

Do steps in this order when cleaning/fixing the calls database.

### 1. Backfill contact data (do this first)

Run the backfill so existing call rows get `contact_data` filled from Excel/CSV lead files in `cleandb\leads\`.

- **Input:** Excel (`.xlsx`) and CSV files in `cleandb\leads\` (and subfolders).
- **Effect:** Matches call rows by phone + contact name; updates `calls.contact_data` with full contact from leads. Rows with no match stay with empty `contact_data`.
- **Run dry-run first:** `run_backfill.bat` (no DB writes).
- **Then apply:** `run_backfill_execute.bat` or `python cleandb\backfill_calls_from_excel.py --execute`.
- **Dependencies:** The batch files install `requirements.txt` and `openpyxl` (for reading .xlsx).

### 2. Clean calls DB (manual entries + old no-contact rows)

Removes:

- Rows where `phone = 'Manual Entry'` and `contact_name = 'Manual Entry'` and `disposition = 'Manual Entry'`
- Rows with **no contact info** (`contact_data` NULL or empty) **older than 30 days** (last month’s no-match rows are kept)

**How to run:**

- **Dry-run (recommended first):**  
  `run_clean_calls_db.bat`  
  or:  
  `python cleandb\clean_calls_db.py`  
  This only prints what would be deleted; it does not change the DB (backup is still created).
- **Actually delete:**  
  `run_clean_calls_db_execute.bat`  
  or:  
  `python cleandb\clean_calls_db.py --execute`

### 3. Fix calls DB (merge No Disposition, dedupe, etc.)

Runs the existing “fix” logic: merge “No Disposition” + real disposition, dedupe Manual Entry, etc.

**How to run:**

- `run_fix_calls_db.bat`  
  or:  
  `python cleandb\fix_calls_db.py`  
  (from the **Backend** folder)

This uses `Backend\clean_db.py` and modifies the DB (after creating a backup).

### 4. Repair dispositions from local CSV/XLSX (optional)

Updates server `calls.disposition` (and `notes`) from user disposition files that have the correct stamps (e.g. from clients that wrote to _DISPO_STAMP / Disposition Stamp). Use this when many calls were incorrectly saved as "No Response" or "No Disposition" and you have the correct dispositions in local files.

- **Input:** CSV/XLSX in `cleandb\disposition_files\` (or `--files path`) with headers including **First Name**, **Last Name**, **Contact Phone 1–5** / **Contact Mobile Phone 1–3**, **_DISPO_STAMP** (or "Disposition Stamp"), **Notes**. Optional: **Date** or **Timestamp** for ordering.
- **Only updates:** Rows whose disposition is **"No Response"** or **"No Disposition"**. Rows that already have a final disposition (e.g. Booked Meeting, Not Interested) are left unchanged.
- **Matching:** (1) name+phone; (2) if no match, phone-only (same number, different name); (3) if still no match, name-only (same person, different number in file). If the same key appears in multiple files, **the latest source wins** (by file modification time, or by a Date/Timestamp column if present).
- **Effect:** For each matching call, `calls.disposition` and `calls.notes` are set from the chosen file row. If DB `contact_data` is empty and the file has First/Last name, a minimal contact_data is filled.
- **Run dry-run first:** `run_repair_dispositions.bat` or `python cleandb\repair_dispositions_from_files.py`.
- **Apply:** `run_repair_dispositions_execute.bat` or `python cleandb\repair_dispositions_from_files.py --execute`.
- **Custom folder:** `python cleandb\repair_dispositions_from_files.py --files "C:\path\to\files" [--execute]`.

**Diagnose mismatches:** If many file rows don't match the DB, run the diagnostic to see why (no DB changes):
- `python cleandb\diagnose_disposition_match.py` or `run_diagnose_disposition_match.bat`
- Optional: `--files "C:\path"` and `--out report.txt` to save the report.

### 4a. Fill contact_data from disposition files (optional)

For call rows that have **missing or empty** `contact_data`, this script looks up the contact in the same XLSX/CSV disposition files and fills `contact_data` in the same format used when a disposition is logged. That way dashboard exports include Title, Company Name, Email 1, Contact Phone 1–5, State, Country, Industry, Company Revenue, Notes.

- **Input:** Same as repair: CSV/XLSX in `cleandb\disposition_files\` (or `--files path`) with columns such as First Name, Last Name, Title, Company Name, Email 1, Contact Phone 1–5, State, Country, Industry, Company Revenue, Notes.
- **Only updates:** Rows where `contact_data` is NULL, empty, or `{}`.
- **Matching:** Same as repair: (1) name+phone, (2) phone-only, (3) name-only. Latest file row wins.
- **Run dry-run first:** `run_fill_contact_data.bat` or `python cleandb\fill_contact_data_from_files.py`.
- **Apply:** `run_fill_contact_data_execute.bat` or `python cleandb\fill_contact_data_from_files.py --execute`.

### 4b. Delete rows without proper contact_data (optional)

Removes call rows that would show up in dashboard exports with no data in the export columns. A row is deleted if `contact_data` is missing/empty (NULL, `''`, or `'{}'`) **or** if it has none of: Title, Company Name, Email 1, Contact Phone 1–5, State, Country, Industry, Company Revenue, Notes (so rows with only First/Last name and no other export fields are removed).

- **Effect:** Deletes all such rows by default. Use `--older-than-days N` to only delete rows older than N days.
- **Run dry-run first:** `run_delete_calls_without_contact_data.bat` or `python cleandb\delete_calls_without_contact_data.py`.
- **Apply:** `run_delete_calls_without_contact_data_execute.bat` or `python cleandb\delete_calls_without_contact_data.py --execute`.
- **Only older rows:** `python cleandb\delete_calls_without_contact_data.py --older-than-days 30 --execute`.

### 5. Dedupe calls (one row per contact name; keep row with most info)

Removes duplicate call rows so that for each (agent, **contact name**) only **one** row is kept. Duplicates are identified by matching **contact name** (normalized First + Last from `contact_data`, or `contact_name`; if no name, fallback to phone so nameless rows don’t all merge).

When choosing which row to keep, the script **first** prefers the row with **more contact info** (phone, company name, email, title, state, country, industry, notes). Rows with less info are deleted. Then as tie-breakers: higher disposition importance (No Response → … → Booked Meeting), then latest `timestamp`.

- **Why duplicates appear:** The same contact can have multiple call rows (e.g. several "No Response" plus later a "Booked Meeting"). This script keeps the single row that has the most complete contact_data (and best disposition if tied).
- **Logic:** Partition by `agent_id` + normalized contact name. Order by: info score DESC (more filled fields = keep), disposition priority DESC, timestamp DESC. Keep rank 1, delete the rest.
- **Run dry-run first:** `run_dedupe_calls_db.bat` or `python cleandb\dedupe_calls_db.py`.
- **Apply:** `run_dedupe_calls_db_execute.bat` or `python cleandb\dedupe_calls_db.py --execute`.

---

## Batch files (install deps then run)

All `.bat` files:

1. Change to the Backend directory.
2. Install dependencies from `requirements.txt` (and `openpyxl` for backfill).
3. Run the corresponding Python script.

- **run_backfill.bat** — backfill dry-run (reports only; no DB writes).
- **run_backfill_execute.bat** — backfill with `--execute` (updates `calls.contact_data`).
- **run_clean_calls_db.bat** — dry-run clean (no deletes).
- **run_clean_calls_db_execute.bat** — run clean with `--execute` (applies deletes).
- **run_fix_calls_db.bat** — run fix/merge/dedupe.
- **run_repair_dispositions.bat** — dry-run repair dispositions from CSV/XLSX (latest wins).
- **run_repair_dispositions_execute.bat** — repair with `--execute` (updates `calls.disposition` and `notes`).
- **run_fill_contact_data.bat** — dry-run fill `contact_data` from disposition files for rows missing it.
- **run_fill_contact_data_execute.bat** — fill with `--execute` (updates `calls.contact_data`).
- **run_delete_calls_without_contact_data.bat** — dry-run delete rows without proper contact_data.
- **run_delete_calls_without_contact_data_execute.bat** — delete with `--execute`.
- **run_dedupe_calls_db.bat** — dry-run dedupe (same contact + same disposition; keep latest).
- **run_dedupe_calls_db_execute.bat** — dedupe with `--execute` (deletes duplicate rows).

---

## Quick reference

| Step | Script / Bat | Purpose |
|------|----------------|--------|
| 1 | `run_backfill.bat` then `run_backfill_execute.bat` | Fill `contact_data` from Excel/CSV in `cleandb\leads\` |
| 2 | `run_clean_calls_db.bat` then `run_clean_calls_db_execute.bat` | Remove manual entries and old no-contact rows |
| 3 | `run_fix_calls_db.bat` | Merge No Disposition, dedupe, etc. |
| 4 | `run_repair_dispositions.bat` then `run_repair_dispositions_execute.bat` | Repair dispositions from local CSV/XLSX (latest wins) |
| 4a | `run_fill_contact_data.bat` then `run_fill_contact_data_execute.bat` | Fill `contact_data` from disposition files for rows missing it |
| 4b | `run_delete_calls_without_contact_data.bat` then `run_..._execute.bat` | Delete rows without proper contact_data |
| 5 | `run_dedupe_calls_db.bat` then `run_dedupe_calls_db_execute.bat` | Dedupe: one row per contact, keep highest disposition importance |

Always run backfill, clean, repair, and dedupe in dry-run first, then use the `*_execute.bat` to apply.
