"""
Opus audio decoder using DIRECT ctypes bindings to libopus-0.dll.
Bypasses 'opuslib' wrapper.
FIXED: Uses c_void_p for binary data to prevent truncation on null bytes.
"""

import queue
import sys
import os
import ctypes
from typing import Generator, Any

# --- DLL LOADING ---
_here = os.path.dirname(os.path.abspath(__file__))
_dll_path = os.path.join(_here, "libopus-0.dll")

_lib = None
if os.path.exists(_dll_path):
    try:
        _lib = ctypes.CDLL(_dll_path)
        print(f"[Opus] Loaded DLL: {_dll_path}")
    except Exception as e:
        print(f"[Opus] DLL Load Error: {e}")
else:
    print(f"[Opus] Warning: libopus-0.dll not found at {_dll_path}")

# --- CTYPES DEFINITIONS ---
if _lib:
    # opus_decoder_create(Fs, channels, *error)
    _lib.opus_decoder_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    _lib.opus_decoder_create.restype = ctypes.c_void_p

    # opus_decoder_destroy(st)
    _lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
    _lib.opus_decoder_destroy.restype = None

    # opus_decode(st, data, len, pcm, frame_size, decode_fec)
    # CRITICAL FIX: Use c_void_p for data to support raw binary with null bytes
    _lib.opus_decode.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,      # data pointer (void* allows raw bytes)
        ctypes.c_int32,       # len
        ctypes.c_void_p,      # pcm output
        ctypes.c_int,         # frame_size
        ctypes.c_int          # decode_fec
    ]
    _lib.opus_decode.restype = ctypes.c_int

# Constants
SAMPLE_RATE = 48000
CHANNELS = 2
FRAME_SIZE = 960  # 20ms
MAX_FRAME_SIZE = 5760  # 120ms safety

_STOP_SENTINEL: Any = object()


class OpusDecoder:
    """Decode Opus packets to PCM s16 48kHz stereo using bare-metal ctypes."""

    def __init__(self):
        self.packet_queue: queue.Queue = queue.Queue()
        self._stopped = False
        self._state = None

        if _lib:
            error = ctypes.c_int()
            self._state = _lib.opus_decoder_create(SAMPLE_RATE, CHANNELS, ctypes.byref(error))
            if error.value != 0:
                print(f"[Opus] Failed to create decoder. Error code: {error.value}")
                self._state = None
        else:
            print("[Opus] Error: Library not loaded.")

    def decode(self, bytes_data: bytes) -> None:
        """Enqueue Opus packet."""
        if bytes_data:
            self.packet_queue.put(bytes_data)

    def stop(self) -> None:
        self._stopped = True
        self.packet_queue.put(_STOP_SENTINEL)
        if self._state and _lib:
            _lib.opus_decoder_destroy(self._state)
            self._state = None

    def _decode_frame(self, opus_data: bytes | None = None) -> bytes:
        """
        Decode a single frame. Pass None to trigger PLC.
        """
        if not self._state or not _lib:
            return b""

        # Create output buffer (int16 array)
        pcm_output = (ctypes.c_int16 * (MAX_FRAME_SIZE * CHANNELS))()

        ptr_data = None
        len_data = 0

        if opus_data:
            # Normal Decode
            # We explicitly prevent Python from treating this as a string
            len_data = len(opus_data)
            # Create a ctypes byte array from the python bytes
            # This ensures 'ptr_data' is a valid memory address to the full binary
            ptr_data = (ctypes.c_ubyte * len_data).from_buffer_copy(opus_data)
        else:
            # PLC Mode
            ptr_data = None
            len_data = 0

        # Call C function
        num_samples = _lib.opus_decode(
            self._state,
            ptr_data,
            len_data,
            pcm_output,
            MAX_FRAME_SIZE if opus_data else FRAME_SIZE,
            0
        )

        if num_samples > 0:
            return bytes(pcm_output[:num_samples * CHANNELS])
        elif num_samples < 0:
            # Errors: -1 (Bad Arg), -2 (Buffer), -3 (Internal), -4 (Invalid Packet)
            # Only print if it's NOT PLC (PLC sometimes returns error on startup)
            if opus_data:
                print(f"[Opus] Decode Error {num_samples} (len={len_data})")
            return b""

        return b""

    def get_audio_stream(self) -> Generator[bytes, None, None]:
        """Generator yielding smooth PCM bytes."""
        import time

        # Buffer pre-fill (~80ms)
        wait_cycles = 0
        while not self._stopped and self.packet_queue.qsize() < 4:
            time.sleep(0.01)
            wait_cycles += 1
            if wait_cycles > 200:  # 2s timeout to avoid blocking forever if no audio
                break

        while True:
            if self._stopped:
                break

            try:
                # 20ms frame time
                packet = self.packet_queue.get(timeout=0.018)

                if packet is _STOP_SENTINEL:
                    break

                pcm = self._decode_frame(packet)
                if pcm:
                    yield pcm

            except queue.Empty:
                # Underrun -> Trigger PLC
                # If we run dry, we "hallucinate" audio to stay smooth
                pcm = self._decode_frame(None)
                if pcm:
                    yield pcm
