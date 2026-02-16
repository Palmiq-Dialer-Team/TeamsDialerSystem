"""Paths and settings."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
CLIENT_DIR = BASE_DIR / "Client_App"
ASSETS_DIR = CLIENT_DIR / "assets"


def _detect_onedrive_path():
    """Detect OneDrive folder (prefer business over personal)."""
    user_home = Path.home()
    
    business_patterns = [
        "OneDrive - Palmiq",  # Specific company folder
    ]
    
    for pattern in business_patterns:
        business_path = user_home / pattern
        if business_path.exists() and business_path.is_dir():
            print(f"Detected business OneDrive folder: {business_path}")
            return str(business_path)
    
    for item in user_home.iterdir():
        if item.is_dir() and item.name.startswith("OneDrive - "):
            print(f"Detected business OneDrive folder: {item}")
            return str(item)
    
    personal_path = user_home / "OneDrive"
    if personal_path.exists() and personal_path.is_dir():
        print(f"Using personal OneDrive folder: {personal_path}")
        return str(personal_path)
    
    default_path = user_home / "OneDrive"
    print(f"Warning: OneDrive folder not found, using default: {default_path}")
    return str(default_path)


EXCEL_FILE_PATH = os.path.expanduser("~/OneDrive/leads.xlsx")
SHEET_NAME = "Leads"  # Default sheet name
ONEDRIVE_BASE_PATH = _detect_onedrive_path()

TEAMS_PROCESS_NAME = "ms-teams.exe"
CALL_BUTTON_TIMEOUT = 10

SAMPLE_RATE = 48000
CHANNELS = 2
AUDIO_FORMAT = "int16"
SYSTEM_AUDIO_MULTIPLIER = 6.0

SERVER_URL = "ws://192.168.1.46/ws"
SERVER_API_URL = "http://192.168.1.46"

# LiveKit server IP/port (UDP in LiveKit config.yaml)
USE_LIVEKIT = False
LIVEKIT_URL = "ws://192.168.1.46:81"

UPDATE_INTERVAL = 100

