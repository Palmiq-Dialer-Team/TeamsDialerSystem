"""
LiveKit WebRTC audio streamer - alternative to WebSocket streamer.
Uses same public API: start(), stop(), send_json_message(), start_audio_streaming(), etc.
"""

import asyncio
import json
import threading
import queue
import time
import requests
import numpy as np
from typing import Dict, Any, Optional


class LiveKitStreamer:
    """Streams audio via LiveKit WebRTC. Same interface as AudioStreamer."""

    def __init__(self, livekit_url: str, agent_id: str, api_base_url: str):
        self.livekit_url = livekit_url.rstrip("/")
        self.agent_id = agent_id
        self.api_base_url = api_base_url.rstrip("/")
        self.room = None
        self.audio_source = None
        self.audio_track = None
        self.is_connected = False
        self.loop = None
        self.loop_thread = None
        self._stop_event = threading.Event()
        self.audio_queue = None
        self.streaming_task = None
        self.metadata_queue = queue.Queue()
        self.metadata_task = None
        self.message_queue = queue.Queue()
        self.message_callbacks: Dict[str, callable] = {}

    def _run_event_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._async_init())

    async def _async_init(self):
        self.metadata_task = asyncio.create_task(self._metadata_sender_loop())
        while not self._stop_event.is_set():
            await asyncio.sleep(0.1)

    def _fetch_token(self) -> Optional[str]:
        try:
            r = requests.get(
                f"{self.api_base_url}/api/livekit/token",
                params={"agent_id": self.agent_id},
                timeout=5,
            )
            r.raise_for_status()
            return r.json().get("token")
        except Exception as e:
            print(f"[LiveKit] Failed to fetch token: {e}")
            return None

    async def _connect(self) -> bool:
        if self.is_connected and self.room and self.room.isconnected():
            return True

        token = self._fetch_token()
        if not token:
            return False

        try:
            from livekit import rtc

            self.room = rtc.Room()

            def on_data(data_packet: rtc.DataPacket):
                try:
                    msg = json.loads(data_packet.data.decode("utf-8"))
                    self.message_queue.put_nowait(msg)
                    msg_type = msg.get("type")
                    if msg_type in self.message_callbacks and self.message_callbacks[msg_type]:
                        self.message_callbacks[msg_type](msg)
                except Exception as e:
                    print(f"[LiveKit] Data handler error: {e}")

            self.room.on("data_received", on_data)
            await self.room.connect(self.livekit_url, token)
            self.is_connected = True
            print(f"[LiveKit] Connected to room as {self.agent_id}")
            return True
        except Exception as e:
            print(f"[LiveKit] Connect failed: {e}")
            self.is_connected = False
            return False

    def start(self):
        if self.loop_thread and self.loop_thread.is_alive():
            return
        self._stop_event.clear()
        self.loop_thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self.loop_thread.start()
        time.sleep(0.5)

    def stop(self):
        self._stop_event.set()
        if self.loop and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._async_cleanup(), self.loop)
        if self.loop_thread and self.loop_thread.is_alive():
            self.loop_thread.join(timeout=2.0)

    async def _async_cleanup(self):
        if self.streaming_task:
            self.streaming_task.cancel()
            try:
                await self.streaming_task
            except asyncio.CancelledError:
                pass
        if self.metadata_task:
            self.metadata_task.cancel()
            try:
                await self.metadata_task
            except asyncio.CancelledError:
                pass
        if self.room and self.room.isconnected():
            await self.room.disconnect()
        self.is_connected = False

    async def _stream_audio(self, audio_queue: queue.Queue):
        from livekit import rtc

        if not await self._connect():
            print("[LiveKit] Cannot stream: not connected")
            return

        # Create audio source and track: 48kHz, 2 channels (mic+loopback)
        self.audio_source = rtc.AudioSource(48000, 2)
        self.audio_track = rtc.LocalAudioTrack.create_audio_track("agent-audio", self.audio_source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self.room.local_participant.publish_track(self.audio_track, options)

        print("[LiveKit] Audio track published, streaming...")

        while not self._stop_event.is_set():
            try:
                try:
                    chunk = audio_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.01)
                    continue

                if isinstance(chunk, np.ndarray):
                    if chunk.dtype != np.int16:
                        chunk = chunk.astype(np.int16)
                    data = chunk.tobytes()
                elif isinstance(chunk, bytes):
                    data = chunk
                else:
                    continue

                # Create AudioFrame and capture (data = int16 interleaved stereo)
                num_samples = len(data) // 2  # 2 bytes per int16 sample
                samples_per_channel = num_samples // 2  # stereo
                frame = rtc.AudioFrame(data, 48000, 2, samples_per_channel)
                await self.audio_source.capture_frame(frame)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[LiveKit] Stream error: {e}")
                await asyncio.sleep(0.1)

        print("[LiveKit] Audio streaming stopped")

    async def _metadata_sender_loop(self):
        while not self._stop_event.is_set():
            try:
                try:
                    metadata = self.metadata_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.1)
                    continue
                metadata["timestamp"] = time.time()
                # Metadata is sent via send_json_message with type "metadata"
                msg = {"type": "metadata", "agent_id": self.agent_id, "data": metadata}
                await self._send_json_async(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await asyncio.sleep(0.1)

    def send_metadata(self, status_dict: Dict[str, Any]):
        try:
            self.metadata_queue.put_nowait(status_dict)
        except queue.Full:
            pass

    def start_audio_streaming(self, audio_queue: queue.Queue):
        self.audio_queue = audio_queue
        if not self.loop:
            print("[LiveKit] Event loop not started. Call start() first.")
            return
        if self.streaming_task and not self.streaming_task.done():
            return
        self.streaming_task = asyncio.run_coroutine_threadsafe(
            self._stream_audio(audio_queue), self.loop
        )

    def stop_audio_streaming(self):
        if self.streaming_task:
            self.streaming_task.cancel()
            self.streaming_task = None

    def register_message_handler(self, message_type: str, callback):
        self.message_callbacks[message_type] = callback

    def send_json_message(self, message_dict: Dict[str, Any]):
        try:
            if not self.loop:
                return
            asyncio.run_coroutine_threadsafe(
                self._send_json_async(message_dict), self.loop
            )
        except Exception as e:
            print(f"[LiveKit] send_json_message error: {e}")

    async def _send_json_async(self, message_dict: Dict[str, Any]):
        if not await self._connect():
            return
        try:
            payload = json.dumps(message_dict).encode("utf-8")
            await self.room.local_participant.publish_data(payload, topic="agent-message")
        except Exception as e:
            print(f"[LiveKit] publish_data error: {e}")
            self.is_connected = False

    def get_message(self, timeout: float = 0.1) -> Optional[Dict[str, Any]]:
        try:
            return self.message_queue.get(timeout=timeout)
        except queue.Empty:
            return None
