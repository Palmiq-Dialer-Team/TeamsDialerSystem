"""
Settings Manager
Handles loading and saving user configuration in a centralized JSON file
"""

import json
import os
from pathlib import Path

CLIENT_APP_DIR = Path(__file__).parent.parent
CONFIG_FILE = CLIENT_APP_DIR / "user_settings.json"

DEFAULT_SETTINGS = {
    "audio": {
        "mic_index": None,
        "loopback_index": None,
        "remember_audio_devices": False,
        "system_audio_volume": 6.0,
    },
    "colors": {
        "Booked Meeting": "#C6EFCE",      # Pastel Green
        "Sent Info": "#B3E5FC",           # Pastel Blue
        "Call Back Later": "#FFEB9C",     # Pastel Yellow-Orange
        "Voicemail": "#E1BEE7",           # Pastel Purple (auto-detected)
        "Voicemail (Verified)": "#E1BEE7",# Pastel Purple (manual)
        "No Response": "#F2F2F2",         # Very Light Gray
        "Not Interested": "#FFCCBC",      # Pastel Peach
        "Wrong Number": "#FFCDD2",        # Pastel Red
        "Invalid Contact": "#EF9A9A"      # Pastel Darker Red
    },
    "display_order": [
        "Email", "Title", "Company", "Website",
        "Industry", "Revenue", "State", "Timezone", "LinkedIn",
    ],
    "hidden_fields": [],
    "phone_priority": {
        "order": ["Mobile", "Direct", "Company"],
        "disabled": [],
    },
    "time_filter": {
        "enabled": False,
        "start_hour": 9,
        "end_hour": 17,
        "my_timezone": "ET",
    },
    "calling": {
        "auto_call_enabled": True,  # Enable call monitoring and auto-advance on timeout
        "auto_call_on_disposition": False,  # Auto-call next number after No Response (same contact only)
    },
    "reminders": {
        "enabled": False,
        "agent_email": ""
    },
    "livekit": {
        "use_livekit": False,
    },
    "excel": {
        "autosave_interval_minutes": 10,  # 0 = disabled, 5/10/15 options
    },
}

class SettingsManager:
    def __init__(self):
        self.settings = self.load_settings()

    def load_settings(self):
        if not CONFIG_FILE.exists():
            return DEFAULT_SETTINGS.copy()
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                merged = DEFAULT_SETTINGS.copy()
                merged.update(data)
                # Backward compatibility: if user had saved devices but no remember flag, assume remember=True
                audio = merged.get("audio", {})
                if "remember_audio_devices" not in audio and audio.get("mic_index") is not None and audio.get("loopback_index") is not None:
                    merged.setdefault("audio", {})["remember_audio_devices"] = True
                return merged
        except Exception as e:
            print(f"Error loading settings: {e}")
            return DEFAULT_SETTINGS.copy()

    def save_settings(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.settings, f, indent=4)
            print("Settings saved.")
        except Exception as e:
            print(f"Error saving settings: {e}")

    # Helper methods for specific sections
    def get_audio_settings(self):
        return self.settings.get("audio", {})

    def set_audio_settings(self, mic_idx, loop_idx, volume=None, remember=None):
        if remember is not None:
            self.settings["audio"]["remember_audio_devices"] = remember
        if remember is False:
            self.settings["audio"]["mic_index"] = None
            self.settings["audio"]["loopback_index"] = None
        else:
            self.settings["audio"]["mic_index"] = mic_idx
            self.settings["audio"]["loopback_index"] = loop_idx
        if volume is not None:
            self.settings["audio"]["system_audio_volume"] = volume
        self.save_settings()

    def get_colors(self):
        """Returns the dictionary of disposition colors."""
        return self.settings.get("colors", DEFAULT_SETTINGS["colors"]).copy()

    def set_colors(self, colors_dict):
        """Updates the disposition colors."""
        self.settings["colors"] = colors_dict
        self.save_settings()

    def get_display_order(self):
        """Returns the list of fields in the user's preferred order."""
        return self.settings.get("display_order", DEFAULT_SETTINGS["display_order"]).copy()

    def get_hidden_fields(self):
        """Returns the list of fields the user wants to hide."""
        return self.settings.get("hidden_fields", DEFAULT_SETTINGS["hidden_fields"]).copy()

    def set_display_preferences(self, order_list, hidden_list):
        """Updates display order and hidden fields."""
        self.settings["display_order"] = order_list
        self.settings["hidden_fields"] = hidden_list
        self.save_settings()

    def get_phone_priority(self):
        """Returns the phone priority settings."""
        return self.settings.get("phone_priority", DEFAULT_SETTINGS["phone_priority"]).copy()

    def set_phone_priority(self, order_list, disabled_list):
        """Updates phone priority and disabled types."""
        self.settings["phone_priority"] = {
            "order": order_list,
            "disabled": disabled_list
        }
        self.save_settings()

    def get_time_filter_settings(self):
        """Returns the saved time filter settings."""
        return self.settings.get("time_filter", DEFAULT_SETTINGS["time_filter"]).copy()

    def set_time_filter_settings(self, enabled, start, end, timezone):
        """Updates and persists time filter settings (working hours / timezone)."""
        self.settings["time_filter"] = {
            "enabled": bool(enabled),
            "start_hour": int(start),
            "end_hour": int(end),
            "my_timezone": str(timezone),
        }
        self.save_settings()

    def get_calling_settings(self):
        """Returns the calling settings (auto-call, etc.)."""
        return self.settings.get("calling", DEFAULT_SETTINGS["calling"]).copy()

    def set_calling_settings(self, auto_call_enabled, auto_call_on_disposition=None):
        """Updates calling settings."""
        self.settings.setdefault("calling", {})
        self.settings["calling"]["auto_call_enabled"] = bool(auto_call_enabled)
        if auto_call_on_disposition is not None:
            self.settings["calling"]["auto_call_on_disposition"] = bool(auto_call_on_disposition)
        self.save_settings()

    def get_reminder_settings(self):
        """Returns the reminder settings (Outlook calendar invites)."""
        return self.settings.get("reminders", DEFAULT_SETTINGS["reminders"]).copy()

    def set_reminder_settings(self, enabled, email):
        """Updates reminder settings."""
        self.settings["reminders"] = {
            "enabled": bool(enabled),
            "agent_email": str(email).strip()
        }
        self.save_settings()

    def get_livekit_settings(self):
        """Returns LiveKit settings (use_livekit toggle)."""
        return self.settings.get("livekit", DEFAULT_SETTINGS["livekit"]).copy()

    def set_livekit_settings(self, use_livekit):
        """Updates LiveKit setting."""
        self.settings.setdefault("livekit", {})["use_livekit"] = bool(use_livekit)
        self.save_settings()

    def get_excel_settings(self):
        """Returns Excel settings (autosave interval, etc.)."""
        return self.settings.get("excel", DEFAULT_SETTINGS["excel"]).copy()

    def set_excel_settings(self, autosave_interval_minutes):
        """Updates Excel autosave interval (0=disabled)."""
        self.settings.setdefault("excel", {})["autosave_interval_minutes"] = int(autosave_interval_minutes)
        self.save_settings()
