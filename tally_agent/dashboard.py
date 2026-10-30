"""
Onsumm Tally Agent — dashboard window.

Shows everything the agent knows: cloud connection, Tally status, the
current sync step, KPIs, every voucher and ledger fetched, sync history,
row errors reported by the server, and a live log.

Worker threads never touch Tk. They write to agent.STATE; this window
polls it once a second. The tray talks to the window via UI_QUEUE.
"""

from __future__ import annotations

import csv
import logging
import os
import queue
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

UI_QUEUE: "queue.Queue[str]" = queue.Queue()      # "show" | "quit" from the tray
_LOG_QUEUE: "queue.Queue[tuple[str, str]]" = queue.Queue()

C_OK, C_ERR, C_WARN, C_IDLE = "#16a34a", "#dc2626", "#d97706", "#64748b"
C_BG, C_CARD, C_INK, C_MUTED, C_BRAND = "#f5f7fb", "#ffffff", "#0f172a", "#64748b", "#004080"


class _QueueLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _LOG_QUEUE.put_nowait((record.levelname, self.format(record)))
        except Exception:
            pass


def _money(v: float) -> str:
    return f"₹{v:,.2f}"


def _ago(ts: float | None) -> str:
    if not ts:
        return "—"
    secs = int(ts - time.time())
    if secs <= 0:
        return "now"
    m, s = divmod(secs, 60)
    return f"in {m}m {s:02d}s" if m else f"in {s}s"


class Dashboard:
    def __init__(self, agent, cfg: dict, start_hidden: bool = False):
        self.a, self.cfg = agent, cfg
        self.root = tk.Tk()
        self.root.title(f"Onsumm Tally Agent v{agent.AGENT_VERSION}")
        self.root.geometry("1180x760")
        self.root.minsize(980, 620)
        self.root.configure(bg=C_BG)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self._told_hidden = False
        self._history_len = -1
        self._vouchers_id = None
        self._ledgers_id = None
        self._row_errors_len = -1

        handler = _QueueLogHandler()
        handler.setFormatter(logging.Formatter(agent._LOG_FORMAT))
        logging.getLogger().addHandler(handler)

        self._style()
        self._build()
        if start_hidden:
            self.root.withdraw()
        self.root.after(200, self._tick)

    # ── look ──────────────────────────────────────────────────────────────────
    def _style(self) -> None:
        st = ttk.Style(self.root)
        try:
            st.theme_use("vista" if self.a.IS_WINDOWS else "clam")
        except tk.TclError:
            st.theme_use("clam")
        st.configure("Treeview", rowheight=24, font=("Segoe UI", 9))
        st.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        st.configure("TNotebook.Tab", padding=(14, 6), font=("Segoe UI", 10))

    def _card(self, parent, **pack) -> tk.Frame:
        f = tk.Frame(parent, bg=C_CARD, highlightbackground="#e2e8f0", highlightthickness=1)
        f.pack(**pack)
        return f

    # ── layout ────────────────────────────────────────────────────────────────
    def _build(self) -> None:
        a = self.a
        top = tk.Frame(self.root, bg=C_BRAND)
        top.pack(fill="x")
        tk.Label(top, text="Onsumm Tally Agent", bg=C_BRAND, fg="white",
                 font=("Segoe UI", 14, "bold")).pack(side="left", padx=16, pady=10)
        self.pills = {}
        for key in ("sync", "tally", "cloud"):
            lbl = tk.Label(top, text="", bg=C_IDLE, fg="white", font=("Segoe UI", 9, "bold"), padx=10, pady=3)
            lbl.pack(side="right", padx=(0, 10), pady=10)
            self.pills[key] = lbl

        bar = tk.Frame(self.root, bg=C_BG)
        bar.pack(fill="x", padx=14, pady=(10, 4))
        self.btn_sync = ttk.Button(bar, text="⟳  Sync now", command=lambda: a.request_sync("manual"))
        self.btn_sync.pack(side="left")
        ttk.Button(bar, text="Test connections", command=self._test).pack(side="left", padx=6)
        ttk.Button(bar, text="Open log folder", command=self._open_folder).pack(side="left")
        ttk.Button(bar, text="Tally settings…", command=self._tally_settings).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Re-pair…", command=self._repair).pack(side="left", padx=6)
        self.step_lbl = tk.Label(bar, text="", bg=C_BG, fg=C_MUTED, font=("Segoe UI", 9))
        self.step_lbl.pack(side="right")

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=14, pady=(4, 14))
        self.nb = nb
        self._build_overview(nb)
        self._build_vouchers(nb)
        self._build_ledgers(nb)
        self._build_history(nb)
        self._build_errors(nb)
        self._build_log(nb)

    def _build_overview(self, nb) -> None:
        tab = tk.Frame(nb, bg=C_BG)
        nb.add(tab, text="Overview")

        kpi_row = tk.Frame(tab, bg=C_BG)
        kpi_row.pack(fill="x", pady=(10, 8))
        self.kpis = {}
        for key, label in (("bills", "Bills"), ("invoices", "Invoices"), ("payments", "Payments"),
                           ("ledgers", "Ledgers"), ("vouchers", "Vouchers fetched"), ("errors", "Row errors")):
            card = self._card(kpi_row, side="left", fill="x", expand=True, padx=(0, 8))
            tk.Label(card, text=label, bg=C_CARD, fg=C_MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(10, 0))
            big = tk.Label(card, text="—", bg=C_CARD, fg=C_INK, font=("Segoe UI", 20, "bold"))
            big.pack(anchor="w", padx=12)
            sub = tk.Label(card, text="", bg=C_CARD, fg=C_MUTED, font=("Segoe UI", 8), justify="left")
            sub.pack(anchor="w", padx=12, pady=(0, 10))
            self.kpis[key] = (big, sub)

        cols = tk.Frame(tab, bg=C_BG)
        cols.pack(fill="both", expand=True)
        self.info = {}
        for title, keys in (
            ("Onsumm cloud", ["Server", "Status", "Tenant", "Connection", "Cloud last sync", "Agent key",
                              "Latency", "Checked", "Message"]),
            ("Tally", ["Address", "Status", "Companies", "Company used", "Latency", "Checked"]),
            ("Sync", ["State", "Last run", "Result", "Duration", "Fetched", "Parsed by server",
                      "Next run", "Interval", "Window", "Paired", "Auto-start", "Config", "Log file"]),
        ):
            card = self._card(cols, side="left", fill="both", expand=True, padx=(0, 8))
            tk.Label(card, text=title, bg=C_CARD, fg=C_BRAND, font=("Segoe UI", 11, "bold")).grid(
                row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 6))
            for i, k in enumerate(keys, start=1):
                tk.Label(card, text=k, bg=C_CARD, fg=C_MUTED, font=("Segoe UI", 9)).grid(
                    row=i, column=0, sticky="nw", padx=(12, 8), pady=2)
                v = tk.Label(card, text="—", bg=C_CARD, fg=C_INK, font=("Segoe UI", 9),
                             wraplength=250, justify="left", anchor="w")
                v.grid(row=i, column=1, sticky="nw", padx=(0, 12), pady=2)
                self.info[(title, k)] = v
            card.grid_columnconfigure(1, weight=1)

        err = self._card(tab, fill="x", pady=(8, 0))
        head = tk.Frame(err, bg=C_CARD)
        head.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(head, text="Last error", bg=C_CARD, fg=C_ERR, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(head, text="Copy", command=lambda: self._copy(self.err_txt)).pack(side="right")
        self.err_txt = tk.Text(err, height=6, wrap="word", font=("Consolas", 9), relief="flat", bg="#fef2f2")
        self.err_txt.pack(fill="x", padx=12, pady=8)
        self._set_text(self.err_txt, "No errors.")

    def _table(self, parent, columns: list[tuple[str, str, int, str]]) -> ttk.Treeview:
        wrap = tk.Frame(parent, bg=C_BG)
        wrap.pack(fill="both", expand=True)
        tv = ttk.Treeview(wrap, columns=[c[0] for c in columns], show="headings")
        for cid, head, width, anchor in columns:
            tv.heading(cid, text=head, command=lambda c=cid: self._sort(tv, c))
            tv.column(cid, width=width, anchor=anchor, stretch=True)
        ys = ttk.Scrollbar(wrap, orient="vertical", command=tv.yview)
        xs = ttk.Scrollbar(wrap, orient="horizontal", command=tv.xview)
        tv.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        tv.pack(fill="both", expand=True)
        tv.tag_configure("bad", foreground=C_ERR)
        tv.tag_configure("muted", foreground=C_MUTED)
        return tv

    def _filters(self, tab, kinds: list[str] | None, on_change, export) -> tuple[tk.StringVar, tk.StringVar, tk.Label]:
        row = tk.Frame(tab, bg=C_BG)
        row.pack(fill="x", pady=(10, 6))
        kind = tk.StringVar(value="All")
        if kinds:
            tk.Label(row, text="Show", bg=C_BG).pack(side="left")
            cb = ttk.Combobox(row, textvariable=kind, values=kinds, width=14, state="readonly")
            cb.pack(side="left", padx=(6, 12))
            cb.bind("<<ComboboxSelected>>", lambda _e: on_change())
        tk.Label(row, text="Search", bg=C_BG).pack(side="left")
        q = tk.StringVar()
        ent = ttk.Entry(row, textvariable=q, width=32)
        ent.pack(side="left", padx=6)
        q.trace_add("write", lambda *_: on_change())
        ttk.Button(row, text="Export CSV", command=export).pack(side="right")
        count = tk.Label(row, text="", bg=C_BG, fg=C_MUTED)
        count.pack(side="right", padx=12)
        return kind, q, count

    def _build_vouchers(self, nb) -> None:
        tab = tk.Frame(nb, bg=C_BG)
        nb.add(tab, text="Bills & vouchers")
        self.v_kind, self.v_q, self.v_count = self._filters(
            tab, ["All", "Bill", "Invoice", "Payment", "Not synced"], self._fill_vouchers,
            lambda: self._export(self._voucher_rows(), "tally_vouchers.csv"))
        self.v_note = tk.Label(tab, text="", bg=C_BG, fg=C_MUTED, font=("Segoe UI", 8), anchor="w", justify="left")
        self.v_note.pack(fill="x")
        self.v_tv = self._table(tab, [
            ("date", "Date", 90, "w"), ("kind", "Onsumm type", 95, "w"), ("type", "Tally voucher type", 130, "w"),
            ("number", "Number", 120, "w"), ("party", "Party", 250, "w"), ("gstin", "GSTIN", 140, "w"),
            ("amount", "Amount", 110, "e"), ("tax", "Tax", 90, "e"), ("items", "Items", 50, "e"),
            ("narration", "Narration", 260, "w"),
        ])

    def _build_ledgers(self, nb) -> None:
        tab = tk.Frame(nb, bg=C_BG)
        nb.add(tab, text="Ledgers")
        self.l_kind, self.l_q, self.l_count = self._filters(
            tab, ["All", "Supplier", "Customer"], self._fill_ledgers,
            lambda: self._export(self._ledger_rows(), "tally_ledgers.csv"))
        self.l_tv = self._table(tab, [
            ("name", "Ledger", 300, "w"), ("group", "Group", 200, "w"), ("party", "Party", 90, "w"),
            ("gstin", "GSTIN", 150, "w"), ("phone", "Phone", 120, "w"), ("email", "Email", 220, "w"),
        ])

    def _build_history(self, nb) -> None:
        tab = tk.Frame(nb, bg=C_BG)
        nb.add(tab, text="Sync history")
        tk.Label(tab, text="Every sync this agent has run (latest first). Saved kept across restarts.",
                 bg=C_BG, fg=C_MUTED).pack(anchor="w", pady=(10, 6))
        self.h_tv = self._table(tab, [
            ("started", "Started", 140, "w"), ("trigger", "Trigger", 80, "w"), ("result", "Result", 70, "w"),
            ("dur", "Duration", 70, "e"), ("bills", "Bills", 60, "e"), ("invoices", "Invoices", 70, "e"),
            ("payments", "Payments", 75, "e"), ("ledgers", "Ledgers", 65, "e"), ("errors", "Row errs", 70, "e"),
            ("kb", "Uploaded", 80, "e"), ("timing", "Tally / upload ms", 130, "e"), ("error", "Error", 360, "w"),
        ])

    def _build_errors(self, nb) -> None:
        tab = tk.Frame(nb, bg=C_BG)
        nb.add(tab, text="Row errors")
        tk.Label(tab, text="Rows the Onsumm server could not save in the last sync (first 25), as reported by the server.",
                 bg=C_BG, fg=C_MUTED).pack(anchor="w", pady=(10, 6))
        self.rowerr_txt = tk.Text(tab, wrap="word", font=("Consolas", 9))
        self.rowerr_txt.pack(fill="both", expand=True)
        self._set_text(self.rowerr_txt, "No row errors reported.")

    def _build_log(self, nb) -> None:
        tab = tk.Frame(nb, bg=C_BG)
        nb.add(tab, text="Live log")
        row = tk.Frame(tab, bg=C_BG)
        row.pack(fill="x", pady=(10, 6))
        self.log_level = tk.StringVar(value="INFO")
        tk.Label(row, text="Level", bg=C_BG).pack(side="left")
        ttk.Combobox(row, textvariable=self.log_level, values=["DEBUG", "INFO", "WARNING", "ERROR"],
                     width=10, state="readonly").pack(side="left", padx=6)
        self.log_follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Follow", variable=self.log_follow).pack(side="left", padx=6)
        ttk.Button(row, text="Copy all", command=lambda: self._copy(self.log_txt)).pack(side="right")
        ttk.Button(row, text="Clear view", command=lambda: self.log_txt.delete("1.0", "end")).pack(side="right", padx=6)
        ttk.Button(row, text="Open log file", command=self._open_log).pack(side="right")
        wrap = tk.Frame(tab)
        wrap.pack(fill="both", expand=True)
        self.log_txt = tk.Text(wrap, wrap="none", font=("Consolas", 9), bg="#0b1220", fg="#e2e8f0",
                               insertbackground="white")
        ys = ttk.Scrollbar(wrap, command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.log_txt.pack(fill="both", expand=True)
        for lvl, col in (("WARNING", "#fbbf24"), ("ERROR", "#f87171"), ("CRITICAL", "#f87171"), ("DEBUG", "#94a3b8")):
            self.log_txt.tag_configure(lvl, foreground=col)
        # Seed with the tail of today's log so the view is never empty
        try:
            tail = self.a.LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
            self.log_txt.insert("end", "\n".join(tail) + "\n")
            self.log_txt.see("end")
        except Exception:
            pass

    # ── refresh ───────────────────────────────────────────────────────────────
    def _tick(self) -> None:
        try:
            self._drain_ui_queue()
            self._drain_log()
            self._refresh()
        except Exception:
            self.a.log.exception("Dashboard refresh failed")
        self.root.after(1000, self._tick)

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                msg = UI_QUEUE.get_nowait()
            except queue.Empty:
                return
            if msg == "show":
                self.show()
            elif msg == "quit":
                self.root.destroy()
                return

    def _drain_log(self) -> None:
        order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
        floor = order.get(self.log_level.get(), 20)
        added = False
        for _ in range(500):
            try:
                lvl, line = _LOG_QUEUE.get_nowait()
            except queue.Empty:
                break
            if order.get(lvl, 20) >= floor:
                self.log_txt.insert("end", line + "\n", lvl)
                added = True
        if added:
            n = int(self.log_txt.index("end-1c").split(".")[0])
            if n > 5000:
                self.log_txt.delete("1.0", f"{n - 5000}.0")
            if self.log_follow.get():
                self.log_txt.see("end")

    def _pill(self, key: str, text: str, color: str) -> None:
        self.pills[key].config(text=text, bg=color)

    def _set(self, card: str, key: str, value, color: str = C_INK) -> None:
        self.info[(card, key)].config(text=str(value) if value not in (None, "") else "—", fg=color)

    def _refresh(self) -> None:
        a, S = self.a, self.a.STATE
        with S.lock:
            cloud, tally, sync = dict(S.cloud), dict(S.tally), dict(S.sync)
            last = dict(sync.get("last") or {})
            next_at, last_error = S.next_sync_at, S.last_error
            vouchers, ledgers, history, row_errors = S.vouchers, S.ledgers, list(S.history), list(S.row_errors)
            fetched_at = S.fetched_at

        # pills
        cs = cloud.get("state")
        self._pill("cloud", "Cloud ● connected" if cs == "ok" else "Cloud ● error" if cs == "error" else "Cloud ● checking",
                   C_OK if cs == "ok" else C_ERR if cs == "error" else C_IDLE)
        ts = tally.get("state")
        self._pill("tally", "Tally ● running" if ts == "ok" else "Tally ● not reachable" if ts == "error" else "Tally ● checking",
                   C_OK if ts == "ok" else C_ERR if ts == "error" else C_IDLE)
        if sync["running"]:
            self._pill("sync", "Sync ● running", C_WARN)
        elif last:
            self._pill("sync", "Sync ● ok" if last.get("ok") else "Sync ● failed", C_OK if last.get("ok") else C_ERR)
        else:
            self._pill("sync", "Sync ● not run yet", C_IDLE)
        self.step_lbl.config(text=f"{sync['step']}…" if sync["running"] else f"Next automatic sync {_ago(next_at)}")
        self.btn_sync.state(["disabled"] if sync["running"] else ["!disabled"])

        # cloud card
        C = "Onsumm cloud"
        self._set(C, "Server", a.CLOUD_BASE_URL)
        self._set(C, "Status", "Connected" if cs == "ok" else cloud.get("error", "Checking…"),
                  C_OK if cs == "ok" else C_ERR if cs == "error" else C_MUTED)
        self._set(C, "Tenant", cloud.get("tenant_name") or self.cfg.get("tenant_name"))
        self._set(C, "Connection", cloud.get("connection_status"))
        self._set(C, "Cloud last sync", (cloud.get("last_sync_at") or "")[:19].replace("T", " "))
        kv = cloud.get("key_verified")
        self._set(C, "Agent key", f"{a._mask(self.cfg.get('api_key', ''))}  " +
                  ("(valid)" if kv else "(rejected)" if cs == "error" and kv is False else "(not verified yet)"),
                  C_OK if kv else C_ERR if cs == "error" else C_MUTED)
        self._set(C, "Latency", f"{cloud['latency_ms']:.0f} ms" if "latency_ms" in cloud else "")
        self._set(C, "Checked", cloud.get("checked_at"))
        self._set(C, "Message", cloud.get("error_message") or cloud.get("note"))

        # tally card
        T = "Tally"
        self._set(T, "Address", f"{a.TALLY_HOST}:{a.TALLY_PORT}" + ("" if a.tally_is_local() else "  (remote)"))
        self._set(T, "Status", "Running" if ts == "ok" else tally.get("error", "Checking…"),
                  C_OK if ts == "ok" else C_ERR if ts == "error" else C_MUTED)
        comps = tally.get("companies") or []
        self._set(T, "Companies", ", ".join(comps) if comps else ("none reported" if ts == "ok" else ""))
        wanted = (self.cfg.get("tally_company") or "").strip()
        if wanted and ts == "ok" and comps and wanted not in comps:
            # On shared servers another customer's Tally can hold the port — never sync blind
            self._set(T, "Company used", f"{wanted} — NOT open in this Tally (it has: {', '.join(comps)})", C_ERR)
        else:
            self._set(T, "Company used", wanted or "the company open in Tally")
        self._set(T, "Latency", f"{tally['latency_ms']:.0f} ms" if "latency_ms" in tally else "")
        self._set(T, "Checked", tally.get("checked_at"))

        # sync card
        Y = "Sync"
        self._set(Y, "State", sync["step"] if sync["running"] else "Idle", C_WARN if sync["running"] else C_INK)
        self._set(Y, "Last run", f"{last.get('started', '')}  ({last.get('trigger', '')})" if last else "")
        self._set(Y, "Result", last.get("summary"), C_OK if last.get("ok") else C_ERR)
        self._set(Y, "Duration", f"{last['duration_s']} s" if "duration_s" in last else "")
        f = last.get("fetched") or {}
        self._set(Y, "Fetched", ", ".join(f"{v} {k}" for k, v in f.items()) if f else "")
        p = last.get("parsed") or {}
        self._set(Y, "Parsed by server", ", ".join(f"{v} {k}" for k, v in p.items()) if p else "")
        self._set(Y, "Next run", _ago(next_at))
        self._set(Y, "Interval", f"every {a.POLL_INTERVAL // 60} min")
        self._set(Y, "Window", f"last {a.SYNC_WINDOW_DAYS} days of vouchers")
        self._set(Y, "Paired", self.cfg.get("paired_at"))
        self._set(Y, "Auto-start", "on" if a._is_autostart_enabled() else "off")
        self._set(Y, "Config", str(a.CONFIG_PATH))
        self._set(Y, "Log file", str(a.LOG_PATH))

        # KPIs: last sync saved / parsed, plus cloud totals
        saved = last.get("saved") or {}
        totals = cloud.get("cloud_counts") or {}
        for key in ("bills", "invoices", "payments", "ledgers"):
            big, sub = self.kpis[key]
            big.config(text=str(saved[key]) if key in saved else "—")
            lines = []
            if key in p:
                lines.append(f"parsed {p[key]} · saved {saved.get(key, 0)}")
            if key in totals:
                lines.append(f"{totals[key]:,} in Onsumm total")
            sub.config(text="\n".join(lines) or "last sync")
        big, sub = self.kpis["vouchers"]
        big.config(text=str(len(vouchers)) if fetched_at else "—")
        sub.config(text=f"from Tally at {fetched_at}" if fetched_at else "no fetch yet")
        big, sub = self.kpis["errors"]
        n_err = saved.get("errors")
        big.config(text=str(n_err) if n_err is not None else "—", fg=C_ERR if n_err else C_INK)
        sub.config(text="see Row errors tab" if n_err else "last sync")

        if last_error:
            self._set_text(self.err_txt, f"[{last_error['at']}] {last_error['headline']}\n\n{last_error['details']}")

        # tables — rebuild only when the data changed
        if id(vouchers) != self._vouchers_id:
            self._vouchers_id = id(vouchers)
            self._fill_vouchers()
        if id(ledgers) != self._ledgers_id:
            self._ledgers_id = id(ledgers)
            self._fill_ledgers()
        if len(history) != self._history_len:
            self._history_len = len(history)
            self._fill_history(history)
        if len(row_errors) != self._row_errors_len or (row_errors and self._row_errors_len == -1):
            self._row_errors_len = len(row_errors)
            self._set_text(self.rowerr_txt, "\n".join(row_errors) if row_errors else "No row errors reported.")

    # ── tables ────────────────────────────────────────────────────────────────
    def _voucher_rows(self) -> list[dict]:
        kind, q = self.v_kind.get(), self.v_q.get().strip().lower()
        rows = self.a.STATE.vouchers
        if kind != "All":
            rows = [r for r in rows if r["kind"] == kind]
        if q:
            rows = [r for r in rows if q in " ".join(str(v) for v in r.values()).lower()]
        return rows

    def _fill_vouchers(self) -> None:
        rows = self._voucher_rows()
        tv = self.v_tv
        tv.delete(*tv.get_children())
        for r in rows[:5000]:
            tv.insert("", "end", tags=("muted",) if r["kind"] == "Not synced" else (), values=(
                r["date"], r["kind"], r["type"], r["number"], r["party"], r["gstin"],
                _money(r["amount"]), _money(r["tax"]) if r["tax"] else "", r["items"] or "", r["narration"][:200]))
        total = sum(r["amount"] for r in rows)
        more = f" (showing first 5,000)" if len(rows) > 5000 else ""
        self.v_count.config(text=f"{len(rows):,} vouchers · {_money(total)}{more}")
        fetched = self.a.STATE.fetched_at
        self.v_note.config(text=(f"Fetched from Tally at {fetched}. Sales → Invoice, Purchase → Bill, "
                                 f"Payment/Receipt → Payment; other voucher types are not synced to Onsumm."
                                 if fetched else "Nothing fetched yet — click Sync now."))

    def _ledger_rows(self) -> list[dict]:
        kind, q = self.l_kind.get(), self.l_q.get().strip().lower()
        rows = self.a.STATE.ledgers
        if kind != "All":
            rows = [r for r in rows if r["party"] == kind]
        if q:
            rows = [r for r in rows if q in " ".join(str(v) for v in r.values()).lower()]
        return rows

    def _fill_ledgers(self) -> None:
        rows = self._ledger_rows()
        tv = self.l_tv
        tv.delete(*tv.get_children())
        for r in rows[:5000]:
            tv.insert("", "end", values=(r["name"], r["group"], r["party"], r["gstin"], r["phone"], r["email"]))
        self.l_count.config(text=f"{len(rows):,} ledgers")

    def _fill_history(self, history: list[dict]) -> None:
        tv = self.h_tv
        tv.delete(*tv.get_children())
        for h in reversed(history):
            s = h.get("saved") or {}
            timing = ""
            if "upload_ms" in h:
                timing = f"{h.get('tally_ledgers_ms', 0) + h.get('tally_vouchers_ms', 0)} / {h['upload_ms']}"
            tv.insert("", "end", tags=() if h.get("ok") else ("bad",), values=(
                h.get("started", ""), h.get("trigger", ""), "OK" if h.get("ok") else "FAILED",
                f"{h.get('duration_s', '')} s", s.get("bills", ""), s.get("invoices", ""), s.get("payments", ""),
                s.get("ledgers", ""), s.get("errors", ""),
                f"{h.get('ledgers_kb', 0) + h.get('vouchers_kb', 0)} KB" if "vouchers_kb" in h else "",
                timing, h.get("error", "")))

    def _sort(self, tv: ttk.Treeview, col: str) -> None:
        items = [(tv.set(k, col), k) for k in tv.get_children("")]

        def key(v):
            t = v[0].replace("₹", "").replace(",", "").replace(" s", "").replace(" KB", "")
            try:
                return (0, float(t))
            except ValueError:
                return (1, v[0].lower())
        desc = getattr(tv, "_sort_desc", {}).get(col, False)
        items.sort(key=key, reverse=not desc)
        for i, (_, k) in enumerate(items):
            tv.move(k, "", i)
        tv._sort_desc = {col: not desc}  # type: ignore[attr-defined]

    # ── actions ───────────────────────────────────────────────────────────────
    def _test(self) -> None:
        import threading

        def run():
            self.a.log.info("Testing connections (manual)")
            self.a.check_cloud(self.cfg)
            self.a.check_tally()
            self.a.log.info("Connection test done: cloud=%s tally=%s",
                            self.a.STATE.cloud.get("state"), self.a.STATE.tally.get("state"))
        threading.Thread(target=run, daemon=True, name="test").start()

    def _export(self, rows: list[dict], name: str) -> None:
        if not rows:
            messagebox.showinfo("Export", "Nothing to export yet.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=name,
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        self.a.log.info("Exported %d rows to %s", len(rows), path)

    def _open_folder(self) -> None:
        self._open(str(self.a.APP_DIR))

    def _open_log(self) -> None:
        self._open(str(self.a.LOG_PATH))

    @staticmethod
    def _open(path: str) -> None:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    def _tally_settings(self) -> None:
        a = self.a
        win = tk.Toplevel(self.root)
        win.title("Tally settings")
        win.transient(self.root)
        win.resizable(False, False)
        f = tk.Frame(win, padx=18, pady=14)
        f.pack(fill="both", expand=True)
        tk.Label(f, text="Where does TallyPrime run?", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Label(f, justify="left", fg="#555", wraplength=420, text=(
            "On this PC: keep localhost and port 9000.\n"
            "Remote App / Tally on Cloud: Tally is on the provider's server. Either run this agent on that "
            "server (keep localhost), or ask the provider to open the Tally port to this PC only (VPN or "
            "firewall rule) and enter the server address here. Tally's port has no password — never open "
            "it to the whole internet.")).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 12))
        host = tk.StringVar(value=a.TALLY_HOST)
        port = tk.StringVar(value=str(a.TALLY_PORT))
        comp = tk.StringVar(value=self.cfg.get("tally_company", ""))
        tk.Label(f, text="Host").grid(row=2, column=0, sticky="w")
        ttk.Entry(f, textvariable=host, width=34).grid(row=2, column=1, sticky="w", pady=3)
        tk.Label(f, text="Port").grid(row=3, column=0, sticky="w")
        ttk.Entry(f, textvariable=port, width=10).grid(row=3, column=1, sticky="w", pady=3)
        tk.Label(f, text="Company").grid(row=4, column=0, sticky="w")
        comps = a.STATE.tally.get("companies") or []
        cb = ttk.Combobox(f, textvariable=comp, values=[""] + comps, width=32)
        cb.grid(row=4, column=1, sticky="w", pady=3)
        tk.Label(f, text="blank = the company open in Tally", fg="#888", font=("Segoe UI", 8)).grid(row=5, column=1, sticky="w")
        result = tk.Label(f, text="", wraplength=420, justify="left")
        result.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 4))

        def apply() -> bool:
            try:
                p = int(port.get().strip())
            except ValueError:
                result.config(text="Port must be a number, e.g. 9000.", fg=C_ERR)
                return False
            a.set_tally_target(host.get(), p, comp.get())
            self.cfg.update(a.load_config())
            return True

        def test() -> None:
            if not apply():
                return
            result.config(text=f"Connecting to {a.TALLY_HOST}:{a.TALLY_PORT} …", fg=C_MUTED)

            def run():
                a.check_tally()
                t = a.STATE.tally
                msg = (f"Connected. Companies open: {', '.join(t.get('companies') or []) or 'none reported'}"
                       if t.get("state") == "ok" else f"Failed: {t.get('error')}  (details in Last error)")
                self.root.after(0, lambda: (result.config(text=msg, fg=C_OK if t.get("state") == "ok" else C_ERR),
                                            cb.config(values=[""] + (t.get("companies") or []))))
            import threading
            threading.Thread(target=run, daemon=True, name="tally-test").start()

        def save() -> None:
            if apply():
                win.destroy()
                a.request_sync("settings changed")

        btns = tk.Frame(f)
        btns.grid(row=7, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(btns, text="Test", command=test).pack(side="left")
        ttk.Button(btns, text="Save & sync", command=save).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left")

    def _repair(self) -> None:
        if not messagebox.askyesno(
                "Re-pair agent",
                "This disconnects this PC from Onsumm and asks for a new pairing token.\n\nContinue?"):
            return
        cfg = self.a.load_config()
        cfg.pop("api_key", None)
        self.a.save_config(cfg)
        self.a.log.warning("Agent key removed by user (re-pair); restarting")
        args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(sys.argv[0])]
        subprocess.Popen(args)
        self.quit()

    def _copy(self, widget: tk.Text) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(widget.get("1.0", "end").strip())

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        if widget.get("1.0", "end").strip() == text.strip():
            return
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.config(state="disabled")

    # ── window ────────────────────────────────────────────────────────────────
    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self) -> None:
        tray = self.a.IS_WINDOWS
        if not tray:
            self.quit()
            return
        self.root.withdraw()
        if not self._told_hidden:
            self._told_hidden = True
            self.a.log.info("Dashboard hidden — agent keeps syncing from the tray")

    def quit(self) -> None:
        self.a._stop_event.set()
        self.a._sync_now.set()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
