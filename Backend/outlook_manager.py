"""
Outlook Manager (Server Side)

Handles creating calendar events via Microsoft Graph API using Application Permissions.
Credentials are loaded from environment variables (see .env.example).
"""

import os
import msal
import requests
import datetime
import json

# Load .env for local development (has no effect if .env doesn't exist)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

# --- CREDENTIALS from environment ---
CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
ORGANIZER_EMAIL = os.getenv("AZURE_ORGANIZER_EMAIL", "")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}" if TENANT_ID else ""
ENDPOINT = f"https://graph.microsoft.com/v1.0/users/{ORGANIZER_EMAIL}/events" if ORGANIZER_EMAIL else ""
SCOPES = ["https://graph.microsoft.com/.default"]

def _safe_get(data: dict, key: str, default: str = "N/A"):
    """Get value from dict; return default if missing, empty, or not a string/number."""
    if data is None:
        return default
    val = data.get(key)
    if val is None or (isinstance(val, str) and not val.strip()):
        return default
    return str(val).strip() if val else default


def _extract_phone_like(notes: str) -> str:
    """
    Try to extract a phone-like string from free-form notes.
    Keeps only characters typically used in phone numbers.
    """
    if not notes:
        return ""
    allowed = set("0123456789+()- ")
    filtered = "".join(ch for ch in str(notes) if ch in allowed).strip()
    return filtered


def _format_callback_event(contact_data_dict, notes="", source_workbook_path=None, source_sheet_name=None):
    """
    Build Outlook event subject and body from full contact metadata.
    Subject uses name + company; body includes the entire contact_data row (all columns).
    Returns (subject, body_html).
    """
    if not isinstance(contact_data_dict, dict):
        contact_data_dict = {}
    first = _safe_get(contact_data_dict, "First Name", "N/A")
    last = _safe_get(contact_data_dict, "Last Name", "N/A")
    contact_name = f"{first} {last}".strip() or "N/A"
    company = _safe_get(contact_data_dict, "Company", "N/A")

    subject = f"Callback: {contact_name} ({company})"

    # Number to call (derived from callback notes)
    notes_line = ""
    phone_str = _extract_phone_like((notes or "").strip())
    if phone_str:
        notes_line = f'<p><b>Number to call:</b> {_html_escape(phone_str)}</p>'

    # Full contact_data row: every key/value from the database (title, company, etc.),
    # excluding internal / noisy fields that should not appear in the invite.
    EXCLUDED_KEYS = {
        "Phone Objects",
        "Phone_Objects",
        "phone_objects",
        "Source",
        "Original Row",
        "original_row",
        "Current Disposition",
        "current_disposition",
        "Row Number",
        "Row Index",
        "row_index",
        "Sheet Name",
        "sheetName",
        "source_row_index",
    }

    rows_html = []
    for key, val in sorted(contact_data_dict.items()):
        if key in EXCLUDED_KEYS:
            continue
        display_val = _format_contact_value(val)
        rows_html.append(f"<tr><td style=\"vertical-align:top;padding:2px 8px 2px 0;\"><b>{_html_escape(key)}:</b></td><td style=\"padding:2px 0;\">{display_val if display_val else '<i>—</i>'}</td></tr>")

    table = f"<table>{''.join(rows_html)}</table>" if rows_html else "<p><i>No contact fields</i></p>"

    body = f"""
    <h3>Callback Reminder</h3>
    {notes_line}
    <h4>Contact details</h4>
    {table}
    """
    return subject, body


def _html_escape(s: str) -> str:
    """Escape for HTML content."""
    if not isinstance(s, str):
        s = str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _format_contact_value(val) -> str:
    """Format a contact_data value for HTML (list -> comma-separated, else string)."""
    if val is None:
        return ""
    if isinstance(val, list):
        parts = []
        for item in val:
            if item is not None and str(item).strip():
                parts.append(str(item).strip())
        return _html_escape(", ".join(parts)) if parts else ""
    if isinstance(val, dict):
        return _html_escape(json.dumps(val))
    s = str(val).strip()
    return _html_escape(s) if s else ""


# Map simplified timezones to Windows Timezone IDs required by Graph API
TZ_MAPPING = {
    "ET": "Eastern Standard Time",
    "EST": "Eastern Standard Time",
    "EDT": "Eastern Standard Time",
    "CT": "Central Standard Time",
    "CST": "Central Standard Time",
    "CDT": "Central Standard Time",
    "MT": "Mountain Standard Time",
    "MST": "Mountain Standard Time",
    "MDT": "Mountain Standard Time",
    "PT": "Pacific Standard Time",
    "PST": "Pacific Standard Time",
    "PDT": "Pacific Standard Time",
    "UTC": "UTC"
}


class OutlookManager:
    @staticmethod
    def format_callback_event(contact_data_dict, notes="", source_workbook_path=None, source_sheet_name=None):
        """Build Outlook event subject and body from contact metadata. Returns (subject, body_html)."""
        return _format_callback_event(contact_data_dict, notes, source_workbook_path, source_sheet_name)

    def __init__(self):
        if not all([CLIENT_ID, TENANT_ID, CLIENT_SECRET, ORGANIZER_EMAIL]):
            print("Outlook Manager: Missing Azure credentials. Set AZURE_CLIENT_ID, AZURE_TENANT_ID, "
                  "AZURE_CLIENT_SECRET, AZURE_ORGANIZER_EMAIL in .env or environment.")
            self.app = None
            return
        self.app = msal.ConfidentialClientApplication(
            CLIENT_ID, authority=AUTHORITY, client_credential=CLIENT_SECRET
        )

    def _get_token(self):
        if not self.app:
            return None
        # Look for token in cache first
        result = self.app.acquire_token_silent(SCOPES, account=None)
        if not result:
            # Request new token
            result = self.app.acquire_token_for_client(scopes=SCOPES)
        
        if "access_token" in result:
            return result["access_token"]
        else:
            print(f"Graph API Auth Error: {result.get('error_description')}")
            return None

    def create_event(self, subject, body, start_time_iso, attendee_email, timezone_code="ET", contact_emails=None):
        """Creates a calendar event on the Organizer's calendar and invites the Agent."""
        if not self.app:
            return False
        if not attendee_email:
            print("No attendee email provided for Outlook invite.")
            return False

        token = self._get_token()
        if not token:
            return False

        # Calculate End Time (Start + 15 mins default)
        try:
            # Use fromisoformat which handles various ISO formats including timezone
            # Remove timezone info if present (Graph API uses timeZone field instead)
            clean_iso = start_time_iso
            if "+" in clean_iso or clean_iso.endswith("Z"):
                # Has timezone info, remove it
                if "+" in clean_iso:
                    clean_iso = clean_iso.split("+")[0]
                elif clean_iso.endswith("Z"):
                    clean_iso = clean_iso[:-1]
            
            # Parse the datetime
            if "." in clean_iso:
                # Has microseconds
                start_dt = datetime.datetime.fromisoformat(clean_iso)
            else:
                # No microseconds
                start_dt = datetime.datetime.strptime(clean_iso, "%Y-%m-%dT%H:%M:%S")
            
            end_dt = start_dt + datetime.timedelta(minutes=15)
            # Format back to ISO without timezone (Graph API uses timeZone field)
            end_time_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
            # Ensure start_time_iso is also clean
            start_time_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
        except Exception as e:
            print(f"Date parsing error in OutlookManager: {e}")
            return False

        ms_timezone = TZ_MAPPING.get(timezone_code, "Eastern Standard Time")

        event_payload = {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": f"{body}<br><br><i>Generated by TeamsPowerDialer System</i>"
            },
            "start": {
                "dateTime": start_time_iso,
                "timeZone": ms_timezone
            },
            "end": {
                "dateTime": end_time_iso,
                "timeZone": ms_timezone
            },
            "attendees": [
                {
                    "emailAddress": {
                        "address": attendee_email,
                        "name": "Agent"
                    },
                    "type": "required"
                }
            ],
            "allowNewTimeProposals": True,
            "isOnlineMeeting": False
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(ENDPOINT, headers=headers, json=event_payload)
            if response.status_code in [200, 201, 202]:
                print(f"Outlook invite sent to {attendee_email} for {start_time_iso}")
                return True
            else:
                print(f"Graph API error: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"Outlook request error: {e}")
            return False

