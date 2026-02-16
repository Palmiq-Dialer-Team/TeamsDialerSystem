# Test Client Launcher - Update Checker & App Starter
import requests
import json
import os
import zipfile
import subprocess
import sys

UPDATE_URL = os.environ.get("TEST_UPDATE_URL", "http://192.168.1.46")
CURRENT_VERSION_FILE = "local_version.json"
APP_ENTRY_POINT = "main.py"

PROTECTED_FILES = [
    "user_settings.json",
]


def load_local_version():
    if os.path.exists(CURRENT_VERSION_FILE):
        try:
            with open(CURRENT_VERSION_FILE, "r") as f:
                return json.load(f).get("version", 0.0)
        except Exception:
            pass
    return 0.0


def check_for_updates():
    print("Checking for tester updates...")
    current = load_local_version()
    try:
        resp = requests.get(f"{UPDATE_URL}/tester_version.json", timeout=8)
        if resp.status_code != 200:
            print("Server unavailable. Skipping update.")
            return
        server_ver = resp.json().get("version", 0.0)
        if server_ver > current:
            print(f"Update found: {server_ver} (Current: {current})")
            print("Downloading...")
            r = requests.get(f"{UPDATE_URL}/tester_package.zip", stream=True)
            with open("update.zip", "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            print("Installing...")
            with zipfile.ZipFile("update.zip", "r") as z:
                for file in z.namelist():
                    if file in PROTECTED_FILES and os.path.exists(file):
                        print(f"Skipping protected: {file}")
                        continue
                    z.extract(file, ".")
            with open(CURRENT_VERSION_FILE, "w") as f:
                json.dump({"version": server_ver}, f)
            os.remove("update.zip")
            print("Update complete!")
            subprocess.call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    except Exception as e:
        print(f"Update check failed: {e}")


if __name__ == "__main__":
    check_for_updates()
    print("Starting Test Client...")
    subprocess.Popen([sys.executable, APP_ENTRY_POINT])
    sys.exit()
