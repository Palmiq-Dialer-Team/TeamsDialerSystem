"""
Server Control Panel - Start, stop, restart, view logs, open DB.
"""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from datetime import datetime
import threading
import re
import json
import urllib.request
import urllib.error
from pathlib import Path

from .server_controller import ServerController
from .config import DASHBOARD_URL, DASHBOARD_URL_NETWORK, BACKEND_DIR, SERVER_PORT, build_auth_headers
from . import db_viewer

ANNOUNCEMENT_IMAGES_DIR = Path(BACKEND_DIR) / "announcement_images"
DEFAULT_ANNOUNCEMENT_IMAGE = "yippee.gif"


# Log view filters: (display_name, pattern_or_keywords)
LOG_FILTERS = [
    ("Full", None),
    ("Audio streaming", r"\[AUDIO\]|audio|stream"),
    ("AI monitoring", r"\[AI\]|Human detected|VAD|Whisper|voicemail|AMD|timeout"),
    ("Disposition", r"\[DISPO\]|Logged call|log_disposition|disposition|callback"),
    ("Connections", r"\[CONN\]|connected|disconnected"),
    ("Debug / Receive", r"\[RECV\]|\[DEBUG\]"),
]

LOG_PATTERNS = {name: pat for name, pat in LOG_FILTERS}


def matches_filter(line: str, pattern) -> bool:
    if pattern is None:
        return True
    try:
        return bool(re.search(pattern, line, re.IGNORECASE))
    except re.error:
        return pattern.lower() in line.lower()


def _fetch_debug_status(base_url: str) -> dict | None:
    """Fetch /api/debug/status from server. Returns None on failure."""
    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/debug/status",
            headers=build_auth_headers(),
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _post_debug_action(base_url: str, path: str) -> dict | None:
    """POST to debug API. Returns response dict or None."""
    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}{path}",
            data=b"",
            method="POST",
            headers=build_auth_headers(),
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


class QueueStatusWindow:
    """Debug window: shows connected agents, asyncio tasks, and disconnect controls."""

    def __init__(self, parent: tk.Tk):
        self.base_url = DASHBOARD_URL
        self.win = tk.Toplevel(parent)
        self.win.title("Queue Status / Debug")
        self.win.geometry("750x500")
        self.win.minsize(500, 400)

        # Summary
        summ = ttk.Frame(self.win, padding=10)
        summ.pack(fill=tk.X)
        self.lbl_summary = ttk.Label(summ, text="Connecting...")
        self.lbl_summary.pack(anchor=tk.W)
        self.lbl_tasks = ttk.Label(summ, text="")
        self.lbl_tasks.pack(anchor=tk.W)

        # Notebook: Agents + Async Tasks tabs
        self.notebook = ttk.Notebook(self.win)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

        # Tab 1: Agents
        agents_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(agents_frame, text="Agents")
        cols = ("agent_id", "status", "last_seen", "last_audio")
        self.tree = ttk.Treeview(agents_frame, columns=cols, show="headings", height=10)
        for c in cols:
            self.tree.heading(c, text=c.replace("_", " ").title())
            self.tree.column(c, width=120)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_agents = ttk.Scrollbar(agents_frame, orient=tk.VERTICAL, command=self.tree.yview)
        sb_agents.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=sb_agents.set)

        # Tab 2: Async Tasks
        tasks_frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(tasks_frame, text="Async Tasks")
        task_cols = ("index", "name", "coro", "file", "line", "done")
        self.task_tree = ttk.Treeview(tasks_frame, columns=task_cols, show="headings", height=12)
        self.task_tree.column("index", width=40)
        self.task_tree.column("name", width=120)
        self.task_tree.column("coro", width=200)
        self.task_tree.column("file", width=100)
        self.task_tree.column("line", width=50)
        self.task_tree.column("done", width=50)
        for c in task_cols:
            self.task_tree.heading(c, text=c.replace("_", " ").title())
        self.task_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_tasks = ttk.Scrollbar(tasks_frame, orient=tk.VERTICAL, command=self.task_tree.yview)
        sb_tasks.pack(side=tk.RIGHT, fill=tk.Y)
        self.task_tree.configure(yscrollcommand=sb_tasks.set)
        ttk.Label(tasks_frame, text="Tip: Disconnecting agents reduces tasks (each agent = 1 websocket handler).", font=("", 9)).pack(anchor=tk.W)

        # Buttons
        btn_frame = ttk.Frame(self.win, padding=10)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="Refresh", command=self._refresh).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Disconnect selected agent", command=self._disconnect_selected).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Disconnect all agents", command=self._disconnect_all).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Auto-refresh (2s)", command=self._toggle_auto_refresh).pack(side=tk.LEFT)
        self._auto_refresh_id = None

        self._refresh()

    def _refresh(self):
        data = _fetch_debug_status(self.base_url)
        for i in self.tree.get_children():
            self.tree.delete(i)
        for i in self.task_tree.get_children():
            self.task_tree.delete(i)
        if data is None:
            self.lbl_summary.config(text="Server unreachable (is it running?)")
            self.lbl_tasks.config(text="")
            return
        agents = data.get("agents", [])
        self.lbl_summary.config(
            text=f"Agents: {data.get('active_agents', 0)} connected | Admins: {data.get('active_admins', 0)}"
        )
        tc = data.get("asyncio_task_count", -1)
        self.lbl_tasks.config(text=f"Asyncio tasks: {tc} (see 'Async Tasks' tab)" if tc >= 0 else "Asyncio tasks: N/A")
        for a in agents:
            la = a.get("last_audio") or "-"
            la_short = la[:20] + "..." if len(la) > 20 else la
            self.tree.insert("", tk.END, values=(
                a.get("agent_id", ""),
                a.get("status", ""),
                a.get("last_seen") or "-",
                la_short,
            ))
        for t in data.get("asyncio_tasks", []):
            coro = (t.get("coro") or "-")[:60]
            if len(str(t.get("coro") or "")) > 60:
                coro += "..."
            self.task_tree.insert("", tk.END, values=(
                t.get("index", ""),
                (t.get("name") or "-")[:40],
                coro,
                t.get("file") or "-",
                t.get("line") or "-",
                "yes" if t.get("done") else "no",
            ))

    def _disconnect_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Queue Status", "Select an agent first.")
            return
        for item in sel:
            vals = self.tree.item(item, "values")
            if vals:
                agent_id = vals[0]
                r = _post_debug_action(self.base_url, f"/api/debug/disconnect_agent/{agent_id}")
                if r and r.get("ok"):
                    messagebox.showinfo("Queue Status", f"Disconnected {agent_id}.")
                else:
                    messagebox.showerror("Queue Status", f"Failed to disconnect {agent_id}.")
        self._refresh()

    def _disconnect_all(self):
        if not messagebox.askyesno("Queue Status", "Disconnect all agents? They will need to reconnect."):
            return
        r = _post_debug_action(self.base_url, "/api/debug/disconnect_all_agents")
        if r and r.get("ok"):
            n = r.get("disconnected", 0)
            messagebox.showinfo("Queue Status", f"Disconnected {n} agent(s).")
        else:
            messagebox.showerror("Queue Status", "Failed to disconnect agents.")
        self._refresh()

    def _toggle_auto_refresh(self):
        if self._auto_refresh_id:
            self.win.after_cancel(self._auto_refresh_id)
            self._auto_refresh_id = None
            messagebox.showinfo("Queue Status", "Auto-refresh stopped.")
        else:
            def _loop():
                self._refresh()
                self._auto_refresh_id = self.win.after(2000, _loop)
            self._auto_refresh_id = self.win.after(2000, _loop)
            messagebox.showinfo("Queue Status", "Auto-refreshing every 2 seconds.")


class AnnouncementDialog:
    """Dialog to compose and send announcements to connected agents."""

    def __init__(self, parent: tk.Tk):
        self.parent = parent
        self.win = tk.Toplevel(parent)
        self.win.title("Send Announcement")
        self.win.geometry("420x280")
        self.win.minsize(380, 240)
        self.win.transient(parent)
        self.win.grab_set()

        self.selected_image: str | None = DEFAULT_ANNOUNCEMENT_IMAGE
        self._photo = None

        # Message
        msg_frame = ttk.Frame(self.win, padding=10)
        msg_frame.pack(fill=tk.X)
        ttk.Label(msg_frame, text="Message:").pack(anchor=tk.W)
        self.msg_text = tk.Text(msg_frame, height=4, wrap=tk.WORD, font=("Segoe UI", 10))
        self.msg_text.pack(fill=tk.X, pady=(2, 0))
        self.msg_text.insert("1.0", "There's a new update available. Please restart your app.")
        self.msg_text.focus_set()

        # Image row
        img_frame = ttk.Frame(self.win, padding=10)
        img_frame.pack(fill=tk.X)
        ttk.Label(img_frame, text="Image:").pack(anchor=tk.W)
        inner = ttk.Frame(img_frame)
        inner.pack(fill=tk.X, pady=(2, 0))
        self.img_label = ttk.Label(inner, text="(click to change)", cursor="hand2")
        self.img_label.pack(side=tk.LEFT, padx=(0, 10))
        self.img_label.bind("<Button-1>", self._on_image_click)
        ttk.Button(inner, text="Select none", command=self._select_none).pack(side=tk.LEFT)

        self._refresh_image_preview()

        # Buttons
        btn_frame = ttk.Frame(self.win, padding=10)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="Send", command=self._send).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Cancel", command=self.win.destroy).pack(side=tk.LEFT)

    def _refresh_image_preview(self):
        if self.selected_image is None:
            self.img_label.config(text="(none - click to select)")
            return
        path = ANNOUNCEMENT_IMAGES_DIR / self.selected_image
        if path.exists():
            try:
                img = tk.PhotoImage(file=str(path))
                self._photo = img
                self.img_label.config(image=img, text="")
            except tk.TclError:
                self.img_label.config(image="", text=self.selected_image)
        else:
            self.img_label.config(image="", text=self.selected_image or "(none)")

    def _on_image_click(self, event=None):
        if not ANNOUNCEMENT_IMAGES_DIR.exists():
            messagebox.showwarning("Announcement", "Announcement images folder not found.")
            return
        files = sorted(
            p.name for p in ANNOUNCEMENT_IMAGES_DIR.iterdir()
            if p.suffix.lower() in (".gif", ".png", ".jpg", ".jpeg")
        )
        if not files:
            messagebox.showinfo("Announcement", "No images in announcement_images folder.")
            return
        picker = tk.Toplevel(self.win)
        picker.title("Select image")
        picker.geometry("260x200")
        picker.transient(self.win)
        lb = tk.Listbox(picker, font=("Segoe UI", 10))
        lb.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        lb.insert(tk.END, "(none)")
        for f in files:
            lb.insert(tk.END, f)

        def on_ok():
            sel = lb.curselection()
            if sel:
                idx = sel[0]
                if idx == 0:
                    self.selected_image = None
                else:
                    self.selected_image = files[idx - 1]
                self._refresh_image_preview()
            picker.destroy()

        ttk.Button(picker, text="OK", command=on_ok).pack(pady=5)
        lb.bind("<Double-Button-1>", lambda e: on_ok())

    def _select_none(self):
        self.selected_image = None
        self._refresh_image_preview()

    def _send(self):
        message = self.msg_text.get("1.0", tk.END).strip()
        if not message:
            messagebox.showwarning("Announcement", "Please enter a message.")
            return
        base = f"http://127.0.0.1:{SERVER_PORT}" if SERVER_PORT != 80 else "http://127.0.0.1"
        url = f"{base}/api/control/announcement"
        body = json.dumps({"message": message, "image": self.selected_image}).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=body, method="POST", headers=build_auth_headers())
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            sent = data.get("sent_to", 0)
            messagebox.showinfo("Announcement", f"Sent to {sent} agent(s).")
            self.win.destroy()
        except urllib.error.HTTPError as e:
            if e.code == 403:
                messagebox.showerror("Announcement", "Forbidden. Run control panel on server machine.")
            else:
                messagebox.showerror("Announcement", f"Server error: {e.code}")
        except Exception as e:
            messagebox.showerror("Announcement", f"Failed to send: {e}")


class ControlPanelApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("TeamsPowerDialer - Server Control Panel")
        self.root.minsize(600, 450)
        self.root.geometry("800x600")

        self._log_queue: list[str] = []
        self.controller = ServerController(on_output=self._on_server_output)
        self._log_buffer: list[str] = []
        self._log_max_lines = 2000
        self._pause_log = False

        self._build_ui()
        self._poll_output()

    def _build_ui(self):
        # Top: Server controls
        ctrl_frame = ttk.Frame(self.root, padding=10)
        ctrl_frame.pack(fill=tk.X)

        self.btn_start = ttk.Button(ctrl_frame, text="Start Server", command=self._start)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 5))

        self.btn_stop = ttk.Button(ctrl_frame, text="Stop Server", command=self._stop)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 5))

        self.btn_restart = ttk.Button(ctrl_frame, text="Restart", command=self._restart)
        self.btn_restart.pack(side=tk.LEFT, padx=(0, 5))

        self.lbl_status = ttk.Label(ctrl_frame, text="Status: Stopped")
        self.lbl_status.pack(side=tk.LEFT, padx=20)

        # Log view controls
        log_ctrl = ttk.Frame(self.root, padding=(10, 0))
        log_ctrl.pack(fill=tk.X)

        ttk.Label(log_ctrl, text="Log view:").pack(side=tk.LEFT, padx=(0, 5))
        self.var_filter = tk.StringVar(value="Full")
        self.combo_filter = ttk.Combobox(
            log_ctrl, textvariable=self.var_filter,
            values=[f[0] for f in LOG_FILTERS], state="readonly", width=20
        )
        self.combo_filter.pack(side=tk.LEFT, padx=(0, 10))
        self.combo_filter.bind("<<ComboboxSelected>>", self._on_filter_change)

        ttk.Button(log_ctrl, text="Clear", command=self._clear_log).pack(side=tk.LEFT, padx=(0, 5))
        self.var_pause = tk.BooleanVar(value=False)
        ttk.Checkbutton(log_ctrl, text="Pause", variable=self.var_pause).pack(side=tk.LEFT)

        # Log area
        log_frame = ttk.Frame(self.root, padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD, height=20, state=tk.DISABLED,
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4", insertbackground="white"
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Bottom: Actions
        bottom = ttk.Frame(self.root, padding=10)
        bottom.pack(fill=tk.X)

        ttk.Button(bottom, text="Open Database Snapshot", command=self._open_db).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(bottom, text="Queue Status", command=self._open_queue_status).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(bottom, text="Send Announcement", command=self._open_announcement).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(bottom, text="Open Dashboard (Local)", command=lambda: self._open_url(DASHBOARD_URL)).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(bottom, text="Open Dashboard (Network)", command=lambda: self._open_url(DASHBOARD_URL_NETWORK)).pack(side=tk.LEFT)

        self._update_buttons()

    def _on_server_output(self, line: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_queue.append(f"{ts} | {line.rstrip()}")

    def _on_filter_change(self, event=None):
        self._refresh_log_display()

    def _refresh_log_display(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        pat = LOG_PATTERNS.get(self.var_filter.get(), None)
        for line in self._log_buffer:
            if matches_filter(line, pat):
                self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _clear_log(self):
        self._log_buffer.clear()
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _poll_output(self):
        self._pause_log = self.var_pause.get()
        if not self._pause_log and self._log_queue:
            # Capture new lines from server
            new_lines = self._log_queue[:]
            self._log_queue.clear()

            # Append to buffer for display
            self._log_buffer.extend(new_lines)
            if len(self._log_buffer) > self._log_max_lines:
                self._log_buffer = self._log_buffer[-self._log_max_lines:]

            # Check for remote console restart command in new log lines
            for line in new_lines:
                if "[RemoteConsole] CMD:RESTART_SERVER" in line:
                    # Schedule restart on the Tk main loop so it runs on the GUI thread
                    self.root.after(0, self._restart)
                    break

            self._refresh_log_display()
        self._update_buttons()
        self.root.after(200, self._poll_output)

    def _update_buttons(self):
        has_process = self.controller.is_running
        port_in_use = self.controller.is_server_on_port()
        running = has_process or port_in_use
        self.btn_start.config(state=tk.NORMAL if not running else tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL if running else tk.DISABLED)
        self.btn_restart.config(state=tk.NORMAL if running else tk.DISABLED)
        if has_process:
            status_text = "Status: Running"
        elif port_in_use:
            status_text = "Status: Running (orphaned - Stop to reclaim)"
        else:
            status_text = "Status: Stopped"
        self.lbl_status.config(
            text=status_text,
            foreground="green" if running else "gray"
        )

    def _start(self):
        if self.controller.start():
            messagebox.showinfo("Server", "Server started.")
        else:
            messagebox.showerror("Server", "Failed to start server.")

    def _stop(self):
        self.controller.stop()
        messagebox.showinfo("Server", "Server stopped.")

    def _restart(self):
        if self.controller.restart():
            messagebox.showinfo("Server", "Server restarted.")
        else:
            messagebox.showerror("Server", "Failed to restart server.")

    def _open_db(self):
        db_viewer.open_viewer(self.root)

    def _open_queue_status(self):
        QueueStatusWindow(self.root)

    def _open_announcement(self):
        AnnouncementDialog(self.root)

    def _open_url(self, url: str):
        import webbrowser
        webbrowser.open(url)

    def run(self):
        self.root.mainloop()


def main():
    app = ControlPanelApp()
    app.run()
