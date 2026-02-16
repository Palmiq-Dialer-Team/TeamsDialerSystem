# Disposition save & NULL id – quick analysis

**Context:** Disposition saving sometimes “breaks” (e.g. export only up to a date); suspected cause: agent disconnects, reconnects, sends disposition, and something gets saved without an id, breaking the DB until server restart.

---

## 1. Code paths that write to `calls`

| Path | Function | Action |
|------|----------|--------|
| Call ends (server receives) | `log_disposition()` | **INSERT** (no `id` column → SQLite AUTOINCREMENT assigns one) |
| User clicks disposition | `update_call_disposition()` | **UPDATE** existing "No Disposition" row by agent+phone (no INSERT) |
| Legacy / manual callback | `log_call()` | **INSERT** (same as above) |
| API | `create_call_record()` | **INSERT** (returns `lastrowid`) |

None of these INSERTs specify `id`; the schema uses `id INTEGER PRIMARY KEY AUTOINCREMENT`, so SQLite always assigns an id. **There is no server code path that explicitly inserts a row with NULL id.**

---

## 2. Disconnect / reconnect scenario

- **Call ends** → client sends `log_disposition` (fire-and-forget; no retry queue).
- If the client is **disconnected**, the send fails; the server never gets the message, so **no row** is created. The client still sets `_call_record_created = True`.
- **User clicks disposition** → client sends `update_disposition`. Server looks for the most recent "No Disposition" row for that agent+phone (last 4h). **Finds nothing** → returns `False`; server does **not** do a fallback INSERT (by design, to avoid phantom calls).
- **Result:** No row for that call (missing data), not a row with NULL id.

If `log_disposition` **does** reach the server, the INSERT runs and the row gets an id. So from this flow we either get one normal row or no row, never a NULL id from our code.

---

## 3. Where NULL id can come from

- **init_db** already documents: “can happen if another process inserted with id=NULL or sequence glitch”.
- Most plausible causes:
  1. **Another process** (e.g. a DB tool) opening the **live** DB and doing bad INSERTs or corrupting the file.
  2. **Rare SQLite/OS/lock glitch** (e.g. under lock contention or a crashed writer).
- So NULL id rows are treated as an occasional side effect of external access or glitches, not as something our disposition logic intentionally creates.

---

## 4. What we did

- **Runtime healing:** The NULL-id fix that used to run only at startup in `init_db()` is now in a shared helper `fix_null_call_ids()`. It still runs once at startup. In addition, **`log_disposition()` calls `fix_null_call_ids()` after each save**. So if any row (from any cause) has a NULL id, the next disposition save will repair it without needing a server restart.
- **TROUBLESHOOTING.md** already describes: export only up to a date → check for NULL ids from that date → first try server restart (startup fix), then clean DB tools; and to avoid the issue, don’t open the live DB, use the Control Panel DB viewer (snapshot).

---

## 5. Summary

| Question | Answer |
|----------|--------|
| Does our code insert rows with NULL id? | No. All INSERTs omit `id`; AUTOINCREMENT assigns it. |
| Can disconnect → reconnect → disposition create NULL id? | Not via our logic: we either insert (with id) or update (no insert), or skip. |
| Why do NULL ids appear? | Likely another process opening the live DB or a rare glitch. |
| How do we handle it now? | Startup fix + **after every `log_disposition()`** we run `fix_null_call_ids()` so the DB heals without restart. |
