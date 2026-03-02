# Client Application — Module Reference

Reference for the Installer app and its modules. See repo root `PROJECT_OVERVIEW.md` for how the client fits in the full system.

## 1. main.py

Everything starts here: the UI, the wiring between components, and the core call flow.

- PyQt6 `QMainWindow` subclass that orchestrates all modules
- Initializes Excel reader, Teams controller, audio capture, streamer, disposition saver, settings, session manager, filter manager, and campaign manager
- Uses `sys.coinit_flags = 2` before imports to avoid COM error 0x80010106
- Runs background threads: `AudioWorker` (capture → queue), `StreamerWorker` (queue → server), `DialingWorker` (Teams automation)

### Key Classes & Functions

- `OverlayHighlight`, `AudioWorker`, `StreamerWorker`, `DialingWorker`
- `DebugQueueDialog`, `SettingsDialog`, `CampaignDialog`, `FiltersDialog`
- `TeamsPowerDialerApp`

### Watch out for

- COM init: `sys.coinit_flags = 2` must run before any other imports or Excel COM will fail.
- Streamer vs workers: `StreamerWorker` only starts/stops audio streaming; the WebSocket connection is long-lived and started at app startup.

---

## 2. config.py (Installer root)

Holds server URLs, audio settings, OneDrive path, and other constants. Paths, `SERVER_URL`, `SERVER_API_URL`. `_detect_onedrive_path()` for OneDrive folder.

---

## 3. launcher.py

Checks for updates from the update server, downloads and extracts if newer, then starts `main.py`. Reads `local_version.json`, fetches from `UPDATE_URL`; extracts skipping `PROTECTED_FILES`; runs pip install; launches `main.py`.

**PROTECTED_FILES** (not overwritten on update): `user_settings.json`, `session_history.json`, `teams_dialer.db`, `client_history.db`, `config.py`.

---

## 4–15. modules (summary)

| Module | Purpose |
|--------|---------|
| `active_data_reader.py` | Excel COM → contacts; `load_data_shadow_copy`, `process_contacts` |
| `audio_core.py` | Mic + loopback capture, 48 kHz stereo [mic, loopback] |
| `streamer.py` | WebSocket to Backend; audio + JSON; callbacks and dashboard audio (no server-side AMD) |
| `teams_auto.py` | Teams dialing; pywinauto "Call" button, call window monitor |
| `disposition_manager.py` | Save to source `_DISPO_STAMP` + per-disposition Excel files; autosave |
| `campaign_manager.py` | FRESH/RECYCLE/TARGETED/ALL filter |
| `filter_manager.py` | State/Industry/Time filters |
| `settings_manager.py` | `user_settings.json` |
| `session_manager.py` | `session_history.json` |
| `data_handler.py` | Thin wrapper over reader (main.py uses reader directly) |
<!-- LiveKit-based streaming has been fully retired; audio uses only the WebSocket `AudioStreamer`. -->

---

## Quick Reference: Data Flow

```
Excel → ActiveExcelReader → process_contacts → contact_list
User clicks Call → TeamsController.start_call()
Call starts → AudioWorker
  ├─ loopback → AMDWorker (client-side AI: Silero VAD + faster-whisper) → human/voicemail/bot decision
  └─ audio_queue → StreamerWorker → AudioStreamer → Server (for dashboard/monitoring only)
Call ends → handle_disposition → DispositionSaver → source + Dispositions/*.xlsx
```
