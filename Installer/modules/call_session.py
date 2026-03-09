"""Per-call state container used by the installer workflow manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .call_state import CallSessionState


@dataclass
class CallSession:
    """Single source of truth for runtime call state."""

    state: CallSessionState = CallSessionState.READY
    state_reason: str = ""
    status_text: str = "Ready"
    call_id: Optional[str] = None
    contact: Optional[Dict[str, Any]] = None
    phone_index: int = 0
    using_local_ai: bool = False
    timer_extended: bool = False
    no_answer_handling: bool = False
    bot_initiated_hangup: bool = False
    confirmed_human: bool = False
    waiting_for_disposition: bool = False
    autocall_active: bool = False
    autocall_resume_after_disposition: bool = False
    has_no_response_history: bool = False
    call_record_created: bool = False
    is_timer_synced: bool = False
    current_ui_state: str = "OFF"
    pending_force_hangup_disposition: Optional[str] = None
    last_transcript: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def reset_for_new_contact(self) -> None:
        """Reset only the per-contact state when changing contacts."""
        self.contact = None
        self.phone_index = 0
        self.has_no_response_history = False
        self.pending_force_hangup_disposition = None

    def reset_for_new_call(self) -> None:
        """Reset fields that should not survive across call attempts."""
        self.call_id = None
        self.using_local_ai = False
        self.timer_extended = False
        self.no_answer_handling = False
        self.bot_initiated_hangup = False
        self.confirmed_human = False
        self.waiting_for_disposition = False
        self.autocall_resume_after_disposition = False
        self.call_record_created = False
        self.is_timer_synced = False
        self.pending_force_hangup_disposition = None
        self.last_transcript = ""
        self.metadata.clear()
