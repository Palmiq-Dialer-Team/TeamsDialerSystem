# VAD / AMD Issue Summary

**Date:** 2025-02-02  
**Status:** Unresolved — Silero VAD returns `prob=0.000` for all chunks despite clear speech on loopback channel

---

## 1. The Problem

Answering Machine Detection (AMD) uses Silero VAD to detect when a human answers the phone. When speech is detected (prob ≥ 0.5), the system marks "human detected" and keeps the call. When no speech is detected within 30 seconds, it sends `force_hangup` and treats it as no answer.

**Symptom:** VAD returns `prob=0.000` for every chunk, even when someone is speaking on the other end of the call. The 30-second timeout always triggers, and the call hangs up on a live person.

---

## 2. What We Know

- **Audio reaches the server:** Client sends ~600 packets; server receives them.
- **Loopback channel has audio:** Server-side RMS logs show `ch1(loopback)` with values like 0.034, 0.060, 0.154, 0.153 — clearly non-silent.
- **Model works in isolation:** Running `test_vad_diagnostic.py` loads Silero and returns non-zero output (e.g. 0.0029 for a sine wave; correctly low for non-speech).
- **Pipeline issue:** Something between receiving the bytes and feeding them to Silero VAD is producing input that the model treats as non-speech.

---

## 3. Audio Pipeline (Current)

1. **Client** (`Installer/modules/audio_core.py`): Captures mic + loopback, stacks as stereo `[mic, loopback, mic, loopback, ...]`, sends interleaved int16 bytes.
2. **Server** (`Backend/main.py`): Receives bytes, reshapes to `(N, 2)`, extracts `system_channel = stereo[:, 1]` (loopback).
3. **Server** (`Backend/ai_core.py`): Converts to float32 `/ 32768.0`, resamples 48k→16k via `scipy.signal.resample`, buffers 512-sample chunks, calls `detector.get_speech_probability(chunk)`.
4. **Silero VAD** returns prob; threshold 0.5 for speech.

---

## 4. What We've Tried

| Attempt | Change | Result |
|---------|--------|--------|
| **reset_states()** | Call `model.reset_states()` at start of each call (Silero is stateful) | No change — prob still 0 |
| **Batch dimension** | Add `unsqueeze(0)` so input is `(1, 512)` | No change |
| **Stride downsampling** | Use `audio[::3]` for 48k→16k instead of `scipy.signal.resample` (match Silero's internal handling) | No change |
| **Mono mix for VAD** | Feed mono mix (same as dashboard playback) instead of loopback-only | User ran without env var; log showed ch1(loopback). No result for mono mix. |
| **Diagnostic script** | `Backend/test_vad_diagnostic.py` — loads model, tests with sine wave | Model returns ~0.003 for sine (correct). Confirms model loads and runs. |
| **RMS logging** | Log ch0 and ch1 RMS every 50 chunks | Confirms ch1 has significant amplitude (0.03–0.15) when remote speaks |

---

## 5. Reverted Changes

- Resampling: reverted to `scipy.signal.resample` (stride downsampling reverted).
- Mono-mix diagnostic: removed; AMD uses loopback (ch1) only.
- RMS log now shows only `ch0(mic)` and `ch1(loopback)` RMS, no VAD source label.

---

## 6. Relevant Files

- `Backend/ai_core.py` — AMDDetector, AMDState, `_resample_to_16k`, `get_speech_probability`, `process_chunk`
- `Backend/main.py` — WebSocket bytes handling, stereo extraction, `system_channel`, AMD call to `amd_state.process_chunk`
- `Installer/modules/audio_core.py` — Capture loop, stereo stacking `column_stack([mic_array, loopback_array]).flatten()`
- `Backend/test_vad_diagnostic.py` — Standalone Silero test script

---

## 7. Hypotheses Not Yet Fully Tested

1. **Channel swap:** Client may send `[loopback, mic]` but we assume `[mic, loopback]`. Try `stereo[:, 0]` for AMD.
2. **Resampling artifacts:** `scipy.signal.resample` may alter the signal in a way that breaks VAD. Try `torchaudio.transforms.Resample` or Silero's `read_audio` pipeline.
3. **Chunk boundaries:** 512-sample chunks may cut speech in unfavorable ways. Try Silero's `get_speech_timestamps` over longer buffers.
4. **Teams/codec audio:** Call audio may be processed (codec, AEC) differently than file-based speech; Silero may not generalize well.
5. **Sample-rate / format mismatch:** Verify client sample rate and that int16→float32 conversion is correct.

---

## 8. Diagnostic Commands

```powershell
# Run VAD diagnostic (requires SSL fix on server)
$env:SSL_CERT_FILE = (python -c "import certifi; print(certifi.where())")
python Backend/test_vad_diagnostic.py
```
