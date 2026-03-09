# TeamsPowerDialer - Version History

Documentation of program status, fixes, and addons by version.

> Full project overview: see `PROJECT_OVERVIEW.md` for architecture, technical flow, module roles, and recent changes.

Version numbering: from version 1.9.9 onward we use x.y.z (e.g. 1.9.11, 1.9.12). Major version 2.0.0 marks the switch to client-side AI. Earlier entries keep the previous numbering for history.

---

## Version 2.1.0

### Status
- First “hardening” release after 2.0.x: moves the Installer away from a single god-script window into a **state-driven workflow core** with safer threading, COM handling, and backend identity/persistence.
- Focus areas:
  - Structural decoupling and explicit call-session state.
  - Thread-safe websocket/UI boundaries.
  - SQLite identity and connection lifecycle.
  - Excel COM stability during dispositions.
  - Basic admin surface + updater integrity hardening.

### Installer – structural refactor (CallWorkflowManager, CallSession)
- Introduced a formal call-session model in `Installer/modules/call_state.py`, `call_session.py`, and `call_workflow_manager.py`:
  - `CallSessionState` enum (IDLE, READY, DIALING, CALLING, CONNECTED, AMD_MONITORING, HUMAN_CONFIRMED, DISPOSITION_PENDING, AUTO_ADVANCING, HANGING_UP, ENDED, ERROR).
  - `CallSession` dataclass as the **single source of truth** for per-call/per-session flags (autocall active, waiting_for_disposition, bot_initiated_hangup, confirmed_human, has_no_response_history, call_id, etc.).
  - `CallWorkflowManager` owns all transitions (`begin_call`, `mark_call_started`, `mark_call_connected`, `mark_waiting_for_disposition`, `mark_hanging_up`, `mark_ended`, `set_autocall_active`, `clear_waiting_for_disposition`).
- `TeamsPowerDialerApp` in `Installer/main.py` now:
  - Holds a `workflow_manager` rather than keeping all flags on the window.
  - Exposes thin property proxies (`call_status`, `is_autocalling_active`, `_bot_initiated_hangup`, `_confirmed_human`, `waiting_for_disposition`, `_autocall_was_active_before_wait`, `_no_answer_handling`, `has_no_response_history`, `_call_record_created`, `current_call_id`, `current_ui_state`) that simply read/write the `CallSession`.
  - Uses these transitions inside `start_call`, `on_call_started`, `on_call_connected`, `on_call_ended`, `handle_human_detected`, `handle_local_hangup`, `handle_force_hangup`, `_process_no_answer_automation`, and `handle_disposition`.
- Result: the Installer’s call lifecycle is now explicit, testable, and no longer depends on scattered booleans on the main window.

### Installer – thread-safe messaging (QtMessageDispatcher)
- Added `Installer/modules/message_dispatcher.py` with `QtMessageDispatcher` (QObject) that:
  - Receives parsed websocket messages from the streamer thread and re-emits them as Qt signals on the **main thread** (`force_hangup_received`, `ai_unavailable_received`, `human_detected_received`, `announcement_received`, `maintenance_warning_received`, `maintenance_shutdown_received`, `callback_*`, `audio_ack_received`, `call_logged`, etc.).
- Updated `Installer/modules/streamer.py`:
  - `AudioStreamer` now accepts an optional dispatcher and calls `dispatcher.dispatch(message_dict)` from `_message_receiver_loop`.
  - Direct UI callbacks from the websocket thread were removed; all UI side-effects now flow through Qt signals.
- `TeamsPowerDialerApp.setup_connections` wires dispatcher signals to slots like `handle_force_hangup`, `handle_ai_unavailable`, `handle_announcement`, `handle_maintenance_warning`, `handle_maintenance_shutdown`, `handle_callback_prompt`, `_on_callback_created_message`, `_on_call_logged_message`.
- Result: websocket handling is now **Qt-thread safe**, duplicated inbound paths have been removed, and message delivery is easier to reason about.

### Installer – Excel COM stability and background import
- Introduced `Installer/modules/excel_import_worker.py` (`ExcelImportWorker`):
  - Lives in its own `QThread`, calls `pythoncom.CoInitialize()` in the worker thread, then uses a private `ActiveExcelReader` to load data.
  - Emits only plain Python data back to the UI (`contacts`, `sheet_name`, `row_count`), avoiding COM objects crossing threads.
- `TeamsPowerDialerApp`:
  - Uses `ExcelImportWorker` via a `request_import_signal` and `_on_import_worker_finished` / `_on_import_worker_error`.
  - Re-acquires the workbook path on the main thread (`ActiveExcelReader.get_active_file_path()`) and passes only `workbook_path` into `DispositionSaver`.
- `DispositionSaver`:
  - Continues to use `_get_workbook_in_thread()` to reattach to Excel by path inside its own background threads (`save_contact_async`, `save_workbook_async`, `delete_phone_number_from_source`).
  - `_bring_excel_to_foreground` is now a no-op to prevent zombie Excel windows caused by repeated `Application.Visible` changes.
- Result: importing contacts and saving dispositions no longer blocks the UI or causes COM “marshalled for a different thread” errors; Excel windows stay stable during high-volume disposition activity.

### Installer – client-side AI lifecycle tweaks
- `Installer/modules/ai_core.py`:
  - `AMDDetector` now preloads a **shared Silero VAD model** once (`_ensure_vad`) and returns per-call deep-copied VAD instances via `get_new_vad_model()`, falling back to a fresh load only if deepcopy fails.
  - Whisper model usage is unchanged: one shared `tiny.en` CPU `WhisperModel` with the tuned decoding parameters (beam_size, best_of, temperature ladder).
- `CLIENT_SIDE_AI.md` and `AUTOCALL_WORKFLOW.md`:
  - Updated to document the new AMDWorker wiring, shared VAD/Whisper lifecycle, and the formal `CallSessionState` transitions (with mermaid state diagrams).

### Backend – call identity and DB connection helper
- `Backend/database.py`:
  - Added a stable `call_id` column to the `calls` table and backfilled missing values with UUIDs (`_ensure_call_ids_impl`).
  - `log_call`, `log_disposition`, and `create_call_record` now:
    - Accept or generate a UUID `call_id`.
    - Insert `call_id` alongside agent/phone/disposition data.
    - Return the `call_id` to callers.
  - `update_call_disposition`:
    - Now prefers an explicit `call_id` when supplied.
    - Falls back to the previous heuristic (most recent row for agent+phone within a time window) only when no `call_id` is provided.
- `Backend/db_connection.py`:
  - New helpers `get_connection(DB_PATH)` and `get_readonly_connection(DB_PATH)` wrap `sqlite3.connect(...)` with:
    - `row_factory = sqlite3.Row`, `busy_timeout`, and consistent commit/rollback semantics.
  - `database.py` was refactored to use these helpers for connection lifecycle instead of scattered `sqlite3.connect(...)` calls.
- `Backend/main.py`:
  - `POST /api/calls` now returns `{"id": call_id, "call_id": call_id, "status": "created"}` to the Installer.
  - WebSocket disposition paths (`log_disposition`, `update_disposition`) accept and pass along `call_id` to database helpers.
- Installer–Backend integration:
  - Installer stores the returned `call_id` in `CallSession` and uses it for all subsequent `log_disposition` / `update_disposition` messages.
  - `CallWorkflowManager.sync_from_backend_message` handles `{"type": "call_logged", "call_id": ...}` to keep session and DB identity in sync.

### Backend – WAL-safe snapshots and admin surface hardening
- `Backend/control_panel/db_viewer.py`:
  - Switched snapshot creation from `shutil.copy2` to the official SQLite backup API:
    - Opens a live connection to the main DB and backs it up into a snapshot DB using `source.backup(dest)`.
    - This correctly incorporates WAL state and avoids stale or corrupt snapshots under concurrent writes.
- `Backend/control_panel/config.py`, `app.py`, `server_controller.py`, `db_viewer.py`:
  - Introduced `INTERNAL_API_TOKEN` (optional env var) and `build_auth_headers()` helper.
  - Control panel now passes this token in `Authorization: Bearer ...` headers when calling:
    - `/api/debug/status`
    - `/api/debug/disconnect_agent/{agent_id}`
    - `/api/debug/disconnect_all_agents`
    - `/api/control/shutdown`
    - `/api/control/maintenance_warning`
    - `/api/control/maintenance_shutdown`
    - `/api/control/announcement`
- `Backend/main.py`:
  - Added `_request_is_local_or_authorized(request)` to gate:
    - All `/api/debug/*` endpoints.
    - All `/api/control/*` endpoints (shutdown, maintenance, announcements).
  - This keeps localhost‑only behavior by default, with the option to authorize remote control tools via a simple Bearer token.

### Dashboard & admin UI – layout and static assets
- Updated the **Exports** section on the main dashboard (`Backend/templates/dashboard.html`) to make time filtering and actions clearer:
  - Added calendar-style date pickers for the **from / to** range so admins can more easily select export windows without typing ISO timestamps.
  - Clarified the export controls and labels so it is more obvious which downloads respect the selected date range and which are “export all” style.
  - Minor layout and copy tweaks to keep the export tools consistent with the hardened backend export rules (dedupe, disposition filtering).
- Introduced a shared static asset layout in `Backend/static/` and mounted it in `Backend/main.py` as `/static`:
  - Moved inline CSS/JS from `Backend/templates/dashboard.html`, `remote_console_panel.html`, `remote_console_login.html`, and `audio_test.html` into `static/css/*.css` and `static/js/*.js` while keeping all behavior identical (including CSRF handling for the remote console).
  - Normalized the dark-theme styles across dashboard, remote console, and audio-test pages so they share a single base palette and typography.
  - Fixed the dashboard layout regression caused by the static split so the **Data Export** sidebar and **Live operations** agent grid once again sit side-by-side (flex layout on the dashboard body), matching the pre-refactor appearance.

### Installer & Backend – updater integrity
- `Backend/main.py`:
  - Added `/client_package.json` manifest endpoint that returns:
    - The update ZIP filename.
    - Its SHA-256 hash.
    - Its file size.
  - Reused the existing `/client_package.zip` endpoint but now through a helper that determines the active ZIP path and is shared with the manifest.
- `Installer/launcher.py`:
  - On update:
    - Fetches `client_package.json` (when available), then downloads `client_package.zip`.
    - Computes the local SHA-256 of `update.zip` and compares it to the server manifest before installing.
    - Refuses to install if the hash does not match.
  - Enforces safe extraction:
    - Rejects archive members with unsafe paths (absolute, parent-directory traversal, drive-qualified).
    - Writes each file through a controlled path, honoring `PROTECTED_FILES`.
  - Reduces redundant `pip install -r requirements.txt` calls:
    - Computes a hash of `requirements.txt` and only runs pip when that hash changes, recording it in `.requirements.sha256`.

---

## Version 2.0.9

### Autocall and in-call UX
- Expanded phrase lists and tuning for client-side AMD/autocall so voicemail, invalid-number, and bot-screening phrases are detected more reliably (fewer “No Response” mislabels).
- Autocall TTS prompts (“Now calling [First Last]…”) are enabled by default so agents get clearer context when the dialer advances through contacts.
- Small arbitration tweaks in `Installer/main.py` so manual dispositions win over automatic ones and autocall stops more predictably when agents intervene.

### Database / cleandb tools
- Improved how dispositions are saved and repaired in `Backend/database.py` and `Backend/cleandb/` so manually chosen dispositions reliably overwrite placeholder rows (for example, “No Response” or “No Disposition”).
- Added and refined `cleandb` scripts:
  - Repair dispositions from local CSV/XLSX files (`repair_dispositions_from_files.py`) with name+phone / phone-only / name-only matching.
  - Diagnose file vs DB mismatches (`diagnose_disposition_match.py`) and generate human-readable reports for support.
  - Dedupe calls per contact name, keeping the row with the most contact info and highest disposition importance.
  - Fill missing `contact_data` from disposition/lead files and delete rows that still have no usable contact info for exports.
- Overall: more predictable exports (fewer duplicate or half-empty rows) and safer DB cleanup (always with backups and dry-run modes).

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
