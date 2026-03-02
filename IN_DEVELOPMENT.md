# In progress / roadmap

Current phase: v2.0.0 with client-side AI is in production. We are mainly refining phrase patterns and small interactions (for example, missing voicemail or invalid-number phrases) based on real-world usage.

---

## 1. Client-side AI (v2.0.0)

Status: In production, under ongoing tuning.

- Client-side AMD is the only AMD path: the Installer runs Silero VAD + faster-whisper locally when Start Auto-Call is on. The server does not perform any audio analysis; it only receives metadata, dispositions, callbacks, and (optionally) audio for dashboards.
- The 35s / 50s no-answer timer, voicemail / bot / invalid-number detection, and auto-advance (next number / next contact) are all driven by the client.
- Most current work is focused on small improvements: adding missing voicemail / bot / invalid-number phrases, adjusting ambiguous-speech thresholds, and improving autocall arbitration in edge cases.

---

## 2. LiveKit audio (optional / experimental)

Goal: Use LiveKit for WebRTC audio as an alternative to the raw WebSocket stream (for future features or environments where WebRTC is preferred).

Current status: Optional. The production dialer uses client-side AI and the standard WebSocket audio path, so LiveKit is not required for AMD. It remains available for experimentation but is not on the critical path.

---

## 3. TTS for autocall (future)

Goal: Add text-to-speech (TTS) on the client so agents hear short announcements during autocall, for example “Now calling [person name]…”.

Current status: Planned; depends on autocall + client-side transcription being stable in production. No dependency on LiveKit.

---

## 4. New-user / first-run experience (future)

Goal: Improve onboarding:

- Short guided tour or hints (for example, “Click here for config”, “Here for filtering”).
- Optional sample/test `.xlsx` so users can try the app without real leads.

Current status: Idea / planned; not yet implemented.

---

## Summary

| Area | Status | Notes |
|------|--------|-------|
| v2.0.0 client-side AI | In production; tuning | Silero VAD + faster-whisper on Installer; Auto-Call, 35s/50s timer, auto-advance; now mainly refining phrase lists and edge cases |
| LiveKit audio | Optional / experimental | Not required for production AMD |
| TTS (“Now calling…”) for autocall | Planned | After 2.0.0 is fully stable |
| First-run tour + test xlsx | Planned | — |
