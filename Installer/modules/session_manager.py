"""
Session Manager
Handles per-user session persistence and per-workbook history.
"""

import json
import os
import getpass
from datetime import datetime
from pathlib import Path

CLIENT_APP_DIR = Path(__file__).parent.parent
SESSION_FILE = CLIENT_APP_DIR / "session_history.json"


class SessionManager:
    def __init__(self):
        self.user_id = getpass.getuser()
        self.history = self._load_history()

    def _load_history(self):
        if not SESSION_FILE.exists():
            return {}
        try:
            with open(SESSION_FILE, "r") as f:
                data = json.load(f)
                return data
        except Exception:
            return {}

    def _save_history(self):
        try:
            with open(SESSION_FILE, "w") as f:
                json.dump(self.history, f, indent=4)
        except Exception as e:
            print(f"Error saving session history: {e}")

    def save_session(self, workbook_path, current_index, sheet_name=None, active_sorts=None):
        """
        Updates:
        1. The global 'last_active' session (for app startup).
        2. The specific history for this workbook path (for switching files).
        3. Saves active sort criteria (Filters & Sort).
        """
        if not workbook_path:
            return

        if self.user_id not in self.history:
            self.history[self.user_id] = {}

        if "last_active" not in self.history[self.user_id]:
            if "workbook_path" in self.history[self.user_id]:
                old_data = self.history[self.user_id]
                self.history[self.user_id] = {
                    "last_active": old_data,
                    "file_history": {}
                }
                if "workbook_path" in old_data:
                    abs_path = os.path.abspath(old_data["workbook_path"])
                    self.history[self.user_id]["file_history"][abs_path] = old_data
            else:
                self.history[self.user_id] = {"last_active": {}, "file_history": {}}

        timestamp = datetime.now().isoformat()
        session_data = {
            "workbook_path": workbook_path,
            "current_index": current_index,
            "sheet_name": sheet_name,
            "timestamp": timestamp,
            "active_sorts": active_sorts
        }

        # 1. Update Global Last Active
        self.history[self.user_id]["last_active"] = session_data

        # 2. Update Specific File History
        # Use absolute path as key to ensure uniqueness
        abs_path = os.path.abspath(workbook_path)
        if "file_history" not in self.history[self.user_id]:
            self.history[self.user_id]["file_history"] = {}
        
        self.history[self.user_id]["file_history"][abs_path] = session_data

        self._save_history()

    def get_last_session(self):
        """Returns the last active session (for startup)."""
        user_data = self.history.get(self.user_id, {})
        # Handle new structure vs old structure
        if "last_active" in user_data:
            return user_data["last_active"]
        # Fallback for old structure (direct dict)
        if "workbook_path" in user_data:
            return user_data
        return None

    def get_workbook_history(self, workbook_path):
        """Returns the saved state for a specific workbook path."""
        if not workbook_path:
            return None
            
        user_data = self.history.get(self.user_id, {})
        file_history = user_data.get("file_history", {})
        
        abs_path = os.path.abspath(workbook_path)
        return file_history.get(abs_path)

    def clear_session(self):
        """Clears the session for the current user."""
        if self.user_id in self.history:
            del self.history[self.user_id]
            self._save_history()
