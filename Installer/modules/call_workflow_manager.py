"""Workflow manager that owns installer call-session state and transitions."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .call_session import CallSession
from .call_state import CallSessionState, LEGACY_STATUS_TO_STATE


class CallWorkflowManager:
    """Owns the active call session and exposes explicit state transitions."""

    def __init__(self) -> None:
        self.session = CallSession()

    def set_state(self, state: CallSessionState, reason: str = "", status_text: Optional[str] = None) -> None:
        self.session.state = state
        self.session.state_reason = reason
        if status_text is not None:
            self.session.status_text = status_text

    def set_legacy_status(self, status: str) -> None:
        state = LEGACY_STATUS_TO_STATE.get(status, CallSessionState.ERROR)
        self.set_state(state, status_text=status)

    def begin_contact(self, contact: Optional[Dict[str, Any]], phone_index: int = 0) -> None:
        self.session.contact = contact
        self.session.phone_index = phone_index
        self.session.has_no_response_history = False
        self.session.pending_force_hangup_disposition = None

    def begin_call(self, contact: Optional[Dict[str, Any]], phone_index: int = 0) -> None:
        self.session.reset_for_new_call()
        self.session.contact = contact
        self.session.phone_index = phone_index
        self.set_state(CallSessionState.DIALING, status_text="Dialing")

    def mark_call_started(self) -> None:
        self.set_state(CallSessionState.CALLING, status_text="Calling")

    def mark_call_connected(self, ai_monitoring: bool = False) -> None:
        state = CallSessionState.AMD_MONITORING if ai_monitoring else CallSessionState.CONNECTED
        self.set_state(state, status_text="Connected")

    def mark_human_confirmed(self) -> None:
        self.session.confirmed_human = True
        self.set_state(CallSessionState.HUMAN_CONFIRMED, status_text="Connected")

    def mark_waiting_for_disposition(self, should_resume_autocall: bool) -> None:
        self.session.waiting_for_disposition = True
        self.session.autocall_resume_after_disposition = should_resume_autocall
        self.set_state(CallSessionState.DISPOSITION_PENDING, status_text="Call Ended")

    def mark_hanging_up(self, disposition: Optional[str] = None) -> None:
        self.session.bot_initiated_hangup = True
        self.session.pending_force_hangup_disposition = disposition
        self.set_state(CallSessionState.HANGING_UP, status_text="Call Ended")

    def mark_ended(self) -> None:
        self.set_state(CallSessionState.ENDED, status_text="Call Ended")

    def mark_ready(self) -> None:
        self.set_state(CallSessionState.READY, status_text="Ready")

    def mark_error(self, reason: str) -> None:
        self.set_state(CallSessionState.ERROR, reason=reason, status_text="Error")

    def set_autocall_active(self, is_active: bool) -> None:
        self.session.autocall_active = is_active
        if is_active and self.session.state in (CallSessionState.READY, CallSessionState.IDLE):
            self.set_state(CallSessionState.AUTO_ADVANCING, status_text="Ready")

    def clear_waiting_for_disposition(self) -> None:
        self.session.waiting_for_disposition = False
        self.session.autocall_resume_after_disposition = False
        if self.session.state == CallSessionState.DISPOSITION_PENDING:
            self.mark_ready()

    def sync_from_backend_message(self, message: Dict[str, Any]) -> None:
        """Apply backend acks that affect the session identity."""
        if not isinstance(message, dict):
            return
        message_type = str(message.get("type") or "")
        if message_type == "call_logged":
            call_id = message.get("call_id")
            if call_id:
                self.session.call_id = str(call_id)
                self.session.call_record_created = True
