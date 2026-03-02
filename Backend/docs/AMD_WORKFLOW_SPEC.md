# AMD (Answering Machine Detection) – Client Only

Reference for hangup/not-hangup logic now that **all AMD is performed on the client**.  
The Backend no longer runs VAD, Whisper, or any audio analysis; it only receives metadata, stores data, and forwards audio for dashboards/listen‑in.

---

## 1. Architecture

- **Client (Installer)**  
  - Captures loopback (system) audio.  
  - Runs **Silero VAD + faster-whisper** locally to detect voicemail / bots / invalid numbers vs humans.  
  - Decides when to **auto‑hangup** and when to **keep the call**.  
  - Sends metadata, dispositions, callbacks, and (optionally) audio bytes to the server for dashboards.

- **Server (Backend)**  
  - **Does not** run VAD or Whisper and **never** sends `force_hangup` based on audio.  
  - Stores calls, dispositions, and callbacks in SQLite.  
  - Streams audio to admin clients for monitoring only (no analysis).

The **client is the master** of call intent and behavior. If AMD decisions are wrong (e.g. voicemail not skipped, human hung up), the fix is always on the client side.

---

## 2. Where AMD logic lives

All AMD rules, phrase lists, and behavior are documented and implemented on the client:

- `Installer/modules/ai_core.py` – Silero VAD and faster-whisper integration; buffering and analysis.  
- `Installer/modules/CLIENT_SIDE_AI.md` – How client-side AMD works, phrase lists, how to add new voicemail/bot/invalid‑number patterns, and common errors.

When updating anything related to voicemail detection, bots, or invalid number logic, work in those files only.

---

## 3. Server responsibilities related to calls

Even without server-side AMD, the Backend still:

- Accepts **WebSocket** connections from agents.  
- Receives and forwards **audio bytes** to admin listeners (for monitoring).  
- Receives **metadata** (call_started, status, etc.), **dispositions**, and **callbacks** from the client.  
- Exposes APIs and exports for stats and dashboards.

The Backend never decides to end calls based on audio content; it just relays and records.

---

## 4. When you see AMD-related issues

- **Voicemail wasn’t skipped**, or **a human was hung up on** → investigate and change **client-side** AMD (see `CLIENT_SIDE_AI.md`).  
- **Dashboard audio missing or broken** → investigate WebSocket/streaming paths, not AMD.  
- **Timeouts or force hangups from server** should be limited to explicit, non‑AI rules (e.g. administrative timeouts), not audio analysis.

If you are tempted to add VAD/Whisper back to the server, prefer extending the client implementation instead, so there is only one source of truth for AMD.
