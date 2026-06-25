#!/usr/bin/env python3
"""
Data Backup — GUI (EAHI brand)
==============================
Staff self-service backup, styled in the EAHI report palette (Navy/Teal).
ONE login backs up, into a single per-user folder:
  • Email                     -> IMAP            (imap.secureserver.net)
  • Calendar / Tasks          -> CalDAV          (am1.myprofessionalmail.com)
  • Contacts                  -> CardDAV         (same host)
  • Drive (+ anything else)   -> folded in from the optional data-export .zip
The same email + password works for all of it; the server resolves each user's
own account automatically (RFC 6764), so there's nothing to look up per person.

Calls the engines ON A WORKER THREAD (no subprocess) so it works once packaged.

    pip install customtkinter
    python3 imap_backup_gui.py
"""
import os, sys, threading, queue
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from imap_backup import backup, fold_in_data_export
import caldav_backup

# ---- EAHI report palette ----------------------------------------------------
NAVY="#1B2B4B"; NAVY_HOVER="#11203B"; TEAL="#1A6B8A"; TEAL_HOVER="#155770"
LIGHT_BLUE="#E8F4F8"; LIGHT_GREY="#F2F5FA"; GREEN="#2D7A3A"; RED="#B22222"
WHITE="#FFFFFF"; TEXT="#1B2B4B"; MUTED="#5F6B7A"; BORDER="#D5DCE6"

ctk.set_appearance_mode("light")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("EAHI — Data Backup")
        self.geometry("760x880")
        self.minsize(680, 800)
        self.configure(fg_color=LIGHT_GREY)
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.archive_zips = []

        self.f_title = ctk.CTkFont(family="Helvetica", size=19, weight="bold")
        self.f_sub   = ctk.CTkFont(family="Helvetica", size=12)
        self.f_label = ctk.CTkFont(family="Helvetica", size=12)
        self.f_btn   = ctk.CTkFont(family="Helvetica", size=14, weight="bold")
        self.f_mono  = ctk.CTkFont(family="Menlo", size=12)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)     # console expands
        self._build_header()
        self._build_form()
        self._build_progress_and_console()
        self.after(100, self._drain)

    def _build_header(self):
        h = ctk.CTkFrame(self, fg_color=NAVY, corner_radius=0, height=66)
        h.grid(row=0, column=0, sticky="ew"); h.grid_propagate(False)
        h.grid_columnconfigure(0, weight=1)
        inner = ctk.CTkFrame(h, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="w", padx=20, pady=12)
        ctk.CTkLabel(inner, text="Data Backup", font=self.f_title, text_color=WHITE).pack(anchor="w")
        ctk.CTkLabel(inner, text="Email + calendar, contacts, tasks  (and Drive via export)",
                     font=self.f_sub, text_color="#AFC2DA").pack(anchor="w")
        ctk.CTkLabel(h, text="EAHI", font=self.f_btn, text_color="#AFC2DA"
                     ).grid(row=0, column=1, sticky="e", padx=20)

    def _entry(self, master, h=36):
        return ctk.CTkEntry(master, height=h, corner_radius=8, fg_color=WHITE,
                            border_color=BORDER, border_width=1, text_color=TEXT)

    def _cap(self, master, text):
        return ctk.CTkLabel(master, text=text, font=self.f_label, text_color=MUTED)

    def _build_form(self):
        card = ctk.CTkFrame(self, fg_color=WHITE, corner_radius=12,
                            border_color=BORDER, border_width=1)
        card.grid(row=1, column=0, sticky="ew", padx=18, pady=(16, 8))
        card.grid_columnconfigure(0, weight=1)
        pad = {"padx": 18}

        # email + password (shared by IMAP + CalDAV/CardDAV)
        ev = ctk.CTkFrame(card, fg_color="transparent")
        ev.grid(row=0, column=0, sticky="ew", pady=(16, 0), **pad)
        self._cap(ev, "Your email address").pack(anchor="w", pady=(0, 4))
        self.user = self._entry(ev); self.user.pack(fill="x")

        pwv = ctk.CTkFrame(card, fg_color="transparent")
        pwv.grid(row=1, column=0, sticky="ew", pady=(12, 0), **pad)
        self._cap(pwv, "Your password   (or an app password, if 2-step verification is on)").pack(anchor="w", pady=(0, 4))
        prow = ctk.CTkFrame(pwv, fg_color="transparent"); prow.pack(fill="x")
        prow.grid_columnconfigure(0, weight=1)
        self.pw = ctk.CTkEntry(prow, height=36, corner_radius=8, fg_color=WHITE, show="•",
                               border_color=BORDER, border_width=1, text_color=TEXT)
        self.pw.grid(row=0, column=0, sticky="ew")
        self.show_pw = ctk.CTkCheckBox(prow, text="Show", font=self.f_label, text_color=MUTED,
                                       fg_color=TEAL, hover_color=TEAL_HOVER, width=20,
                                       command=self._toggle_pw)
        self.show_pw.grid(row=0, column=1, padx=(12, 0))

        # destination
        dv = ctk.CTkFrame(card, fg_color="transparent")
        dv.grid(row=2, column=0, sticky="ew", pady=(12, 0), **pad)
        self._cap(dv, "Save backup to").pack(anchor="w", pady=(0, 4))
        drow = ctk.CTkFrame(dv, fg_color="transparent"); drow.pack(fill="x")
        drow.grid_columnconfigure(0, weight=1)
        self.dest = self._entry(drow); self.dest.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(drow, text="Browse", width=90, height=36, corner_radius=8, font=self.f_label,
                      fg_color=LIGHT_BLUE, text_color=NAVY, hover_color="#D7EAF2",
                      command=self._pick_dest).grid(row=0, column=1, padx=(8, 0))

        # calendar/contacts/tasks (CalDAV/CardDAV)
        df = ctk.CTkFrame(card, fg_color=LIGHT_GREY, corner_radius=10)
        df.grid(row=3, column=0, sticky="ew", pady=(16, 0), **pad)
        dfin = ctk.CTkFrame(df, fg_color="transparent"); dfin.pack(fill="x", padx=12, pady=12)
        self.do_dav = ctk.CTkSwitch(dfin, text="Back up calendar, contacts & tasks (live)",
                                    font=self.f_label, text_color=TEXT, progress_color=TEAL,
                                    onvalue=True, offvalue=False)
        self.do_dav.select()
        self.do_dav.pack(anchor="w")
        hrow = ctk.CTkFrame(dfin, fg_color="transparent"); hrow.pack(fill="x", pady=(8, 0))
        hrow.grid_columnconfigure(1, weight=1)
        self._cap(hrow, "Calendar/contacts server:").grid(row=0, column=0, sticky="w")
        self.caldav_host = ctk.CTkEntry(hrow, height=30, corner_radius=8, fg_color=WHITE,
                                        border_color=BORDER, border_width=1, text_color=TEXT)
        self.caldav_host.insert(0, "am1.myprofessionalmail.com")
        self.caldav_host.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        # Drive + anything else, from the data export .zip
        av = ctk.CTkFrame(card, fg_color=LIGHT_GREY, corner_radius=10)
        av.grid(row=4, column=0, sticky="ew", pady=(12, 0), **pad)
        avin = ctk.CTkFrame(av, fg_color="transparent"); avin.pack(fill="x", padx=12, pady=12)
        ctk.CTkLabel(avin, text="Drive & anything else  (optional)",
                     font=self.f_label, text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(avin, text="Drive isn't reachable live. To include it, in webmail: Settings → "
                     "Download your personal data → tick Drive → download the .zip, then add it here.",
                     font=self.f_label, text_color=MUTED, wraplength=600, justify="left").pack(anchor="w", pady=(2, 8))
        arow = ctk.CTkFrame(avin, fg_color="transparent"); arow.pack(fill="x")
        ctk.CTkButton(arow, text="Add export .zip(s)…", width=150, height=32, corner_radius=8,
                      font=self.f_label, fg_color=LIGHT_BLUE, text_color=NAVY, hover_color="#D7EAF2",
                      command=self._add_archive).pack(side="left")
        ctk.CTkButton(arow, text="Clear", width=70, height=32, corner_radius=8, font=self.f_label,
                      fg_color=WHITE, text_color=MUTED, border_color=BORDER, border_width=1,
                      hover_color=LIGHT_GREY, command=self._clear_archive).pack(side="left", padx=(8, 0))
        self.archive_label = self._cap(arow, "none added"); self.archive_label.pack(side="left", padx=(12, 0))

        # advanced + dry-run
        adv = ctk.CTkFrame(card, fg_color="transparent")
        adv.grid(row=5, column=0, sticky="ew", pady=(14, 0), **pad)
        self.insecure = ctk.CTkCheckBox(adv, text="Skip certificate check (only if you get a TLS/certificate error)",
                                        font=self.f_label, text_color=MUTED, fg_color=TEAL, hover_color=TEAL_HOVER)
        self.insecure.pack(anchor="w")
        self.dry = ctk.CTkSwitch(card, text="Dry run  (test email login & count — no download)",
                                 font=self.f_label, text_color=TEXT, progress_color=TEAL,
                                 onvalue=True, offvalue=False)
        self.dry.grid(row=6, column=0, sticky="w", pady=(12, 0), **pad)

        # advanced mail server (rarely changed)
        sv = ctk.CTkFrame(card, fg_color="transparent")
        sv.grid(row=7, column=0, sticky="ew", pady=(10, 0), **pad)
        sv.grid_columnconfigure(1, weight=1)
        self._cap(sv, "Mail server:").grid(row=0, column=0, sticky="w")
        self.host = ctk.CTkEntry(sv, height=30, corner_radius=8, fg_color=WHITE,
                                 border_color=BORDER, border_width=1, text_color=TEXT)
        self.host.insert(0, "imap.secureserver.net")
        self.host.grid(row=0, column=1, sticky="ew", padx=(8, 12))
        self._cap(sv, "Port:").grid(row=0, column=2, sticky="e")
        self.port = ctk.CTkEntry(sv, width=70, height=30, corner_radius=8, fg_color=WHITE,
                                 border_color=BORDER, border_width=1, text_color=TEXT)
        self.port.insert(0, "993"); self.port.grid(row=0, column=3, padx=(6, 12))
        self.ssl = ctk.CTkSwitch(sv, text="SSL", font=self.f_label, text_color=TEXT,
                                 progress_color=TEAL, onvalue=True, offvalue=False, width=46)
        self.ssl.select(); self.ssl.grid(row=0, column=4, sticky="e")

        # run / cancel
        brow = ctk.CTkFrame(card, fg_color="transparent")
        brow.grid(row=8, column=0, sticky="ew", pady=(16, 16), **pad)
        brow.grid_columnconfigure(0, weight=1)
        self.runbtn = ctk.CTkButton(brow, text="Back up my data", height=44, corner_radius=8,
                                    font=self.f_btn, fg_color=TEAL, hover_color=TEAL_HOVER,
                                    text_color=WHITE, command=self.run)
        self.runbtn.grid(row=0, column=0, sticky="ew")
        self.cancelbtn = ctk.CTkButton(brow, text="Cancel", width=110, height=44, corner_radius=8,
                                       font=self.f_btn, fg_color=WHITE, text_color=RED,
                                       border_color=RED, border_width=1, hover_color="#FBEAEA",
                                       command=self._cancel)

    def _build_progress_and_console(self):
        self.status = ctk.CTkLabel(self, text="Idle.", font=self.f_label, text_color=MUTED)
        self.status.grid(row=2, column=0, sticky="w", padx=24, pady=(2, 0))
        self.bar = ctk.CTkProgressBar(self, height=8, corner_radius=8, progress_color=TEAL, fg_color=BORDER)
        self.bar.set(0); self.bar.grid(row=3, column=0, sticky="ew", padx=18, pady=(4, 0))
        self.log = ctk.CTkTextbox(self, font=self.f_mono, corner_radius=10, fg_color=NAVY,
                                  text_color="#DDE6F0", border_width=0)
        self.log.grid(row=4, column=0, sticky="nsew", padx=18, pady=(10, 16))
        self.log.configure(state="disabled")

    # ---- helpers ----
    def _toggle_pw(self):
        self.pw.configure(show="" if self.show_pw.get() else "•")

    def _pick_dest(self):
        d = filedialog.askdirectory(title="Choose where to save the backup")
        if d:
            self.dest.delete(0, "end"); self.dest.insert(0, d)

    def _add_archive(self):
        for f in filedialog.askopenfilenames(title="Select your data-export .zip (add all split parts)",
                                             filetypes=[("Zip archives", "*.zip")]):
            if f and f not in self.archive_zips:
                self.archive_zips.append(f)
        self._update_archive_label()

    def _clear_archive(self):
        self.archive_zips = []; self._update_archive_label()

    def _update_archive_label(self):
        n = len(self.archive_zips)
        self.archive_label.configure(text=("%d file(s) added" % n) if n else "none added")

    def _write(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text if text.endswith("\n") else text + "\n")
        self.log.see("end"); self.log.configure(state="disabled")

    # ---- run ----
    def run(self):
        if self.running:
            return
        user = self.user.get().strip(); pw = self.pw.get()
        dest = self.dest.get().strip(); dry = bool(self.dry.get())
        host = self.host.get().strip(); port = self.port.get().strip()
        if "@" not in user:
            self._write("⚠  Enter your email address."); return
        if not pw:
            self._write("⚠  Enter your password."); return
        if not dest or not os.path.isdir(dest):
            self._write("⚠  Pick a valid folder to save into."); return
        if not port.isdigit():
            self._write("⚠  Port must be a number (usually 993)."); return

        self.running = True; self.stop_event.clear(); self.bar.set(0)
        self.runbtn.configure(state="disabled", text=("Checking…" if dry else "Backing up…"))
        self.cancelbtn.grid(row=0, column=1, padx=(10, 0))
        self.status.configure(text="Connecting…", text_color=MUTED)

        args = dict(host=host, port=int(port), ssl=bool(self.ssl.get()), user=user,
                    password=pw, dest=dest, dry_run=dry, insecure=bool(self.insecure.get()))
        dav = None
        if self.do_dav.get():
            dav = dict(host=self.caldav_host.get().strip() or "am1.myprofessionalmail.com",
                       insecure=bool(self.insecure.get()))
        threading.Thread(target=self._worker, args=(args, list(self.archive_zips), dav),
                         daemon=True).start()

    def _worker(self, args, zips, dav):
        def log(s):  self.q.put(("log", s))
        def prog(d, t): self.q.put(("prog", (d, t)))
        try:
            res = backup(log=log, progress=prog, should_stop=self.stop_event.is_set, **args)
            if dav and not args["dry_run"] and not self.stop_event.is_set():
                log("\n— calendar, contacts & tasks (CalDAV / CardDAV) —")
                try:
                    caldav_backup.set_log(log)
                    d = caldav_backup.backup_dav(args["user"], args["password"], args["dest"],
                                                 caldav_host=dav["host"], do_contacts=True,
                                                 insecure=dav["insecure"], log_fn=log)
                    log("  -> events %d, tasks %d, contacts %d"
                        % (d.get("events", 0), d.get("tasks", 0), d.get("contacts", 0)))
                except Exception as e:
                    log("  CalDAV/CardDAV error: %s" % e)
            elif dav and args["dry_run"]:
                log("\n(Dry run: calendar/contacts/tasks are pulled only on a real run.)")
            if zips and not self.stop_event.is_set() and res.get("dest"):
                log("\n— folding in your data export (Drive etc.) —")
                fold_in_data_export(zips, res["dest"], dry_run=args["dry_run"], log=log)
            self.q.put(("done", res))
        except Exception as e:
            self.q.put(("log", "\n[error] %s" % e)); self.q.put(("done", {"ok": False}))

    def _cancel(self):
        self.stop_event.set(); self.status.configure(text="Stopping…", text_color=RED)

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._write(payload)
                elif kind == "prog":
                    d, t = payload
                    self.bar.set(d / t if t else 0)
                    verb = "Checking" if bool(self.dry.get()) else "Downloading"
                    self.status.configure(text="%s %d / %d emails…" % (verb, d, t), text_color=MUTED)
                elif kind == "done":
                    self.running = False; self.cancelbtn.grid_forget()
                    self.runbtn.configure(state="normal", text="Back up my data")
                    if payload.get("dry_run"):
                        self.status.configure(text="Dry run complete — email login OK.", text_color=GREEN)
                    elif payload.get("ok"):
                        self.bar.set(1)
                        self.status.configure(text="Done — your backup is complete.", text_color=GREEN)
                    else:
                        self.status.configure(text="Finished with problems — check the log.", text_color=RED)
        except queue.Empty:
            pass
        self.after(100, self._drain)


if __name__ == "__main__":
    App().mainloop()
