# TeamsPowerDialer - Version History 

Documentation of program status, fixes, and addons by version.

> Full project overview: see `PROJECT_OVERVIEW.md` for architecture, technical flow, module roles, and recent changes.

Version numbering: from version 1.9.9 onward we use x.y.z (e.g. 1.9.11, 1.9.12). Major version 2.0.0 marks the switch to client-side AI. Earlier entries keep the previous numbering for history.

---

## Version 2.0.0

### Status
- Client-side AI is implemented: Answering Machine Detection (AMD) now runs on the Installer client instead of the server. The client processes audio locally with Silero VAD + faster-whisper and no longer streams audio to the server for AMD (metadata and `call_started` are still sent when using Auto-Call).
- Backend continues to serve dashboard, dispositions, callbacks, and WebSocket. It no longer needs to run AMD for the production dialer; any server-side AMD code is considered legacy.
- File version numbers (for example `WebUpdates/newversion.json`, `version.json`) may still show 1.9.x on some deployments; bump to 2.0.0 in those files when you ship the final release.

### Major changes

Client-side AMD (Installer)
- `Installer/modules/ai_core.py`: Silero VAD + faster-whisper (tiny.en, float32); VAD grace period; buffered speech with early transcription after silence; intent detection (voicemail, bot_screening, invalid_number) and human-greeting logic; persistence so repeated empty/ambiguous results can be treated as human after several events.
- AMDWorker (QThread): consumes loopback-only float32 from a dedicated queue; runs `process_chunk` and `analyze_buffered_audio`; emits `human_detected_signal`, `force_hangup_signal`, `transcription_update_signal`, `continue_result_signal`.
- AudioWorker: optionally feeds an `amd_queue` with the loopback channel as float32 for AMD; main audio queue is still used for the streamer when not in dry-run.
- 35s / 50s no-answer timer: single-shot 35s on call start when Auto-Call + AMD are active; on AMD "continue" (ambiguous/empty) the timer can be extended once to 50s from call start.
- Transcription UI: in-call read-only text area shows VAD / transcription / analysis messages when Auto-Call is active; hidden when the call ends.

Auto-Call mode
- Start Auto-Call button toggles `is_autocalling_active`. When on, clicking Call starts a call and local AMD runs; human detected → stop AI and timer; voicemail / timeout / invalid_number / bot → `handle_local_hangup` (disposition Wrong Number or No Response, hang up, stop threads, then `_no_answer_automation_timer` to try the next number or the next contact and `start_call()`).
- Arbitrator (`on_call_ended`): invalid-number phrase in last transcript → Wrong Number; autocall + confirmed human → pause for disposition ("Waiting for Disposition..."); short call (<10s) with autocall → safe stop (toggle autocall off); otherwise → auto-next after a short delay.
- `handle_force_hangup`: ignored when `_using_local_ai_this_call`; server-driven hangups are still supported for non–Auto-Call or legacy clients.

TeamsController (Installer)
- `call_connected_signal`: emitted once when the in-call timer first appears (not 00:00), used to set `actual_start_time` for duration and safety logic.
- `current_call_handle`: cached when the call window is discovered; `hang_up()` targets that handle (UIA Leave → window.close() → WM_CLOSE via win32), no pyautogui.
- `on_ended_callback=None` in the monitor so cleanup is done from the main thread in `on_call_ended`.

Dependencies (Installer)
- Added: `torch`, `torchaudio`, `faster-whisper` (see `Installer/requirements.txt`).

Optional
- `DRY_RUN_CLIENT_AI`: if set to `True`, client-side AI runs and shows predictions in the transcription box but does not hang up and does not start the streamer (no audio is sent to the server).
- Launcher: version check prefers `newversion.json`, falls back to `version.json` (see launcher/version flow in earlier notes).

---

## Version 1.9.12 

### Current approach: testing the Opus pipeline and AMD end-to-end

We are testing an experimental Opus audio path alongside the existing WebSocket raw-audio path, so we can validate encoding/decoding, server AMD (VAD + transcription), and listening from the dashboard without affecting production agents. When this works, we can consider moving to Opus for cleaner transmission and more accurate transcription (see `IN_DEVELOPMENT.md`).

What we’ve done so far

1. LiveKit room connection off by default  
   - The Backend no longer starts the LiveKit worker thread unless `LIVEKIT_ENABLED=1` (or "true"/"yes").  
   - This avoids firewall/connection issues during development and focuses testing on the Opus pipeline.
  
2. Experimental Opus WebSocket endpoint  
   - New endpoint: `GET /ws/opus/{agent_id}` (existing `/ws/{agent_id}` is unchanged).  
   - Protocol: client sends binary messages: `[4-byte big-endian length] + [Opus packet bytes]`.  
   - Server: `Backend/opus_handler.py` – `OpusDecoder` (PyAV) decodes Opus → PCM s16 48kHz stereo; same JSON messages as main endpoint (`call_started`, `Call Ended`, `metadata`).  
   - Pipeline: decoded PCM is fed into the same AMD logic (VAD + faster-whisper) and broadcast to admins for “Listen” like the main endpoint.  
   - Dependencies: Backend `requirements.txt` includes `av` (PyAV).
  
3. Opus test client (separate folder for another machine)  
   - Folder: `OpusTester/` – standalone so it can be copied to another PC.  
   - Contents: `opus_tester.py` (connects to `ws://192.168.1.46:80/ws/opus/TesterAgent`), `requirements.txt` (av, websocket-client, PyAudio, numpy), `run_opus_tester.bat`.  
   - Behavior: captures mic (or sine wave if no mic), encodes PCM to Opus with PyAV, sends `call_started` then stream of `[4-byte length] + Opus packet`.  
   - Goal: run server on 192.168.1.46, run the tester on another machine; server logs show `[AI] Human detected` or `Speech buffered` when AMD runs; audio can be heard on the dashboard audio-test page.
  
4. Dashboard: audio testing page  
   - Bug link: top-right of the main dashboard has a bug link → `/audio-test`.  
   - Audio test page: connects to the admin WebSocket, “Start listening to TesterAgent” streams audio from the Opus test client (or any agent on the Opus endpoint).  
   - Purpose: verify that Opus-decoded audio is broadcast correctly and playable in the browser; read server console for AMD messages.
  
5. Summary of testing flow  
   - Server runs on 192.168.1.46 (or your host).  
   - On another machine: copy `OpusTester/`, run `pip install -r requirements.txt` once, then `run_opus_tester.bat` (or `python opus_tester.py`).  
   - On the server machine: open `http://192.168.1.46/audio-test`, click “Start listening to TesterAgent”.  
   - Talk (or let sine wave run); server console should show `[RECV] Opus Agent TesterAgent: audio packet #N`, then `[AI] … Human detected` or `Speech buffered` when AMD triggers.  
   - This confirms: Opus encode → send → server decode → AMD pipeline → admin listen, all without touching the main Installer or LiveKit.
  
Files added/updated in this approach

- `Backend/opus_handler.py` – OpusDecoder (now using direct libopus ctypes bindings with real Packet Loss Concealment).  
- `Backend/main.py` – `/ws/opus/{agent_id}`, lifespan LiveKit gated by `LIVEKIT_ENABLED`, `GET /audio-test`, bug link on dashboard, maintenance warning/shutdown control endpoints.  
- `Backend/templates/audio_test.html` – listen-to-TesterAgent UI.  
- `OpusTester/` – opus_tester.py, requirements.txt, run_opus_tester.bat (for testing from another machine).  
- `Backend/requirements.txt` – av, websocket-client, PyAudio (for Backend copy of tester if used locally).  
- `Installer/modules/settings_manager.py` – defaults for nightly maintenance restart (enabled, 19:00, 300s grace).  
- `Installer/main.py` – handles `maintenance_warning` (per-agent opt-out dialog) and `maintenance_shutdown` (graceful dialer close).

---

## Version 1.9.11 

### Disposition / calls table – NULL id fix (export “only up to a date”)
- Cause: under load, a race in the SQLite driver could leave some call rows with NULL `id`, breaking dashboard export from that point on.
- Prevention:
  - `log_disposition` now uses `INSERT ... RETURNING id` (SQLite 3.35+) so the inserted id is read atomically; fallback to `lastrowid` on older SQLite.
  - WAL mode enabled in `init_db()`: `PRAGMA journal_mode=WAL` for better concurrency when multiple agents save dispositions.
- Runtime healing: after each disposition save, the server runs `fix_null_call_ids()` to repair any rows with NULL id (so export recovers without restart).
- One-time schema fix: if the DB already has bad rows, run `Backend/cleandb/fix_db_schema.py` with the server stopped (or use `run_fix_db_schema.bat`). It renames `calls` → `calls_backup`, creates a fresh `calls` table with correct AUTOINCREMENT, copies data (assigning new ids where id was NULL), recreates indexes, and enables WAL. Keeps `calls_backup` for safety.
- Docs: `Backend/docs/NULL_ID_ISSUE_REPORT.md`, `Backend/docs/DISPOSITION_SAVE_ANALYSIS.md`; `TROUBLESHOOTING.md` updated with steps.

---

## Version 1.9.10
- Bug fixes and preparation for disposition/DB robustness (NULL id handling, WAL).
- Clean DB tools and `fix_db_schema` script.

---

## Version 1.9.9
- Documentation and run instructions (`RUN.md`, `PROJECT_OVERVIEW.md`, `TROUBLESHOOTING.md`).
- Setup and control panel build notes; version numbering aligned to x.y.z.

---

## Version 1.98
- Export and dashboard improvements; agent filter and stats refresh.
- Minor backend and installer stability fixes.

---

## Version 1.97
- Bug fixes and improvements to disposition flow and Excel sync.
- Tweaks to AMD timeout and VAD logging.

---

## Version 1.96
- Announcement system: control panel sends message + optional image to all agents; Installer shows popup.

---

## Version 1.95
- Bug fix: mic audio for stereo USB headsets (low pitch / choppy) – open as stereo then downmix to mono.
- Audio Capture (DO NOT BREAK) behavior documented.

---

## Version 1.94
- DB viewer pagination (Prev/Next, 500 rows per page).
- Graceful server shutdown via Control Panel; explicit startup order (lifespan).

---

## Version 1.91 till 1.93
- Features: Queue Status / Debug in Control Panel and Installer (agent list, async tasks, queue clearing).
- Diagnostic logging on server (call_started, audio packets, call ended).

---

## Version 1.86 till 1.9
- Improvements to WebSocket reconnection and error handling.
- Control panel and dashboard small fixes.

---

## Version 1.62 till 1.85
- Bug fixes and stability improvements across Backend and Installer.
- Minor UI tweaks and export behavior adjustments.

---

## Version 1.61

### Diagnostic Logging (Server)
- `[RECV] Agent X: call_started received` when server receives `call_started`.
- `[RECV] Agent X: audio received (packet #N, X bytes)` on first packet and every 100th.
- `[RECV] Agent X: call ended (total N audio packets received)` when call ends.
- Log filter "Debug / Receive" added to control panel.

### Queue Status / Debug (Control Panel)
- Queue Status button opens a window with two tabs:
  - Agents: connected agents (agent_id, status, last_seen, last_audio), Disconnect selected, Disconnect all, Auto-refresh (2s).
  - Async Tasks: table of asyncio tasks (index, name, coro, file, line, done) for visibility when tasks appear stuck.
- Server debug API:
  - `GET /api/debug/status` – agents, connection counts, asyncio task count and task list.
  - `POST /api/debug/disconnect_agent/{agent_id}` – force-disconnect one agent.
  - `POST /api/debug/disconnect_all_agents` – force-disconnect all agents.

### Debug / Queue Status (Installer)
- Debug button next to Settings in top bar.
- Dialog shows: audio_queue, metadata_queue, message_queue, capture_queue sizes; streamer connected; worker states.
- Buttons: Refresh, Clear audio queue, Clear metadata queue, Clear message queue, Clear all, Auto-refresh (1s).
- Use when streaming/audio appears stuck after a failed call.

### DB viewer pagination
- Database snapshot viewer now has Prev/Next buttons to page through 500 rows at a time.
- Page indicator shows "Rows X-Y of Total | Page N/M" per tab (calls, callbacks, etc.).
- Each tab keeps its own page; switching tabs shows the correct page.

### Graceful shutdown and explicit startup order
- Shutdown: control panel now POSTs to `/api/control/shutdown` (localhost-only) instead of killing the process. Server sends itself SIGTERM so uvicorn drains in-flight connections before exit. Falls back to terminate/kill after 30s if process doesn't exit.
- Startup: lifespan context manager ensures Outlook Manager and callback scheduler are fully initialized before the server accepts any connections. No connections until startup completes.

### Mic audio fix (stereo USB headsets) – AUDIO STREAMING FIX IMPLEMENTED
- Stereo mics (for example, Logi USB Headset) were opened as mono, causing low pitch and choppy audio.
- Same bug as loopback: interleaved [L,R] misinterpreted as mono = half-speed playback.
- Mic now opens with `channels=2` when `maxInputChannels >= 2`, then downmixes to mono before stacking with loopback.

### Announcement system
- Control panel: "Send Announcement" button opens a dialog to compose and send announcements to all connected agents.
  - Editable message (default: "There's a new update available. Please restart your app.").
  - Default image: `yippee.gif`; click image to pick another from `Backend/announcement_images/` or "Select none".
  - Sends to `POST /api/control/announcement` (localhost-only).
- Server: `broadcast_to_agents` sends `{type: "announcement", message: "...", image: "filename"}` to all Installer clients.
- Installer: receives announcement, shows non-modal popup with title "Announcement", message text, and optional image (64x64 fixed box, scaled to fit).
- Images served from `/static/announcements/<filename>`; folder `Backend/announcement_images/`.

---

## Version 1.60

### Call Registration Fix (One Call Per Connection)
- Correct flow: call connects → duration tracked → call ends (window closes) → one call record created with "No Disposition" → user clicks disposition → disposition updates that same call (no new record).
- Previously: clicking a disposition (for example, "No Response") created a second call record, inflating the dashboard count.
- Changes:
  - `log_disposition`: only when call ends (`on_call_ended`) – creates the single call record.
  - `update_disposition`: when user clicks a disposition – updates the existing "No Disposition" call.
  - `database.update_call_disposition()`: finds most recent "No Disposition" call for agent+phone (last 4h) and updates it.
  - Force-hangup/timeout: `on_call_ended` uses `_pending_force_hangup_disposition` when set (Voicemail/No Response), so no follow-up update needed.
  - Early returns (try next number): "No Response" and "Wrong Number" paths now send `update_disposition` before trying the next number.
- Disposition without calling: if user clicks a disposition without having made a call, no call is added to the dashboard. `_call_record_created` flag ensures `update_disposition` is only sent when `on_call_ended` created a record. Excel disposition save still runs. Server no longer falls back to INSERT when no matching call exists.

---

## Version 1.59

### Installer AI Monitoring Fixes
- Double processing guard: `_no_answer_handling` prevents both local timeout and server `force_hangup` from acting on the same call.
- Hang-up before next call: `hang_up()` now waits up to 4s for the call window to close; delay before auto-next increased to 3.5s; safety `hang_up()` call at start of `_process_no_answer_automation`.
- `on_call_ended` + `force_hangup`: when server sends `force_hangup`, `hang_up()` closes the call; monitor emits and `on_call_ended` runs. It now uses `_pending_force_hangup_disposition` (Voicemail/No Response) for `log_disposition` instead of "No Disposition".
- Local timeout hang_up: `handle_timeout` now calls `hang_up()` so the Teams call is actually ended when local ring detection times out.
- `human_detected` feedback: status bar shows "Human detected - call connected" when server detects human pickup.

### AMD: Silero VAD + faster-whisper (Replaces YAMNet)

Transition from general audio classification to specialized speech detection:

- Removed: YAMNet (TensorFlow) for general-purpose audio event classification.
- Added: Silero VAD for voice activity detection (faster speech triggers).
- Added: faster-whisper (tiny, INT8) for Answering Machine Detection (AMD).

Workflow:
1. Detection: Silero VAD detects "Speech Start" on incoming audio.
2. Buffering: first 3 seconds of speech are buffered.
3. Transcription: 3s chunk is passed to faster-whisper (tiny, INT8).
4. Analysis:
   - Machine / hangup: voicemail keywords in transcript or >4s speech with no VAD pause.
   - Human / keep: short greeting ("Aló?", "Hola?") and duration <2s.

Voicemail keywords (Spanish + English):
- Spanish: "buzón de voz", "no se encuentra disponibile", "deje su mensaje", "después del tono", "el número que marcó", "casilla de voz".
- English: "number you have reached", "not available", "leave a message", "after the tone", "voicemail".

Client behavior:
- `force_hangup` with `reason: "voicemail"` or `"machine_no_pause"` saves disposition "Voicemail".
- `reason: "timeout"` saves "No Response".

Dependencies added:
- `torch`, `torchaudio`, `silero-vad`, `faster-whisper`.
- Removed: `tensorflow`, `tensorflow-hub`.

Safety:
- Non-blocking: transcription runs in `asyncio.to_thread` with timeout.
- Fail-safe: on AI error, default is to keep call (assume human).

---

## Version 1.58

### Additions
- Server Control Panel (`Backend/control_panel/`)
  - Start, Stop, Restart server from GUI.
  - Run via `start_control_panel.bat` or `python -m control_panel`.
- Log viewer with filters: Full, Audio streaming, AI monitoring, Disposition, Connections.
  - Log prefixes in server: `[AUDIO]`, `[AI]`, `[DISPO]`, `[CONN]`.
- Database snapshot viewer: read-only copy of DB; does not block running server.
- Open Dashboard buttons: Local and Network URLs.

### Files
- `Backend/control_panel/` – app, server_controller, db_viewer, config.
- `Backend/start_control_panel.bat` – launcher.
- `Backend/db_snapshots/` – created on first DB view.

---

## Version 1.57
just started saving version history from here
### Status
- Backend server with FastAPI, WebSocket, AI monitoring.
- Installer client with PyQt6 GUI, Excel import, disposition handling.
- Dashboard for admin monitoring.
- IIS reverse proxy support (port 80 → 8001).

### Recent Additions / Fixes
- Lazy-load AI classifier; server starts even if TensorFlow/YAMNet fails.
- AI monitoring isolated: predict timeout, exception handling; failures do not crash server.
- When AI unavailable: no `force_hangup` (avoids hanging up on real callers); client uses local timeout.
- `ai_unavailable` message to client when AI not loaded.
- Audio device selection: filtered lists (mic vs system audio), "Remember preferred devices" option.
- Calendar day/month stats via LIKE pattern matching in database.
- Duplicate call cleanup script (`clean_db.py`).
- Comment and emoji cleanup across Backend and Installer.

---
