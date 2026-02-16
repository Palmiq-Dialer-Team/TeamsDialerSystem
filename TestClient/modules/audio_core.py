"""
Dual-Channel Audio Capture for Real-Time Monitoring
=====================================================
Captures audio from a microphone and a speaker loopback device simultaneously,
combines them into a single mono stream, and provides methods for analysis.
"""
import pyaudiowpatch as pyaudio
import numpy as np
import threading
import time
import queue
from typing import Optional, Tuple
import resampy
import config
from pydub import AudioSegment
from pydub.effects import compress_dynamic_range


class AudioCapture:
    """Handles true dual-channel audio capture in a separate thread."""

    def __init__(self):
        self.audio = pyaudio.PyAudio()
        self.mic_stream = None
        self.loopback_stream = None
        self.is_capturing = False
        self.capture_thread = None

        # Device indices
        self.mic_device_index = None
        self.loopback_device_index = None

        # Audio settings from config
        self.sample_rate = config.SAMPLE_RATE
        self.format = pyaudio.paInt16
        self.system_audio_multiplier = config.SYSTEM_AUDIO_MULTIPLIER

        # Internal queue for the final mixed audio
        self.audio_queue = queue.Queue(maxsize=100)
        self.buffer_lock = threading.Lock()

        # Ring detection state
        self.timeout_detected = False
        self.ring_detection_enabled = True
        self.high_volume_start_time = None
        self.high_volume_threshold = 0.3
        self.high_volume_duration = 30.0
        self.conversation_threshold = 0.1
        self._debug_volume_counter = 0

    def get_loopback_devices(self):
        """Returns a list of available WASAPI loopback devices (speakers)."""
        loopback_devices = []
        try:
            wasapi_info = self.audio.get_host_api_info_by_type(pyaudio.paWASAPI)
            for i in range(self.audio.get_device_count()):
                dev_info = self.audio.get_device_info_by_index(i)
                if dev_info.get("hostApi") == wasapi_info["index"] and dev_info.get("isLoopbackDevice"):
                    loopback_devices.append((i, dev_info))
            return loopback_devices
        except Exception as e:
            print(f"Error getting loopback devices: {e}")
            return []

    def get_input_devices(self):
        """Returns a list of available WASAPI input devices (microphones)."""
        input_devices = []
        try:
            wasapi_info = self.audio.get_host_api_info_by_type(pyaudio.paWASAPI)
            for i in range(self.audio.get_device_count()):
                dev_info = self.audio.get_device_info_by_index(i)
                if dev_info.get("hostApi") == wasapi_info["index"] and dev_info.get("maxInputChannels") > 0 and not dev_info.get("isLoopbackDevice"):
                    input_devices.append((i, dev_info))
            return input_devices
        except Exception as e:
            print(f"Error getting input devices: {e}")
            return []

    def get_all_recording_devices(self):
        """Returns a list of all available WASAPI recording devices, including Stereo Mix."""
        recording_devices = []
        try:
            wasapi_info = self.audio.get_host_api_info_by_type(pyaudio.paWASAPI)
            for i in range(self.audio.get_device_count()):
                dev_info = self.audio.get_device_info_by_index(i)
                if dev_info.get("hostApi") == wasapi_info["index"] and dev_info.get("maxInputChannels") > 0:
                    recording_devices.append((i, dev_info))
            return recording_devices
        except Exception as e:
            print(f"Error getting all recording devices: {e}")
            return []

    def get_system_audio_devices(self):
        """Returns loopback devices + Stereo Mix for system audio selection."""
        seen = set()
        devices = []
        for idx, info in self.get_loopback_devices():
            if idx not in seen:
                seen.add(idx)
                devices.append((idx, info))
        for idx, info in self.get_all_recording_devices():
            if idx not in seen and 'stereo mix' in info.get("name", "").lower():
                seen.add(idx)
                devices.append((idx, info))
        return devices

    def get_device_name(self, index: int) -> str:
        """Helper to get a device name from its index."""
        try:
            info = self.audio.get_device_info_by_index(index)
            return info.get("name", f"Device {index}")
        except Exception:
            return f"Device {index}"

    def is_device_available(self, index: int) -> bool:
        """Check if a device index is still valid and available."""
        if index is None:
            return False
        try:
            self.audio.get_device_info_by_index(index)
            return True
        except Exception:
            return False

    def select_system_default_devices(self):
        """
        Select mic = Windows default input, system audio = loopback of Windows default output.
        Used for fresh installs or when user has not saved preferences.
        """
        try:
            default_input_idx = None
            try:
                default_input = self.audio.get_default_input_device_info()
                default_input_idx = default_input['index']
                self.mic_device_index = default_input_idx
                print(f"Using default input: {self.get_device_name(default_input_idx)}")
            except Exception as e:
                print(f"No default input device: {e}")
                for idx, info in self.get_input_devices():
                    self.mic_device_index = idx
                    break

            found_loopback = None
            try:
                default_output = self.audio.get_default_output_device_info()
                out_name = default_output['name']
                wasapi_info = self.audio.get_host_api_info_by_type(pyaudio.paWASAPI)
                wasapi_index = wasapi_info["index"]
                for i in range(self.audio.get_device_count()):
                    dev = self.audio.get_device_info_by_index(i)
                    if dev["hostApi"] == wasapi_index and dev.get("isLoopbackDevice") is True:
                        if out_name in dev["name"] or dev["name"] in out_name:
                            found_loopback = i
                            break
            except Exception as e:
                print(f"Error finding default loopback: {e}")

            if found_loopback is None:
                for idx, info in self.get_system_audio_devices():
                    if 'stereo mix' in info.get('name', '').lower():
                        found_loopback = idx
                        break
                if found_loopback is None and self.get_system_audio_devices():
                    found_loopback = self.get_system_audio_devices()[0][0]

            self.loopback_device_index = found_loopback
            if self.loopback_device_index is not None:
                print(f"Using system audio: {self.get_device_name(self.loopback_device_index)}")

            return True, "Using system default devices."
        except Exception as e:
            print(f"Error in select_system_default_devices: {e}")
            return False, str(e)

    def auto_select_devices(self):
        """
        Select devices: use pre-set indices if provided and valid, else system defaults.
        """
        if self.mic_device_index is not None and self.loopback_device_index is not None:
            if self.is_device_available(self.mic_device_index) and self.is_device_available(self.loopback_device_index):
                print(f"Using preferred devices: Mic={self.get_device_name(self.mic_device_index)}, System={self.get_device_name(self.loopback_device_index)}")
                return True, "Using preferred audio devices."

        self.mic_device_index = None
        self.loopback_device_index = None
        return self.select_system_default_devices()

    def _calculate_volume(self, audio_data: np.ndarray) -> float:
        """Calculates the RMS volume of audio data, normalized to a 0.0-1.0 scale."""
        if len(audio_data) == 0:
            return 0.0
        audio_float = audio_data.astype(np.float32) / 32768.0
        rms = np.sqrt(np.mean(audio_float**2))
        return min(rms, 1.0)

    def _detect_ring_timeout(self, speaker_volume: float, mic_volume: float):
        """Detects if high speaker volume persists without microphone activity."""
        if not self.ring_detection_enabled:
            return
        
        if speaker_volume > self.high_volume_threshold:
            if mic_volume <= self.conversation_threshold:
                if self.high_volume_start_time is None:
                    self.high_volume_start_time = time.time()
                elif (time.time() - self.high_volume_start_time) > self.high_volume_duration:
                    self.timeout_detected = True
            else:
                self.high_volume_start_time = None
        else:
            self.high_volume_start_time = None

    def _capture_loop(self):
        """
        Main capture loop. Opens and reads from two separate audio streams,
        resamples both to exactly 48kHz for consistency, combines them, and puts
        the mixed audio into the output queue.
        """
        try:
            mic_info = self.audio.get_device_info_by_index(self.mic_device_index)
            loopback_info = self.audio.get_device_info_by_index(self.loopback_device_index)

            mic_rate = int(mic_info["defaultSampleRate"])
            loopback_rate = int(loopback_info["defaultSampleRate"])
            
            # CRITICAL: Always use 48000 Hz as target rate for consistency with browser playback
            # This ensures the audio matches what the browser expects, regardless of device rates
            TARGET_SAMPLE_RATE = 48000
            target_rate = TARGET_SAMPLE_RATE

            # Diagnostic logging
            print(f"[AudioCapture] Device rates - Mic: {mic_rate}Hz, Loopback: {loopback_rate}Hz")
            print(f"[AudioCapture] Target rate: {target_rate}Hz (forced)")

            # Process audio in small, fixed-duration chunks (e.g., 50ms)
            chunk_duration_s = 0.05  # 50ms

            # Calculate frames to read per chunk for each device (at their native rates)
            mic_frames_per_chunk = int(mic_rate * chunk_duration_s)
            loopback_frames_per_chunk = int(loopback_rate * chunk_duration_s)

            # CRITICAL: Stereo mics (e.g. USB headsets) must be opened as channels=2, then downmixed.
            # Opening a stereo mic as mono causes interleaved [L,R,L,R] to be misinterpreted
            # -> half-speed playback (low pitch) and choppy audio (same bug we fixed for loopback)
            mic_channels = min(2, int(mic_info.get("maxInputChannels", 1)))
            self.mic_stream = self.audio.open(
                format=self.format, channels=mic_channels, rate=mic_rate, input=True,
                input_device_index=self.mic_device_index, frames_per_buffer=mic_frames_per_chunk
            )
            print(f"Mic stream opened on '{mic_info['name']}' at {mic_rate} Hz ({mic_channels}ch)")

            # Open Loopback Stream (STEREO) at device's native rate
            # CRITICAL: WASAPI loopback devices output Float32 in STEREO (2 channels)
            # Opening as mono causes interleaved [L,R,L,R] to be interpreted as mono at 2x duration
            # This causes half-speed playback (one octave down - "demonic voice")
            self.loopback_stream = self.audio.open(
                format=pyaudio.paFloat32,  # Float32 format for WASAPI loopback
                channels=2,  # CRITICAL: WASAPI loopback is always stereo
                rate=loopback_rate, input=True,
                input_device_index=self.loopback_device_index, frames_per_buffer=loopback_frames_per_chunk
            )
            print(f"Loopback stream opened on '{loopback_info['name']}' at {loopback_rate} Hz (Float32, Stereo format)")

            # Calculate expected output chunk size at target rate
            target_frames_per_chunk = int(target_rate * chunk_duration_s)
            print(f"[AudioCapture] Output chunk size: {target_frames_per_chunk} samples at {target_rate}Hz")

            while self.is_capturing:
                # 1. Read Mic (The Master Clock)
                # This blocks for exactly 50ms, keeping time steady
                mic_data = self.mic_stream.read(mic_frames_per_chunk, exception_on_overflow=False)
                mic_array = np.frombuffer(mic_data, dtype=np.int16)
                
                if len(mic_array) == 0:
                    continue

                # Downmix stereo mic to mono (same fix as loopback - avoids low pitch/choppy)
                if mic_channels == 2:
                    if len(mic_array) % 2 != 0:
                        mic_array = mic_array[:-1]
                    mic_stereo = mic_array.reshape(-1, 2)
                    mic_array = np.mean(mic_stereo, axis=1).astype(np.int16)
                
                # 2. Try to Read Loopback (Non-Blocking attempt)
                # WASAPI Loopback is event-driven and can be silent, so we must not block
                try:
                    # Check how many frames are waiting in the WASAPI buffer
                    available = self.loopback_stream.get_read_available()
                    
                    if available >= loopback_frames_per_chunk:
                        # Data is ready, read it fast
                        # Note: frames_per_buffer refers to frames (pairs of samples for stereo)
                        # So reading 2400 frames returns 4800 floats (2400 L, 2400 R)
                        loopback_data = self.loopback_stream.read(loopback_frames_per_chunk, exception_on_overflow=False)
                        
                        # Parse as Float32 (native WASAPI format)
                        loopback_float = np.frombuffer(loopback_data, dtype=np.float32)
                        
                        # Downmix Stereo -> Mono
                        # Handle potential odd length if buffer is weird (safety)
                        if len(loopback_float) % 2 != 0:
                            loopback_float = loopback_float[:-1]  # Remove last sample to make even
                        
                        # Reshape interleaved [L, R, L, R, ...] to (N, 2) for L/R separation
                        loopback_stereo = loopback_float.reshape(-1, 2)
                        # Average L and R channels to create mono
                        loopback_mono = np.mean(loopback_stereo, axis=1)
                        
                        # Convert Float32 Mono to Int16
                        # Clip to range [-1.0, 1.0] to avoid overflow before scaling
                        loopback_clipped = np.clip(loopback_mono, -1.0, 1.0)
                        loopback_array = (loopback_clipped * 32767.0).astype(np.int16)
                    else:
                        # No data ready (Silent period). Generate Zeros.
                        # Generate at source rate, then let resampling logic handle it
                        loopback_array = np.zeros(loopback_frames_per_chunk, dtype=np.int16)
                
                except Exception as e:
                    # Safety fallback: use silence if loopback read fails
                    print(f"[AudioCapture] Loopback read error: {e}, using silence")
                    loopback_array = np.zeros(loopback_frames_per_chunk, dtype=np.int16)
                
                if len(loopback_array) == 0:
                    continue

                # --- Resampling Logic ---
                # Resample BOTH streams to exactly 48kHz for consistency
                # This ensures perfect pitch matching with browser playback
                
                # Resample mic to target rate (usually 8kHz -> 48kHz)
                if mic_rate != target_rate:
                    mic_array_float = mic_array.astype(np.float32) / 32768.0  # Normalize to [-1, 1]
                    mic_array_resampled = resampy.resample(mic_array_float, mic_rate, target_rate)
                    mic_array = (mic_array_resampled * 32768.0).astype(np.int16)  # Denormalize
                
                # Resample loopback to target rate (in case it's not exactly 48kHz)
                if loopback_rate != target_rate:
                    loopback_array_float = loopback_array.astype(np.float32) / 32768.0  # Normalize to [-1, 1]
                    loopback_array_resampled = resampy.resample(loopback_array_float, loopback_rate, target_rate)
                    loopback_array = (loopback_array_resampled * 32768.0).astype(np.int16)  # Denormalize
                
                # --- Frame Alignment (Safety Net) ---
                # After resampling, both arrays should be the same length, but verify
                len_mic = len(mic_array)
                len_loop = len(loopback_array)
                if len_mic != len_loop:
                    # This should be rare after resampling, but handle it safely
                    if len_mic > len_loop:
                        loopback_array = np.pad(loopback_array, (0, len_mic - len_loop), 'constant')
                    else:
                        mic_array = np.pad(mic_array, (0, len_loop - len_mic), 'constant')
                    print(f"[AudioCapture] WARNING: Frame length mismatch after resample - mic={len_mic}, loopback={len_loop}")
                
                # --- Volume Analysis & Ring Detection ---
                mic_vol = self._calculate_volume(mic_array)
                
                # Apply volume multiplier to system audio before calculating its volume
                if self.system_audio_multiplier != 1.0:
                    # Use float32 for multiplication to avoid overflow, then clip
                    loopback_float = loopback_array.astype(np.float32) * self.system_audio_multiplier
                    np.clip(loopback_float, -32768, 32767, out=loopback_float)
                    loopback_array = loopback_float.astype(np.int16)

                speaker_vol = self._calculate_volume(loopback_array)
                self._detect_ring_timeout(speaker_vol, mic_vol)

                self._debug_volume_counter += 1
                if self._debug_volume_counter % 20 == 0: # Log every second or so
                    print(f"[AudioCapture] Mic Vol: {mic_vol:.3f}, Speaker Vol: {speaker_vol:.3f} (Post-Amp)")

                # --- STEREO STACKING (Client Side) ---
                # Left = Mic, Right = System (Loopback)
                # We do NOT mix them here. We send them discrete.
                # Flatten to interleaved format: [L, R, L, R, ...]
                stereo_frame = np.column_stack([mic_array, loopback_array]).flatten()

                # --- Queueing (drop-oldest policy on overflow) ---
                try:
                    self.audio_queue.put_nowait(stereo_frame)
                except queue.Full:
                    try:
                        self.audio_queue.get_nowait()
                        self.audio_queue.put_nowait(stereo_frame)
                    except queue.Empty:
                        pass
                # The stream.read() calls are blocking, so no sleep is needed.
        except Exception as e:
            print(f"CRITICAL ERROR in capture thread: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._cleanup_streams()
            print("Audio capture thread ended.")

    def _cleanup_streams(self):
        """Safely stops and closes both audio streams."""
        if self.mic_stream:
            try:
                self.mic_stream.stop_stream()
                self.mic_stream.close()
            except Exception as e:
                print(f"Error closing mic stream: {e}")
            self.mic_stream = None

        if self.loopback_stream:
            try:
                self.loopback_stream.stop_stream()
                self.loopback_stream.close()
            except Exception as e:
                print(f"Error closing loopback stream: {e}")
            self.loopback_stream = None

    def start_capture(self, mic_device_index: Optional[int] = None, loopback_device_index: Optional[int] = None) -> bool:
        """Starts the audio capture in a separate thread."""
        
        # 1. Aggressive Cleanup
        if self.is_capturing or (self.capture_thread and self.capture_thread.is_alive()):
            print("Capture already running/alive. Stopping previous instance...")
            self.stop_capture()
            # Wait a bit longer if thread is still alive
            if self.capture_thread and self.capture_thread.is_alive():
                print("Warning: Previous capture thread still alive after stop_capture(). Waiting...")
                self.capture_thread.join(timeout=1.0)
                if self.capture_thread.is_alive():
                    print("ERROR: Previous capture thread failed to stop. Proceeding anyway...")

        self.mic_device_index = mic_device_index
        self.loopback_device_index = loopback_device_index

        if self.mic_device_index is None or self.loopback_device_index is None:
            success, message = self.auto_select_devices()
            if not success:
                print(f"Failed to start capture: {message}")
                # Ensure cleanup on failure
                self.stop_capture()
                return False
            print(message)

        self.is_capturing = True
        self.timeout_detected = False
        self.high_volume_start_time = None
        
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

        # Wait for streams to initialize (Smart Wait)
        for _ in range(20):  # Wait up to 2.0 seconds
            if self.mic_stream and self.loopback_stream:
                break
            time.sleep(0.1)

        # 2. Validate Success and Cleanup on Failure
        if not self.mic_stream or not self.loopback_stream:
            print("Error: Audio streams failed to initialize. Aborting capture.")
            # CRITICAL: Stop the thread we just started so it doesn't zombie
            self.stop_capture()
            return False
        
        print("Dual-stream audio capture started successfully.")
        return True

    def stop_capture(self):
        """Stops the audio capture thread and cleans up resources."""
        if not self.is_capturing:
            return

        self.is_capturing = False
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        
        self._cleanup_streams()
        print("Audio capture stopped.")
    
    def get_mono_mixed_frame_from_queue(self) -> Optional[np.ndarray]:
        """Gets a mixed mono audio frame from the output queue."""
        try:
            return self.audio_queue.get_nowait()
        except queue.Empty:
            return None

    def reset_timeout_flag(self):
        """Resets the ring timeout flag."""
        self.timeout_detected = False
        self.high_volume_start_time = None

    def cleanup(self):
        """Performs final cleanup of all audio resources."""
        self.stop_capture()
        if self.audio:
            try:
                self.audio.terminate()
            except Exception as e:
                print(f"Error terminating PyAudio: {e}")

# Alias for backward compatibility
AudioCore = AudioCapture
