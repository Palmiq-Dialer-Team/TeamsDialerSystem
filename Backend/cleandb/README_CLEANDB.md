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

---

## Quick reference

| Step | Script / Bat | Purpose |
|------|----------------|--------|
| 1 | `run_backfill.bat` then `run_backfill_execute.bat` | Fill `contact_data` from Excel/CSV in `cleandb\leads\` |
| 2 | `run_clean_calls_db.bat` then `run_clean_calls_db_execute.bat` | Remove manual entries and old no-contact rows |
| 3 | `run_fix_calls_db.bat` | Merge No Disposition, dedupe, etc. |

Always run backfill and clean in dry-run first, then use the `*_execute.bat` to apply.
