# How to run

Short setup and run guide. For architecture and config details see **PROJECT_OVERVIEW.md**.

---

## Server side

1. **Setup (once)**  
   In **Backend**, run **`setup.bat`**. It creates the virtual environment (`venv`) if missing and installs dependencies from `requirements.txt`.  
   (This is **Backend/setup.bat** — not the Installer folder’s setup.)

2. **Start the server**  
   In **Backend**, run **`start_control_panel.bat`**. It uses that venv and opens the Control Panel; from there you Start the server.

   **Note (server machine):** You can also run the **build control panel** bat to produce an exe in `Backend/dist/`. Put a **desktop shortcut** to that exe so that after a reboot or shutdown anyone can open the Control Panel and start the server quickly without opening the repo or the bat.

3. **Using the Control Panel**  
   From the Control Panel you **Start** / **Stop** / **Restart** the server, view logs, open the DB snapshot viewer, and send announcements.  
   → See **PROJECT_OVERVIEW.md** (Control Panel / Backend) for more.

4. **Updates**  
   - **Server-only change:** Restart the server from the Control Panel (or Stop then Start).  
   - **Control Panel change:** Rebuild the Control Panel (e.g. PyInstaller from `Backend/`). Rebuild only when you need to ship an updated exe.

---

## Remote console (optional, for admins)

The Backend exposes a simple **remote console** UI that lets an authorized admin view logs and request shutdown/restart or trigger a client release from a browser.

1. **Enable it (server config)**  
   Set these environment variables on the server machine before starting the Backend:
   - `REMOTE_CONSOLE_ENABLED=1`
   - `REMOTE_CONSOLE_PASSWORD_HASH=<sha256 of your chosen password>`  
     (Generate with a short one-liner, e.g. `python -c "import hashlib; print(hashlib.sha256(b'my-password').hexdigest())"`)
   - Optional but recommended: `REMOTE_CONSOLE_LOG_PATH` – path to the log file the console should tail (default is `Backend/server.log` if not set).

2. **Open the remote console**  
   - Navigate to `http://<server-host>/remote-console/login` in a browser.
   - Solve the simple math captcha and enter the password that matches `REMOTE_CONSOLE_PASSWORD_HASH`.

3. **What you can do there**  
   - View the tail of the server log (`/remote-console/logs` via the panel).
   - Request a **graceful shutdown** of the server (`/remote-console/shutdown` – session + CSRF protected).
   - Request a **logical restart** (writes a marker to the log that the Control Panel can react to).
   - Trigger `release_client.py` via **“Push client update”** so the server rebuilds/publishes a new client ZIP (output appended to the log).

> For full details and security notes, see `PROJECT_OVERVIEW.md` (Backend section) and `Backend/main.py` (remote console endpoints).

---

## Client side

1. Get the **zip** from **WebUpdates** (or wherever the client package is published).
2. Put it in the folder you want, **unzip**.
3. **Setup (once):** In PowerShell, run as Administrator:
   ```powershell
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope LocalMachine
   ```
   Then run the installer/setup as provided (e.g. run the launcher or setup script once).
4. After that, agents **run the launcher** (e.g. `launcher.py` or the packaged launcher); it checks for updates and starts the app.

→ **Using the client app:** see **Installer/modules/CLIENT_MODULES.md** (and PROJECT_OVERVIEW for flows).

---

## Quick reference

| What | Where / how |
|------|-------------|
| Backend setup (venv + deps) | `Backend/setup.bat` |
| Start server + open Control Panel | `Backend/start_control_panel.bat` |
| Client app usage | `Installer/modules/CLIENT_MODULES.md` |
| Control Panel / Backend | `PROJECT_OVERVIEW.md` |
