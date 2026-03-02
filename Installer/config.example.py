"""Example configuration - copy to config.py and adjust for your environment."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent


def _detect_onedrive_path():
    """Detect OneDrive folder (prefer business over personal)."""
    user_home = Path.home()
    for item in user_home.iterdir():
        if item.is_dir() and item.name.startswith("OneDrive - "):
            return str(item)
    personal_path = user_home / "OneDrive"
    return str(personal_path)


EXCEL_FILE_PATH = os.path.expanduser("~/OneDrive/leads.xlsx")
SHEET_NAME = "Leads"
ONEDRIVE_BASE_PATH = _detect_onedrive_path()

TEAMS_PROCESS_NAME = "ms-teams.exe"
CALL_BUTTON_TIMEOUT = 10

SAMPLE_RATE = 48000
CHANNELS = 2
AUDIO_FORMAT = "int16"
SYSTEM_AUDIO_MULTIPLIER = 6.0

# Server URLs - change to your server's hostname or IP
SERVER_URL = "ws://YOUR_SERVER_IP/ws"
SERVER_API_URL = "http://YOUR_SERVER_IP"

UPDATE_INTERVAL = 100
