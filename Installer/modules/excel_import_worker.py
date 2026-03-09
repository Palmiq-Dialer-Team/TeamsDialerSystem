"""Background Excel import worker to keep the UI responsive."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pythoncom
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from .active_data_reader import ActiveExcelReader


class ExcelImportWorker(QObject):
    """Load Excel contacts on a worker thread and emit structured results."""

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()

    @pyqtSlot(object, bool)
    def import_contacts(self, sheet_name: Optional[str] = None, visible_only: bool = False) -> None:
        pythoncom.CoInitialize()
        try:
            reader = ActiveExcelReader()
            df, _ = reader.load_data_shadow_copy(sheet_name=sheet_name, visible_only=visible_only)
            active_sheet_name = reader.active_sheet_name or sheet_name
            contacts = reader.process_contacts(df, sheet_name=active_sheet_name)
            result: Dict[str, Any] = {
                "contacts": contacts,
                "sheet_name": active_sheet_name,
                "row_count": len(df.index) if hasattr(df, "index") else 0,
            }
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            pythoncom.CoUninitialize()
