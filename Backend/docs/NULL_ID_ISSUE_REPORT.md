# NULL id on INSERT – issue report

**Date:** 2025-02-10  
**Status:** Under investigation – evidence suggests the server’s own `log_disposition` INSERT may be creating rows with NULL `id`; fix runs immediately after and repairs them.

---

## 0. Timeline / when it may have started

You reported that **export stopped showing dispositions from a certain date in January** – i.e. data “breaks” from that point onward (NULL ids break export). There is **no calendar date in HISTORY.md**, but the change that introduced the exact flow involved is:

**Version 1.60 – Call Registration Fix (One Call Per Connection)** (`HISTORY.md`):

- **Before:** Clicking a disposition could create a **second** call record (duplicate rows).
- **After (v1.60):**
  - `log_disposition` **only when call ends** (`on_call_ended`) → creates the **single** call record (INSERT).
  - `update_disposition` **when user clicks disposition** → **updates** that existing “No Disposition” row (no INSERT, no fallback).
  - Server no longer falls back to INSERT when no matching call exists.

So the flow where we **always** do one INSERT (log_disposition at call end) and then often an immediate UPDATE (update_disposition when the user clicks) was introduced in **v1.60**. If v1.60 was deployed around **January**, that is the most likely point when NULL id rows (and thus “export only up to a date”) could have started. Worth confirming with your team when v1.60 went live and whether that matches the “certain date in January” where export stops.

---

## 1. What the logs show

From production-style logs (control panel / server console):

```
10:58:41 | [AI] Agent SebastianGarcia: Call ended
10:58:41 | [DB] Fixed 1 call row(s) with NULL id (assigned ids 20076.0..20076.0)
10:58:41 | [DISPO] Logged call for SebastianGarcia: No Disposition | Phone: 407.832.9900 | Contact: Tony Fulton | ...
10:58:41 | [DISPO] Updated disposition for SebastianGarcia: No Response | Phone: 407.832.9900 | ...

11:00:02 | [AI] Agent SebastianGarcia: Call ended
11:00:02 | [DB] Fixed 1 call row(s) with NULL id (assigned ids 20077.0..20077.0)
11:00:02 | [DISPO] Logged call for SebastianGarcia: No Disposition | Phone: 407.675.4463 | ...
11:00:02 | [DISPO] Updated disposition for SebastianGarcia: No Response | Phone: 407.675.4463 | ...
```

**Observed pattern:**

1. Call ends → server receives **log_disposition** (INSERT) and then **update_disposition** (UPDATE) almost in the same second.
2. **Before** the “[DISPO] Logged call” line, we see **“[DB] Fixed 1 call row(s) with NULL id”**.
3. That fix runs **inside** `log_disposition()` (at the end, after the INSERT and commit).
4. So either:
   - **A)** The row we **just** inserted in this same `log_disposition()` call has NULL `id` (INSERT is not getting an id), or  
   - **B)** An older row had NULL and we’re fixing it on this run; the new INSERT would then have a normal id.

Given the same pattern twice in a short time for the same agent (and each time we fix exactly 1 row), **A is plausible**: the INSERT from `log_disposition` may be creating rows with no id, and the runtime fix is repairing them immediately after.

**Side note:** The log shows `20076.0..20076.0` (float). That’s likely from `max(id)` or the assigned id being handled as float somewhere; the stored value should still be integer. Worth normalizing to `int` when assigning id in the fix.

---

## 2. Why our INSERT could end up with NULL id (speculation)

- **Schema:** `id INTEGER PRIMARY KEY AUTOINCREMENT` – we never pass `id` in the INSERT, so SQLite should always assign it.
- So in normal SQLite behavior we **should not** see NULL `id` from our current code. Possible explanations if it *is* our INSERT:
  - **Concurrency:** Multiple threads/connections writing at once (e.g. several agents’ `log_disposition` in parallel) and something in SQLite/connection/locking so that one INSERT doesn’t get an id.
  - **Connection/transaction:** One connection does INSERT, commit; another connection or a follow-up read sees the row before the rowid/sequence is fully visible, or some isolation/rollback edge case.
  - **SQLite version / build:** Unusual behavior in a specific SQLite version or build (e.g. AUTOINCREMENT/sequence handling).
  - **update_disposition timing:** Client sends **log_disposition** and **update_disposition** back-to-back. Server handles both; if they run in parallel (different asyncio tasks, same process), we could have:
    - Task 1: `log_disposition()` → INSERT (row might get NULL id under contention?).
    - Task 2: `update_call_disposition()` → SELECT “No Disposition” by agent+phone. If it runs before Task 1’s commit, it finds nothing; if after, it finds the row (with or without id). So **update_disposition** doesn’t insert, but the **order** of INSERT vs SELECT/UPDATE could interact with locking and visibility.

None of this is confirmed; it just lists plausible angles.

---

## 3. Files involved

| File | Role |
|------|------|
| **Backend/database.py** | **log_disposition()** – single INSERT into `calls` (no `id` column), then `fix_null_call_ids()`. **update_call_disposition()** – SELECT most recent “No Disposition” by agent+phone, then UPDATE by id. **fix_null_call_ids()** / **_fix_null_call_ids_impl()** – find rows with `id IS NULL`, assign new ids, bump `sqlite_sequence`. **init_db()** – schema, creates `calls` with `id INTEGER PRIMARY KEY AUTOINCREMENT`, runs NULL-id fix at startup. |
| **Backend/main.py** | WebSocket message handling: on **log_disposition** calls `database.log_disposition(...)`; on **update_disposition** calls `database.update_call_disposition(...)`. Both run in the same async flow (order depends on when client sends). |
| **Installer (client)** | Sends **log_disposition** when call ends (`on_call_ended`) and **update_disposition** when user clicks a disposition (e.g. “No Response”). Can send both in quick succession. |

So the only place we **insert** call rows in this flow is **database.log_disposition()**. If the row that gets fixed is the one we just inserted, the bug is in or around that INSERT (or its environment).

---

## 4. What to do next (for deeper analysis)

1. **Confirm whether the new row has NULL id**  
   In **database.py** in **log_disposition()**, right after the INSERT and before `conn.commit()`:
   - Read `cursor.lastrowid`.
   - If it is `None` or `0`, log a critical error (e.g. “INSERT did not get an id”) and optionally log the inserted row (e.g. by `SELECT * FROM calls WHERE rowid = cursor.lastrowid` in a separate connection after commit, or by checking after the fact). That will tell us if **our** INSERT is the one without an id.

2. **Tighten the fix display**  
   In **_fix_null_call_ids_impl**, when assigning id use `int(max_id + 1 + i)` so the log shows integer ids (e.g. `20076..20076`) and the stored value is clearly an integer.

3. **Concurrency**  
   Check whether multiple WebSocket messages (e.g. log_disposition from several agents, or log_disposition + update_disposition for the same agent) can run in parallel and how many DB connections are open at once. If multiple threads or tasks share or open connections simultaneously, consider a single writer (e.g. a small queue or a dedicated DB thread) or connection pooling so only one writer runs at a time.

4. **SQLite**  
   Note the SQLite version (e.g. from Python’s `sqlite3.sqlite_version`) and whether the DB is on a network share or under heavy concurrency; look for known issues around AUTOINCREMENT or concurrent INSERTs.

5. **update_disposition**  
   Confirm the exact order of messages (log_disposition vs update_disposition) and whether update_disposition can run before the log_disposition INSERT is committed. If so, document the race and consider serializing “log then update” for the same agent/phone on the server (e.g. per-agent lock or queue).

---

## 5. Summary

- **Symptom:** Right after a call ends we see “Fixed 1 call row(s) with NULL id” then “Logged call” then “Updated disposition”.
- **Likely source:** The INSERT in **log_disposition** may sometimes create a row with NULL `id`; the runtime fix then repairs it.
- **Relevant code:** **Backend/database.py** (log_disposition, update_call_disposition, fix_null_call_ids, init_db), **Backend/main.py** (WebSocket handlers). Client sends both log_disposition and update_disposition in quick succession.
- **Next step:** Add lastrowid check after the INSERT in **log_disposition** to confirm whether our INSERT is the one without an id, then address concurrency/SQLite/ordering as needed.
