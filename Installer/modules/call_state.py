"""Call-session state definitions for the installer workflow."""

from __future__ import annotations

from enum import Enum


class CallSessionState(str, Enum):
    """Authoritative lifecycle states for the active call session."""

    IDLE = "idle"
    READY = "ready"
    DIALING = "dialing"
    CALLING = "calling"
    CONNECTED = "connected"
    AMD_MONITORING = "amd_monitoring"
    HUMAN_CONFIRMED = "human_confirmed"
    HANGING_UP = "hanging_up"
    DISPOSITION_PENDING = "disposition_pending"
    AUTO_ADVANCING = "auto_advancing"
    ENDED = "ended"
    ERROR = "error"


LEGACY_STATUS_TO_STATE = {
    "Ready": CallSessionState.READY,
    "Dialing": CallSessionState.DIALING,
    "Calling": CallSessionState.CALLING,
    "Connected": CallSessionState.CONNECTED,
    "Call Ended": CallSessionState.ENDED,
}
