"""
Shared AMD (Answering Machine Detection) controller for both WebSocket and LiveKit flows.
Processes audio and returns actions; caller is responsible for sending messages.
"""

import time
import asyncio
import numpy as np
from typing import Optional, Tuple, Any, Dict
from dataclasses import dataclass, field

RING_TIMEOUT = 30


@dataclass
class AMDSession:
    """Per-agent AMD session state."""
    agent_id: str
    amd_state: Any = None
    call_start_time: float = 0
    is_ringing: bool = False
    ai_available: bool = False
    amd_last_log_elapsed: int = -1


async def process_amd_audio(
    session: AMDSession,
    system_float: np.ndarray,
    stereo: Optional[np.ndarray],
    get_detector,
) -> Tuple[AMDSession, Optional[Dict]]:
    """
    Process one audio chunk through AMD. Caller sends the returned message if any.
    Returns (updated_session, message_dict or None).
    """
    if not session.is_ringing or session.amd_state is None:
        return session, None

    elapsed = time.time() - session.call_start_time
    elapsed_int = int(elapsed)

    if system_float is None:
        return session, None

    try:
        # Timeout check
        if elapsed > RING_TIMEOUT:
            if session.ai_available:
                return _clear_session(session), {"type": "force_hangup", "reason": "timeout"}
            return _clear_session(session), None

        result = session.amd_state.process_chunk(system_float, elapsed + session.call_start_time)

        if result is None:
            return session, None

        if result[0] == "human_detected":
            return _clear_session(session), {"type": "human_detected"}

        if result[0] == "force_hangup":
            reason = result[1]
            return _clear_session(session), {"type": "force_hangup", "reason": reason}

        if result[0] == "transcribe":
            _, buf, dur = result
            print(f"[AI] Agent {session.agent_id}: AMD → transcribe (Whisper)")
            try:
                from ai_core import analyze_buffered_audio
                detector = session.amd_state.detector
                action, reason = await asyncio.wait_for(
                    asyncio.to_thread(analyze_buffered_audio, detector, buf, dur),
                    timeout=10.0,
                )
                if action == "human_detected":
                    print(f"[AI] Agent {session.agent_id}: Whisper → human_detected")
                    return _clear_session(session), {"type": "human_detected"}
                if action == "continue":
                    session.amd_state.reset()
                    return session, None
                # force_hangup
                print(f"[AI] Agent {session.agent_id}: Whisper → force_hangup ({reason})")
                return _clear_session(session), {"type": "force_hangup", "reason": reason}
            except asyncio.TimeoutError:
                print(f"[AI] Agent {session.agent_id}: Whisper timeout")
                pass  # fail-safe: keep call
            except Exception as e:
                print(f"[AI] Agent {session.agent_id}: AMD/Whisper analysis error: {e}")
        return session, None
    except Exception as e:
        print(f"[AI] Agent {session.agent_id}: AMD buffer error: {e}")
    return session, None


def _clear_session(session: AMDSession) -> AMDSession:
    session.is_ringing = False
    session.amd_state = None
    session.amd_last_log_elapsed = -1
    return session


def start_amd_session(agent_id: str, get_detector) -> Optional[AMDSession]:
    """Create and return an AMDSession when call starts, or None if AMD unavailable."""
    detector, err = get_detector()
    if detector is None:
        return None
    try:
        from ai_core import AMDState
        amd_state = AMDState(detector)
        return AMDSession(
            agent_id=agent_id,
            amd_state=amd_state,
            call_start_time=time.time(),
            is_ringing=True,
            ai_available=True,
        )
    except Exception as e:
        print(f"[AI] Failed to create AMD session for {agent_id}: {e}")
        return None
