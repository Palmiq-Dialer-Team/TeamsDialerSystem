"""Control panel configuration."""
import os
import sys
from pathlib import Path

# Paths (when frozen, exe is typically in Backend/dist/ - use Backend as root)
if getattr(sys, "frozen", False):
    exe_dir = Path(sys.executable).parent
    # If exe is in dist/, Backend is the parent of dist
    if exe_dir.name.lower() == "dist":
        BACKEND_DIR = exe_dir.parent
    else:
        BACKEND_DIR = exe_dir
else:
    BACKEND_DIR = Path(__file__).resolve().parent.parent
VENV_PYTHON = BACKEND_DIR / "venv" / "Scripts" / "python.exe"
MAIN_MODULE = "main:app"
# Use same TEAMS_DIALER_DB as server so snapshot viewer sees the DB the server actually writes to
DB_PATH = Path(os.environ["TEAMS_DIALER_DB"]).resolve() if os.environ.get("TEAMS_DIALER_DB") else (BACKEND_DIR / "teams_dialer.db").resolve()
SNAPSHOT_DIR = BACKEND_DIR / "db_snapshots"

# Server defaults (direct uvicorn)
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 80
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "").strip()

# Dashboard URL (for "Open in Browser")
DASHBOARD_URL = "http://127.0.0.1"
# Alternative for network access - uses local IP if available
try:
    import socket
    hostname = socket.gethostname()
    local_ips = socket.gethostbyname_ex(hostname)[2]
    network_ip = next((ip for ip in local_ips if ip.startswith(("192.168.", "10.", "172."))), None)
    if network_ip:
        port_suffix = "" if SERVER_PORT == 80 else f":{SERVER_PORT}"
        DASHBOARD_URL_NETWORK = f"http://{network_ip}{port_suffix}"
    else:
        DASHBOARD_URL_NETWORK = DASHBOARD_URL
except Exception:
    DASHBOARD_URL_NETWORK = DASHBOARD_URL

# Uvicorn args
UVICORN_ARGS = [
    "-m", "uvicorn", MAIN_MODULE,
    "--host", SERVER_HOST,
    "--port", str(SERVER_PORT),
    "--ws", "websockets",
    "--timeout-keep-alive", "75",
    "--workers", "1",
]


def build_auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if INTERNAL_API_TOKEN:
        headers["Authorization"] = f"Bearer {INTERNAL_API_TOKEN}"
    return headers
