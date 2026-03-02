## Autocall Workflow – Start to Finish

This document explains **exactly what happens** when you use **Auto-Call** in the Teams Power Dialer, from the moment you click the button in the UI until the app moves to the next contact. It is written for someone with **basic coding knowledge** (can read function names, understands “thread” and “module” at a high level).

Names in backticks like `toggle_autocall` (method) are **functions or methods**. Names like `teams_auto.py` (module/file) are **modules** (Python files).

---

## 1. Where the main pieces live

- **UI & main logic**: `Installer/main.py` (file/module)
  - Class `TeamsPowerDialerApp` (QMainWindow subclass) – the main window and call controller
  - Important methods:
    - `toggle_autocall` (method) – turns autocall on/off
    - `start_call` (method) – starts a single call to the current phone number
    - `on_call_started` / `on_call_ended` (methods) – handle life-cycle of a call
    - `handle_no_answer_timeout` (method) – handles long ringing with no answer
    - `handle_disposition` (method, not shown here, but lives in `main.py`) – applies the user-selected disposition
    - `reset_autocall_state`, `_stop_autocall_listening` (methods) – manage paused/autocall states
    - `stop_audio_threads` (method) – stops audio capture + streaming threads after each call

- **Teams automation**: `Installer/modules/teams_auto.py` (module/file)
  - Class `TeamsController` (QObject subclass) – **clicks the Teams call button**, finds the call window, and watches it.
  - Emits signals:
    - `call_started_signal` (Qt signal)
    - `call_connected_signal` (Qt signal)
    - `call_ended_signal` (Qt signal)
    - `call_failed_signal` (Qt signal; Teams “we couldn’t connect you” or recording failure)

- **Audio capture and streaming**:
  - `Installer/modules/audio_core.py` (module/file) – class `AudioCapture` (audio capture class)
    - Captures **mic + loopback** audio at 48 kHz.
    - Mixes into a mono stream and pushes chunks into a queue.
  - `Installer/modules/streamer.py` (module/file) – class `AudioStreamer` (WebSocket streamer class)
    - Maintains a WebSocket connection to the Backend.
    - Sends audio bytes from a queue + JSON metadata (e.g. “Call Started”, “Call Ended”, dispositions).
  - In `main.py`:
    - `AudioWorker` (QThread subclass) – calls `AudioCapture` and pushes audio chunks into `audio_queue`.
    - `StreamerWorker` (QThread subclass) – pulls from `audio_queue` and calls `AudioStreamer.stream_audio(...)`.

- **Client-side AI (AMD)**: `Installer/modules/ai_core.py` (module/file)
  - AMD = Answering Machine Detection.
  - Class `AMDDetector` (class) + `AMDState` (class in `main.py`) + `AMDWorker` (QThread subclass)
  - Decides **voicemail / bot / invalid number / human**, tells the main window whether to **auto-hangup** or **keep call**.
  - Documented in detail in `Installer/modules/CLIENT_SIDE_AI.md` (documentation file).

- **Filters, session, dispositions**:
  - `Installer/modules/filter_manager.py` (module/file) – time-of-day / state / industry filtering; affects which contacts get called next.
  - `Installer/modules/session_manager.py` (module/file) – remembers last workbook, row, etc.
  - `Installer/modules/disposition_manager.py` (module/file) – `DispositionSaver` (class); writes results back to Excel and per-disposition files.

---

## 2. The Auto-Call button in the UI

In `TeamsPowerDialerApp.setup_ui` (method in `main.py`), the top bar has a button:

- `self.btn_autocall_toggle = QPushButton("▶ Start Auto-Call")`
- Connected in `setup_connections` (method):
  - `self.btn_autocall_toggle.clicked.connect(self.toggle_autocall)`

So when you click **“Start Auto-Call”**:

1. Qt calls the method `TeamsPowerDialerApp.toggle_autocall(self)` (method call on the main window).

### 2.1 What `toggle_autocall` does

Code location (simplified):

```python
def toggle_autocall(self):
    # 1) If we were paused waiting for a disposition, reset that state.
    if getattr(self, "waiting_for_disposition", False):
        print("[Autocall] Resetting paused state via toggle_autocall().")
        self.reset_autocall_state()

    if self.is_autocalling_active:
        # 2) TURNING OFF: move from active → stopped.
        self.is_autocalling_active = False
        self.btn_autocall_toggle.setText("▶ Start Auto-Call")
        self.btn_autocall_toggle.setToolTip("Start autocall to begin monitoring and auto-advance")
        # Reset button style (dark theme)
        self.btn_autocall_toggle.setStyleSheet("...dark gray style...")
        self._no_answer_automation_timer.stop()
        call_in_progress = self.call_status in ("Dialing", "Calling", "Connected")
        if call_in_progress:
            # Stop AI + timers, but DO NOT hang up the live call.
            self._stop_autocall_listening()
        self.statusBar().showMessage("Autocall stopped.")
    else:
        # 3) TURNING ON: move from stopped → active.
        self.is_autocalling_active = True
        self.btn_autocall_toggle.setText("■ Stop Auto-Call")
        self.btn_autocall_toggle.setToolTip("Stop autocall (safe stop; does not hang up active call)")
        self.btn_autocall_toggle.setStyleSheet("...dark gray style...")
        self.statusBar().showMessage("Autocall started.")
        if self.current_contact:
            # Already on a contact: start call immediately.
            self.start_call()
        else:
            # No contact loaded: move to first valid contact, then start.
            self.move_to_next_valid_contact()
            if self.current_contact:
                self.start_call()
```

**Plain English:**

- If autocall is **ON**, clicking the button:
  - Stops the “no-answer auto-advance” timer.
  - Stops the local AI worker and no-answer timer **if** a call is in progress.
  - Leaves the Teams call itself alone (you can still hang up manually).
- If autocall is **OFF**, clicking the button:
  - Marks `is_autocalling_active = True`.
  - If a contact is already loaded, it **immediately** calls `start_call()`.
  - Otherwise it moves to the next valid contact and then calls `start_call()`.

---

## 3. Starting a call (when Auto-Call is ON)

When `toggle_autocall` decides to start a call, it calls:

- `self.start_call()` (method in `TeamsPowerDialerApp`).

Inside `start_call` (simplified):

1. It checks that:
   - A contact is loaded (`self.current_contact`), and
   - A phone number is selected (`self.current_phone_idx`).
2. It sets `call_status = "Dialing"` and starts a small **dot animation** timer to show `Calling.` → `Calling..` → `Calling...`.
3. It creates and starts a `DialingWorker` QThread that:
   - Calls `TeamsController.start_call(phone_number)`.
   - Emits a `finished_signal(success: bool)` that feeds back into `TeamsPowerDialerApp`.
4. In parallel, it:
   - Ensures the **WebSocket streamer** is running (`self.streamer.start()` is called in `__init__`).
   - Prepares to start audio capture and AMD when the call actually connects.

**Teams side (`teams_auto.py`):**

- `TeamsController.start_call(phone_number)`:
  - Cleans the number and builds a `callto:` URL for Teams (`callto:5551234567`).
  - Uses `os.system('start callto:...')` to ask Windows/Teams to dial.
  - Searches for the Teams confirmation dialog and clicks the **“Call”** button.
  - Sets up a baseline list of Teams windows to help it later identify the **call window**.
  - Returns `True` if it believes the call was successfully initiated.

If initiating the call fails, the dialing worker finishes with `success=False`, and `start_call` can show an error or fail gracefully. In the normal case, Teams opens a new call window shortly after that.

---

## 4. When the call actually starts and connects

### 4.1 Detecting call start and end (TeamsController)

`TeamsController.monitor_call_window(...)` runs in a Python thread and:

1. Uses the baseline windows captured in `start_call` to find the **new** Teams window that represents the active call.
2. Once it finds that window, it:
   - Watches for the **call timer** (e.g. “00:05”) or other signals that the call is live.
   - When the timer appears the first time, it emits `call_connected_signal`.
   - When the call window closes, it emits `call_ended_signal`.
   - If it sees Teams show a **connection/recording failure** plus a “Dismiss” button, it clicks Dismiss and emits `call_failed_signal`.

These Qt signals are connected in `TeamsPowerDialerApp.setup_connections`:

- `call_started_signal` → `on_call_started`
- `call_connected_signal` → `on_call_connected`
- `call_ended_signal` → `on_call_ended`
- `call_failed_signal` → `handle_call_failure`

### 4.2 `on_call_started`

This runs **once per call** when Teams indicates the call has opened:

- Logs the start time (`call_start_time`).
- Resets per-call flags:
  - `_confirmed_human = False`
  - `waiting_for_disposition = False`
  - `_autocall_was_active_before_wait = False`
- Sends a “Call Started” message to the server via `self.streamer.send_json_message(...)`:
  - Includes phone number, contact name, agent id, etc.
- Increments the “calls today” counter.

### 4.3 `on_call_connected`

This runs when the **in-call timer** appears in Teams (more precise than just “call window exists”):

- Records `actual_start_time = time.time()` – used later to compute the accurate call duration.
- Registers WebSocket handlers:
  - `"force_hangup"` → `handle_force_hangup`
  - `"audio_ack"` → `handle_audio_ack`
- **Starts audio streaming + AI**:
  - Starts `AudioWorker` (mic + loopback capture to `audio_queue`, plus loopback-only to `amd_queue`).
  - Starts `StreamerWorker` (reads `audio_queue` and sends audio to server).
  - Creates `AMDDetector` and `AMDWorker` to run local AMD (client-side AI).
  - Hooks `AMDWorker` signals:
    - `human_detected_signal`
    - `force_hangup_signal`
    - `continue_result_signal`
    - `transcription_update_signal`
    - `speech_detected_signal` (resets no-answer timer when someone speaks).
  - Starts the **no-answer timer** (`no_answer_timer`), which later calls `handle_no_answer_timeout` if the phone just keeps ringing.

In other words, **once the call is connected**:

- Audio is captured and streamed.
- Local AI is listening.
- The app is ready to auto-hangup on voicemail/bots, or keep the call if a human answers.

---

## 5. During the call – AI and timers

During a connected call, these components run in parallel:

1. **AudioWorker (thread)** – uses `AudioCapture` to:
   - Read mic + loopback audio at device rates.
   - Resample both to 48 kHz.
   - Mix into a single mono stream.
   - Push mixed frames into `audio_queue` (for the server).
   - Push **loopback-only** float32 frames into `AMDWorker.input_queue` (for AI).
   - Detect “high speaker volume but no mic” for a long time → sets `timeout_detected = True`, which feeds into the call timeout logic.

2. **StreamerWorker (thread)** – runs `AudioStreamer.stream_audio(audio_queue)`:
   - Maintains a WebSocket connection.
   - Sends mixed audio bytes to the Backend continuously.

3. **AMDWorker (thread)** – uses `AMDState` + `AMDDetector` (from `ai_core.py`):
   - Processes loopback audio.
   - Emits:
     - `human_detected_signal` – marks `_confirmed_human = True`, and the arbitrator will later pause autocall.
     - `force_hangup_signal(reason)` – sets `_pending_force_hangup_disposition` (e.g. “Voicemail”) and hangs up.
     - `continue_result_signal` – extends the no-answer timer when only ambiguous sound was detected.
     - `speech_detected_signal` – tells the main window to reset the 35s no-answer timer whenever real speech is heard.

4. **Timers in the main window**:
   - `no_answer_timer` – fires `handle_no_answer_timeout` if the call is ringing for too long with no speech.
   - `_no_answer_automation_timer` – after a call **ends**, this timer is used to wait a short delay (e.g. 3.5 seconds) before auto-moving to the next contact when autocall is active.

### 5.1 `handle_no_answer_timeout`

When the call just rings and rings (high loopback volume, no microphone activity, and no AI-detected human/voicemail), the no-answer timer fires:

- Sets `_pending_force_hangup_disposition = "No Response"`.
- Treats the call as a no-answer outcome.
- Invokes the same internal logic used for other automated hangups (e.g. it will eventually cause autocall to advance after the call ends).

This is the **“no one picked up”** safety net.

---

## 6. How a call ends and how autocall moves on

When the call ends (for any reason):

- `TeamsController` emits `call_ended_signal`.
- `TeamsPowerDialerApp.on_call_ended` is called.

### 6.1 What `on_call_ended` does (high level)

Inside `on_call_ended`:

1. **Sends “Call Ended” to the server**:
   - Uses `actual_start_time` (if available) or `call_start_time` to calculate a duration in seconds.
   - Sends a JSON message via `self.streamer.send_json_message({...})` including:
     - status `"Call Ended"`
     - `phone`, `contact_name`, `duration`, `agent_id`, etc.

2. **Logs a disposition on the server**:
   - Chooses a disposition:
     - If `_pending_force_hangup_disposition` is set (e.g. `"Voicemail"`, `"No Response"`, `"Invalid Number"`), that value is used.
     - Otherwise defaults to `"No Disposition"` (until user picks one manually).
   - Sends another message `{"type": "log_disposition", ...}` with `contact_data` attached.
   - Sets `_call_record_created = True` so the later UI “disposition click” will send an **update** instead of creating a duplicate.

3. **Runs the “arbitrator” logic** (decides autocall behavior):

Key variables:

- `last_text` – last AI transcript from `AMDWorker` (used to detect invalid numbers).
- `is_invalid` – `True` if transcript contains any phrase from `INVALID_NUMBER_PHRASES`.
- `duration` – call duration in seconds.
- `had_confirmed_human` – whether AI saw a human greeting (`_confirmed_human`).
- `has_pending_forced_dispo` – whether `_pending_force_hangup_disposition` attribute exists.
- `bot_initiated` – `_bot_initiated_hangup`: `True` if **automation** (AI or Teams error handler) initiated the hangup.
- `is_autocalling_active` – whether the user currently has autocall ON.

The decision tree:

```python
if is_invalid and not bot_initiated:
    # Transcript clearly says invalid number, and the user (not bot) ended.
    self.handle_disposition("Wrong Number")
elif self.is_autocalling_active:
    if bot_initiated and has_pending_forced_dispo:
        # Bot/automation ended the call AND set a disposition (e.g. No Response, Voicemail).
        # → Continue autocall after short delay (timer).
        self._no_answer_automation_timer.start(3500)
    elif had_confirmed_human and not bot_initiated:
        # Human detected during this autocall session, user hung up.
        # → Pause autocall to let user choose a disposition.
        self.waiting_for_disposition = True
        self._autocall_was_active_before_wait = True
        self.is_autocalling_active = False
        # Button text changes to "Waiting for Disposition..."
    elif duration < 10 and not bot_initiated:
        # Very short call with autocall active, user likely killed quickly.
        # → Safe Stop: turn autocall OFF.
        self.toggle_autocall()
    elif not bot_initiated:
        # Normal case: remote hung up or call ended naturally during autocall.
        # → Continue autocall after short delay.
        self._no_answer_automation_timer.start(3500)
```

Finally:

- `stop_audio_threads()` is called to stop AudioWorker and StreamerWorker.
- `_bot_initiated_hangup` and `_confirmed_human` are reset for the next call.

### 6.2 How it moves to the next contact

When `_no_answer_automation_timer` fires, it calls `_process_no_answer_automation` (in `main.py`), which:

1. Checks if autocall is still active.
2. Chooses the **next contact and phone number**:
   - Uses `move_to_next_valid_contact()`.
   - Takes filters into account via `FilterManager` (time window, state, etc.).
3. Calls `start_call()` again for the next phone number.

This loop continues until:

- You click the **Stop Auto-Call** button (which stops autocall and listening).
- You hit a safe-stop condition (e.g. very short calls repeatedly).
- You run out of valid contacts.

---

## 7. Special case: Teams connection/recording failure

If Teams shows **“We couldn’t connect you”** or **“There was a problem setting up the recording”**:

1. `TeamsController.monitor_call_window`:
   - Detects the error text in the call window.
   - Finds and clicks the `"Dismiss"` button.
   - Sets `monitoring = False` and emits `call_failed_signal`.

2. In `TeamsPowerDialerApp.handle_call_failure`:
   - Logs a message: `"[Main] Connection failure detected - auto-dispositioning as No Response."`
   - Sets `_bot_initiated_hangup = True`.
   - Sets `_pending_force_hangup_disposition = "No Response"`.
   - Calls `stop_audio_threads()` because the call never really started.

3. Later, `on_call_ended` runs as usual, sees:
   - `bot_initiated = True`
   - `has_pending_forced_dispo = True` (No Response)
   - `is_autocalling_active = True`

   and so the arbitrator branch:

   - **“Bot/automation hangup with pending disposition → continuing autocall after short delay”**

   is triggered, and `_no_answer_automation_timer` fires to go to the **next contact**.

---

## 8. Takeaways for maintainers

- **Autocall ON** means:
  - After each call, `on_call_ended` will decide whether to:
    - **Pause** (waiting for disposition, when human was confirmed), or
    - **Stop** (safe-stop short call), or
    - **Continue** (normal progression / AI or Teams decided the outcome).

- **Auto-advance** is always driven by:
  - `_no_answer_automation_timer` → `_process_no_answer_automation` → `move_to_next_valid_contact()` → `start_call()`.

- **Local AI** drives most decisions:
  - Sets `_pending_force_hangup_disposition`.
  - Sets `_confirmed_human`.
  - Calls or influences `_bot_initiated_hangup`.

- **Server** is mostly passive:
  - Receives audio and JSON from `AudioStreamer`.
  - Stores call logs and dispositions.
  - Does **not** control autocall; the **client is the master** of call intent and behavior.

