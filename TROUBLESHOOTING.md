# Troubleshooting

Usual problems, warnings, and when to escalate. Use this for agent support and for issues that need quick intervention.

---

## Usual problems / warnings

### Server: WebSocket connection drops (WinError 121)

#### What you see

In the server console, after the server has been running for a while:

```
File "...\websockets\legacy\protocol.py", line 945, in transfer_data
    message = await self.read_message()
...
OSError: [WinError 121] The semaphore timeout period has expired
INFO:     connection closed
INFO:     192.168.1.72:60645 - "WebSocket /ws/MatiasAlamo" [accepted]
INFO:     connection open
```

#### What it means

- One agent’s **WebSocket** hit a timeout (connection went idle: sleep, Wi‑Fi blip, firewall closing idle TCP).
- The server **does not crash**; that one connection closes and the client reconnects. Seeing a new `WebSocket ... [accepted]` and `connection open` is normal.

#### What to do

- **Occasional**: No action.
- **Frequent for one agent**: Check that agent’s network (Wi‑Fi, VPN, sleep).
- **Frequent for many**: Check firewall/router idle timeouts; consider shorter WebSocket ping/keep‑alive.

---

## Issues agents may report – self‑help

### Calls timing out, hanging up on their own, or advancing to next number without clicking

#### What you see

- Calls end by themselves after a short time.
- Dialer moves to the next number without the agent clicking “Next” or similar.

#### What it means

- The **autocall** (auto-advance / experimental) feature is likely **turned on**. That behavior is expected when it’s enabled.

#### What to do

- Open the **config** (gear/cog) → **Calling**.
- **Deactivate** the autocall / auto-advance check (the option that controls calling the next number automatically).
- If they want that behavior, they can turn it back on knowing it’s still experimental.

---

### Dispositions saving or row coloring in the wrong row

#### What you see

- Agent sets a disposition or colors a row, but it’s saved or shown on the **wrong row** (wrong contact).
- Numbers and rows seem out of sync.

#### What it means

- The **shadow copy** of the imported list (the in-memory list the dialer uses) is probably **stale** — e.g. list was re-imported elsewhere or the view got out of sync.

#### What to do

- Have the agent click the **Import** button again (top left) to **refresh** the list and reload the shadow copy.
- After that, dispositions and row coloring should save to the correct row.
- If they already saved something in the wrong place, they can use **Previous contact** (or the equivalent) to go back and fix it.

---

## Dashboard export / database (leads management)

### Export only returns data up to a certain date (nothing from there on)

#### What you see

- The **export tool** on the dashboard page only exports data **up to a certain date**; nothing from that date onward appears in the export.

#### What it usually means

- At some point someone **opened the live database** (e.g. in a SQL viewer or another tool) **while the server was still running**.
- That can block or interfere with the server writing to the DB, so the server either didn’t save new rows or saved them incorrectly — effectively **breaking the database** from that point on.

#### How to check if that’s the cause

1. **Shut down the server** (so the DB is not in use).
2. Open the database in a **SQL viewer** and look at **dispositions from that date onward**.
3. You’ll likely see that the **next dispositions have no ID** (or missing/corrupt data) — that confirms the DB was damaged when the live DB was opened while the server was running.

#### What to do

1. **First try a quick reset:** Turn the server **off**, then **on** again. On startup the server repairs rows with NULL id; after each disposition save it also runs a repair. That often fixes the problem without further steps.
2. **If export is still broken (e.g. still only up to a date):** **Stop the server**, then run the **schema fix script** so the `calls` table is rebuilt with correct ids:
   - From `Backend`: run **`cleandb\run_fix_db_schema.bat`** (or `python cleandb/fix_db_schema.py`). The script renames `calls` to `calls_backup`, creates a new `calls` table, copies all data (assigning new ids where they were NULL), recreates indexes, and enables WAL. It keeps `calls_backup` for safety. Then start the server again.
3. **If you need more than that:** Use the **clean database tools** (see `Backend/cleandb/` and any README there) to backfill or clean data as needed, then start the server.

#### How to avoid this in the future

- **Do not open the live database** directly (no SQL viewer or other tool on the running DB file).
- To review data while the server is running, use the **DB viewer in the Control Panel**: it takes a **snapshot** of the database for read-only review and does not lock or conflict with the server.
- The server now uses **WAL mode** and **atomic INSERT … RETURNING id** for dispositions, which greatly reduces the chance of new NULL-id rows under load.

---

## IMPORTANT – need quick intervention (contact me)

These are issues that often need code or automation changes. Try the steps below first; if they can’t fix it, **contact me**.

### Clicking Call sends the command but the call doesn’t start (e.g. after a Teams update)

#### What you see

- Agent clicks **Call** in the dialer; the app sends the command but **Teams doesn’t start the call** (no dial, no window, or wrong window).
- Started after a **Teams** (or Windows) update.

#### What it means

- The **Teams automation** (UI automation / accessibility) is prone to breaking when Microsoft changes button positions, labels, or control types. The code is looking for the old layout.

#### What to try first

1. **Accessibility Insights for Windows**  
   - Install and run it: [Accessibility Insights for Windows](https://accessibilityinsights.io/docs/windows/overview/).  
   - Use it to **inspect** the Teams window when you’re about to place a call (same state as when the dialer would click “Call”).
2. **Compare with the code**  
   - Check what the automation is targeting (e.g. in the Installer: Teams automation / “call” button logic).  
   - See what **role**, **name**, **position**, or **automation ID** the code expects vs what Accessibility Insights shows now.
3. **Update the code**  
   - Adjust selectors, directions, or steps (e.g. new “Call” or “Dial” button location, new window hierarchy) to match the current Teams UI.  
   - Test with a real call to confirm the click triggers the call.

#### If that doesn’t work

- **Contact me.** Don’t leave agents without a working Call button; we’ll fix the automation or find a workaround.

---

### Other automation issues (buttons, focus, dial pad)

#### What you see

- Other Teams-related actions break after an update: e.g. dial pad not opening, wrong field focused, “End call” or “Next” not working as before.

#### What it means

- Same as above: **UI/accessibility** has changed; the automation is no longer finding the right controls.

#### What to try first

- Use **Accessibility Insights for Windows** on the relevant Teams screen (call window, dial pad, etc.).
- Find the **new** control (role, name, position) for the action that’s failing.
- Update the corresponding automation code in the Installer to use the new selectors or steps.

#### If that doesn’t work

- **Contact me** so we can update the automation properly and avoid repeated breakage.
