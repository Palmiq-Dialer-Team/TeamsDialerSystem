"""
Database connection and operations
Uses SQLite for storing call data and callbacks.
"""

import sqlite3
import csv
import io
import json
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional
import os
from pathlib import Path
from uuid import uuid4

from db_connection import get_connection, get_readonly_connection

# Database file path (TEAMS_DIALER_DB can override)
CENTRAL_SERVER_DIR = Path(__file__).parent
_default_db = (CENTRAL_SERVER_DIR / "teams_dialer.db").resolve()
DB_PATH = Path(os.environ["TEAMS_DIALER_DB"]).resolve() if os.environ.get("TEAMS_DIALER_DB") else _default_db


def init_db():
    """
    Initialize database and ensure schema is up to date.
    """
    # Log which database file is in use
    print(f"[DB] Using database: {DB_PATH.resolve()}")
    with get_connection(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
    
        # Check if calls table exists and has old schema
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='calls'")
        table_exists = cursor.fetchone()
        
        if table_exists:
            # Check if table has old schema (agent column) or new schema (agent_id column)
            cursor.execute("PRAGMA table_info(calls)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'agent' in columns and 'agent_id' not in columns:
                print("Detected old database schema. Migrating to new schema...")
                cursor.execute("DROP TABLE IF EXISTS calls")
            elif 'agent_id' not in columns:
                print("Detected incomplete schema. Recreating calls table...")
                cursor.execute("DROP TABLE IF EXISTS calls")
    
        # Enhanced calls table to store contact details, notes, and full contact_data JSON
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id TEXT UNIQUE,
                agent_id TEXT NOT NULL,
                phone TEXT,
                contact_name TEXT,
                disposition TEXT,
                duration INTEGER,
                notes TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                campaign_mode TEXT,
                contact_data TEXT
            )
        """)

        # Ensure contact_data and call_id columns exist on existing databases
        cursor.execute("PRAGMA table_info(calls)")
        call_columns = [row[1] for row in cursor.fetchall()]
        if "contact_data" not in call_columns:
            cursor.execute("ALTER TABLE calls ADD COLUMN contact_data TEXT")
        if "call_id" not in call_columns:
            cursor.execute("ALTER TABLE calls ADD COLUMN call_id TEXT")

        # Fix rows with NULL id or missing call_id.
        _fix_null_call_ids_impl(conn)
        _ensure_call_ids_impl(conn)

        # Create callbacks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS callbacks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                scheduled_time TEXT NOT NULL,
                notes TEXT,
                status TEXT DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source_workbook_path TEXT,
                source_sheet_name TEXT,
                source_row_index INTEGER,
                contact_data TEXT
            )
        """)
        
        # Drop old indexes if they exist (they might reference wrong columns)
        cursor.execute("DROP INDEX IF EXISTS idx_agent_timestamp")
        
        # Ensure indexes for performance on calls table
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_calls_call_id ON calls(call_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_time ON calls(agent_id, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_disposition ON calls(disposition)")
        
        # Create indexes on callbacks for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_callback_agent_status 
            ON callbacks(agent_id, status)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_callback_scheduled_time 
            ON callbacks(scheduled_time, status)
        """)


def _fix_null_call_ids_impl(conn: sqlite3.Connection) -> int:
    """
    Fix call rows with NULL id (assign new ids and bump sqlite_sequence).
    Returns the number of rows fixed. Caller must commit if > 0.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT rowid FROM calls WHERE id IS NULL ORDER BY rowid")
    null_id_rowids = [row[0] for row in cursor.fetchall()]
    if not null_id_rowids:
        return 0
    cursor.execute("SELECT coalesce(max(id), 0) FROM calls")
    max_id = cursor.fetchone()[0]
    max_id = int(max_id)  # ensure int (SQLite may return float)
    for i, rowid in enumerate(null_id_rowids):
        cursor.execute("UPDATE calls SET id = ? WHERE rowid = ?", (max_id + 1 + i, rowid))
    new_max = max_id + len(null_id_rowids)
    cursor.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'calls'", (new_max,))
    if cursor.rowcount == 0:
        cursor.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('calls', ?)", (new_max,))
    conn.commit()
    print(f"[DB] Fixed {len(null_id_rowids)} call row(s) with NULL id (assigned ids {max_id + 1}..{new_max})")
    return len(null_id_rowids)


def _ensure_call_ids_impl(conn: sqlite3.Connection) -> int:
    """Backfill missing UUID call identifiers on existing rows."""
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM calls WHERE call_id IS NULL OR TRIM(call_id) = '' ORDER BY id")
    missing_rows = [row[0] for row in cursor.fetchall()]
    if not missing_rows:
        return 0
    for row_id in missing_rows:
        cursor.execute("UPDATE calls SET call_id = ? WHERE id = ?", (str(uuid4()), row_id))
    print(f"[DB] Backfilled {len(missing_rows)} call_id value(s)")
    return len(missing_rows)


def fix_null_call_ids() -> int:
    """
    Open DB, fix any call rows with NULL id, close. Used at startup and after disposition writes
    so the DB can heal without restart. Returns number of rows fixed.
    """
    with get_connection(DB_PATH) as conn:
        return _fix_null_call_ids_impl(conn)


def log_call(data_dict: Dict[str, Any]):
    """
    Backwards-compatible simple call logger used by existing callback flows.
    Kept for legacy use; new code should prefer log_disposition with richer data.
    """
    call_uuid = str(data_dict.get("call_id") or uuid4())
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        # Use provided values or defaults for backwards compatibility
        contact_name = data_dict.get("contact_name", "")
        notes = data_dict.get("notes", "")
        campaign_mode = data_dict.get("campaign_mode", "General")
        timestamp = data_dict.get("timestamp") or datetime.now().isoformat()
        contact_data = data_dict.get("contact_data")
        if isinstance(contact_data, dict):
            contact_data = json.dumps(contact_data)
        
        cursor.execute(
            """
            INSERT INTO calls (call_id, agent_id, phone, disposition, duration, timestamp, contact_name, notes, campaign_mode, contact_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_uuid,
                data_dict.get("agent"),
                data_dict.get("phone"),
                data_dict.get("disposition"),
                int(data_dict.get("duration") or 0),
                timestamp,
                contact_name,
                notes,
                campaign_mode,
                contact_data,
            ),
        )
    return call_uuid


def log_disposition(data: Dict[str, Any]) -> str:
    """
    Saves a finalized call disposition to the central database.
    Expected keys:
        - agent_id
        - phone
        - contact_name
        - disposition
        - duration (seconds)
        - notes
        - campaign_mode
    """
    call_uuid = str(data.get("call_id") or uuid4())
    contact_data = data.get("contact_data")
    if isinstance(contact_data, dict):
        contact_data = json.dumps(contact_data)
    
    insert_args = (
        call_uuid,
        data.get("agent_id"),
        data.get("phone"),
        data.get("contact_name"),
        data.get("disposition"),
        int(data.get("duration") or 0),
        data.get("notes", ""),
        datetime.now().isoformat(),
        data.get("campaign_mode", "General"),
        contact_data,
    )
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO calls (call_id, agent_id, phone, contact_name, disposition, duration, notes, timestamp, campaign_mode, contact_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                insert_args,
            )
            row = cursor.fetchone()
            if row is None:
                _id = cursor.lastrowid
                if _id is None or _id == 0:
                    print(f"[DB] WARNING: log_disposition INSERT RETURNING gave no row, lastrowid={_id}")
        except sqlite3.OperationalError:
            cursor.execute(
                """
                INSERT INTO calls (call_id, agent_id, phone, contact_name, disposition, duration, notes, timestamp, campaign_mode, contact_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                insert_args,
            )
            lastrowid = cursor.lastrowid
            if lastrowid is None or lastrowid == 0:
                print(f"[DB] WARNING: log_disposition INSERT did not get an id (lastrowid={lastrowid}) — possible NULL id row created")
    # Safety net: heal any NULL-id rows (e.g. from older code paths or glitches)
    fix_null_call_ids()
    return call_uuid


def update_call_disposition(data: Dict[str, Any]) -> bool:
    """
    Updates the disposition of the most recent 'No Disposition' call for this agent+phone.
    Used when the user clicks a disposition button after a call has ended.
    Returns True if a row was updated, False if no matching call (will need INSERT elsewhere).
    """
    agent_id = data.get("agent_id")
    phone = data.get("phone", "").strip()
    explicit_call_id = str(data.get("call_id") or "").strip()
    if not agent_id:
        return False

    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        call_row_id = None
        if explicit_call_id:
            cursor.execute("SELECT id FROM calls WHERE call_id = ?", (explicit_call_id,))
            row = cursor.fetchone()
            if row:
                call_row_id = row[0]
        if call_row_id is None:
            if not phone:
                return False
            cutoff = (datetime.now() - timedelta(hours=4)).isoformat()
            cursor.execute(
                """
                SELECT id FROM calls
                WHERE agent_id = ? AND phone = ? AND disposition = 'No Disposition' AND timestamp >= ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (agent_id, phone, cutoff),
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute(
                    """
                    SELECT id FROM calls
                    WHERE agent_id = ? AND phone = ? AND timestamp >= ?
                    ORDER BY timestamp DESC LIMIT 1
                    """,
                    (agent_id, phone, cutoff),
                )
                row = cursor.fetchone()
                if not row:
                    return False
            call_row_id = row[0]

        contact_data = data.get("contact_data")
        if isinstance(contact_data, dict):
            contact_data = json.dumps(contact_data)

        if contact_data is not None:
            cursor.execute(
                """
                UPDATE calls SET disposition = ?, notes = ?, contact_data = ?
                WHERE id = ?
                """,
                (data.get("disposition", ""), data.get("notes", ""), contact_data, call_row_id),
            )
        else:
            cursor.execute(
                """
                UPDATE calls SET disposition = ?, notes = ?
                WHERE id = ?
                """,
                (data.get("disposition", ""), data.get("notes", ""), call_row_id),
            )
        return cursor.rowcount > 0


def create_call_record(call_data: Dict[str, Any]) -> str:
    """
    Create a new call record and return its ID.
    This is used by the /api/calls endpoint to persist a call as soon as it is initiated.
    """
    call_uuid = str(call_data.get("call_id") or uuid4())
    timestamp = call_data.get("timestamp") or datetime.now().isoformat()

    contact_data = call_data.get("contact_data")
    if isinstance(contact_data, dict):
        contact_data = json.dumps(contact_data)

    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO calls (call_id, agent_id, phone, contact_name, disposition, duration, notes, timestamp, campaign_mode, contact_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_uuid,
                call_data.get("agent_id"),
                call_data.get("phone"),
                call_data.get("contact_name", ""),
                call_data.get("disposition", "Pending"),
                int(call_data.get("duration") or 0),
                call_data.get("notes", ""),
                timestamp,
                call_data.get("campaign_mode", "General"),
                contact_data,
            ),
        )
    return call_uuid


def get_agent_stats(agent_id: str) -> Dict[str, Any]:
    """
    Calculates daily and monthly stats for a specific agent.
    Uses string pattern matching (LIKE) to ensure strict adherence 
    to the current calendar day and month.
    """
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        
        now = datetime.now()
        day_prefix = now.strftime("%Y-%m-%d")
        month_prefix = now.strftime("%Y-%m")
        
        stats: Dict[str, Any] = {
            "calls_today": 0,
            "calls_month": 0,
            "dispositions_today": {},
        }
        
        cursor.execute(
            "SELECT COUNT(*) FROM calls WHERE agent_id = ? AND timestamp LIKE ?",
            (agent_id, f"{day_prefix}%"),
        )
        row = cursor.fetchone()
        stats["calls_today"] = row[0] if row else 0
        
        cursor.execute(
            "SELECT COUNT(*) FROM calls WHERE agent_id = ? AND timestamp LIKE ?",
            (agent_id, f"{month_prefix}%"),
        )
        row = cursor.fetchone()
        stats["calls_month"] = row[0] if row else 0
        
        cursor.execute(
            """
            SELECT disposition, COUNT(*)
            FROM calls
            WHERE agent_id = ? AND timestamp LIKE ?
            GROUP BY disposition
            """,
            (agent_id, f"{day_prefix}%"),
        )
        
        for dispo, count in cursor.fetchall():
            stats["dispositions_today"][dispo] = count
        
        return stats


def get_all_unique_agents() -> List[str]:
    """
    Returns a list of distinct agent_id values from the calls table.
    Returns an empty list if no agents are found.
    """
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT agent_id FROM calls ORDER BY agent_id")
        rows = cursor.fetchall()
        return [row[0] for row in rows]


# CSV export column order to match Excel (First Name, Last Name, Title, Company Name, Email 1, Contact Phones, State, Country, Industry, Revenue, Notes, Disposition, etc.)
EXPORT_CSV_HEADERS = [
    "Agent", "First Name", "Last Name", "Title", "Company Name",
    "Email 1", "Contact Phone 1", "Contact Phone 2", "Contact Phone 3", "Contact Phone 4", "Contact Phone 5",
    "State", "Country", "Industry", "Company Revenue", "Notes",
    "Disposition", "Duration", "Timestamp",
]


def _looks_like_phone(s: str) -> bool:
    """True if string is mostly digits/dots/dashes/parens (e.g. company phone mistaken for company name)."""
    if not s or not isinstance(s, str):
        return False
    cleaned = re.sub(r"[\s\-\.\(\)]", "", s)
    digits = sum(1 for c in cleaned if c.isdigit())
    return len(cleaned) >= 7 and digits >= 6


def _normalize_phone_for_match(phone: str) -> str:
    """Strip and remove spaces, dashes, dots, parens for matching."""
    if not phone or not isinstance(phone, str):
        return ""
    return re.sub(r"[\s\-\.\(\)]", "", str(phone).strip())


def _get_contact_val(contact: Dict[str, Any], *keys: str, default: str = "", skip_phone_like: bool = False) -> str:
    """Get first present key from contact; normalize to string for CSV. If skip_phone_like, reject values that look like a phone."""
    for k in keys:
        v = contact.get(k)
        if v is None:
            continue
        if isinstance(v, str) and v.strip():
            s = v.strip()
        else:
            s = str(v).strip()
        if s and s.lower() != "-":
            if skip_phone_like and _looks_like_phone(s):
                continue
            return s
    return default


def _row_to_export_columns(
    agent_id: str,
    disposition: str,
    notes: str,
    timestamp: str,
    duration: int,
    contact_data_str: str,
    call_phone: Optional[str] = None,
) -> Tuple[List[Any], Optional[int]]:
    """Turn one call row + contact_data JSON into (row values, index of phone that matches call_phone or None)."""
    contact = {}
    if contact_data_str and contact_data_str.strip():
        try:
            contact = json.loads(contact_data_str)
        except (json.JSONDecodeError, TypeError):
            pass
    emails = contact.get("Emails") or []
    phones = contact.get("Phones") or []
    # Company: skip values that look like a phone (e.g. company phone column mistaken for company name)
    company = _get_contact_val(contact, "Company Name", "Company", default="", skip_phone_like=True)
    state = _get_contact_val(contact, "State", "Company State Abbr", "Company State", default="")
    country = _get_contact_val(contact, "Country", "Company Country", "Company Country (Alpha 3)", default="")
    industry = _get_contact_val(contact, "Industry", "Company Industry", "Company Description", default="")
    revenue = _get_contact_val(contact, "Company Revenue", "Revenue", "Company Revenue Range", "Annual Revenue", default="")
    contact_notes = _get_contact_val(contact, "Notes", default="")
    # Which contact phone (0-4) matches the call's phone (for bold in xlsx)
    match_index: Optional[int] = None
    if call_phone:
        call_norm = _normalize_phone_for_match(call_phone)
        if call_norm:
            for i, p in enumerate(phones[:5]):
                if p and _normalize_phone_for_match(str(p)) == call_norm:
                    match_index = i
                    break
    row_vals = [
        agent_id or "",
        _get_contact_val(contact, "First Name", default=""),
        _get_contact_val(contact, "Last Name", default=""),
        _get_contact_val(contact, "Title", "Job Title", default=""),
        company,
        emails[0] if len(emails) > 0 else "",
        phones[0] if len(phones) > 0 else "",
        phones[1] if len(phones) > 1 else "",
        phones[2] if len(phones) > 2 else "",
        phones[3] if len(phones) > 3 else "",
        phones[4] if len(phones) > 4 else "",
        state,
        country,
        industry,
        revenue,
        contact_notes,
        disposition or "",
        duration if duration is not None else "",
        timestamp or "",
    ]
    return (row_vals, match_index)


def export_dispositions_to_csv(
    disposition_filter: str | None = None,
    agent_id: str | None = None,
    start_ts: str | None = None,
    end_ts: str | None = None,
) -> io.StringIO:
    """
    Generates a CSV string of calls with full contact columns from contact_data.
    - If disposition_filter is 'ALL' or None: returns all rows that HAVE a disposition,
      deduplicated by (agent_id, phone) keeping only the latest per contact.
    - If disposition_filter is a specific disposition: returns all matching rows.
    - Only includes rows with contact_data (no-contact-info rows excluded).
    """
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        
        if disposition_filter and disposition_filter != "ALL":
            query = """SELECT agent_id, contact_name, phone, disposition, notes, timestamp, duration, contact_data
                       FROM calls"""
            params: List[Any] = []
            conditions = [
                "disposition = ?",
                "contact_data IS NOT NULL",
                "contact_data != ''",
            ]
            params.append(disposition_filter)
            if agent_id and agent_id != "ALL":
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if start_ts:
                conditions.append("timestamp >= ?")
                params.append(start_ts)
            if end_ts:
                conditions.append("timestamp <= ?")
                params.append(end_ts)
            query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY timestamp DESC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
        else:
            base_conditions = [
                "disposition IS NOT NULL",
                "disposition != ''",
                "disposition != 'No Disposition'",
                "contact_data IS NOT NULL",
                "contact_data != ''",
            ]
            params = []
            if agent_id and agent_id != "ALL":
                base_conditions.append("agent_id = ?")
                params.append(agent_id)
            if start_ts:
                base_conditions.append("timestamp >= ?")
                params.append(start_ts)
            if end_ts:
                base_conditions.append("timestamp <= ?")
                params.append(end_ts)
            where_clause = " AND ".join(base_conditions)
            partition_by_contact = (
                "agent_id, (COALESCE(TRIM(json_extract(contact_data, '$.First Name')), '') || '|' || "
                "COALESCE(TRIM(json_extract(contact_data, '$.Last Name')), '') || '|' || "
                "COALESCE(json_extract(contact_data, '$.Emails[0]'), ''))"
            )
            query = f"""
                WITH ranked AS (
                    SELECT agent_id, contact_name, phone, disposition, notes, timestamp, duration, contact_data,
                           ROW_NUMBER() OVER (PARTITION BY {partition_by_contact} ORDER BY timestamp DESC) AS rn
                    FROM calls
                    WHERE {where_clause}
                )
                SELECT agent_id, contact_name, phone, disposition, notes, timestamp, duration, contact_data
                FROM ranked
                WHERE rn = 1
                ORDER BY timestamp DESC
            """
            cursor.execute(query, params)
            rows = cursor.fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(EXPORT_CSV_HEADERS)
    for row in rows:
        (agent_id_val, _contact_name, call_phone, disposition, notes, timestamp, duration, contact_data_str) = row
        duration = duration if duration is not None else 0
        out_row, _ = _row_to_export_columns(
            agent_id_val, disposition, notes, timestamp, duration, contact_data_str or "", call_phone=call_phone
        )
        writer.writerow(out_row)
    
    output.seek(0)
    return output


# 0-based column index of "Contact Phone 1" in EXPORT_CSV_HEADERS (for xlsx bold)
PHONE_COL_START_IDX = 6


def export_dispositions_to_xlsx(
    disposition_filter: str | None = None,
    agent_id: str | None = None,
    start_ts: str | None = None,
    end_ts: str | None = None,
) -> io.BytesIO:
    """
    Same as export_dispositions_to_csv but returns an xlsx file with the phone cell
    that matches the call's phone (DB column phone) bolded. Requires openpyxl.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font

    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        if disposition_filter and disposition_filter != "ALL":
            query = """SELECT agent_id, contact_name, phone, disposition, notes, timestamp, duration, contact_data
                       FROM calls"""
            params: List[Any] = []
            conditions = [
                "disposition = ?",
                "contact_data IS NOT NULL",
                "contact_data != ''",
            ]
            params.append(disposition_filter)
            if agent_id and agent_id != "ALL":
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if start_ts:
                conditions.append("timestamp >= ?")
                params.append(start_ts)
            if end_ts:
                conditions.append("timestamp <= ?")
                params.append(end_ts)
            query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY timestamp DESC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
        else:
            base_conditions = [
                "disposition IS NOT NULL",
                "disposition != ''",
                "disposition != 'No Disposition'",
                "contact_data IS NOT NULL",
                "contact_data != ''",
            ]
            params = []
            if agent_id and agent_id != "ALL":
                base_conditions.append("agent_id = ?")
                params.append(agent_id)
            if start_ts:
                base_conditions.append("timestamp >= ?")
                params.append(start_ts)
            if end_ts:
                base_conditions.append("timestamp <= ?")
                params.append(end_ts)
            where_clause = " AND ".join(base_conditions)
            partition_by_contact = (
                "agent_id, (COALESCE(TRIM(json_extract(contact_data, '$.First Name')), '') || '|' || "
                "COALESCE(TRIM(json_extract(contact_data, '$.Last Name')), '') || '|' || "
                "COALESCE(json_extract(contact_data, '$.Emails[0]'), ''))"
            )
            query = f"""
                WITH ranked AS (
                    SELECT agent_id, contact_name, phone, disposition, notes, timestamp, duration, contact_data,
                           ROW_NUMBER() OVER (PARTITION BY {partition_by_contact} ORDER BY timestamp DESC) AS rn
                    FROM calls
                    WHERE {where_clause}
                )
                SELECT agent_id, contact_name, phone, disposition, notes, timestamp, duration, contact_data
                FROM ranked
                WHERE rn = 1
                ORDER BY timestamp DESC
            """
            cursor.execute(query, params)
            rows = cursor.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Export"
    for col_idx, header in enumerate(EXPORT_CSV_HEADERS, start=1):
        ws.cell(row=1, column=col_idx, value=header)
    for row_idx, row in enumerate(rows):
        (agent_id_val, _contact_name, call_phone, disposition, notes, timestamp, duration, contact_data_str) = row
        duration = duration if duration is not None else 0
        out_row, match_index = _row_to_export_columns(
            agent_id_val, disposition, notes, timestamp, duration, contact_data_str or "", call_phone=call_phone
        )
        excel_row = row_idx + 2
        for col_idx, val in enumerate(out_row, start=1):
            cell = ws.cell(row=excel_row, column=col_idx, value=val)
            if match_index is not None and col_idx == PHONE_COL_START_IDX + 1 + match_index:
                cell.font = Font(bold=True)
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out


def create_callback(agent_id: str, scheduled_time: str, notes: str = None,
                   source_workbook_path: str = None, source_sheet_name: str = None,
                   source_row_index: int = None, contact_data: str = None) -> int:
    """
    Create a new callback record.
    
    Args:
        agent_id: Agent ID
        scheduled_time: Scheduled time in ISO 8601 format
        notes: Optional notes
        source_workbook_path: Path to the source Excel workbook
        source_sheet_name: Name of the sheet in the workbook
        source_row_index: Row index in the source sheet (1-based)
        contact_data: Full contact data as JSON string
    
    Returns:
        The ID of the newly created callback
    """
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO callbacks (agent_id, scheduled_time, notes, status, 
                                  source_workbook_path, source_sheet_name, source_row_index, contact_data)
            VALUES (?, ?, ?, 'PENDING', ?, ?, ?, ?)
        """, (agent_id, scheduled_time, notes, source_workbook_path, source_sheet_name, source_row_index, contact_data))
        return cursor.lastrowid


def update_callback_time(callback_id: int, new_scheduled_time: str):
    """
    Update the scheduled time for a callback.
    
    Args:
        callback_id: Callback ID to update
        new_scheduled_time: New scheduled time in ISO 8601 format
    """
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE callbacks 
            SET scheduled_time = ?
            WHERE id = ?
        """, (new_scheduled_time, callback_id))


def complete_callback(callback_id: int):
    """
    Mark a callback as completed.
    
    Args:
        callback_id: Callback ID to complete
    """
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE callbacks 
            SET status = 'COMPLETED'
            WHERE id = ?
        """, (callback_id,))


def cancel_callback(callback_id: int):
    """
    Cancel a callback.
    
    Args:
        callback_id: Callback ID to cancel
    """
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE callbacks 
            SET status = 'CANCELLED'
            WHERE id = ?
        """, (callback_id,))


def get_overdue_callbacks(agent_id: str):
    """
    Get all overdue callbacks for an agent.
    
    Args:
        agent_id: Agent ID
    
    Returns:
        List of callback records as dictionaries
    """
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        current_time = datetime.now().isoformat()
        cursor.execute("""
            SELECT * FROM callbacks
            WHERE agent_id = ? 
            AND status = 'PENDING'
            AND scheduled_time < ?
            ORDER BY scheduled_time ASC
        """, (agent_id, current_time))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_due_callbacks():
    """
    Get all callbacks that are due (scheduled_time is within the last minute).
    
    Returns:
        List of callback records as dictionaries
    """
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        from datetime import timedelta
        now = datetime.now()
        one_minute_ago = (now - timedelta(minutes=1)).isoformat()
        now_iso = now.isoformat()
        
        cursor.execute("""
            SELECT * FROM callbacks
            WHERE status = 'PENDING'
            AND scheduled_time >= ?
            AND scheduled_time <= ?
            ORDER BY scheduled_time ASC
        """, (one_minute_ago, now_iso))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_callback(callback_id: int):
    """
    Get a single callback by ID.
    
    Args:
        callback_id: Callback ID
    
    Returns:
        Callback record as dictionary, or None if not found
    """
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM callbacks WHERE id = ?
        """, (callback_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return dict(row)


# Initialize database on module import
init_db()
