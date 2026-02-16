"""
Server-side Answering Machine Detection (AMD) using Silero VAD and faster-whisper.
Replaces YAMNet with: VAD for speech detection, Whisper for voicemail keyword detection.
"""

import re
import numpy as np
import scipy.signal
import torch
from typing import Optional, Tuple, List, Union

# Return: None | (action, reason) | ("transcribe", buffer, duration_sec)
AMDResult = Optional[Union[Tuple[str, str], Tuple[str, np.ndarray, float]]]

# Constants
INPUT_RATE = 48000
VAD_RATE = 16000
BUFFER_SECONDS = 5
CONTINUOUS_SPEECH_MACHINE_SEC = 4
HUMAN_GREETING_MAX_SEC = 2
VAD_SPEECH_THRESHOLD = 0.65
VAD_SILENCE_THRESHOLD = 0.3
VAD_CHUNK_SAMPLES_16K = 512  # ~32ms at 16kHz
SAMPLES_3SEC_48K = BUFFER_SECONDS * INPUT_RATE
SAMPLES_3SEC_16K = BUFFER_SECONDS * VAD_RATE

# Voicemail keywords - match any of these to trigger hangup
VOICEMAIL_PATTERNS = [
    # Spanish (Peru-specific)
    r"buz[oó]n\s+de\s+voz",
    r"no\s+se\s+encuentra\s+disponible",
    r"deje\s+su\s+mensaje",
    r"despu[eé]s\s+del\s+tono",
    r"el\s+n[uú]mero\s+que\s+marc[oó]",
    r"casilla\s+de\s+voz",
    # English
    r"number\s+you\s+have\s+reached",
    r"not\s+available",
    r"leave\s+(a\s+)?message",
    r"after\s+the\s+tone",
    r"voicemail",
]
VOICEMAIL_REGEX = re.compile("|".join(f"({p})" for p in VOICEMAIL_PATTERNS), re.IGNORECASE)

# Short greetings that suggest human pickup
HUMAN_GREETINGS = ["aló", "alo", "hola", "hello", "bueno", "sí", "si", "diga", "dígame", "yes", "hi"]


def _resample_to_16k(audio_float: np.ndarray, orig_rate: int = INPUT_RATE) -> np.ndarray:
    """Resample audio to 16kHz for VAD and Whisper."""
    if orig_rate == VAD_RATE:
        return audio_float
    n = round(len(audio_float) * VAD_RATE / orig_rate)
    return scipy.signal.resample(audio_float, n).astype(np.float32)


class AMDDetector:
    """
    Global resource manager for AMD.
    Holds the heavy Whisper model globally (stateless/shared).
    Acts as a factory for VAD models (stateful/unique per call).
    """

    def __init__(self):
        self._whisper_model = None
        self._whisper_load_error: Optional[str] = None

    def get_new_vad_model(self):
        """
        Load a FRESH Silero VAD instance for each call.
        Silero VAD is stateful (LSTM); sharing one instance across calls can corrupt
        internal state and cause prob=0. Each call gets its own model.
        """
        try:
            model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-vad", model="silero_vad", trust_repo=True
            )
            model.reset_states()
            return model
        except Exception as e:
            print(f"[AI] Failed to load VAD model: {e}")
            return None

    def _ensure_whisper(self) -> bool:
        if self._whisper_model is not None:
            return True
        if self._whisper_load_error is not None:
            return False
        try:
            from faster_whisper import WhisperModel
            self._whisper_model = WhisperModel(
                "base", device="cpu", compute_type="int8"
            )
            return True
        except Exception as e:
            self._whisper_load_error = str(e)
            return False

    def transcribe(self, audio_16k: np.ndarray) -> str:
        """Transcribe 16kHz mono float32 audio. Returns text or empty on failure."""
        if not self._ensure_whisper():
            return ""
        try:
            segments, _ = self._whisper_model.transcribe(
                audio_16k, sample_rate=VAD_RATE, language=None,
                beam_size=1, vad_filter=False, condition_on_previous_text=False
            )
            text = " ".join(s.text.strip() for s in segments if s.text).strip()
            return text
        except Exception:
            return ""

    def has_voicemail_keywords(self, text: str) -> bool:
        """Check if transcript contains voicemail keywords."""
        if not text:
            return False
        return bool(VOICEMAIL_REGEX.search(text))

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
    """State machine for AMD: VAD → buffer → transcribe → analyze."""

    def __init__(self, detector: AMDDetector):
        self.detector = detector
        self.vad_model = detector.get_new_vad_model()
        self.state = "ringing"
        self.ring_buffer_16k: List[float] = []
        self.speech_buffer_48k: List[float] = []
        self.speech_start_time: Optional[float] = None
        self.last_silence_time: Optional[float] = None
        self.consecutive_speech_chunks = 0

    def reset(self):
        self.state = "ringing"
        self.ring_buffer_16k.clear()
        self.speech_buffer_48k.clear()
        self.speech_start_time = None
        self.last_silence_time = None
        self.consecutive_speech_chunks = 0
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

    def process_chunk(
        self, audio_float: np.ndarray, current_time: float
    ) -> AMDResult:
        """
        Process audio chunk. Returns:
        - None: continue
        - ("human_detected", ""): human pickup
        - ("force_hangup", reason): voicemail/machine
        - ("transcribe", buffer, duration_sec): 5s ready, run transcription then analyze_buffered_audio
        """
        if self.vad_model is None:
            return None

        if self.state == "ringing":
            wav_16k = _resample_to_16k(audio_float)
            self.ring_buffer_16k.extend(wav_16k.tolist())
            if len(self.ring_buffer_16k) >= VAD_CHUNK_SAMPLES_16K:
                chunk = np.array(self.ring_buffer_16k[:VAD_CHUNK_SAMPLES_16K], dtype=np.float32)
                self.ring_buffer_16k = self.ring_buffer_16k[VAD_CHUNK_SAMPLES_16K:]
                prob = self._get_speech_prob(chunk)
                if not hasattr(self, "_vad_debug_ctr"):
                    self._vad_debug_ctr = 0
                self._vad_debug_ctr += 1
                if self._vad_debug_ctr % 50 == 0 or prob > 0.2:
                    print(f"[AI] AMD DEBUG: chunk#{self._vad_debug_ctr} prob={prob:.3f}")
                if prob >= VAD_SPEECH_THRESHOLD:
                    self.consecutive_speech_chunks += 1
                    if self.consecutive_speech_chunks >= 3:
                        self.state = "speech_buffering"
                        self.speech_start_time = current_time
                        self.last_silence_time = None
                        self.speech_buffer_48k = list(audio_float)
                        print("[AI] AMD: Speech detected by VAD, buffering 5s...")
                else:
                    self.consecutive_speech_chunks = 0
            return None

        if self.state == "speech_buffering":
            self.speech_buffer_48k.extend(audio_float.tolist())
            elapsed = current_time - (self.speech_start_time or current_time)

            # Don't hang up on VAD pattern alone (ringback can look like "continuous speech").
            # Always buffer 5s and transcribe; analyze_buffered_audio decides based on transcript.

            if len(self.speech_buffer_48k) >= SAMPLES_3SEC_48K:
                self.state = "done"
                buffer = np.array(self.speech_buffer_48k[:SAMPLES_3SEC_48K], dtype=np.float32)
                dur = current_time - (self.speech_start_time or current_time)
                return ("transcribe", buffer, dur)

            return None

        return None


def analyze_buffered_audio(
    detector: AMDDetector, audio_48k: np.ndarray, speech_duration_sec: float
) -> Tuple[str, str]:
    """
    Analyze 5s buffer: transcribe and decide human vs voicemail.
    Returns (action, reason): ("human_detected", "") or ("force_hangup", "voicemail")
    Fail-safe: on error, return human_detected to keep call.
    """
    try:
        print("[AI] AMD: Transcribing 5s buffer...")
        wav_16k = _resample_to_16k(audio_48k)
        text = detector.transcribe(wav_16k)
        transcript_preview = (text[:60] + "...") if text and len(text) > 60 else (text or "(empty)")
        print(f"[AI] AMD: Transcript: {transcript_preview!r}")

        if detector.has_voicemail_keywords(text):
            print("[AI] AMD: Voicemail keywords found -> hangup")
            return ("force_hangup", "voicemail")

        # Empty transcript FIRST: VAD triggered on non-speech (ringback, hold music, noise).
        # Never hang up on empty; reset and keep waiting. Let 30s timeout handle no-answer.
        if not text or not text.strip():
            print("[AI] AMD: Empty transcript (non-speech/unintelligible) -> keep waiting")
            return ("continue", "empty_transcript")

        if detector.is_human_greeting(text) and speech_duration_sec < HUMAN_GREETING_MAX_SEC:
            print("[AI] AMD: Short greeting detected -> human")
            return ("human_detected", "")

        # Long speech with real transcript (not empty) + no greeting = likely voicemail
        if speech_duration_sec > CONTINUOUS_SPEECH_MACHINE_SEC:
            print("[AI] AMD: Long speech, no greeting -> machine")
            return ("force_hangup", "machine_no_pause")

        print("[AI] AMD: Default -> human (fail-safe)")
        return ("human_detected", "")
    except Exception as e:
        print(f"[AI] AMD: Analysis error (fail-safe to human): {e}")
        return ("human_detected", "")
