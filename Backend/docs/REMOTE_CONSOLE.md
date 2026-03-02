# Remote console (admin-only) configuration

This document explains how to enable and secure the **remote console** page, how
to set the admin password, and what security guarantees/assumptions it has.

The remote console lets you:

- See a **tail of the server logs**.
- Trigger a **graceful shutdown** of the backend.
- Trigger a **logical restart** (same as shutdown; external service/control panel
  is responsible for starting uvicorn again).

The UI is reachable only by:

- Clicking the **green circle** next to “System Online” on the dashboard
  (top-right of the admin page).
- Successfully authenticating with **password + captcha**.

---

## 1. Enabling the remote console

By default, the remote console is **disabled**. It becomes active only when:

1. `REMOTE_CONSOLE_ENABLED` is set to `1`, `true` or `yes`, **and**
2. `REMOTE_CONSOLE_PASSWORD_HASH` is set to a valid SHA-256 hash of the admin
   password.

Set these as **environment variables** before starting the backend:

```powershell
# Example on Windows PowerShell
$env:REMOTE_CONSOLE_ENABLED = "1"
$env:REMOTE_CONSOLE_PASSWORD_HASH = "<your_sha256_hash_here>"
python -m uvicorn main:app --host 0.0.0.0 --port 80
```

Or in a `.bat` launcher:

```bat
set REMOTE_CONSOLE_ENABLED=1
set REMOTE_CONSOLE_PASSWORD_HASH=<your_sha256_hash_here>
python -m uvicorn main:app --host 0.0.0.0 --port 80
```

If either variable is missing or the hash is empty, all `/remote-console/*`
routes return **404**.

---

## 2. Generating the password hash

The backend expects a **SHA-256 hex string** as the password hash. To generate
one, run this small Python snippet **once** on a trusted machine:

```python
import hashlib

password = "YourStrongAdminPasswordHere"
hash_hex = hashlib.sha256(password.encode("utf-8")).hexdigest()
print(hash_hex)
```

Copy the printed hex string and use it as the value for
`REMOTE_CONSOLE_PASSWORD_HASH`.

> **Important:**  
> - Do **not** store the plaintext password in the repo.  
> - Use a reasonably strong password (at least 12–16 characters, mix of
>   letters/numbers/symbols).  
> - Rotate it periodically if possible.

---

## 3. Optional settings

The following environment variables fine-tune behavior (all optional):

- `REMOTE_CONSOLE_SESSION_TTL_SECONDS`  
  Default: `3600` (1 hour).  
  How long a login session remains valid before requiring re-login.

- `REMOTE_CONSOLE_MAX_ATTEMPTS`  
  Default: `5`.  
  Maximum allowed failed login attempts per IP within the attempt window.

- `REMOTE_CONSOLE_ATTEMPT_WINDOW_SECONDS`  
  Default: `900` (15 minutes).  
  Rolling time window used for counting failed login attempts.

- `REMOTE_CONSOLE_LOG_PATH`  
  Default: `<Backend>/server.log`.  
  Path to the log file that the remote console tails. You should configure
  uvicorn / your process manager to write logs to this file.

Example:

```bat
set REMOTE_CONSOLE_ENABLED=1
set REMOTE_CONSOLE_PASSWORD_HASH=<your_sha256_hash_here>
set REMOTE_CONSOLE_LOG_PATH=z:\Backend\logs\server.log
set REMOTE_CONSOLE_SESSION_TTL_SECONDS=1800
set REMOTE_CONSOLE_MAX_ATTEMPTS=5
set REMOTE_CONSOLE_ATTEMPT_WINDOW_SECONDS=900
```

---

## 4. Security behavior (what the code does)

- **Password handling**  
  - The server never stores or echoes the plaintext password.  
  - On login, it hashes the submitted password with SHA-256 and compares it to
    `REMOTE_CONSOLE_PASSWORD_HASH` using a constant-time comparison
    (`hmac.compare_digest`).

- **Captcha**  
  - The login form shows a small math challenge (`a + b = ?`).  
  - The backend stores the correct answer server-side (in memory) keyed by a
    random captcha ID.  
  - On POST, the server checks the captcha first. If it’s wrong or expired, the
    login fails without revealing which part failed.

- **Rate limiting**  
  - Failed attempts are tracked per client IP with a rolling time window.  
  - After `REMOTE_CONSOLE_MAX_ATTEMPTS` failures within
    `REMOTE_CONSOLE_ATTEMPT_WINDOW_SECONDS`, new attempts from that IP are
    temporarily blocked (HTTP 429 with a generic error).

- **Sessions + CSRF**  
  - On successful login, the server creates a random session ID and CSRF token,
    stores them in memory with an expiry, and sets a `remote_console_session`
    cookie (HTTP-only, SameSite=Lax).  
  - The panel, logs, and control actions require a valid session.  
  - The shutdown and restart forms include a hidden `csrf_token` field, and the
    backend checks it against the session before executing actions.

- **No code injection**  
  - The remote console does **not** build shell commands from user input.  
  - Shutdown/restart actions call `os.kill(os.getpid(), signal.SIGTERM)` directly
    (graceful uvicorn/ASGI shutdown) and rely on your existing service/control
    panel to restart the process if desired.

- **Network rails (recommended)**  
  - Serve the backend behind **HTTPS** (e.g. via IIS / Nginx / reverse proxy) so
    the password and session cookie are not sent in clear text.  
  - Ideally, restrict access to the remote console route to your LAN only
    (e.g. via firewall or proxy rules).

---

## 5. How to use the remote console

1. Set the environment variables as described above.
2. Start the backend as usual.
3. Open the main admin **Dashboard** in your browser.
4. In the top-right corner, next to “System Online”, click the **green circle**
   (🟢).  
   - This redirects to `/remote-console/login`.
5. Enter the admin password and solve the captcha.  
   - On success, you are redirected to the remote console panel.
6. From the panel you can:
   - View a tail of the server log (auto-refresh every few seconds).  
   - Click **Shutdown Server** to trigger a graceful shutdown.  
   - Click **Restart Server** to perform the same shutdown; your OS/service
     manager is expected to start uvicorn again.

If the remote console is disabled or misconfigured (e.g. no password hash set),
the login and panel routes respond with 404 or a short error message.

