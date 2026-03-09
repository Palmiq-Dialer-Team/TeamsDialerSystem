# Client-Side AI (AMD) – Phrases, Logic, and Maintenance

This document explains how the client-side Answering Machine Detection (AMD) works, which phrase lists control hangup vs keep-call behavior, and how to add or tune phrases when users report issues (for example, “voicemail wasn’t skipped” or “it hung up on a human”). It is intended for maintainers taking over the project.

Code location: `Installer/modules/ai_core.py`

---

## 1. What the client-side AI does

When **Autocall is ON** and a call is **Connected**, the Installer:

1. Captures loopback (system) audio only (what the remote party says) alongside mic audio.
2. Feeds loopback audio into an `AMDWorker` thread, which wraps:
   - `AMDDetector` from `ai_core.py` (shared Whisper model + VAD factory).
   - `AMDState` in `main.py` (per-call state machine).
3. Uses Silero VAD (16 kHz mono) to detect speech, buffer a few seconds of speech (`BUFFER_SECONDS`), and decide **when** to transcribe.
4. Calls faster-whisper once enough speech has been buffered, then runs phrase matching on the transcript to decide:
   - **Hang up** – voicemail, bot/screening, invalid number (local AI sets a pending disposition and triggers a hangup).
   - **Keep call** – human detected.
   - **Keep listening** – ambiguous; may transcribe again later, with a fail-safe that eventually treats it as human.

All AMD logic (VAD + Whisper + intent detection) runs **entirely on the client**:

- The Backend does **not** run VAD/Whisper and does **not** send any AMD `force_hangup` messages.
- The server only receives mixed audio for monitoring and the final disposition metadata once the call outcome is known.

---

## 2. Libraries and model lifecycle

| Library | Role |
|--------|------|
| Silero VAD (`torch.hub.load("snakers4/silero-vad", "silero_vad")`) | Voice activity detection: “is there speech in this chunk?”. Runs on 16 kHz mono. **Preloaded once** per process via `AMDDetector._ensure_vad()`. Each call gets a **deep-copied** model instance with its own state. |
| faster-whisper | Transcribes buffered audio to text. Model: `tiny.en` (English), CPU, float32. First run downloads the model from Hugging Face (ensure `pip-system-certs` is in requirements for HTTPS). Held as a **single shared Whisper model** inside `AMDDetector`. |
| torch / torchaudio | PyTorch for VAD and resampling. `torchaudio.transforms.Resample(48000, 16000)` resamples loopback 48 kHz → 16 kHz for VAD and Whisper. |
| numpy | Audio buffers and array handling. |

Relevant constants in `ai_core.py`: `INPUT_RATE = 48000`, `VAD_RATE = 16000`, `BUFFER_SECONDS = 5`, `VAD_SPEECH_THRESHOLD = 0.65`, `REMOTE_SPEECH_THRESHOLD = 0.6`, `HUMAN_GREETING_MAX_SEC = 2`.

---

## 3. High-level flow

1. Call connects (`TeamsController.call_connected_signal`) → `TeamsPowerDialerApp.on_call_connected`:
   - Starts `AudioWorker` and `StreamerWorker`.
   - Creates (or reuses) a global `AMDDetector`.
   - Creates an `AMDWorker` QThread for this call, passing it:
     - The shared `AMDDetector`.
     - A fresh `AMDState` object that tracks per-call VAD/transcript state.
   - Hooks AMD signals:
     - `human_detected_signal` → `handle_human_detected`
     - `force_hangup_signal(reason)` → `handle_local_hangup(reason)`
     - `continue_result_signal` → `_on_amd_continue_extend_timer`
     - `transcription_update_signal` → `_append_transcription_display`
2. First ~3 seconds → grace period: AMDState ignores VAD so ringback tones / early noise don’t trigger early decisions.
3. VAD runs on 16 kHz loopback chunks (~32 ms). When speech probability stays above threshold for several chunks, AMDState moves into `speech_buffering` and starts filling a buffer of up to `BUFFER_SECONDS` (~5 s).
4. Buffering:
   - Early transcription: if there is a clear speech event and subsequent silence > ~1.2 s and the buffer has ≥ ~2 s of audio, the buffer is sent to Whisper early.
   - Full buffer: when `BUFFER_SECONDS` is reached, the accumulated buffer is sent to Whisper.
5. Transcription → `AMDDetector.transcribe(...)`:
   - Uses the **shared** Whisper model (lazy-loaded on first use).
   - Returns a text string or empty string on failure.
6. Analysis → `analyze_buffered_audio()` in `ai_core.py`:
   - Calls `detect_call_intent(text)` (see phrase lists below) to classify:
     - `invalid_number`
     - `bot_screening`
     - `voicemail`
     - or `none`.
   - If intent is `invalid_number` / `bot_screening` / `voicemail` → returns `("force_hangup", reason)`.
   - Else if `is_human_greeting(text)` and buffer duration \< `HUMAN_GREETING_MAX_SEC` (~2 s) → `("human_detected", reason)`.
   - Else if transcript is empty or ambiguous:
     - AMDState counts “ambiguous events”.
     - After several ambiguous cycles (configurable via `ambiguous_event_count`), it fails safe as **human**.
   - On any exception, it returns **human_detected** (fail-safe).

---

## 4. Phrase lists (where they are and what they do)

All lists are in `Installer/modules/ai_core.py`. Matching is **substring** on **normalized** text: lowercase, punctuation removed (see `_normalize_text()`).

### 4.1 `INVALID_NUMBER_PHRASES`

- **Checked first.** If any phrase is found in the transcript → intent **invalid_number** → hang up (wrong number).
- Examples: `"no longer in service"`, `"number you have dialed"`, `"disconnected"`, `"cannot be completed"`, carrier-style messages.

### 4.2 `GOOGLE_BOT_PHRASES`

- **Checked second.** If any phrase is found → intent **bot_screening** → hang up (e.g. Google screening, IVR, “press one”).
- Examples: `"google recording the call"`, `"call screening service"`, `"press one"`, `"extension"`, `"directory"`.

### 4.3 `VOICEMAIL_PHRASES`

- **Checked third.** If any phrase is found → intent **voicemail** → hang up (leave-a-message systems).
- Examples: `"record your message"`, `"at the tone"`, `"please leave a message"`, `"voice mailbox"`, `"away from my desk"`, `"currently unavailable"`, `"leave a message"`.

### 4.4 `HUMAN_GREETINGS`

- **Not used in `detect_call_intent`.** Used by `is_human_greeting(text)`:
  - Only if the transcript is **short** (≤ 30 chars) and the **buffered speech duration** is &lt; 2 seconds.
  - If the normalized text **equals** or **starts with** one of these (e.g. `"hello"`, `"hola"`, `"yes"`, `"speaking"`), the system returns **human_detected** (keep call).

**Order of intent checks:** `invalid_number` → `bot_screening` → `voicemail` → `none`. The first match wins.

---

## 5. How to add more phrases

**When a user reports:** e.g. “I got a voicemail and auto-dial didn’t skip it” or “It hung up on a real person.”

### 5.1 Voicemail was not skipped (should have hung up)

- **Cause:** The voicemail message wasn’t matched by any current phrase.
- **Action:** Add **new phrases** to `VOICEMAIL_PHRASES` in `ai_core.py`.
- **How:**
  1. Get the **exact wording** the user heard (or a close quote). If you have a transcript from logs, use that.
  2. Normalize it the same way the code does: **lowercase**, **remove punctuation** (e.g. “Leave a message after the tone.” → `leave a message after the tone`).
  3. Prefer **short, stable substrings** that appear in many voicemails (e.g. `"after the tone"`, `"leave a message"`) rather than one long sentence.
  4. Add one string per line to the `VOICEMAIL_PHRASES` list. Example:

     ```python
     VOICEMAIL_PHRASES = [
         # ... existing ...
         "your preferred phrase here",   # e.g. from user report or carrier
     ]
     ```

  5. Restart the client (or ship an update). No server change needed.

- **Avoid:** Phrases that could appear in a **human** saying something (e.g. “I’m not available right now” said by a person). When in doubt, use phrases that are typical of **recorded** prompts (e.g. “at the tone”, “after the beep”, “mailbox is full”).

### 5.2 Bot / screening / IVR was not skipped

- Same idea: add phrases to **`GOOGLE_BOT_PHRASES`** (e.g. “this call may be recorded”, “virtual assistant”, “press one to continue”).

### 5.3 Invalid number / carrier message was not skipped

- Add phrases to **`INVALID_NUMBER_PHRASES`** (e.g. carrier-specific “number has been changed”, “not in service”).

### 5.4 Real person was hung up on (false positive)

- **Cause:** Something they said (or the way Whisper transcribed it) matched a voicemail/bot/invalid phrase.
- **Actions:**
  1. **Narrow the phrase:** If you added something very generic (e.g. “leave a message”), make it more specific (e.g. “please leave a message after the tone”) or remove it from the list.
  2. **Human greetings:** If the person said something like “Yes, speaking” or “Hello?”, ensure that phrase (or a short substring) is in **`HUMAN_GREETINGS`** and that the buffer was short enough (&lt; 2 s) so `is_human_greeting()` runs. Short greetings are matched by **exact or prefix** after normalization (e.g. “yes”, “hello”, “speaking”).
  3. **Order of checks:** Intent is checked in order: invalid_number → bot_screening → voicemail. If a human’s sentence accidentally contains a voicemail substring, it will hang up. Prefer phrases that are **unlikely** to be said by a live person.

---

## 6. Matching logic (reference)

- **Normalization:** `_normalize_text(text)` → `text.lower()` then remove all `string.punctuation`. So “Leave a message!” and “leave a message” both become `leave a message`.
- **Intent:** `detect_call_intent(text)` runs `norm = _normalize_text(text)`, then checks in order:
  - if any `phrase in norm` for `INVALID_NUMBER_PHRASES` → `"invalid_number"`
  - else if any `phrase in norm` for `GOOGLE_BOT_PHRASES` → `"bot_screening"`
  - else if any `phrase in norm` for `VOICEMAIL_PHRASES` → `"voicemail"`
  - else → `"none"`
- **Human greeting:** `is_human_greeting(text)` is used only when transcript length ≤ 30 and speech duration &lt; 2 s. It checks whether the normalized text **equals** a greeting or **starts with** a greeting + space/question (e.g. `"hello"`, `"hello?"`).

---

## 7. Common errors and fixes (quick reference)

| User report | Likely cause | What to do |
|------------|----------------|------------|
| “Voicemail wasn’t skipped” / “Auto-dial didn’t hang up on voicemail” | Transcript didn’t match any voicemail phrase | Add the exact (normalized) phrase or a short substring to **`VOICEMAIL_PHRASES`** in `ai_core.py`. Prefer phrases from the actual prompt (e.g. “after the beep”). |
| “It hung up on a real person” | Transcript matched a voicemail/bot/invalid phrase | Make the matching phrase more specific or remove it; or add the person’s reply to **`HUMAN_GREETINGS`** if it’s a short greeting. |
| “IVR / screening / bot wasn’t skipped” | No match in bot list | Add phrase to **`GOOGLE_BOT_PHRASES`** (e.g. “press one”, “virtual assistant”). |
| “Invalid number / carrier message wasn’t skipped” | No match in invalid list | Add phrase to **`INVALID_NUMBER_PHRASES`** (e.g. carrier “number has been changed”). |
| Whisper/model errors, “Calling Whisper with model type: NoneType” | Model not loaded yet or load failed | Ensure **pip-system-certs** (or certifi) is in `Installer/requirements.txt` and launcher runs `pip install -r requirements.txt` before app start. First run downloads model from Hugging Face. Check logs for “Whisper model failed to load”. |
| VAD never fires / no transcription | No loopback audio or wrong device | Confirm loopback (system) device is selected and stereo stream has system on channel 1. Check “[AI] AMD receiving stereo audio” in logs. |

For more general server/client issues (WebSocket, exports, etc.), see **`TROUBLESHOOTING.md`** in the repo root.

---

## 8. Summary

- **Phrase lists** in `Installer/modules/ai_core.py` control when the client **hangs up** (voicemail, bot, invalid number) vs **keeps the call** (human).
- **Add phrases** by appending to the right list (`VOICEMAIL_PHRASES`, `GOOGLE_BOT_PHRASES`, or `INVALID_NUMBER_PHRASES`); use **normalized** substrings (lowercase, no punctuation).
- **Libraries:** Silero VAD (speech detection), faster-whisper (transcription), torch/torchaudio (resampling and VAD), numpy (buffers).
- **Fail-safes:** Exceptions and persistent ambiguous transcripts default to **human_detected** so the call is not dropped by mistake.
