# TeamsPowerDialer - Version History

Documentation of program status, fixes, and addons by version.

> **Full project overview**: See `PROJECT_OVERVIEW.md` for architecture, technical flow, module roles, and recent changes.

---

## Version 1.57

### Status
- Backend server with FastAPI, WebSocket, AI monitoring
- Installer client with PyQt6 GUI, Excel import, disposition handling
- Dashboard for admin monitoring
- IIS reverse proxy support (port 80 → 8001)

### Recent Additions / Fixes
- Lazy-load AI classifier; server starts even if TensorFlow/YAMNet fails
- AI monitoring isolated: predict timeout, exception handling; failures do not crash server
- When AI unavailable: no force_hangup (avoids hanging up on real callers); client uses local timeout
- `ai_unavailable` message to client when AI not loaded
- Audio device selection: filtered lists (mic vs system audio), "Remember preferred devices" option
- Calendar day/month stats via LIKE pattern matching in database
- Duplicate call cleanup script (`clean_db.py`)
- Comment and emoji cleanup across Backend and Installer

---

## Version 1.58

### Additions
- **Server Control Panel** (`Backend/control_panel/`)
  - Start, Stop, Restart server from GUI
  - Run via `start_control_panel.bat` or `python -m control_panel`
- **Log viewer** with filters: Full, Audio streaming, AI monitoring, Disposition, Connections
  - Log prefixes in server: `[AUDIO]`, `[AI]`, `[DISPO]`, `[CONN]`
- **Database snapshot viewer**: read-only copy of DB; does not block running server
- **Open Dashboard** buttons: Local and Network URLs

### Files
- `Backend/control_panel/` – app, server_controller, db_viewer, config
- `Backend/start_control_panel.bat` – launcher
- `Backend/db_snapshots/` – created on first DB view

---

## Version 1.59

### Installer AI Monitoring Fixes
- **Double processing guard**: `_no_answer_handling` prevents both local timeout and server `force_hangup` from acting on the same call.
- **Hang-up before next call**: `hang_up()` now waits up to 4s for the call window to close; delay before auto-next increased to 3.5s; safety `hang_up()` call at start of `_process_no_answer_automation`.
- **on_call_ended + force_hangup**: When server sends `force_hangup`, `hang_up()` closes the call; monitor emits and `on_call_ended` runs. It now uses `_pending_force_hangup_disposition` (Voicemail/No Response) for `log_disposition` instead of "No Disposition".
- **Local timeout hang_up**: `handle_timeout` now calls `hang_up()` so the Teams call is actually ended when local ring detection times out.
- **human_detected feedback**: Status bar shows "Human detected - call connected" when server detects human pickup.

### AMD: Silero VAD + faster-whisper (Replaces YAMNet)

**Transition from General Audio Classification to Specialized Speech Detection**

- **Removed**: YAMNet (TensorFlow) for general-purpose audio event classification
- **Added**: Silero VAD for voice activity detection (faster speech triggers)
- **Added**: faster-whisper (tiny, INT8) for Answering Machine Detection (AMD)

**Workflow**
1. **Detection**: Silero VAD detects "Speech Start" on incoming audio
2. **Buffering**: First 3 seconds of speech are buffered
3. **Transcription**: 3s chunk is passed to faster-whisper (tiny, INT8)
4. **Analysis**:
   - **Machine/Hangup**: Voicemail keywords in transcript OR >4s speech with no VAD pause
   - **Human/Keep**: Short greeting ("Aló?", "Hola?") and duration <2s

**Voicemail Keywords** (Spanish + English)
- Spanish: "buzón de voz", "no se encuentra disponible", "deje su mensaje", "después del tono", "el número que marcó", "casilla de voz"
- English: "number you have reached", "not available", "leave a message", "after the tone", "voicemail"

**Client**
- `force_hangup` with `reason: "voicemail"` or `"machine_no_pause"` saves disposition "Voicemail"
- `reason: "timeout"` saves "No Response"

**Dependencies Added**
- `torch`, `torchaudio`, `silero-vad`, `faster-whisper`
- Removed: `tensorflow`, `tensorflow-hub`

**Safety**
- Non-blocking: transcription runs in `asyncio.to_thread` with timeout
- Fail-safe: on AI error, default is to keep call (assume human)

---

## Version 1.60

### Call Registration Fix (One Call Per Connection)
- **Correct flow**: Call connects → duration tracked → call ends (window closes) → **one** call record created with "No Disposition" → user clicks disposition → disposition **updates** that same call (no new record).
- **Previously**: Clicking a disposition (e.g. "No Response") created a second call record, inflating the dashboard count.
- **Changes**:
  - `log_disposition`: Only when call ends (`on_call_ended`) – creates the single call record.
  - `update_disposition`: When user clicks a disposition – updates the existing "No Disposition" call.
  - `database.update_call_disposition()`: Finds most recent "No Disposition" call for agent+phone (last 4h) and updates it.
  - Force-hangup/timeout: `on_call_ended` uses `_pending_force_hangup_disposition` when set (Voicemail/No Response), so no follow-up update needed.
  - Early returns (try next number): "No Response" and "Wrong Number" paths now send `update_disposition` before trying the next number.
- **Disposition without calling**: If user clicks a disposition without having made a call, no call is added to the dashboard. `_call_record_created` flag ensures `update_disposition` is only sent when `on_call_ended` created a record. Excel disposition save still runs. Server no longer falls back to INSERT when no matching call exists.

---

## Version 1.62 (Current)

### Disposition / calls table – NULL id fix (export “only up to a date”)
- **Cause:** Under load, a race in the SQLite driver could leave some call rows with NULL `id`, breaking dashboard export from that point on.
- **Prevention:**
  - **log_disposition** now uses `INSERT ... RETURNING id` (SQLite 3.35+) so the inserted id is read atomically; fallback to `lastrowid` on older SQLite.
  - **WAL mode** enabled in `init_db()`: `PRAGMA journal_mode=WAL` for better concurrency when multiple agents save dispositions.
- **Runtime healing:** After each disposition save, the server runs `fix_null_call_ids()` to repair any rows with NULL id (so export recovers without restart).
- **One-time schema fix:** If the DB already has bad rows, run **`Backend/cleandb/fix_db_schema.py`** with the server **stopped** (or use `run_fix_db_schema.bat`). It renames `calls` → `calls_backup`, creates a fresh `calls` table with correct AUTOINCREMENT, copies data (assigning new ids where id was NULL), recreates indexes, and enables WAL. Keeps `calls_backup` for safety.
- **Docs:** `Backend/docs/NULL_ID_ISSUE_REPORT.md`, `Backend/docs/DISPOSITION_SAVE_ANALYSIS.md`; TROUBLESHOOTING.md updated with steps.

---

## Version 1.61

### Diagnostic Logging (Server)
- `[RECV] Agent X: call_started received` when server receives call_started
- `[RECV] Agent X: audio received (packet #N, X bytes)` on first packet and every 100th
- `[RECV] Agent X: call ended (total N audio packets received)` when call ends
- Log filter "Debug / Receive" added to control panel

### Queue Status / Debug (Control Panel)
- **Queue Status** button opens a window with two tabs:
  - **Agents**: Connected agents (agent_id, status, last_seen, last_audio), Disconnect selected, Disconnect all, Auto-refresh (2s)
  - **Async Tasks**: Table of asyncio tasks (index, name, coro, file, line, done) for visibility when tasks appear stuck
- Server debug API:
  - `GET /api/debug/status` – agents, connection counts, asyncio task count and task list
  - `POST /api/debug/disconnect_agent/{agent_id}` – force-disconnect one agent
  - `POST /api/debug/disconnect_all_agents` – force-disconnect all agents

### Debug / Queue Status (Installer)
- Debug button next to Settings in top bar
- Dialog shows: audio_queue, metadata_queue, message_queue, capture_queue sizes; streamer connected; worker states
- Buttons: Refresh, Clear audio queue, Clear metadata queue, Clear message queue, Clear all, Auto-refresh (1s)
- Use when streaming/audio appears stuck after a failed call

### DB viewer pagination
- Database snapshot viewer now has Prev/Next buttons to page through 500 rows at a time
- Page indicator shows "Rows X-Y of Total | Page N/M" per tab (calls, callbacks, etc.)
- Each tab keeps its own page; switching tabs shows the correct page

### Graceful shutdown and explicit startup order
- **Shutdown**: Control panel now POSTs to `/api/control/shutdown` (localhost-only) instead of killing the process. Server sends itself SIGTERM so uvicorn drains in-flight connections before exit. Falls back to terminate/kill after 30s if process doesn't exit.
- **Startup**: Lifespan context manager ensures Outlook Manager and callback scheduler are fully initialized before the server accepts any connections. No connections until startup completes.

### Mic audio fix (stereo USB headsets) – AUDIO STREAMING FIX IMPLEMENTED
- Stereo mics (e.g. Logi USB Headset) were opened as mono, causing low pitch and choppy audio
- Same bug as loopback: interleaved [L,R] misinterpreted as mono = half-speed playback
- Mic now opens with `channels=2` when `maxInputChannels >= 2`, then downmixes to mono before stacking with loopback

### Announcement system
- **Control panel**: "Send Announcement" button opens a dialog to compose and send announcements to all connected agents
  - Editable message (default: "There's a new update available. Please restart your app.")
  - Default image: `yippee.gif`; click image to pick another from `Backend/announcement_images/` or "Select none"
  - Sends to `POST /api/control/announcement` (localhost-only)
- **Server**: `broadcast_to_agents` sends `{type: "announcement", message: "...", image: "filename"}` to all Installer clients
- **Installer**: Receives announcement, shows non-modal popup with title "Announcement", message text, and optional image (64x64 fixed box, scaled to fit)
- Images served from `/static/announcements/<filename>`; folder `Backend/announcement_images/`

---

## Audio Capture (DO NOT BREAK)

**Context**: Audio streaming was broken by treating stereo devices as mono. Fixed in v1.61.

**How it works now** (`Installer/modules/audio_core.py`):
1. **Loopback (system audio)**: Opened as `channels=2`, `paFloat32`. Stereo interleaved [L,R,L,R]. Downmix to mono (mean of L/R) before stacking. CRITICAL: Opening as mono causes half-speed, low-pitch playback.
2. **Mic**: Check `mic_info["maxInputChannels"]`. If >= 2, open as `channels=2` (int16). Downmix stereo to mono (mean of L/R) before resampling/stacking. If mono device, open as `channels=1`. CRITICAL: Same bug as loopback—stereo mic opened as mono = low pitch + choppy.
3. **Output**: Both streams resampled to 48kHz, stacked as stereo frame [mic_L, loopback_R, mic_L, loopback_R], sent to server.

**What would break it**:
- Opening loopback with `channels=1` → low pitch system audio
- Opening stereo mic with `channels=1` → low pitch, choppy mic audio
- Changing mic/loopback channel handling without downmixing stereo to mono first
- Mixing up which channel is mic vs loopback in the stack (column_stack: [mic_array, loopback_array])

**If mic/loopback sounds wrong (low pitch, choppy)**: Re-apply the stereo fix—open device with `channels=2` when `maxInputChannels >= 2`, then downmix to mono before processing.

---

