const REMOTE_CONSOLE_CSRF = window.CSRF_TOKEN || "";

async function loadLogs() {
    const logContainer = document.getElementById("log-container");
    if (!logContainer) {
        return;
    }
    try {
        const res = await fetch("/remote-console/logs?lines=200", { cache: "no-store" });
        if (!res.ok) {
            logContainer.textContent = "Unable to load logs (" + res.status + ").";
            return;
        }
        const data = await res.json();
        const lines = data.lines || [];
        logContainer.textContent = lines.join("");
    } catch (e) {
        logContainer.textContent = "Error loading logs: " + e;
    }
}

async function postConsoleAction(url, confirmMessage, redirectToLogin) {
    if (!window.confirm(confirmMessage)) {
        return;
    }
    const statusEl = document.getElementById("action-status");
    if (!statusEl) {
        return;
    }
    statusEl.textContent = "Sending request...";
    try {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({ csrf_token: REMOTE_CONSOLE_CSRF })
        });
        let data = {};
        try {
            data = await res.json();
        } catch (_e) {
            // Non-JSON response (e.g., on shutdown), ignore parse error.
        }
        statusEl.textContent =
            "Response " +
            res.status +
            (data.returncode !== undefined ? ", return code: " + data.returncode : "");
        loadLogs();
        if (redirectToLogin && res.ok) {
            window.setTimeout(function () {
                window.location.href = "/remote-console/login";
            }, 1000);
        }
    } catch (e) {
        statusEl.textContent = "Request failed: " + e;
    }
}

// Initialize logs polling when the script loads.
loadLogs();
window.setInterval(loadLogs, 5000);

