# Update checker + app starter
import requests
import json
import os
import zipfile
import subprocess
import sys
import time

UPDATE_URL = "http://192.168.1.46" 
CURRENT_VERSION_FILE = "local_version.json"
APP_ENTRY_POINT = "main.py"

PROTECTED_FILES = [
    "user_settings.json",
    "session_history.json",
    "teams_dialer.db",
    "client_history.db"
]

def _parse_version_tuple(raw) -> tuple[int, int, int]:
    """
    Convert raw version (float/int/str) to a (major, minor, patch) tuple.
    Supports:
      - legacy float style: 1.92 => (1, 9, 2)
      - '1.9.2' string style => (1, 9, 2)
      - '1.92' string style  => (1, 9, 2)
    """
    try:
        s = str(raw).strip()
        if not s:
            return (0, 0, 0)
        parts = s.split(".")
        # New style: x.y.z
        if len(parts) == 3:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
        # Fallback: x.yy -> map yy -> minor, patch (e.g. 1.92 -> 1.9.2)
        if len(parts) == 2:
            major = int(parts[0])
            frac_digits = "".join(ch for ch in parts[1] if ch.isdigit())
            if not frac_digits:
                return (major, 0, 0)
            if len(frac_digits) == 1:
                minor = int(frac_digits[0])
                patch = 0
            else:
                minor = int(frac_digits[0])
                patch = int(frac_digits[1:])
            return (major, minor, patch)
    except Exception:
        pass
    return (0, 0, 0)


def load_local_version_raw():
    if os.path.exists(CURRENT_VERSION_FILE):
        try:
            with open(CURRENT_VERSION_FILE, "r") as f:
                return json.load(f).get("version", "0.0.0")
        except Exception:
            pass
    return "0.0.0"

def check_for_updates():
    print("Checking for updates...")
    current_raw = load_local_version_raw()
    current = _parse_version_tuple(current_raw)
    
    try:
        resp = requests.get(f"{UPDATE_URL}/newversion.json", timeout=8, headers={"Connection": "keep-alive"})
        if resp.status_code != 200:
            print("Server unavailable. Skipping update.")
            return

        server_raw = resp.json().get("version", "0.0.0")
        server_ver = _parse_version_tuple(server_raw)

        if server_ver > current:
            print(f"Update found: {server_raw} (Current: {current_raw})")
            print("Downloading...")
            
            r = requests.get(f"{UPDATE_URL}/client_package.zip", stream=True)
            with open("update.zip", "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            print("Installing...")
            with zipfile.ZipFile("update.zip", 'r') as z:
                for file in z.namelist():
                    if file in PROTECTED_FILES and os.path.exists(file):
                        print(f"Skipping protected config: {file}")
                        continue
                    z.extract(file, ".")
            
            with open(CURRENT_VERSION_FILE, "w") as f:
                json.dump({"version": server_raw}, f)
            
            os.remove("update.zip")
            print("Update Complete!")
            
            subprocess.call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    except Exception as e:
        print(f"Update check failed: {e}")

if __name__ == "__main__":
    check_for_updates()
    print("Starting App...")
    subprocess.Popen([sys.executable, APP_ENTRY_POINT])
    sys.exit()
