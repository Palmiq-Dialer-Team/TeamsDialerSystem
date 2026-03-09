"""
Client-side Answering Machine Detection (AMD) using Silero VAD and faster-whisper.
VAD for speech detection, Whisper for voicemail keyword detection.

This module is the single source of truth for AMD logic:
- The Installer decides human vs voicemail/bot/invalid-number locally.
- The Backend does not run VAD/Whisper and never sends force_hangup.
"""

import copy
import re
import string
import time
import numpy as np
import torch
import torchaudio
from typing import Optional, Tuple, List, Union

# Return: None | (action, reason) | ("transcribe", buffer, duration_sec)
AMDResult = Optional[Union[Tuple[str, str], Tuple[str, np.ndarray, float]]]

# Constants
INPUT_RATE = 48000
VAD_RATE = 16000
# Amount of speech to buffer once VAD fires (seconds)
BUFFER_SECONDS = 5
HUMAN_GREETING_MAX_SEC = 2
VAD_SPEECH_THRESHOLD = 0.65
VAD_SILENCE_THRESHOLD = 0.3
VAD_CHUNK_SAMPLES_16K = 512  # ~32ms at 16kHz
SAMPLES_BUFFER_48K = int(BUFFER_SECONDS * INPUT_RATE)
SAMPLES_BUFFER_16K = int(BUFFER_SECONDS * VAD_RATE)
# Remote speech persistence thresholds
REMOTE_SPEECH_THRESHOLD = 0.6           # loopback VAD threshold for a speech event
REMOTE_SPEECH_MIN_GAP_SEC = 1.5         # minimum gap between events to count as separate
REMOTE_SPEECH_EVENTS_FOR_HUMAN = 3      # events before we treat as human (persistent_speech)

# Voicemail keywords - match any of these to trigger hangup
GOOGLE_BOT_PHRASES = [
    "google recording the call",
    "call screening service",
    "virtual assistant",
    "transcribing this call",
    "state your name and reason",
    # Smart blockers / IVRs
    "screened by",
    "call blocker",
    "press one",
    "extension",
    "directory",
    # Screening prompts
    "if you record your name",
    "reason for calling",
    "see if this person is available",
    # Directory / extension prompts
    "if you know the extension you are trying to reach",
    "know the extension you are trying to reach",
    "have you know the extension you are trying to reach",
]

VOICEMAIL_PHRASES = [
    "record your message",
    "order your message",
    "at the tone",
    "not available",
    "please leave a message",
    "call is being forwarded",
    "voice mailbox",
    "press the pound button",
    "after the tone",
    # Carrier / IVR voicemail
    "reached the voicemail",
    "automated voice messaging system",  # AT&T
    "voicemail box",
    "press 1",
    "press pound",  # T-Mobile variant
    # Casual / mobile voicemail phrasing (safe to treat as not-present)
    "get back to you",
    "soon as i can",
    "soon as possible",
    "unable to take your call",
    "missed your call",
    "leave me a text",
    "returned your call",
    "finish recording",
    "when you have finished recording",
    "you may hang up",
    "you may start your message now",
    "start your message now",
    # Structural / desk / brief message
    "away from my desk",
    "not at my desk",
    "leave a brief message",
    "with the",
    "at the moment",
    # Additional voicemail patterns
    "have you reached",
    "youve reached a voicemail",
    "youve reached the voicemail",
    "currently unavailable",
    "pound key",
    "leave a message",
    "brief message",
]

INVALID_NUMBER_PHRASES = [
    "check the number",
    "dial again",
    "no longer in service",
    "number you have dialed",
    "you have dialed",           # fragments e.g. "then you have dialed"
    "not a working number",
    "disconnected",
    "has been disconnected",
    "cannot be completed",
    "unable to complete",
    "you are not setup to use this calling feature",
    "please contact your admin",
    "we are sorry",             # carrier messages
    "were sorry",               # "we're sorry" after punctuation strip
    # Carrier-specific error / disconnected scripts
    "welcome to verizon",        # Verizon error messages
    "number you have reached",  # Verizon/AT&T variant
    "subscriber you have dialed",  # T-Mobile
    "announcement",             # e.g. "Announcement 1"
    "switch",                   # carrier switch error codes
]

# Short greetings / phrases that suggest human pickup
HUMAN_GREETINGS = [
    "aló", "alo", "hola", "hello", "bueno", "sí", "si", "diga", "dígame", "yes", "hi",
    "who is this", "who is calling", "speaking", "this is", "go ahead", "yeah",
    "can i help you",
]


def _normalize_text(text: str) -> str:
    """Lowercase and strip punctuation for robust substring matching."""
    text = text.lower()
    return "".join(ch for ch in text if ch not in string.punctuation)


def detect_call_intent(text: str) -> str:
    """
    Detect high-level call intent from transcript.

    Returns:
        "bot_screening" | "voicemail" | "invalid_number" | "none"
    """
    if not text:
        return "none"
    norm = _normalize_text(text)

    # Invalid number patterns first
    for phrase in INVALID_NUMBER_PHRASES:
        if phrase in norm:
            return "invalid_number"

    for phrase in GOOGLE_BOT_PHRASES:
        if phrase in norm:
            return "bot_screening"

    for phrase in VOICEMAIL_PHRASES:
        if phrase in norm:
            return "voicemail"

    return "none"


class AMDDetector:
    """
    Global resource manager for AMD.
    Holds the heavy Whisper model globally (stateless/shared).
    Acts as a factory for VAD models (stateful/unique per call).
    """

    def __init__(self):
        self._whisper_model = None
        self._whisper_load_error: Optional[str] = None
        self._shared_vad_model = None
        self._vad_load_error: Optional[str] = None
        self.resampler = torchaudio.transforms.Resample(orig_freq=48000, new_freq=16000)
        self.last_transcript: str = ""
        self.ambiguous_event_count: int = 0  # persistence: ambiguous/empty buffers before declaring human
        self._ensure_vad()

    def _ensure_vad(self) -> bool:
        if self._shared_vad_model is not None:
            return True
        if self._vad_load_error is not None:
            print(f"[AI] Silero VAD previously failed to load: {self._vad_load_error}")
            return False
        try:
            model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-vad", model="silero_vad", trust_repo=True
            )
            model.reset_states()
            self._shared_vad_model = model
            return True
        except Exception as e:
            print(f"[AI] Failed to load VAD model: {e}")
            self._vad_load_error = str(e)
            return False

    def get_new_vad_model(self):
        """
        Reuse the preloaded Silero VAD base model and deep-copy per-call state.
        Falls back to a fresh load if deepcopy is not supported by this model build.
        """
        if not self._ensure_vad():
            return None
        try:
            model = copy.deepcopy(self._shared_vad_model)
            model.reset_states()
            return model
        except Exception as e:
            print(f"[AI] VAD deepcopy failed, falling back to direct load: {e}")
            try:
                model, _ = torch.hub.load(
                    repo_or_dir="snakers4/silero-vad", model="silero_vad", trust_repo=True
                )
                model.reset_states()
                return model
            except Exception as load_error:
                print(f"[AI] Failed to load fallback VAD model: {load_error}")
                return None

    def _ensure_whisper(self) -> bool:
        if self._whisper_model is not None:
            return True
        if self._whisper_load_error is not None:
            print(f"[AI] Whisper previously failed to load: {self._whisper_load_error}")
            return False
        try:
            from faster_whisper import WhisperModel
            # Match standalone test: tiny.en + float32 (no int8) for reliable CPU transcription
            self._whisper_model = WhisperModel(
                model_size_or_path="tiny.en",
                device="cpu",
                compute_type="float32",
            )
            return True
        except Exception as e:
            print(f"[AI] CRITICAL: Whisper model failed to load! Error: {e}")
            import traceback
            traceback.print_exc()
            self._whisper_load_error = str(e)
            return False

    def transcribe(self, audio_input) -> str:
        """Transcribe audio: path to WAV file (str) or 16kHz mono float32 array. Returns text or empty on failure."""
        print(f"[AI] Calling Whisper with model type: {type(self._whisper_model)}")
        if not self._ensure_whisper():
            return ""
        try:
            kwargs = dict(
                language="en",
                beam_size=5,
                temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                best_of=5,
                initial_prompt="Hello? Is this a voicemail? Please leave a message.",
                vad_filter=True,
                word_timestamps=True,
                no_speech_threshold=0.1,
                compression_ratio_threshold=2.4,
                condition_on_previous_text=False,
            )
            if isinstance(audio_input, str):
                segments, info = self._whisper_model.transcribe(audio_input, **kwargs)
            else:
                # Array path: this faster_whisper version does not accept sample_rate
                audio_arr = np.asarray(audio_input, dtype=np.float32)
                if audio_arr.ndim > 1:
                    audio_arr = audio_arr.flatten()
                segments, info = self._whisper_model.transcribe(audio_arr, **kwargs)
            lang = getattr(info, "language", "?")
            lang_prob = getattr(info, "language_probability", None)
            duration = getattr(info, "duration", None)
            lang_str = f"{lang_prob:.2f}" if lang_prob is not None else "?"
            dur_str = f"{duration:.2f}s" if duration is not None else "?"
            texts = []
            seg_list = list(segments)
            if not seg_list:
                print("[AI] Whisper found 0 segments.")
            for i, seg in enumerate(seg_list):
                t = seg.text.strip() if seg.text else ""
                print(f"[AI] Whisper segment {i}: {t!r}")
                if t:
                    texts.append(t)
            text = " ".join(texts).strip()
            print(f"[AI] Whisper Info: Language={lang} ({lang_str}), Duration={dur_str}")
            return text
        except Exception as e:
            print(f"[AI] Whisper Crash: {e}")
            import traceback
            traceback.print_exc()
            return ""

    def is_human_greeting(self, text: str) -> bool:
        """Short greeting like Aló, Hola - likely human."""
        if not text or len(text) > 30:
            return False
        t = text.lower().strip().rstrip("?")
        for g in HUMAN_GREETINGS:
            if t == g or t.startswith(g + " ") or t.startswith(g + "?"):
                return True
        return False


class AMDState:
    """State machine for AMD: VAD → buffer → transcribe → analyze.
    Dual-stream input (mic + loopback), but persistence logic relies only on loopback (remote) speech.
    """

    def __init__(self, detector: AMDDetector):
        self.detector = detector
        self.vad_model = detector.get_new_vad_model()
        self.state = "ringing"
        self.ring_buffer_16k: List[float] = []  # loopback at 16k for VAD
        self.speech_buffer_48k: List[float] = []  # loopback only for transcription
        self.speech_start_time: Optional[float] = None
        self.last_silence_time: Optional[float] = None
        self.consecutive_speech_chunks = 0
        # Last loopback VAD probability (used by AMDWorker for timer kill)
        self.last_loopback_prob: float = 0.0
        # Last time we saw clear speech on loopback (for silence-triggered early transcription)
        self.last_speech_activity_time: Optional[float] = None
        # Call start time for VAD grace period (ignore loopback VAD for first 3s)
        self.call_start_ts: float = time.time()

    def reset(self):
        self.state = "ringing"
        self.ring_buffer_16k.clear()
        self.speech_buffer_48k.clear()
        self.speech_start_time = None
        self.last_silence_time = None
        self.consecutive_speech_chunks = 0
        self.last_speech_activity_time = None
        self.call_start_ts = time.time()
        if self.vad_model is not None and hasattr(self.vad_model, "reset_states"):
            self.vad_model.reset_states()

    def _get_speech_prob(self, chunk: np.ndarray) -> float:
        """Run VAD on this call's private model instance."""
        if self.vad_model is None:
            return 0.0
        try:
            t = torch.from_numpy(chunk).float()
            if t.dim() == 1:
                t = t.unsqueeze(0)
            if t.shape[1] == 0:
                return 0.0
            with torch.no_grad():
                out = self.vad_model(t, VAD_RATE)
            prob = float(out.item() if out.numel() == 1 else out.flatten()[-1].item())
            return prob
        except Exception as e:
            print(f"[AI] VAD Error: {e}")
            return 0.0

    def process_chunk(self, loopback_float: np.ndarray, current_time: float) -> AMDResult:
        """
        Process loopback-only chunk. VAD triggers buffering; human/voicemail/bot is decided in analyze_buffered_audio.

        Returns:
        - None: continue
        - ("transcribe", buffer, duration_sec): loopback buffer ready for Whisper + analyze_buffered_audio.
        """
        if self.vad_model is None:
            return None

        # Strict VAD grace period: ignore all audio and clear buffers for the first 3 seconds.
        if (current_time - self.call_start_ts) < 3.0:
            self.ring_buffer_16k.clear()
            self.speech_buffer_48k.clear()
            return None

        loopback_float = np.asarray(loopback_float, dtype=np.float32)
        if loopback_float.ndim > 1:
            loopback_float = loopback_float.flatten()

        # Resample loopback to 16k for VAD (mic is unused for persistence)
        t = torch.from_numpy(loopback_float).float()
        if t.dim() == 1:
            t = t.unsqueeze(0)
        loopback_16k = self.detector.resampler(t).squeeze(0).numpy()
        self.ring_buffer_16k.extend(loopback_16k.tolist())

        # Run VAD on loopback while we have enough samples
        while len(self.ring_buffer_16k) >= VAD_CHUNK_SAMPLES_16K:
            lb_chunk = np.array(self.ring_buffer_16k[:VAD_CHUNK_SAMPLES_16K], dtype=np.float32)
            self.ring_buffer_16k = self.ring_buffer_16k[VAD_CHUNK_SAMPLES_16K:]
            loopback_prob = self._get_speech_prob(lb_chunk)
            self.last_loopback_prob = loopback_prob
            if loopback_prob >= REMOTE_SPEECH_THRESHOLD:
                self.last_speech_activity_time = current_time

            if self.state == "ringing":
                if loopback_prob >= VAD_SPEECH_THRESHOLD:
                    self.consecutive_speech_chunks += 1
                    if self.consecutive_speech_chunks >= 3:
                        self.state = "speech_buffering"
                        self.speech_start_time = current_time
                        self.last_silence_time = None
                        self.speech_buffer_48k = list(loopback_float)
                        # print("[AI] AMD: Speech detected on loopback, buffering...")
                else:
                    self.consecutive_speech_chunks = 0

        if self.state == "speech_buffering":
            self.speech_buffer_48k.extend(loopback_float.tolist())

            # Silence-triggered early transcription:
            # If we've had clear speech on loopback, followed by >1.2s of silence,
            # and we have at least ~2s of buffered audio, transcribe immediately
            # instead of waiting for the full BUFFER_SECONDS.
            if self.last_speech_activity_time is not None:
                time_since_speech = current_time - self.last_speech_activity_time
                if (
                    self.last_loopback_prob < VAD_SILENCE_THRESHOLD
                    and time_since_speech > 1.2
                    and len(self.speech_buffer_48k) >= int(2 * INPUT_RATE)
                ):
                    print(
                        f"[AI] AMD: Early transcription triggered after silence "
                        f"(buffer_len={len(self.speech_buffer_48k)}, "
                        f"time_since_speech={time_since_speech:.2f}s)"
                    )
                    self.state = "done"
                    buffer = np.array(self.speech_buffer_48k, dtype=np.float32)
                    dur = current_time - (self.speech_start_time or current_time)
                    return ("transcribe", buffer, dur)

            # Fallback: full BUFFER_SECONDS worth of audio
            if len(self.speech_buffer_48k) >= SAMPLES_BUFFER_48K:
                self.state = "done"
                buffer = np.array(self.speech_buffer_48k[:SAMPLES_BUFFER_48K], dtype=np.float32)
                dur = current_time - (self.speech_start_time or current_time)
                return ("transcribe", buffer, dur)

        return None


def analyze_buffered_audio(
    detector: AMDDetector, audio_48k: np.ndarray, speech_duration_sec: float
) -> Tuple[str, str]:
    """
    Analyze buffered speech: transcribe and conservatively decide intent.

    Returns:
        ("force_hangup", "voicemail" | "bot_screening")
        ("human_detected", "")
        ("continue", "empty_transcript" | "ambiguous_speech")

    Fail-safe: on error, keep the call ("human_detected").
    """
    try:
        print("[AI] AMD: Transcribing 5s buffer...")
        audio_48k = np.asarray(audio_48k, dtype=np.float32)
        if audio_48k.ndim > 1:
            audio_48k = audio_48k.flatten()

        # Normalize: boost quiet signal to ~80% peak so Whisper doesn't treat it as silence
        max_val = float(np.max(np.abs(audio_48k)))
        if max_val < 0.01:
            # Basically silent; don't boost pure noise
            audio_to_save = audio_48k
            print(f"[AI] AMD: Skipping boost (max_val={max_val:.4f} < 0.01)")
        else:
            target_gain = 0.8 / max_val
            audio_boosted = audio_48k * target_gain
            audio_to_save = np.clip(audio_boosted, -1.0, 1.0).astype(np.float32)
            print(f"[AI] AMD: Boost applied (max_val={max_val:.4f} -> peak ~0.8)")

        # Resample 48k -> 16k via torchaudio (C++ backend, zero disk I/O)
        audio_tensor = torch.from_numpy(audio_to_save).float()
        if audio_tensor.dim() == 1:
            audio_tensor = audio_tensor.unsqueeze(0)
        audio_16k = detector.resampler(audio_tensor).squeeze(0).numpy()
        text = detector.transcribe(audio_16k)
        detector.last_transcript = text or ""
        transcript_preview = (text[:60] + "...") if text and len(text) > 60 else (text or "(empty)")
        print(f"[AI] AMD: Transcript: {transcript_preview!r}")

        # Step 1–2: strict hangup only when explicit voicemail or bot phrases detected
        intent = detect_call_intent(text)
        if intent == "invalid_number":
            detector.ambiguous_event_count = 0
            print("[AI] AMD: Invalid-number pattern found -> hangup as Wrong Number")
            return ("force_hangup", "invalid_number")
        if intent == "bot_screening":
            detector.ambiguous_event_count = 0
            print("[AI] AMD: Bot/assistant pattern found -> hangup as No Response")
            return ("force_hangup", "bot_screening")
        if intent == "voicemail":
            detector.ambiguous_event_count = 0
            print("[AI] AMD: Voicemail pattern found -> hangup")
            return ("force_hangup", "voicemail")

        # Step 3: explicit human operator phrases (e.g. receptionists)
        norm = _normalize_text(text or "")
        if (
            "how can i help you" in norm
            or "how may i help you" in norm
            or "can i help you" in norm
        ):
            detector.ambiguous_event_count = 0
            print("[AI] AMD: Operator phrase detected -> human")
            return ("human_detected", "")

        # Step 4: short human greeting -> clear human, reset persistence counter
        if detector.is_human_greeting(text) and speech_duration_sec < HUMAN_GREETING_MAX_SEC:
            detector.ambiguous_event_count = 0
            print("[AI] AMD: Short greeting detected -> human")
            return ("human_detected", "")

        # Step 5: ambiguous or empty (and not negative) -> persistence counter
        detector.ambiguous_event_count = getattr(detector, "ambiguous_event_count", 0) + 1
        if detector.ambiguous_event_count >= 3:
            print("[AI] AMD: Persistent ambiguous/empty speech (count >= 3) -> treating as human")
            return ("human_detected", "persistent_ambiguous_speech")
        if not text or not text.strip():
            rms = float(np.sqrt(np.mean(audio_to_save ** 2)))
            print(f"[AI] AMD: Empty transcript (ambiguous_event_count={detector.ambiguous_event_count}, rms={rms:.4f}) -> continue")
            return ("continue", "empty_transcript")
        print("[AI] AMD: Ambiguous transcript -> continue listening")
        return ("continue", "ambiguous_speech")
    except Exception as e:
        print(f"[AI] AMD: Analysis error (fail-safe to human): {e}")
        return ("human_detected", "")
