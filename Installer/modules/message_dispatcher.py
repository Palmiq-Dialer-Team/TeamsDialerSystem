"""Qt-safe dispatcher for inbound websocket messages."""

from __future__ import annotations

from typing import Any, Dict

from PyQt6.QtCore import QObject, pyqtSignal


class QtMessageDispatcher(QObject):
    """Re-emits websocket messages onto the Qt thread."""

    message_received = pyqtSignal(dict)
    overdue_callbacks_received = pyqtSignal(list)
    callback_prompt_received = pyqtSignal(dict)
    callback_created = pyqtSignal(dict)
    callback_updated = pyqtSignal(dict)
    callback_cancelled = pyqtSignal(dict)
    callback_logged = pyqtSignal(dict)
    force_hangup_received = pyqtSignal(dict)
    ai_unavailable_received = pyqtSignal(dict)
    human_detected_received = pyqtSignal(dict)
    announcement_received = pyqtSignal(dict)
    maintenance_warning_received = pyqtSignal(dict)
    maintenance_shutdown_received = pyqtSignal(dict)
    audio_ack_received = pyqtSignal(dict)
    call_logged = pyqtSignal(dict)

    def dispatch(self, message: Dict[str, Any]) -> None:
        """Emit the generic and specific Qt signals for a message."""
        if not isinstance(message, dict):
            return
        self.message_received.emit(message)
        message_type = str(message.get("type") or "")

        if message_type == "overdue_callbacks_list":
            self.overdue_callbacks_received.emit(message.get("callbacks", []))
        elif message_type == "show_callback_prompt":
            self.callback_prompt_received.emit(message)
        elif message_type == "callback_created":
            self.callback_created.emit(message)
        elif message_type == "callback_updated":
            self.callback_updated.emit(message)
        elif message_type == "callback_cancelled":
            self.callback_cancelled.emit(message)
        elif message_type == "callback_logged":
            self.callback_logged.emit(message)
        elif message_type == "force_hangup":
            self.force_hangup_received.emit(message)
        elif message_type == "ai_unavailable":
            self.ai_unavailable_received.emit(message)
        elif message_type == "human_detected":
            self.human_detected_received.emit(message)
        elif message_type == "announcement":
            self.announcement_received.emit(message)
        elif message_type == "maintenance_warning":
            self.maintenance_warning_received.emit(message)
        elif message_type == "maintenance_shutdown":
            self.maintenance_shutdown_received.emit(message)
        elif message_type == "audio_ack":
            self.audio_ack_received.emit(message)
        elif message_type == "call_logged":
            self.call_logged.emit(message)
