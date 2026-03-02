"""
Opus test client: captures mic (or sine wave), encodes to Opus, sends to ws/opus/TesterAgent.
Run the server, then run this script; open /audio-test on the dashboard to listen.
"""

import av
import pyaudio
import websocket
import threading
import struct
import json
import time
import math

# Config
WS_URL = "ws://localhost:80/ws/opus/TesterAgent"  # or use your server IP
SAMPLE_RATE = 48000
CHANNELS = 2
CHUNK_FRAMES = 960
FORMAT = pyaudio.paInt16


def make_sine_chunk(freq=440.0, duration_frames=960):
    """Generate a chunk of sine wave (int16 stereo) as fallback when no mic."""
    import numpy as np
    t = np.arange(duration_frames, dtype=np.float32) / SAMPLE_RATE
    left = (np.sin(2 * math.pi * freq * t) * 0.3).astype(np.float32)
    right = left.copy()
    stereo = np.column_stack([left, right])
    return (stereo * 32767).astype(np.int16).tobytes()


def run_opus_tester():
    use_mic = True
    stream = None
    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_FRAMES,
        )
        print("[OpusTester] Using microphone input.")
    except Exception as e:
        print(f"[OpusTester] No mic: {e}. Using sine wave.")
        use_mic = False

    # Opus encoder: PCM s16 48k stereo -> Opus (av)
    codec = av.codec.CodecContext.create("opus", "w")
    codec.sample_rate = SAMPLE_RATE
    codec.channels = CHANNELS
    codec.layout = "stereo"
    codec.open()
    # Resampler: s16 -> fltp for Opus input if needed (Opus encoder in av often accepts s16)
    # Many Opus encoders accept s16; if not, we'd use a resampler. Try without first.
    buffer = bytearray()

    def on_message(ws, message):
        if isinstance(message, bytes):
            return
        try:
            msg = json.loads(message)
            print(f"[OpusTester] RX: {msg.get('type', msg)}")
            if msg.get("type") == "force_hangup":
                print(f"[OpusTester] Server sent force_hangup: {msg.get('reason', '')}")
            if msg.get("type") == "human_detected":
                print("[OpusTester] Server: Human detected (VAD).")
        except Exception:
            pass

    def on_error(ws, error):
        print(f"[OpusTester] WS error: {error}")

    def on_close(ws, close_status_code, close_msg):
        print(f"[OpusTester] WS closed: {close_status_code} {close_msg}")

    def on_open(ws):
        ws.send(json.dumps({"type": "call_started", "agent_id": "TesterAgent"}))
        print("[OpusTester] Sent call_started. Streaming audio… (Ctrl+C to stop)")

    ws = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    wst = threading.Thread(target=lambda: ws.run_forever(), daemon=True)
    wst.start()
    time.sleep(0.5)
    if not ws.sock or not ws.sock.connected:
        print("[OpusTester] Could not connect. Is the server running on port 80?")
        return
    packet_count = 0
    try:
        while ws.sock and ws.sock.connected:
            if use_mic and stream:
                chunk_bytes = stream.read(CHUNK_FRAMES, exception_on_overflow=False)
            else:
                chunk_bytes = make_sine_chunk(440.0, CHUNK_FRAMES)
            # Encode to Opus: wrap s16 stereo in an av frame and encode
            try:
                arr = __import__("numpy").frombuffer(chunk_bytes, dtype="int16")
                arr = arr.reshape(-1, 2)
                frame = av.AudioFrame.from_ndarray(arr.T, format="s16", layout="stereo")
                frame.sample_rate = SAMPLE_RATE
                frame.time_base = "1/48000"
                for packet in codec.encode(frame):
                    payload = bytes(packet)
                    msg = struct.pack("!I", len(payload)) + payload
                    ws.send(msg, opcode=websocket.ABNF.OPCODE_BINARY)
                    packet_count += 1
                    if packet_count % 100 == 0:
                        print(f"[OpusTester] Sent {packet_count} packets")
            except Exception as enc_err:
                print(f"[OpusTester] Encode error: {enc_err}")
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\n[OpusTester] Stopped.")
    finally:
        if stream:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        pa.terminate()


if __name__ == "__main__":
    run_opus_tester()
