# In development / roadmap

Items still being fixed or planned. Not yet completed.

---

## 1. LiveKit audio transmission (cleaner path for transcription)

**Goal:** Use LiveKit for audio streaming instead of (or in addition to) pure WebSockets, so the server gets a cleaner audio stream and transcription (e.g. AMD / faster-whisper) can be more accurate.

**Current status:** Still in setup. **Firewall is dropping connections** (LiveKit uses different ports/protocols than plain HTTP/WS), so we’re working through network/firewall configuration before this can be the primary path.

**When done:** Expect more reliable, higher-quality audio for AMD and any future transcription features.

---

## 2. TTS for autocall (after LiveKit + autocall work)

**Goal:** Once LiveKit audio and autocall work as intended with transcription, add **text-to-speech (TTS)** for the client so agents hear short announcements during autocall, e.g. *“Now calling [person name]…”*, so they don’t get lost and the flow is more user-friendly.

**Current status:** Planned; depends on (1) being stable and autocall + transcription behaving correctly.

---

## 3. New-user / first-run experience

**Goal:** Improve onboarding for first install or first use of the app:

- **Quick lookaround:** A short guided tour with small popups or hints pointing to main areas: *“Click here for config”*, *“Click here for filtering”*, etc., so new users see what does what.
- **Optional test Excel:** Ship or offer a **sample/test .xlsx** so users can try filtering, importing, and calling flow without using real leads. They can get used to how the app works in a safe way.

**Current status:** Idea / planned; not yet implemented.

---

## Summary

| Area | Status | Blocker / dependency |
|------|--------|----------------------|
| LiveKit audio for cleaner transmission | In setup | Firewall dropping LiveKit connections |
| TTS (“Now calling…”) for autocall | Planned | LiveKit audio + autocall with transcription working |
| First-run tour + test xlsx | Planned | — |
