## TeamsPowerDialer – Overview

TeamsPowerDialer helps agents call through Excel lead lists using Microsoft Teams and log what happened on each call.

- The **Installer client** reads contacts from Excel, places calls in Teams, and records dispositions.
- The **Backend server** receives audio and metadata, runs answering‑machine detection (AMD), and stores call data in SQLite.
- An optional **LiveKit server** provides a WebRTC audio path instead of the raw WebSocket stream.

This document is a short guide for developers to understand how the pieces fit together.

---

## 1. Components

- **Installer (`Installer/`)**
  - PyQt desktop app for agents.
  - Reads contacts from the active Excel workbook.
  - Automates Teams to dial numbers.
  - Streams audio and call metadata to the Backend.
  - Writes dispositions back into Excel.

- **Backend (`Backend/`)**
  - FastAPI app with a WebSocket endpoint for audio/metadata.
  - Runs AMD and basic audio analysis.
  - Persists calls and callbacks in `teams_dialer.db` (SQLite).
  - Exposes JSON APIs for stats and exports.

- **Control Panel (`Backend/control_panel/`)**
  - Tkinter app to start/stop the server, view logs, and open a read‑only DB snapshot viewer.

- **LiveKit (`LiveKit/`)**
  - Optional WebRTC audio path.
  - `start_livekit.bat` starts `livekit-server` with `config.yaml`.
  - `Backend/livekit_worker.py` joins the room, listens to agent audio, and runs AMD on it.

---

## 2. Main Flows

### 2.1 Contact import

1. Agent opens the Excel workbook with leads.
2. Installer attaches to the running Excel instance and reads visible rows from the active sheet.
3. Simple header detection maps columns like first name, last name, company, phone, etc.
4. The in‑app list shows one row per contact, with filters (campaign mode, geography, etc.).

### 2.2 Call and audio

1. Agent starts a call from the Installer.
2. The app drives the Teams UI to dial the number and notifies the Backend that a call started.
3. Audio is captured on the client and sent to the Backend:
   - Either via the original WebSocket streamer, or
   - Via LiveKit if enabled (Installer connects to the LiveKit server; the AMD worker subscribes to the agent’s audio track).
4. The Backend runs AMD. When it is confident the call is a voicemail or machine, it can tell the client to hang up.
5. When the call ends, the client:
   - Logs a disposition back into Excel (and related Excel disposition files), and
   - Sends the disposition to the Backend so it is available for exports.

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
| **Repo root** | `IN_DEVELOPMENT.md` | In progress / roadmap (LiveKit audio, TTS, first-run tour) |
| **Installer/modules/** | `CLIENT_MODULES.md` | Client app and module reference |
| **Backend/docs/** | `AMD_WORKFLOW_SPEC.md` | AMD rules: when to hang up, LiveKit + faster-whisper pipeline |
| **Backend/cleandb/** | `README_CLEANDB.md` | DB backfill, clean, and fix scripts |

---

## 6. Where to look next (code)

- `Installer/main.py` – main window, call controls, Excel integration.
- `Installer/modules/` – Excel reader, Teams automation, audio capture, streamers, settings.
- `Backend/main.py` – server endpoints and WebSocket handling.
- `Backend/database.py` – schema and DB helpers.
- `Backend/livekit_worker.py` – LiveKit AMD worker.
- `Backend/amd_controller.py` – shared AMD session and processing (WebSocket + LiveKit).
