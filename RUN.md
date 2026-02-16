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

5. **LiveKit (optional)**  
   From repo root run **`start_livekit.bat`** to start the LiveKit server (needs `LiveKit/config.yaml`).

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
| Start LiveKit | Repo root: `start_livekit.bat` |
| Client app usage | `Installer/modules/CLIENT_MODULES.md` |
| Control Panel / Backend | `PROJECT_OVERVIEW.md` |
