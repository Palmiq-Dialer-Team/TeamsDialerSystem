"""Server process controller: start, stop, restart."""
import subprocess
import sys
import threading
import queue
import time
import urllib.request
from pathlib import Path
from typing import Optional, Callable

from .config import BACKEND_DIR, VENV_PYTHON, UVICORN_ARGS, SERVER_PORT, build_auth_headers

SHUTDOWN_GRACEFUL_TIMEOUT = 30


def _is_port_in_use(port: int) -> bool:
    """Check if something is listening on the given port."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
        return True
    except (socket.error, OSError):
        return False


def _kill_process_on_port(port: int) -> bool:
    """Find and terminate the process using the given port (Windows)."""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
            timeout=5,
        )
        # Match LISTENING lines with :80 (not :8080) - require :80 followed by space
        for line in out.splitlines():
            if "LISTENING" not in line or f":{port} " not in line:
                continue
            parts = line.split()
            if len(parts) >= 5:
                pid = parts[-1]
                if pid.isdigit():
                    subprocess.run(
                        ["taskkill", "/PID", pid, "/F"],
                        capture_output=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        timeout=5,
                    )
                    return True
        return False
    except Exception:
        return False


class ServerController:
    def __init__(self, on_output: Optional[Callable[[str], None]] = None):
        self.process: Optional[subprocess.Popen] = None
        self._output_queue: queue.Queue = queue.Queue()
        self._on_output = on_output
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_reader = threading.Event()

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def is_server_on_port(self) -> bool:
        """True if something is listening on the server port (e.g. orphaned from previous session)."""
        return _is_port_in_use(SERVER_PORT)

    def start(self) -> bool:
        if self.is_running:
            return True
        if self.is_server_on_port():
            if self._on_output:
                self._on_output(
                    f"[CONTROL] Port {SERVER_PORT} is already in use. "
                    "Stop the existing server first (it may have been started by another control panel).\n"
                )
            return False
        try:
            # Never use sys.executable when frozen - it points to the exe, not Python
            if not VENV_PYTHON.exists():
                if self._on_output and getattr(sys, "frozen", False):
                    self._on_output(
                        f"[CONTROL] venv not found at {VENV_PYTHON}. "
                        "Place the exe in Backend\\dist\\ and ensure Backend has a venv.\n"
                    )
                return False
            python_exe = str(VENV_PYTHON)
            self.process = subprocess.Popen(
                [python_exe] + UVICORN_ARGS,
                cwd=str(BACKEND_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
            )
            self._stop_reader.clear()
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()
            return True
        except Exception as e:
            if self._on_output:
                self._on_output(f"[CONTROL] Failed to start server: {e}\n")
            return False

    def _read_output(self):
        if not self.process or not self.process.stdout:
            return
        try:
            for line in iter(self.process.stdout.readline, ""):
                if self._stop_reader.is_set():
                    break
                if line:
                    self._output_queue.put(line)
                    if self._on_output:
                        self._on_output(line)
        except (ValueError, OSError):
            pass

    def stop(self) -> bool:
        if self.process:
            self._stop_reader.set()
            # Try graceful shutdown via HTTP first (lets uvicorn drain connections)
            shutdown_url = f"http://127.0.0.1:{SERVER_PORT}/api/control/shutdown"
            try:
                req = urllib.request.Request(
                    shutdown_url,
                    data=b"",
                    method="POST",
                    headers=build_auth_headers(),
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass  # Server may already be down; proceed to terminate
            # Wait for process to exit (graceful shutdown)
            deadline = time.monotonic() + SHUTDOWN_GRACEFUL_TIMEOUT
            while time.monotonic() < deadline and self.process.poll() is None:
                time.sleep(0.5)
            # Force kill if still running
            if self.process.poll() is None:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
                except Exception:
                    pass
            self.process = None
            if self._reader_thread and self._reader_thread.is_alive():
                self._reader_thread.join(timeout=1)
            self._reader_thread = None
            return True
        # No process reference (orphaned) - try to kill whatever is on the port
        if self.is_server_on_port():
            return _kill_process_on_port(SERVER_PORT)
        return True

    def restart(self) -> bool:
        self.stop()
        import time
        time.sleep(1)
        return self.start()

    def get_output(self, timeout: float = 0.01) -> Optional[str]:
        try:
            return self._output_queue.get(timeout=timeout)
        except queue.Empty:
            return None
