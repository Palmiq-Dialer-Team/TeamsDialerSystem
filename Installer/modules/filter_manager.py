from datetime import datetime, time
import pytz
import re


class FilterManager:
    """
    Manages sort prioritization: selected values per category are brought to the top.
    No exclusion; all rows stay in the list. Time window is for prioritization only.
    """
    def __init__(self):
        self.active_sorts = {}
        self.sort_az = {}  # category -> True means sort that column A–Z (mutually exclusive with active_sorts for that category)

        self.time_filter = {
            "enabled": False,
            "start": time(9, 0),
            "end": time(17, 0),
            "my_timezone": "ET",
        }

    def set_sort(self, category, selected_values):
        """Set selected values for a category (rows with these values go to top). Empty = no sort for that category."""
        self.active_sorts[category] = selected_values

    def set_sort_az(self, category: str, enabled: bool):
        """Set whether to sort this category's column A–Z (enabled = no selection order, just alphabetical)."""
        self.sort_az[category] = enabled

    def set_time_filter(self, enabled, start_hour, end_hour, my_tz_str):
        self.time_filter = {
            "enabled": enabled,
            "start": time(start_hour, 0),
            "end": time(end_hour, 0),
            "my_timezone": my_tz_str,
        }

    def in_time_window(self, contact_tz_abbr):
        """Returns True if current time is in the contact's local time window. Used for optional prioritization."""
        tz_map = {
            "ET": "US/Eastern", "EST": "US/Eastern", "EDT": "US/Eastern",
            "CT": "US/Central", "CST": "US/Central", "CDT": "US/Central",
            "MT": "US/Mountain", "MST": "US/Mountain", "MDT": "US/Mountain",
            "PT": "US/Pacific", "PST": "US/Pacific", "PDT": "US/Pacific",
        }
        clean_abbr = (contact_tz_abbr or "").replace("(", "").replace(")", "").strip().upper()
        target_tz_name = tz_map.get(clean_abbr)
        if not target_tz_name:
            return True
        try:
            utc_now = datetime.now(pytz.utc)
            target_tz = pytz.timezone(target_tz_name)
            target_now = utc_now.astimezone(target_tz).time()
            return self.time_filter["start"] <= target_now <= self.time_filter["end"]
        except Exception:
            return True
