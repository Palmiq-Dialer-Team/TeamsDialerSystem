# AMD (Answering Machine Detection) Workflow

Reference for hangup/not-hangup logic. Use this when changing AMD.

---

## 1. Audio paths

AMD runs on audio that reaches the Backend in two ways:

- **WebSocket**: Client sends stereo audio (mic + loopback) over the main WebSocket. Backend extracts loopback and feeds it to AMD in `main.py`.
- **LiveKit**: When the client uses LiveKit (WebRTC), the client publishes an audio track to a LiveKit room. `Backend/livekit_worker.py` joins the room, subscribes to each agent’s track, and runs the same AMD logic on that stream via `Backend/amd_controller.py` and `Backend/ai_core.py`. Transcription is done with **faster-whisper**.

**Note:** LiveKit/AMD can be blocked by firewall or network rules (e.g. UDP/port or WebSocket to LiveKit server). If agents cannot connect to the LiveKit server or the worker cannot reach it, AMD over LiveKit will not run until those are fixed.

---

## 2. Audio states

| State | Description |
|-------|-------------|
| **Ringing** | Ringback, no answer yet. Do not treat as speech. |
| **Non-speech** | Ringback, hold music, dial tone, silence. |
| **Human speech** | Real person: "Hello", "Aló", "Smith speaking", "Yeah?", "Bueno". |
| **Voicemail speech** | Automated: "Leave a message after the tone", "Buzón de voz", etc. |

---

## 3. Golden rules

1. **Ringing is NOT speech.** Do not hang up during ringing. VAD can false-trigger on ringback.
2. **Only hang up on positive voicemail evidence.** No greeting is not evidence of voicemail.
3. **Uncertainty = keep the call.** Prefer staying on voicemail over hanging up on a human.
4. **People answer in many ways.** "John Smith speaking", "Yeah?", "Bueno" — do not hang up.

---

## 4. When to HANG UP (force_hangup)

- Voicemail keywords in transcript.
- 30-second timeout (no answer).
- Long continuous speech (>4 s), no pause, no greeting, **and** non-empty transcript (likely machine).

---

## 5. When to NOT hang up

- Ringing; empty transcript (VAD false positive); unintelligible/low-quality; human greeting; any speech without voicemail keywords; errors (fail-safe: keep call).

---

## 6. Flow summary

```
Call starts → RINGING
  ├─ VAD detects speech → buffer ~5 s → transcribe (faster-whisper)
  │   ├─ Voicemail keywords? → HANGUP
  │   ├─ Empty transcript? → RESET, keep waiting
  │   ├─ Short greeting? → HUMAN
  │   ├─ Long speech, no greeting? → HANGUP
  │   └─ Else → HUMAN (fail-safe)
  └─ 30 s no valid speech? → HANGUP (timeout)
```

---

## 7. Transcript analysis order (analyze_buffered_audio)

1. Voicemail keywords → hangup  
2. Empty transcript → continue (reset, keep waiting)  
3. Short greeting → human  
4. Long continuous speech, no greeting, non-empty transcript → hangup  
5. Default → human  

Never hang up on VAD alone; machine detection requires a non-empty transcript (faster-whisper).

---

## 8. Related files

| File | Role |
|------|------|
| `Backend/ai_core.py` | Silero VAD, buffer, faster-whisper, `analyze_buffered_audio` |
| `Backend/amd_controller.py` | Shared AMD session and `process_amd_audio`; used by WebSocket and LiveKit |
| `Backend/livekit_worker.py` | LiveKit room connection; subscribes to agent audio; calls AMD and sends force_hangup/human_detected |
| `Backend/main.py` | WebSocket audio path; calls AMD; sends force_hangup, handles timeout |

---

## 9. When changing this logic

1. Re-read this document.  
2. Ask: "Could this hang up on a live person who answered in an unusual way?"  
3. If yes, do not add it. Prefer false negatives (stay on voicemail) over false positives (hang up on human).
