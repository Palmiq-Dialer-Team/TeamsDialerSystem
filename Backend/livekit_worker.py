"""
LiveKit backend worker: joins dispatch-room, subscribes to agent audio,
runs AMD on incoming frames, and sends force_hangup/human_detected via data.
"""

import asyncio
import json
import os
import sys
import time
import numpy as np
from typing import Dict, Optional


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:81")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
ROOM_NAME = "dispatch-room"
WORKER_IDENTITY = "livekit-amd-worker"


def _get_detector():
    """Import and call get_amd_detector from main."""
    from main import get_amd_detector
    return get_amd_detector()


def _generate_worker_token() -> str:
    """Generate token for the worker (subscribe-only listener)."""
    from livekit.api import AccessToken, VideoGrants
    token = AccessToken(api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
    token.with_identity(WORKER_IDENTITY).with_grants(
        VideoGrants(room_join=True, room=ROOM_NAME, can_publish=False, can_subscribe=True)
    )
    return token.to_jwt()


async def run_worker():
    """Connect to LiveKit room, subscribe to audio, run AMD."""
    from livekit import rtc
    from amd_controller import AMDSession, process_amd_audio, start_amd_session

    room = rtc.Room()
    sessions: Dict[str, AMDSession] = {}
    audio_tasks: Dict[str, asyncio.Task] = {}

    def on_data_received(data_packet: rtc.DataPacket):
        """Handle call_started, call_ended, metadata from agents."""
        try:
            msg = json.loads(data_packet.data.decode("utf-8"))
            agent_id = data_packet.participant.identity if data_packet.participant else None
            if not agent_id:
                return
            msg_type = msg.get("type")
            if msg_type == "metadata":
                # Forward metadata to dashboard
                try:
                    from main import manager, _app_loop
                    if _app_loop:
                        meta = msg.get("data", msg)
                        asyncio.run_coroutine_threadsafe(
                            manager.update_agent_metadata(agent_id, meta), _app_loop
                        )
                except Exception:
                    pass
            elif msg_type == "call_started":
                session = start_amd_session(agent_id, _get_detector)
                if session:
                    sessions[agent_id] = session
                    print(f"[LiveKit] Agent {agent_id}: AMD session started")
                else:
                    print(f"[LiveKit] Agent {agent_id}: AMD unavailable")
            elif msg_type == "call_ended" or msg.get("status") == "Call Ended":
                if agent_id in sessions:
                    del sessions[agent_id]
                if agent_id in audio_tasks:
                    audio_tasks[agent_id].cancel()
                    del audio_tasks[agent_id]
        except Exception as e:
            print(f"[LiveKit] Data handler error: {e}")

    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        """When agent publishes audio, start consuming frames for AMD."""
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        agent_id = participant.identity
        if agent_id in audio_tasks:
            return
        print(f"[LiveKit] Agent {agent_id}: streaming audio")
        audio_stream = rtc.AudioStream(track, sample_rate=48000, num_channels=2)
        task = asyncio.create_task(_consume_audio(agent_id, audio_stream, room, sessions))
        audio_tasks[agent_id] = task

    def on_track_unsubscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        agent_id = participant.identity
        if agent_id in audio_tasks:
            audio_tasks[agent_id].cancel()
            del audio_tasks[agent_id]
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                print(f"[LiveKit] Agent {agent_id}: stopped streaming audio")

    def on_participant_disconnected(participant: rtc.RemoteParticipant, *_):
        agent_id = participant.identity
        if agent_id in sessions:
            del sessions[agent_id]
        if agent_id in audio_tasks:
            audio_tasks[agent_id].cancel()
            del audio_tasks[agent_id]
        # Mark agent disconnected for dashboard
        try:
            from main import manager, _app_loop
            if _app_loop and agent_id in getattr(manager, "agent_metadata", {}):
                from datetime import datetime

                async def _mark_disconnected():
                    manager.agent_metadata[agent_id]["status"] = "disconnected"
                    manager.agent_metadata[agent_id]["last_seen"] = datetime.now().isoformat()
                    await manager.broadcast_to_admins({"type": "agent_disconnected", "agent_id": agent_id})

                asyncio.run_coroutine_threadsafe(_mark_disconnected(), _app_loop)
        except Exception as e:
            print(f"[LiveKit] Failed to mark agent disconnected: {e}")

    room.on("data_received", on_data_received)
    room.on("track_subscribed", on_track_subscribed)
    room.on("track_unsubscribed", on_track_unsubscribed)
    room.on("participant_disconnected", on_participant_disconnected)

    token = _generate_worker_token()
    try:
        await room.connect(LIVEKIT_URL, token)
        print(f"[LiveKit] Worker connected to {ROOM_NAME}")
    except Exception as e:
        print(f"[LiveKit] Worker failed to connect: {e}")
        return

    while room.isconnected():
        await asyncio.sleep(1)
    await room.disconnect()


def _relay_audio_to_dashboard(agent_id: str, audio_bytes: bytes):
    """Relay audio to dashboard (for Listen). Runs from worker thread."""
    try:
        from main import manager, _app_loop
        if _app_loop:
            asyncio.run_coroutine_threadsafe(
                manager.broadcast_audio_to_admin(agent_id, audio_bytes), _app_loop
            )
    except Exception as e:
        pass  # Silently ignore if manager/loop not ready


async def _consume_audio(
    agent_id: str,
    audio_stream: "rtc.AudioStream",
    room: "rtc.Room",
    sessions: Dict[str, "AMDSession"],
):
    """Consume audio frames: relay to dashboard, run AMD when session active."""
    from amd_controller import process_amd_audio

    try:
        async for event in audio_stream:
            frame = event.frame
            data = getattr(frame, "data", None) or bytes(frame)
            if not data:
                continue
            samples = np.frombuffer(data, dtype=np.int16)
            if len(samples) == 0:
                continue
            # Stereo: ch0=mic, ch1=loopback
            if len(samples) >= 2 and len(samples) % 2 == 0:
                stereo = samples.reshape(-1, 2)
                system_channel = stereo[:, 1]
                mono_mix = np.mean(stereo, axis=1).astype(np.int16)
                dashboard_audio = mono_mix.tobytes()
            else:
                system_channel = samples
                dashboard_audio = data

            # Relay to dashboard for Listen
            _relay_audio_to_dashboard(agent_id, dashboard_audio)

            # AMD when session active
            session = sessions.get(agent_id)
            if not session:
                continue
            system_float = system_channel.astype(np.float32) / 32768.0

            session, msg = await process_amd_audio(
                session, system_float, None, _get_detector
            )
            if agent_id in sessions:
                sessions[agent_id] = session
            if msg:
                msg_type = msg.get("type", "")
                reason = msg.get("reason", "")
                print(f"[LiveKit] Agent {agent_id}: AMD → {msg_type}" + (f" ({reason})" if reason else ""))
                try:
                    await room.local_participant.publish_data(
                        json.dumps(msg).encode("utf-8"),
                        topic="amd",
                        destination_identities=[agent_id],
                    )
                except Exception as e:
                    print(f"[LiveKit] Failed to send to {agent_id}: {e}")
                if msg_type in ("force_hangup", "human_detected"):
                    if agent_id in sessions:
                        del sessions[agent_id]
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[LiveKit] Agent {agent_id}: audio stream failed — {e}")


def run_worker_thread():
    """Entry point for background thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_worker())
    except Exception as e:
        print(f"[LiveKit] Worker error: {e}")
    finally:
        loop.close()
