## TeamsPowerDialer – Overview

TeamsPowerDialer helps agents call through Excel lead lists using Microsoft Teams and log what happened on each call.

- The Installer client reads contacts from Excel, places calls in Teams, and records dispositions. In v2.0.0 it runs client-side answering-machine detection (AMD) when Start Auto-Call is on: Silero VAD + faster-whisper run locally; no audio is streamed to the server for AMD in that mode.
- The Backend server receives metadata and disposition data, stores calls and callbacks in SQLite, and exposes dashboard, exports, and APIs. It no longer needs to run AMD for the production dialer; it focuses on storage, dashboards, and control endpoints.
- LiveKit is optional and not required for the production dialer; it remains available for experimentation.

Current phase: v2.0.0 with client-side AI is in production. See `HISTORY.md` and `IN_DEVELOPMENT.md` for details.

This document is a short guide for developers to understand how the pieces fit together.

---

## 1. Components

- Installer (`Installer/`)
  - PyQt desktop app for agents.
  - Reads contacts from the active Excel workbook.
  - Automates Teams to dial numbers.
  - Client-side AMD (v2.0.0): when Start Auto-Call is on, runs Silero VAD + faster-whisper locally (`Installer/modules/ai_core.py`); decides human vs voicemail / bot / invalid-number and triggers hangup + auto-advance. Sends metadata and call_started to the Backend; does not stream audio to the server for AMD in this mode.
  - Can still stream audio over WebSocket (or LiveKit if enabled) for dashboards or legacy paths.
  - Writes dispositions back into Excel and sends them to the Backend.

- Backend (`Backend/`)
  - FastAPI app with a WebSocket endpoint for audio / metadata.
  - Persists calls and callbacks in `teams_dialer.db` (SQLite).
  - Exposes JSON APIs for stats, exports, and dashboard.

- Control Panel (`Backend/control_panel/`)
  - Tkinter app to start/stop the server, view logs, and open a read‑only DB snapshot viewer.

- LiveKit (`LiveKit/`)
  - Optional WebRTC audio path.
  - `start_livekit.bat` starts `livekit-server` with `config.yaml`.

---

## 2. Main Flows

### 2.1 Contact import

1. Agent opens the Excel workbook with leads.
2. Installer attaches to the running Excel instance and reads visible rows from the active sheet.
3. Simple header detection maps columns like first name, last name, company, phone, etc.
4. The in‑app list shows one row per contact, with filters (campaign mode, geography, etc.).

### 2.2 Call and audio

**Production path (v2.0.0 – client-side AI, Auto-Call on):**

1. Agent turns on **Start Auto-Call** and starts a call from the Installer.
2. The app drives the Teams UI to dial the number and notifies the Backend that a call started (metadata + `call_started`).
3. Audio is captured on the client; **only the loopback (system) channel** is fed to the local AMD pipeline. No audio is sent to the server for AMD.
4. The **Installer** runs AMD (Silero VAD + faster-whisper in `ai_core.py`). On voicemail/bot/invalid-number or 35s/50s no-answer, it hangs up and auto-advances (next number or next contact). On human detected, it stops the timer and leaves the call connected.
5. When the call ends, the client logs a disposition (Excel + disposition files) and sends it to the Backend for exports.

**Legacy / optional path (server-side AMD):**

- If the client is not using Auto-Call or is an older build, it can still stream audio to the Backend via WebSocket (or LiveKit if enabled). The Backend then runs AMD and may send `force_hangup` to the client. This path remains supported.

### 2.3 Persistence and exports

- The Backend stores calls and callbacks in `teams_dialer.db` (SQLite).
- Export endpoints read directly from this DB and:
  - Include only rows with a real disposition and non‑empty contact data.
  - For “Export All”, deduplicate per agent + contact so only the most recent row per contact is kept.
- The Control Panel “DB Snapshot” button copies the live DB to a snapshot file and opens it read‑only, so you can inspect tables without locking the main file.

---

## 3. Configuration

### 3.1 Client (Installer)

`Installer/config.py` holds local settings:
- Excel file location.
- Backend URLs (`SERVER_URL`, `SERVER_API_URL`).
- Optional LiveKit URL for WebRTC audio (`LIVEKIT_URL`, server IP + port 81).

The launcher (`Installer/launcher.py`) checks for updates from `UPDATE_URL`, but it does **not** overwrite `config.py` once it exists locally (it is treated as a local config file).

### 3.2 Backend

`Backend/database.py`:
- Uses `teams_dialer.db` in the `Backend` folder by default.
- If the `TEAMS_DIALER_DB` environment variable is set, it uses that absolute path instead, so the server and control panel always look at the same DB file.

`Backend/main.py`:
- WebSocket endpoint for agents.
- REST endpoints for stats, exports, LiveKit tokens, and debug status.

### 3.3 LiveKit

`LiveKit/config.yaml` (runtime config):
- `port: 81` — HTTP/WebSocket signaling.
- `rtc.udp_port: 3478` — UDP media port.
- API keys for LiveKit.

Agents connect to `ws://<server-ip>:81` (set in `Installer/config.py`). The LiveKit worker connects to `ws://localhost:81` from the Backend machine.

`LiveKit/config.example.yaml` (template) is a generic sample; the real ports/keys live in `config.yaml`.

---

## 4. How to run (dev)

- **Backend server**
  - From `Backend/`:  
    `python -m uvicorn main:app --host 0.0.0.0 --port 80`
  - Or run the built control‑panel executable/script if you have it.

- **LiveKit (optional)**
  - From repo root:  
    `start_livekit.bat`  
    This changes into `LiveKit\` and runs `livekit-server` with `config.yaml`.

- **Client**
  - From `Installer/`:  
    `python launcher.py`  
    The launcher checks for updates, then starts `main.py`.

---

## 5. Documentation (by location)

| Location | Doc | Purpose |
|----------|-----|---------|
| **Repo root** | `PROJECT_OVERVIEW.md` | This file — high-level architecture and run instructions |
| **Repo root** | `RUN.md` | How to run: server (control panel, venv, rebuild), client (zip, setup, ExecutionPolicy) |
| **Repo root** | `TROUBLESHOOTING.md` | Common issues (e.g. WebSocket WinError 121 after long uptime) |
| **Repo root** | `HISTORY.md` | Version history and changes |
| **Repo root** | `IN_DEVELOPMENT.md` | In progress / roadmap (production testing v2.0.0, LiveKit optional, TTS, first-run tour) |
| **Installer/modules/** | `CLIENT_MODULES.md` | Client app and module reference |
| **Installer/modules/** | `CLIENT_SIDE_AI.md` | Client-side AMD: phrase lists, logic, how to add voicemail/bot phrases, libraries, common errors |
| **Backend/docs/** | `AMD_WORKFLOW_SPEC.md` | AMD rules: when to hang up, LiveKit + faster-whisper pipeline |
| **Backend/cleandb/** | `README_CLEANDB.md` | DB backfill, clean, and fix scripts |

---

## 6. Where to look next (code)

- `Installer/main.py` – main window, call controls, Auto-Call, Excel integration.
- `Installer/modules/ai_core.py` – client-side AMD (Silero VAD, faster-whisper, intent detection). **To add or tune voicemail/bot phrases and understand the logic**, see `Installer/modules/CLIENT_SIDE_AI.md`.
- `Installer/modules/` – Excel reader, Teams automation, audio capture, streamers, settings.
- `Backend/main.py` – server endpoints and WebSocket handling (and server-side AMD for legacy path).
- `Backend/database.py` – schema and DB helpers.
- `Backend/livekit_worker.py` – optional LiveKit AMD worker.
- `Backend/ai_core.py` – server-side AMD (used when client streams audio to server).
