"""Mail-Assistant: read-only mail triage for visually impaired users.

Reads the inbox via IMAP (read-only), sorts mails into
Wichtig / Nicht wichtig / Vorsicht and shows them in large, high-contrast type
with optional speech output. It never deletes, moves or answers mails.
"""
import imaplib
import json
import os
import queue
import socket
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from tkinter import messagebox

from analyse import ANALYSIS_VERSION, analyse_mail
from config import Config, app_dir
from mailfetch import fetch_mails
from speech import Speaker
from version import VERSION
import update

APP_TITLE = "Mail-Assistant"
BG, FG, PANEL = "#000000", "#FFFFFF", "#1A1A1A"
CATS = [
    # key, button label, colour, spoken name
    ("wichtig", "Wichtig", "#FFD400", "Wichtig"),
    ("unwichtig", "Nicht wichtig", "#BFBFBF", "Nicht wichtig"),
    ("verdacht", "Vorsicht", "#FF5050", "Vorsicht, Verdacht auf Spam oder Betrug"),
]
CAT_INFO = {c[0]: c for c in CATS}
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def fmt_date(iso: str) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    days = (now.date() - dt.date()).days
    if days == 0:
        return f"Heute {dt:%H:%M}"
    if days == 1:
        return f"Gestern {dt:%H:%M}"
    return f"{WEEKDAYS[dt.weekday()]} {dt:%d.%m. %H:%M}"


def spoken_date(iso: str) -> str:
    return fmt_date(iso).replace(":", " Uhr ").replace(".", ". ")


class Cache:
    """Analysis results by Message-ID, so each mail is sent to the API only once."""

    def __init__(self):
        self.path = app_dir() / "cache.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


class SenderRules:
    """User overrides: sender address (or '@domain') -> category. Stored in sender_rules.json."""

    def __init__(self):
        self.path = app_dir() / "sender_rules.json"
        try:
            self.data = {k.lower(): v for k, v in json.loads(self.path.read_text(encoding="utf-8")).items()
                         if v in CAT_INFO}
        except Exception:
            self.data = {}

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def lookup(self, addr: str):
        addr = (addr or "").lower()
        if addr in self.data:
            return self.data[addr]
        domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
        return self.data.get("@" + domain) if domain else None

    def set(self, addr: str, cat):
        addr = addr.lower()
        if cat:
            self.data[addr] = cat
        else:
            self.data.pop(addr, None)
        self.save()


class SettingsDialog(tk.Toplevel):
    FIELDS = [
        ("imap_server", "Mailserver (IMAP)", False),
        ("imap_port", "Port", False),
        ("imap_user", "E-Mail-Adresse / Benutzer", False),
        ("password", "Mail-Passwort", True),
        ("api_key", "Claude API-Key", True),
        ("model", "KI-Modell", False),
        ("folder", "Ordner", False),
        ("days", "Mails der letzten … Tage", False),
        ("max_mails", "Höchstens … Mails", False),
        ("trusted_senders", "Bekannte Absender (Komma-getrennt, z. B. anna@gmx.at, @meinarzt.at)", False),
        ("speech_rate", "Sprechtempo (-10 bis 10)", False),
        ("auto_speak", "Automatisch vorlesen (1 = ja, 0 = nein)", False),
        ("auto_refresh_minutes", "Automatisch neu laden alle … Minuten (0 = aus)", False),
        ("auto_update", "Updates automatisch installieren (1 = ja, 0 = nein)", False),
    ]

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Einstellungen")
        self.configure(bg=BG, padx=20, pady=20)
        self.transient(app.root)
        font = tkfont.Font(family="Segoe UI", size=12)
        self.vars = {}
        cfg = app.cfg
        for row, (key, label, secret) in enumerate(self.FIELDS):
            tk.Label(self, text=label, bg=BG, fg=FG, font=font, anchor="w").grid(row=row, column=0, sticky="w", pady=3)
            if key == "password":
                value = cfg.get_secret(f"imap:{cfg.get('imap_user')}") if cfg.get("imap_user") else ""
            elif key == "api_key":
                value = cfg.get_secret("anthropic")
            else:
                value = cfg.get(key)
            var = tk.StringVar(value=value)
            tk.Entry(self, textvariable=var, show="•" if secret else "", width=48, font=font).grid(
                row=row, column=1, sticky="we", padx=(12, 0), pady=3)
            self.vars[key] = var
        btns = tk.Frame(self, bg=BG)
        btns.grid(row=len(self.FIELDS), column=0, columnspan=2, pady=(16, 0), sticky="e")
        tk.Button(btns, text="Abbrechen", font=font, command=self.destroy).pack(side="right", padx=4)
        tk.Button(btns, text="Speichern", font=font, command=self.save).pack(side="right", padx=4)
        self.grab_set()

    def save(self):
        cfg = self.app.cfg
        values = {k: v.get().strip() for k, v in self.vars.items()}
        for key in ("imap_port", "days", "max_mails", "speech_rate", "auto_speak", "auto_refresh_minutes", "auto_update"):
            try:
                int(values[key])
            except ValueError:
                messagebox.showerror("Einstellungen", f"Ungültige Zahl bei: {key}", parent=self)
                return
        for key, val in values.items():
            if key not in ("password", "api_key"):
                cfg.set(key, val)
        if values["imap_user"] and values["password"]:
            cfg.set_secret(f"imap:{values['imap_user']}", values["password"])
        if values["api_key"]:
            cfg.set_secret("anthropic", values["api_key"])
        cfg.save()
        self.app.speaker.set_rate(cfg.getint("speech_rate"))
        self.destroy()
        self.app.schedule_auto_refresh()
        self.app.refresh()


class App:
    def __init__(self, root: tk.Tk, demo: bool = False):
        self.root, self.demo = root, demo
        self.cfg = Config()
        self.cache = Cache()
        self.rules = SenderRules()
        self.speaker = Speaker(self.cfg.getint("speech_rate"))
        self.q = queue.Queue()
        self.records = {}          # key -> analysis result
        self.order = []            # keys, newest first
        self.visible = []          # keys shown in the list
        self.current = "wichtig"
        self.show_text = False
        self.busy = False
        self._suppress_speak = False
        self._refresh_job = None
        self.quiet = False
        self.counts = {}

        size = self.cfg.getint("font_size")
        self.font = tkfont.Font(family="Segoe UI", size=size)
        self.font_bold = tkfont.Font(family="Segoe UI", size=size, weight="bold")
        self.font_big = tkfont.Font(family="Segoe UI", size=size + 6, weight="bold")
        self.font_small = tkfont.Font(family="Segoe UI", size=max(12, size - 6))

        self._build_ui()
        self._bind_keys()
        root.after(100, self._poll)
        root.after(300, self.start)

    # ---------- UI ----------
    def _build_ui(self):
        r = self.root
        r.title(f"{APP_TITLE} {VERSION}")
        r.configure(bg=BG)
        try:
            r.state("zoomed")  # maximised on Windows
        except tk.TclError:
            r.geometry("1400x900")

        top = tk.Frame(r, bg=BG)
        top.pack(fill="x", padx=12, pady=(12, 6))
        self.cat_buttons = {}
        for i, (key, label, color, _) in enumerate(CATS):
            b = tk.Button(top, text=label, font=self.font_big, bd=4, relief="flat", cursor="hand2",
                          activebackground=color, activeforeground="#000",
                          command=lambda k=key: self.select_category(k))
            b.grid(row=0, column=i, sticky="nsew", padx=6, ipady=10)
            top.columnconfigure(i, weight=1)
            self.cat_buttons[key] = b

        bar = tk.Frame(r, bg=BG)
        bar.pack(fill="x", padx=12, pady=6)
        def tool(text, cmd, side="left"):
            b = tk.Button(bar, text=text, font=self.font_small, bg=PANEL, fg=FG, bd=2, relief="raised",
                          activebackground="#444", activeforeground=FG, cursor="hand2", command=cmd)
            b.pack(side=side, padx=4, ipady=4, ipadx=8)
            return b
        tool("🔊 Vorlesen (Leertaste)", self.speak_current)
        tool("⏹ Stopp (Esc)", self.speaker.stop)
        self.text_btn = tool("📄 Text anzeigen (T)", self.toggle_text)
        tool("⟳ Neu laden (F5)", self.refresh)
        tool("⚙", self.open_settings, side="right")
        tool("A –", lambda: self.change_font(-2), side="right")
        tool("A +", lambda: self.change_font(+2), side="right")

        body = tk.PanedWindow(r, orient="horizontal", bg=BG, sashwidth=8, bd=0)
        body.pack(fill="both", expand=True, padx=12, pady=6)

        self.listbox = tk.Listbox(body, font=self.font, bg=PANEL, fg=FG, selectbackground=FG,
                                  selectforeground="#000", activestyle="none", bd=0,
                                  highlightthickness=3, highlightcolor="#FFD400", exportselection=False, height=1)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self.on_select())
        body.add(self.listbox, minsize=300, stretch="always")

        right = tk.Frame(body, bg=PANEL)
        self.detail = tk.Text(right, font=self.font, bg=PANEL, fg=FG, wrap="word", bd=0, padx=20, pady=16, height=1,
                              cursor="arrow", spacing1=4, spacing3=4)
        sb = tk.Scrollbar(right, command=self.detail.yview, width=24)
        self.detail.configure(yscrollcommand=sb.set, state="disabled")
        rule_bar = tk.Frame(right, bg="#262626", padx=16, pady=8)
        rule_bar.pack(side="bottom", fill="x")
        tk.Label(rule_bar, text="Diesen Absender immer einstufen als:",
                 font=self.font_small, bg="#262626", fg="#9AD0FF", anchor="w").pack(fill="x")
        row = tk.Frame(rule_bar, bg="#262626")
        row.pack(fill="x", pady=(6, 0))
        rule_buttons = [(k, lbl, c) for k, lbl, c, _ in CATS] + [(None, "Automatisch", FG)]
        for col, (key, label, color) in enumerate(rule_buttons):
            tk.Button(row, text=label, font=self.font_small, bg="#000", fg=color, bd=2,
                      activebackground=color if key else "#444", activeforeground="#000" if key else FG,
                      cursor="hand2", command=lambda k=key: self.set_sender_rule(k)).grid(
                row=0, column=col, sticky="we", padx=3, ipady=2)
            row.columnconfigure(col, weight=1)
        sb.pack(side="right", fill="y")
        self.detail.pack(side="left", fill="both", expand=True)
        body.add(right, minsize=400, stretch="always")
        # place divider at ~45 % once the window has its final size
        r.after(300, lambda: body.sash_place(0, int(body.winfo_width() * 0.45), 0))

        for key, _, color, _ in CATS:
            self.detail.tag_configure(key, foreground=color, font=self.font_big)
        self.detail.tag_configure("label", foreground="#9AD0FF", font=self.font_bold)
        self.detail.tag_configure("subject", font=self.font_big)
        self.detail.tag_configure("warn", foreground="#FF5050", font=self.font_bold)
        self.detail.tag_configure("hint", foreground="#9A9A9A", font=self.font_small)

        self.status = tk.Label(r, text="", font=self.font_small, bg=BG, fg="#9AD0FF", anchor="w")
        self.status.pack(fill="x", padx=16, pady=(0, 10))
        self._update_cat_buttons()

    def _bind_keys(self):
        r = self.root
        for i, (key, *_rest) in enumerate(CATS, start=1):
            r.bind(str(i), lambda e, k=key: self.select_category(k))
        for i, (key, *_rest) in enumerate(CATS, start=1):
            r.bind(f"<Control-Key-{i}>", lambda e, k=key: (self.set_sender_rule(k), "break")[1])
        r.bind("<Control-Key-0>", lambda e: (self.set_sender_rule(None), "break")[1])
        r.bind("<F5>", lambda e: self.refresh())
        r.bind("<space>", lambda e: (self.speak_current(), "break")[1])
        r.bind("<Return>", lambda e: self.speak_current())
        r.bind("<Escape>", lambda e: self.speaker.stop())
        r.bind("t", lambda e: self.toggle_text())
        r.bind("<Control-plus>", lambda e: self.change_font(+2))
        r.bind("<Control-equal>", lambda e: self.change_font(+2))
        r.bind("<Control-minus>", lambda e: self.change_font(-2))
        r.bind("<Control-e>", lambda e: self.open_settings())
        r.bind("<Down>", lambda e: self.move(+1))
        r.bind("<Up>", lambda e: self.move(-1))
        self.listbox.bind("<Down>", lambda e: (self.move(+1), "break")[1])
        self.listbox.bind("<Up>", lambda e: (self.move(-1), "break")[1])
        self.listbox.bind("<space>", lambda e: (self.speak_current(), "break")[1])

    # ---------- categories ----------
    def rule_for(self, rec):
        """User rule for this sender, unless the sender looks forged."""
        rule = self.rules.lookup(rec.get("from_addr", ""))
        return rule if rule and not rec.get("spoofed") else None

    def cat_of(self, rec) -> str:
        return self.rule_for(rec) or rec["kategorie"]

    def set_sender_rule(self, cat):
        key = self.selected_key()
        if not key:
            return
        rec = self.records[key]
        addr = rec.get("from_addr", "")
        if not addr:
            return
        old_idx = self.visible.index(key)
        self.rules.set(addr, cat)
        if cat and rec.get("spoofed"):
            msg = "Regel gespeichert. Diese Nachricht bleibt aber unter Vorsicht, weil der Absender gefälscht sein könnte."
        elif cat:
            msg = f"Nachrichten von {addr} kommen ab jetzt immer unter {CAT_INFO[cat][1]}."
        else:
            msg = f"Eigene Regel für {addr} entfernt. Die Einstufung erfolgt wieder automatisch."
        self.set_status(msg)
        self.speaker.say(msg)
        # selected mail may have left the current group: keep the cursor position
        self._update_cat_buttons()
        self.visible = [k for k in self.order if k in self.records and self.cat_of(self.records[k]) == self.current]
        if key in self.visible:
            self.render(keep_selection=True)
        else:
            self.render(keep_selection=False)
            if self.visible:
                self._suppress_speak = True
                self._select_index(min(old_idx, len(self.visible) - 1))
                self._suppress_speak = False

    # ---------- actions ----------
    def change_font(self, delta: int):
        size = max(12, min(48, self.font.cget("size") + delta))
        self.font.configure(size=size)
        self.font_bold.configure(size=size)
        self.font_big.configure(size=size + 6)
        self.font_small.configure(size=max(12, size - 6))
        self.cfg.set("font_size", size)
        self.cfg.save()

    def _update_cat_buttons(self):
        counts = {k: 0 for k in CAT_INFO}
        for key in self.order:
            rec = self.records.get(key)
            if rec:
                counts[self.cat_of(rec)] += 1
        for key, label, color, _ in CATS:
            b = self.cat_buttons[key]
            text = f"{label} ({counts[key]})"
            if key == "verdacht":
                text = "⚠ " + text
            if key == self.current:
                b.configure(text=text, bg=color, fg="#000", relief="sunken", highlightthickness=4)
            else:
                b.configure(text=text, bg=PANEL, fg=color, relief="raised")
        self.counts = counts

    def select_category(self, key: str):
        self.current = key
        self.show_text = False
        self.render(keep_selection=False)
        if self.cfg.getint("auto_speak"):
            n = self.counts[key]
            self.speaker.say(f"{CAT_INFO[key][3]}. {n} {'Nachricht' if n == 1 else 'Nachrichten'}.")
        self.listbox.focus_set()

    def render(self, keep_selection=True):
        selected = self.selected_key() if keep_selection else None
        self._update_cat_buttons()
        self.visible = [k for k in self.order if k in self.records and self.cat_of(self.records[k]) == self.current]
        self.listbox.delete(0, "end")
        for key in self.visible:
            rec = self.records[key]
            sender = rec["from_name"] or rec["from_addr"]
            self.listbox.insert("end", f"  {sender}:  {rec['subject']}")
        color = CAT_INFO[self.current][2]
        self.listbox.configure(fg=color if self.current != "unwichtig" else FG)
        if self.visible:
            idx = self.visible.index(selected) if selected in self.visible else 0
            self._suppress_speak = keep_selection  # no speech on background re-render
            self._select_index(idx)
            self._suppress_speak = False
        else:
            self._show_detail(None)

    def _select_index(self, idx: int):
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(idx)
        self.listbox.activate(idx)
        self.listbox.see(idx)
        self.on_select()

    def selected_key(self):
        sel = self.listbox.curselection()
        return self.visible[sel[0]] if sel and sel[0] < len(self.visible) else None

    def move(self, delta: int):
        if not self.visible:
            return
        sel = self.listbox.curselection()
        idx = max(0, min(len(self.visible) - 1, (sel[0] if sel else -1) + delta))
        self.show_text = False
        self._select_index(idx)

    def on_select(self):
        key = self.selected_key()
        self._show_detail(self.records.get(key) if key else None)
        if key and not self._suppress_speak and self.cfg.getint("auto_speak"):
            self.speak_current()

    def toggle_text(self):
        self.show_text = not self.show_text
        self.text_btn.configure(text="📋 Übersicht (T)" if self.show_text else "📄 Text anzeigen (T)")
        key = self.selected_key()
        self._show_detail(self.records.get(key) if key else None)

    def _show_detail(self, rec):
        d = self.detail
        d.configure(state="normal")
        d.delete("1.0", "end")
        if not rec:
            msg = "Keine Nachrichten in dieser Gruppe." if not self.busy else "Nachrichten werden geladen …"
            d.insert("end", msg)
        else:
            cat = self.cat_of(rec)
            if cat == "verdacht":
                d.insert("end", "⚠ ACHTUNG: Verdacht auf Spam oder Betrug\n", "verdacht")
            else:
                d.insert("end", CAT_INFO[cat][1] + "\n", cat)
            d.insert("end", "\n")
            d.insert("end", "Von: ", "label")
            d.insert("end", f"{rec['from_name']}\n" if rec["from_name"] else "\n")
            d.insert("end", f"       {rec['from_addr']}\n")
            d.insert("end", "Datum: ", "label")
            d.insert("end", fmt_date(rec["date"]) + "\n")
            d.insert("end", "Betreff:\n", "label")
            d.insert("end", rec["subject"] + "\n\n", "subject")
            if self.show_text:
                d.insert("end", "Text der Nachricht:\n", "label")
                if cat == "verdacht":
                    d.insert("end", "Hinweis: Links in dieser Nachricht nicht abtippen oder öffnen.\n\n", "warn")
                d.insert("end", rec.get("text") or "(kein Text)")
            else:
                d.insert("end", "Worum geht es:\n", "label")
                d.insert("end", (rec.get("zusammenfassung") or "-") + "\n\n")
                if rec.get("handlung"):
                    d.insert("end", "Was tun:\n", "label")
                    d.insert("end", rec["handlung"] + "\n\n", ("warn",) if cat == "verdacht" else ())
                d.insert("end", "Begründung:\n", "label")
                rule = self.rules.lookup(rec.get("from_addr", ""))
                if rule and not rec.get("spoofed"):
                    d.insert("end", f"Eigene Regel: Absender immer unter „{CAT_INFO[rule][1]}“.\n")
                    d.insert("end", f"(Automatische Einstufung wäre: {CAT_INFO[rec['kategorie']][1]})\n", "hint")
                else:
                    d.insert("end", (rec.get("grund") or "-") + "\n")
                    if rule:
                        d.insert("end", "Ihre Regel für diesen Absender wurde nicht angewendet, weil die Nachricht "
                                        "gefälscht sein könnte.\n", "warn")
                if rec.get("esp"):
                    d.insert("end", f"Versendet über: {rec['esp']} (Newsletter-Dienst)\n", "hint")
                if rec.get("attachments"):
                    d.insert("end", "\nAnhänge: ", "label")
                    d.insert("end", ", ".join(rec["attachments"]) + "\n")
        d.configure(state="disabled")
        d.yview_moveto(0)

    def speak_current(self):
        key = self.selected_key()
        if not key:
            self.speaker.say(f"{CAT_INFO[self.current][3]}. Keine Nachrichten.")
            return
        rec = self.records[key]
        sender = rec["from_name"] or rec["from_addr"]
        if self.show_text:
            text = f"Nachricht von {sender}. {rec.get('text', '')[:4000]}"
        else:
            cat = self.cat_of(rec)
            prefix = "Achtung, Verdacht auf Betrug." if cat == "verdacht" else CAT_INFO[cat][3] + "."
            text = (f"{prefix} Von {sender}, {spoken_date(rec['date'])}. Betreff: {rec['subject']}. "
                    f"{rec.get('zusammenfassung', '')} {rec.get('handlung', '')}")
        self.speaker.say(text)

    def open_settings(self):
        SettingsDialog(self)

    # ---------- loading ----------
    def start(self):
        cfg = self.cfg
        if not self.demo and (not cfg.get("imap_user") or not cfg.get_secret(f"imap:{cfg.get('imap_user')}")):
            self.set_status("Bitte zuerst die Zugangsdaten eintragen.")
            self.open_settings()
            return
        self.schedule_auto_refresh()
        self.refresh()
        if not self.demo and self.cfg.getint("auto_update"):
            self.root.after(5000, lambda: threading.Thread(target=self._update_worker, daemon=True).start())

    def _update_worker(self):
        try:
            found = update.check_for_update()
            if not found:
                return
            new_version, exe_url, sha_url = found
            self.q.put(("updating", new_version))
            update.install_update(exe_url, sha_url)
            self.q.put(("restart", None))
        except Exception:
            pass  # update problems must never block normal use

    def schedule_auto_refresh(self):
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None
        minutes = self.cfg.getint("auto_refresh_minutes")
        if minutes > 0:
            def tick():
                self.refresh(quiet=True)
                self._refresh_job = self.root.after(minutes * 60_000, tick)
            self._refresh_job = self.root.after(minutes * 60_000, tick)

    def set_status(self, text, error=False):
        self.status.configure(text=text, fg="#FF5050" if error else "#9AD0FF")

    def refresh(self, quiet=False):
        if self.busy:
            return
        self.busy = True
        self.quiet = quiet
        if self.demo:
            from demo import demo_records
            recs = demo_records()
            self.q.put(("order", [r["key"] for r in recs]))
            for r in recs:
                self.q.put(("record", r))
            self.q.put(("done", len(recs)))
            return
        cfg = self.cfg
        settings = {k: cfg.get(k) for k in ("imap_server", "imap_port", "imap_user", "folder", "days", "max_mails", "model")}
        settings["password"] = cfg.get_secret(f"imap:{cfg.get('imap_user')}")
        settings["api_key"] = cfg.get_secret("anthropic")
        settings["trusted"] = cfg.trusted()
        # entries from an older analysis version are re-analysed
        current = {k: v for k, v in self.cache.data.items() if v.get("v") == ANALYSIS_VERSION}
        known = set(current)
        for key, rec in current.items():  # show cached results immediately
            self.records.setdefault(key, rec)
        threading.Thread(target=self._worker, args=(settings, known), daemon=True).start()

    def _worker(self, s, known):
        q = self.q
        try:
            q.put(("status", "Verbinde mit dem Mailserver …"))
            ordered, new = fetch_mails(
                s["imap_server"], s["imap_port"], s["imap_user"], s["password"], s["folder"],
                s["days"], s["max_mails"], known,
                progress=lambda i, n: q.put(("status", f"Lade Nachricht {i + 1} von {n} …")))
            q.put(("order", ordered))
            if new:
                if not s["api_key"]:
                    q.put(("status", "Kein API-Key eingetragen – nur einfache Prüfung möglich."))
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = [pool.submit(analyse_mail, m, s["trusted"], s["api_key"], s["model"]) for m in new]
                    for i, fut in enumerate(as_completed(futures), start=1):
                        q.put(("record", fut.result()))
                        q.put(("status", f"Prüfe Nachrichten: {i} von {len(new)} …"))
            q.put(("done", len(ordered)))
        except imaplib.IMAP4.error:
            q.put(("error", "Anmeldung beim Mailserver fehlgeschlagen. Bitte Benutzer und Passwort prüfen."))
        except (socket.gaierror, socket.timeout, ConnectionError, OSError):
            q.put(("error", "Keine Verbindung zum Mailserver. Ist das Internet verbunden?"))
        except Exception as e:
            q.put(("error", f"Fehler beim Laden: {e}"))

    def _poll(self):
        changed = False
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "status":
                    self.set_status(payload)
                elif kind == "order":
                    self.order = payload
                    self.records = {k: v for k, v in self.records.items() if k in set(payload)}
                    changed = True
                elif kind == "record":
                    self.records[payload["key"]] = payload
                    changed = True
                elif kind == "done":
                    self._finish(payload)
                    changed = True
                elif kind == "updating":
                    self.set_status(f"Neue Version {payload} wird installiert …")
                    self.speaker.say("Das Programm wird aktualisiert und startet gleich neu.")
                elif kind == "restart":
                    self.root.after(2500, self.root.destroy)
                elif kind == "error":
                    self.busy = False
                    self.set_status(payload, error=True)
                    if not self.quiet:
                        self.speaker.say(payload)
        except queue.Empty:
            pass
        if changed:
            self.render(keep_selection=True)
        self.root.after(150, self._poll)

    def _finish(self, total):
        self.busy = False
        if not self.demo:
            # cache only successfully AI-checked results; prune to mails still in the inbox
            self.cache.data = {k: v for k, v in self.records.items() if k in set(self.order) and v.get("checked")}
            try:
                self.cache.save()
            except OSError:
                pass
        unchecked = sum(1 for k in self.order if k in self.records and not self.records[k].get("checked"))
        msg = f"Fertig. {total} Nachrichten. Stand: {datetime.now():%H:%M} Uhr."
        if unchecked and not self.demo:
            msg += f"  {unchecked} konnten nicht von der KI geprüft werden."
        self.set_status(msg, error=bool(unchecked and not self.demo))
        self._update_cat_buttons()
        if self.cfg.getint("auto_speak") and not self.quiet:
            c = self.counts
            self.speaker.say(f"{c['wichtig']} wichtige Nachrichten, {c['unwichtig']} nicht wichtige, "
                             f"und {c['verdacht']} verdächtige.")


def main():
    demo = "--demo" in sys.argv
    if sys.platform == "win32":
        try:  # sharp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root, demo=demo)
    root.mainloop()


if __name__ == "__main__":
    main()
