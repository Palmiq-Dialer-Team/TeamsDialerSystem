"""Database snapshot viewer - read-only."""
import json
import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import shutil
import urllib.request
from pathlib import Path
from datetime import datetime

from .config import DB_PATH, DASHBOARD_URL, SNAPSHOT_DIR


def _server_database_path() -> str | None:
    """Return the DB path reported by the server, or None if unreachable."""
    try:
        req = urllib.request.Request(f"{DASHBOARD_URL.rstrip('/')}/api/debug/status")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return data.get("database_path")
    except Exception:
        return None


def _live_db_info(snapshot_path: Path | None) -> tuple[str, str]:
    """Return lines: (1) what we're viewing, (2) snapshot source + server DB info."""
    if snapshot_path is not None:
        viewing_line = f"Viewing snapshot (read-only): {snapshot_path}"
    else:
        viewing_line = "Viewing: (no snapshot path)"

    try:
        if DB_PATH.exists():
            mtime = datetime.fromtimestamp(DB_PATH.stat().st_mtime)
            source_line = f"Snapshot copied from: {DB_PATH} | Last modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')}"
        else:
            source_line = f"Snapshot copied from: {DB_PATH} (file not found)"
    except Exception as e:
        source_line = f"Snapshot source: {DB_PATH} | Error: {e}"

    server_path = _server_database_path()
    try:
        live_resolved = DB_PATH.resolve()
    except Exception:
        live_resolved = DB_PATH
    if server_path is None:
        extra = f"{source_line}\nServer DB: (server unreachable — is it running?)"
    elif Path(server_path).resolve() != Path(live_resolved).resolve():
        extra = (
            f"{source_line}\n"
            f"Server DB: {server_path}\n"
            "⚠ MISMATCH: Server is writing to a different file. Set env TEAMS_DIALER_DB to the Server DB path above, then restart server and control panel, so both use the same DB."
        )
    else:
        extra = f"{source_line}\nServer DB: {server_path} (matches — OK)"

    return viewing_line, extra

PAGE_SIZE = 500


def _ensure_snapshot_dir():
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _create_snapshot() -> Path | None:
    if not DB_PATH.exists():
        return None
    _ensure_snapshot_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snap = SNAPSHOT_DIR / f"teams_dialer_snapshot_{ts}.db"
    shutil.copy2(DB_PATH, snap)
    return snap


def open_viewer(parent: tk.Tk):
    snap = _create_snapshot()
    if snap is None:
        messagebox.showerror("Database", f"Database not found: {DB_PATH}")
        return
    _DbViewerWindow(parent, snap)


class _DbViewerWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk, db_path: Path):
        super().__init__(parent)
        self.title(f"Database Snapshot - {db_path.name}")
        self.geometry("900x550")
        self.db_path = db_path
        # Per-table: {table_name: {"tree": ..., "cols": [...], "page": int, "total": int}}
        self._tab_data = {}

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=5)
        ttk.Button(btn_row, text="Refresh Snapshot", command=self._refresh).pack(side=tk.LEFT, padx=(0, 10))
        self._info_label = ttk.Label(btn_row, text="", font=("", 9))
        self._info_label.pack(side=tk.LEFT)
        info_note = ttk.Label(
            self,
            text="Snapshots are taken only when you open this window or click Refresh (no automatic interval).",
            font=("", 9),
        )
        info_note.pack(pady=(0, 2))
        self._info_frame = ttk.Frame(self)
        self._info_frame.pack(fill=tk.X, pady=(0, 4))
        self._info_label2 = ttk.Label(self._info_frame, text="", font=("", 9))
        self._info_label2.pack(anchor=tk.W)
        self._update_info_label()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._load_tables()

    def _update_info_label(self):
        viewing_line, extra_line = _live_db_info(self.db_path)
        self._info_label.config(text=viewing_line)
        self._info_label2.config(text=extra_line)

    def _get_order_col(self, table: str) -> str:
        """Return the ORDER BY column for pagination (prefer id)."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in cursor.fetchall()]
            if "id" in cols:
                return "id"
            if "timestamp" in cols:
                return "timestamp"
            if "created_at" in cols:
                return "created_at"
            return cols[0] if cols else "rowid"
        finally:
            conn.close()

    def _get_total_count(self, table: str) -> int:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            return cursor.fetchone()[0]
        finally:
            conn.close()

    def _load_page(self, table: str, page: int):
        """Load a specific page of a table into its tree."""
        if table not in self._tab_data:
            return
        data = self._tab_data[table]
        tree = data["tree"]
        cols = data["cols"]
        order_col = self._get_order_col(table)

        for i in tree.get_children():
            tree.delete(i)

        offset = page * PAGE_SIZE
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM {table} ORDER BY {order_col} DESC LIMIT {PAGE_SIZE} OFFSET ?",
                (offset,),
            )
            rows = cursor.fetchall()
            for row in rows:
                tree.insert("", tk.END, values=tuple(str(v)[:80] if v is not None else "" for v in row))
        finally:
            conn.close()

        data["page"] = page
        data["total"] = self._get_total_count(table)
        self._update_pagination_ui(table)

    def _update_pagination_ui(self, table: str):
        if table not in self._tab_data:
            return
        data = self._tab_data[table]
        page = data["page"]
        total = data["total"]
        start = page * PAGE_SIZE + 1
        end = min((page + 1) * PAGE_SIZE, total)
        data["label"].config(
            text=f"Rows {start}-{end} of {total} | Page {page + 1}/{(total + PAGE_SIZE - 1) // PAGE_SIZE or 1}"
        )
        data["btn_prev"].config(state=tk.NORMAL if page > 0 else tk.DISABLED)
        data["btn_next"].config(
            state=tk.NORMAL if (page + 1) * PAGE_SIZE < total else tk.DISABLED
        )

    def _on_prev(self):
        tab_id = self.notebook.select()
        table = self.notebook.tab(tab_id, "text")
        if table in self._tab_data and self._tab_data[table]["page"] > 0:
            self._load_page(table, self._tab_data[table]["page"] - 1)

    def _on_next(self):
        tab_id = self.notebook.select()
        table = self.notebook.tab(tab_id, "text")
        if table in self._tab_data:
            data = self._tab_data[table]
            if (data["page"] + 1) * PAGE_SIZE < data["total"]:
                self._load_page(table, data["page"] + 1)

    def _on_tab_changed(self, event=None):
        tab_id = self.notebook.select()
        if not tab_id:
            return
        table = self.notebook.tab(tab_id, "text")
        if table in self._tab_data:
            self._update_pagination_ui(table)

    def _load_tables(self):
        for tab_id in list(self.notebook.tabs()):
            self.notebook.forget(tab_id)
        self._tab_data.clear()

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [r[0] for r in cursor.fetchall()]
            if not tables:
                return

            for table in tables:
                try:
                    cursor.execute(f"SELECT * FROM {table} LIMIT 0")
                    cols = [d[0] for d in cursor.description]
                except sqlite3.OperationalError:
                    cols = []

                frame = ttk.Frame(self.notebook)
                self.notebook.add(frame, text=table)

                tree_frame = ttk.Frame(frame)
                tree_frame.pack(fill=tk.BOTH, expand=True)

                vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
                hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL)

                tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=15,
                                   yscrollcommand=vsb.set, xscrollcommand=hsb.set)
                vsb.config(command=tree.yview)
                hsb.config(command=tree.xview)

                tree.grid(row=0, column=0, sticky="nsew")
                vsb.grid(row=0, column=1, sticky="ns")
                hsb.grid(row=1, column=0, sticky="ew")
                tree_frame.grid_rowconfigure(0, weight=1)
                tree_frame.grid_columnconfigure(0, weight=1)

                for c in cols:
                    tree.heading(c, text=c)
                    tree.column(c, width=100, minwidth=50)

                # Pagination bar
                pagination = ttk.Frame(frame)
                pagination.pack(fill=tk.X, pady=(5, 0))
                btn_prev = ttk.Button(pagination, text="← Previous", command=self._on_prev)
                btn_prev.pack(side=tk.LEFT, padx=(0, 5))
                btn_next = ttk.Button(pagination, text="Next →", command=self._on_next)
                btn_next.pack(side=tk.LEFT)
                label = ttk.Label(pagination, text="")
                label.pack(side=tk.LEFT, padx=20)

                self._tab_data[table] = {
                    "tree": tree,
                    "cols": cols,
                    "page": 0,
                    "total": 0,
                    "label": label,
                    "btn_prev": btn_prev,
                    "btn_next": btn_next,
                }

                self._load_page(table, 0)
        finally:
            conn.close()

    def _refresh(self):
        # Remember which tab is selected so we can restore it after reload (tabs are rebuilt in table name order, which switches to callbacks)
        tab_id = self.notebook.select()
        current_tab_text = self.notebook.tab(tab_id, "text") if tab_id else None

        snap = _create_snapshot()
        if snap:
            self.db_path = snap
            self.title(f"Database Snapshot - {snap.name}")
            self._load_tables()

            # Restore the previously selected tab (e.g. stay on Calls when refreshing)
            if current_tab_text:
                for t in self.notebook.tabs():
                    if self.notebook.tab(t, "text") == current_tab_text:
                        self.notebook.select(t)
                        break
            self._update_info_label()
