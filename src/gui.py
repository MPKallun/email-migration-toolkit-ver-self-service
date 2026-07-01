#!/usr/bin/env python3
"""
Data Backup — GUI (EAHI brand), two tabs
========================================
  • Backup   — pull a mailbox to a local folder: email (IMAP) + calendar/tasks
               (CalDAV) + contacts (CardDAV), and Drive from the export .zip.
  • Restore  — (SCAFFOLD) upload a local backup folder back into Gmail.

Engines run on a worker thread (no subprocess) so it works once packaged.

    pip install customtkinter
    python3 src/gui.py
"""
import os, sys, threading, queue, time
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from imap_backup import backup, fold_in_data_export, verify_backup, verify_and_repair
import caldav_backup
import gmail_upload
from utils import translate_error, log_debug

# ---- EAHI report palette ----
NAVY="#1B2B4B"; TEAL="#1A6B8A"; TEAL_HOVER="#155770"
LIGHT_BLUE="#E8F4F8"; LIGHT_GREY="#F2F5FA"; GREEN="#2D7A3A"; RED="#B22222"; AMBER="#8A5A00"
WHITE="#FFFFFF"; TEXT="#1B2B4B"; MUTED="#5F6B7A"; BORDER="#D5DCE6"

ctk.set_appearance_mode("light")

# ---- provider presets --------------------------------------------------
# (display name, host, default port) -- Mail server dropdown. "Custom"
# leaves the address field blank & editable; every other choice locks it to
# a known-good value pulled from each provider's current docs, so it can't
# be fat-fingered.
MAIL_PROVIDERS = [
    ("GoDaddy", "imap.secureserver.net", "993"),
    ("Titan",   "imap.titan.email",      "993"),
    ("Gmail",   "imap.gmail.com",        "993"),
    ("Custom",  "",                      ""),
]
# (display name, host, warning-or-"") -- Calendar/contacts (CalDAV/CardDAV)
# dropdown. am1.myprofessionalmail.com is what this app's Titan/GoDaddy
# accounts actually use today (Titan's own docs list dav.titan.email as the
# current standard host -- switch to Custom + that address if you're on a
# different Titan account and am1 doesn't resolve for you).
CALDAV_PROVIDERS = [
    ("GoDaddy", "caldav.secureserver.net",
     "GoDaddy's CalDAV is calendar-only (no CardDAV) and non-standard — most reliable from Apple clients."),
    ("Titan",   "am1.myprofessionalmail.com", ""),
    ("Gmail",   "apidata.googleusercontent.com",
     "⚠  Google requires OAuth for CalDAV — this app only does password auth, so this will fail today."),
    ("Custom",  "", ""),
]

def _provider_labels(providers):
    """Dropdown option text: 'Service  —  address' so the actual host is
    visible right in the list, not just the provider name."""
    return ["%s  —  %s" % (name, host) if host else name for name, host, _ in providers]


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("EAHI — Data Backup")
        self.geometry("770x740"); self.minsize(690, 450)
        self.configure(fg_color=LIGHT_GREY)
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.archive_zips = []
        self.last_backup_path = None
        self.insecure_var = tk.BooleanVar(value=True)
        self.transfer_start_time = None
        self.accumulated_bytes = 0
        self.current_op = "backup"
        self.google_tokens = None

        # provider-preset lookups: dropdown label -> (name, host, port/note)
        self._mail_labels = _provider_labels(MAIL_PROVIDERS)
        self._mail_map = dict(zip(self._mail_labels, MAIL_PROVIDERS))
        self._caldav_labels = _provider_labels(CALDAV_PROVIDERS)
        self._caldav_map = dict(zip(self._caldav_labels, CALDAV_PROVIDERS))

        self.f_title = ctk.CTkFont(family="Helvetica", size=19, weight="bold")
        self.f_sub   = ctk.CTkFont(family="Helvetica", size=12)
        self.f_label = ctk.CTkFont(family="Helvetica", size=12)
        self.f_btn   = ctk.CTkFont(family="Helvetica", size=14, weight="bold")
        self.f_mono  = ctk.CTkFont(family="Menlo", size=12)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)        # tabview expands
        self._build_header()

        self.tabs = ctk.CTkTabview(self, fg_color=WHITE, segmented_button_selected_color=TEAL,
                                   segmented_button_selected_hover_color=TEAL_HOVER,
                                   text_color=TEXT)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=14, pady=(12, 4))
        self.tabs.add("Backup")
        self.tabs.add("Restore")
        self._build_backup_tab(self.tabs.tab("Backup"))
        self._build_restore_tab(self.tabs.tab("Restore"))
        self._build_progress_and_console()
        self._bind_mouse_wheel_recursive(self.scroll_backup, self.scroll_backup)
        self._bind_mouse_wheel_recursive(self.scroll_restore, self.scroll_restore)
        self.after(100, self._drain)

    def _build_header(self):
        h = ctk.CTkFrame(self, fg_color=NAVY, corner_radius=0, height=64)
        h.grid(row=0, column=0, sticky="ew"); h.grid_propagate(False)
        h.grid_columnconfigure(0, weight=1)
        inner = ctk.CTkFrame(h, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="w", padx=20, pady=11)
        ctk.CTkLabel(inner, text="Data Backup", font=self.f_title, text_color=WHITE).pack(anchor="w")
        ctk.CTkLabel(inner, text="Back up your mailbox — or restore it to Gmail",
                     font=self.f_sub, text_color="#AFC2DA").pack(anchor="w")
        ctk.CTkLabel(h, text="EAHI", font=self.f_btn, text_color="#AFC2DA").grid(row=0, column=1, sticky="e", padx=20)

    # ---- provider dropdowns: fill + lock the address field to the preset,
    # unlock it only for "Custom" ----
    def _on_mail_provider(self, choice):
        name, host, port = self._mail_map[choice]
        self.host.configure(state="normal")
        self.host.delete(0, "end")
        if host:
            self.host.insert(0, host)
        if port:
            self.port.delete(0, "end"); self.port.insert(0, port)
        self.host.configure(state=("normal" if name == "Custom" else "disabled"))
        
        # Toggle visibility
        if name == "Custom":
            self.mail_server_frame.pack(fill="x", pady=(8, 0))
        else:
            self.mail_server_frame.pack_forget()
        self._adjust_window_height()

    def _on_caldav_provider(self, choice):
        name, host, note = self._caldav_map[choice]
        self.caldav_host.configure(state="normal")
        self.caldav_host.delete(0, "end")
        if host:
            self.caldav_host.insert(0, host)
        self.caldav_host.configure(state=("normal" if name == "Custom" else "disabled"))
        
        # Toggle Server Address field visibility
        if name == "Custom" and self.do_dav.get():
            self.caldav_server_frame.pack(fill="x", pady=(6, 0))
        else:
            self.caldav_server_frame.pack_forget()
            
        # Toggle warning message visibility
        if note and self.do_dav.get():
            self.caldav_warning.configure(text=note)
            self.caldav_warning.pack(anchor="w", pady=(4, 0))
        else:
            self.caldav_warning.pack_forget()
            
        self._adjust_window_height()

    def _entry(self, master, h=36, show=None):
        e = ctk.CTkEntry(master, height=h, corner_radius=8, fg_color=WHITE,
                         border_color=BORDER, border_width=1, text_color=TEXT)
        if show:
            e.configure(show=show)
        return e

    def _cap(self, master, text):
        return ctk.CTkLabel(master, text=text, font=self.f_label, text_color=MUTED)

    def _card_header(self, master, text):
        return ctk.CTkLabel(master, text=text.upper(), font=ctk.CTkFont(family="Helvetica", size=11, weight="bold"), text_color=TEAL)

    def _adjust_window_height(self):
        self.update_idletasks()
        req_height = self.winfo_reqheight()
        self.geometry(f"770x{max(740, req_height)}")

    def _bind_mouse_wheel_recursive(self, widget, scroll_frame):
        widget.bind("<MouseWheel>", lambda event: self._on_mouse_wheel(event, scroll_frame), "+")
        widget.bind("<Button-4>", lambda event: self._on_mouse_wheel(event, scroll_frame), "+")
        widget.bind("<Button-5>", lambda event: self._on_mouse_wheel(event, scroll_frame), "+")
        for child in widget.winfo_children():
            self._bind_mouse_wheel_recursive(child, scroll_frame)

    def _on_mouse_wheel(self, event, scroll_frame):
        try:
            if isinstance(event.widget, ctk.CTkTextbox):
                return
            if event.num == 4:  # Linux scroll up
                scroll_frame._parent_canvas.yview("scroll", -1, "units")
            elif event.num == 5:  # Linux scroll down
                scroll_frame._parent_canvas.yview("scroll", 1, "units")
            else:  # Windows / macOS
                if sys.platform == "darwin":
                    scroll_frame._parent_canvas.yview("scroll", -event.delta, "units")
                else:
                    scroll_frame._parent_canvas.yview("scroll", -int(event.delta / 120), "units")
        except Exception:
            pass

    def _update_advanced_visibility(self):
        if self.show_advanced_var.get():
            self.adv_card.pack(fill="x", padx=12, pady=4, after=self.adv_toggle_frame)
        else:
            self.adv_card.pack_forget()
        self._adjust_window_height()

    def _update_r_advanced_visibility(self):
        if self.r_show_advanced_var.get():
            self.r_adv_card.pack(fill="x", padx=12, pady=4, after=self.r_adv_toggle_frame)
        else:
            self.r_adv_card.pack_forget()
        self._adjust_window_height()

    def _update_caldav_visibility(self):
        if self.do_dav.get():
            self.caldav_frame.pack(fill="x", padx=12, pady=(0, 6))
            self._on_caldav_provider(self.caldav_provider.get())
        else:
            self.caldav_frame.pack_forget()
        self._adjust_window_height()

    # ============================ BACKUP TAB ============================
    def _build_backup_tab(self, tab):
        # Scrollable container for smaller screens
        self.scroll_backup = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.scroll_backup.pack(fill="both", expand=True)
        scroll = self.scroll_backup

        # 1. Connection Details Card
        conn_card = ctk.CTkFrame(scroll, fg_color=LIGHT_GREY, corner_radius=12, border_color=BORDER, border_width=1)
        conn_card.pack(fill="x", padx=12, pady=6)
        
        # Header
        self._card_header(conn_card, "1. Connection Settings").pack(anchor="w", padx=12, pady=(10, 4))
        
        # Email field
        ev = ctk.CTkFrame(conn_card, fg_color="transparent")
        ev.pack(fill="x", padx=12, pady=4)
        self._cap(ev, "Email address").pack(anchor="w", pady=(0, 2))
        self.user = self._entry(ev)
        self.user.pack(fill="x")
        
        # Password field
        pwv = ctk.CTkFrame(conn_card, fg_color="transparent")
        pwv.pack(fill="x", padx=12, pady=4)
        self._cap(pwv, "Password (or app password if 2-step verification is on)").pack(anchor="w", pady=(0, 2))
        prow = ctk.CTkFrame(pwv, fg_color="transparent")
        prow.pack(fill="x")
        prow.grid_columnconfigure(0, weight=1)
        self.pw = self._entry(prow, show="•")
        self.pw.grid(row=0, column=0, sticky="ew")
        self._sh_b = ctk.BooleanVar()
        ctk.CTkCheckBox(prow, text="Show", font=self.f_label, text_color=MUTED, fg_color=TEAL,
                        hover_color=TEAL_HOVER, width=20, variable=self._sh_b,
                        command=lambda: self.pw.configure(show="" if self._sh_b.get() else "•")
                        ).grid(row=0, column=1, padx=(12, 0))
                        
        # Mail Provider Select
        mpv = ctk.CTkFrame(conn_card, fg_color="transparent")
        mpv.pack(fill="x", padx=12, pady=(4, 10))
        self._cap(mpv, "Mail provider").pack(anchor="w", pady=(0, 2))
        self.mail_provider = ctk.CTkOptionMenu(
            mpv, values=self._mail_labels, command=self._on_mail_provider,
            fg_color=LIGHT_BLUE, text_color=NAVY, button_color=TEAL, button_hover_color=TEAL_HOVER,
            dropdown_fg_color=WHITE, dropdown_text_color=TEXT, font=self.f_label, height=32)
        self.mail_provider.pack(fill="x")

        # Mail Server Details Frame (hidden by default unless "Custom" is selected)
        self.mail_server_frame = ctk.CTkFrame(conn_card, fg_color="transparent")
        # Sub-grid inside mail_server_frame
        self.mail_server_frame.grid_columnconfigure(1, weight=1)
        
        self._cap(self.mail_server_frame, "Server address:").grid(row=0, column=0, sticky="w", pady=4)
        self.host = self._entry(self.mail_server_frame, h=30)
        self.host.grid(row=0, column=1, sticky="ew", padx=(8, 12), pady=4)
        
        self._cap(self.mail_server_frame, "Port:").grid(row=0, column=2, sticky="e", pady=4)
        self.port = self._entry(self.mail_server_frame, h=30)
        self.port.configure(width=66)
        self.port.grid(row=0, column=3, padx=(6, 12), pady=4)
        
        self.ssl = ctk.CTkSwitch(self.mail_server_frame, text="SSL", font=self.f_label, text_color=TEXT, progress_color=TEAL, width=46)
        self.ssl.select()
        self.ssl.grid(row=0, column=4, sticky="e", pady=4)
        
        self.mail_provider.set(self._mail_labels[0])               # default: GoDaddy
        self._on_mail_provider(self._mail_labels[0])

        # 2. Backup Destination & Data Card
        dest_card = ctk.CTkFrame(scroll, fg_color=LIGHT_GREY, corner_radius=12, border_color=BORDER, border_width=1)
        dest_card.pack(fill="x", padx=12, pady=6)
        
        # Header
        self._card_header(dest_card, "2. Destination & Content Options").pack(anchor="w", padx=12, pady=(10, 4))
        
        # Save backup to
        dv = ctk.CTkFrame(dest_card, fg_color="transparent")
        dv.pack(fill="x", padx=12, pady=4)
        self._cap(dv, "Save backup to").pack(anchor="w", pady=(0, 2))
        drow = ctk.CTkFrame(dv, fg_color="transparent")
        drow.pack(fill="x")
        drow.grid_columnconfigure(0, weight=1)
        self.dest = self._entry(drow)
        self.dest.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(drow, text="Browse", width=88, height=36, corner_radius=8, font=self.f_label,
                      fg_color=LIGHT_BLUE, text_color=NAVY, hover_color="#D7EAF2",
                      command=lambda: self._browse_into(self.dest)).grid(row=0, column=1, padx=(8, 0))
                      
        # Calendar & Contacts Switch
        self.do_dav = ctk.CTkSwitch(dest_card, text="Back up calendar, contacts & tasks (live)", font=self.f_label,
                                    text_color=TEXT, progress_color=TEAL, onvalue=True, offvalue=False,
                                    command=self._update_caldav_visibility)
        self.do_dav.select()
        self.do_dav.pack(anchor="w", padx=12, pady=6)
        
        # CalDAV config frame (conditionally visible)
        self.caldav_frame = ctk.CTkFrame(dest_card, fg_color="transparent")
        
        self._cap(self.caldav_frame, "Calendar/contacts provider:").pack(anchor="w", pady=(0, 2))
        self.caldav_provider = ctk.CTkOptionMenu(
            self.caldav_frame, values=self._caldav_labels, command=self._on_caldav_provider,
            fg_color=LIGHT_BLUE, text_color=NAVY, button_color=TEAL, button_hover_color=TEAL_HOVER,
            dropdown_fg_color=WHITE, dropdown_text_color=TEXT, font=self.f_label, height=32)
        self.caldav_provider.pack(fill="x")
        
        # CalDAV server frame (conditionally visible if CalDAV provider is Custom)
        self.caldav_server_frame = ctk.CTkFrame(self.caldav_frame, fg_color="transparent")
        self.caldav_server_frame.grid_columnconfigure(1, weight=1)
        self._cap(self.caldav_server_frame, "Server address:").grid(row=0, column=0, sticky="w", pady=4)
        self.caldav_host = self._entry(self.caldav_server_frame, h=30)
        self.caldav_host.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=4)
        
        self.caldav_warning = self._cap(self.caldav_frame, "")
        self.caldav_warning.configure(text_color=AMBER, wraplength=600, justify="left")
        # Do not pack here, handled dynamically in _on_caldav_provider
        
        self.caldav_provider.set(self._caldav_labels[1])          # default: Titan
        self._update_caldav_visibility()
        
        # Drive / zip archives section
        av = ctk.CTkFrame(dest_card, fg_color="transparent")
        av.pack(fill="x", padx=12, pady=(6, 10))
        self._cap(av, "Drive & local archives (optional)").pack(anchor="w", pady=(0, 2))
        arow = ctk.CTkFrame(av, fg_color="transparent")
        arow.pack(fill="x")
        ctk.CTkButton(arow, text="Add export .zip(s)…", width=150, height=32, corner_radius=8, font=self.f_label,
                      fg_color=LIGHT_BLUE, text_color=NAVY, hover_color="#D7EAF2", command=self._add_archive).pack(side="left")
        ctk.CTkButton(arow, text="Clear", width=66, height=32, corner_radius=8, font=self.f_label, fg_color=WHITE,
                      text_color=MUTED, border_color=BORDER, border_width=1, hover_color=LIGHT_GREY,
                      command=self._clear_archive).pack(side="left", padx=(8, 0))
        self.archive_label = self._cap(arow, "none added")
        self.archive_label.pack(side="left", padx=(12, 0))

        # 3. Advanced & Troubleshooting Options Header / Toggle
        self.adv_toggle_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self.adv_toggle_frame.pack(fill="x", padx=12, pady=4)
        
        self.show_advanced_var = tk.BooleanVar(value=False)
        self.show_advanced_switch = ctk.CTkSwitch(
            self.adv_toggle_frame, text="Show Advanced Options", font=self.f_label,
            text_color=MUTED, progress_color=TEAL, variable=self.show_advanced_var,
            command=self._update_advanced_visibility)
        self.show_advanced_switch.pack(anchor="w")
        
        # Advanced Options Card
        self.adv_card = ctk.CTkFrame(scroll, fg_color=LIGHT_GREY, corner_radius=12, border_color=BORDER, border_width=1)
        self.adv_card.grid_columnconfigure(0, weight=1)
        self.adv_card.grid_columnconfigure(1, weight=1)
        
        self.auto_verify = ctk.CTkSwitch(self.adv_card, text="Verify & auto-repair backup files", font=self.f_label,
                                         text_color=TEXT, progress_color=TEAL, onvalue=True, offvalue=False)
        self.auto_verify.select()
        self.auto_verify.grid(row=0, column=0, sticky="w", padx=12, pady=8)
        
        self.dry = ctk.CTkSwitch(self.adv_card, text="Dry run (test login & count only)", font=self.f_label,
                                 text_color=TEXT, progress_color=TEAL, onvalue=True, offvalue=False)
        self.dry.grid(row=0, column=1, sticky="w", padx=12, pady=8)
        
        self.mbox = ctk.CTkSwitch(self.adv_card, text="Create importable (.mbox) files", font=self.f_label,
                                  text_color=TEXT, progress_color=TEAL, onvalue=True, offvalue=False)
        self.mbox.grid(row=1, column=0, sticky="w", padx=12, pady=8)
        
        self.insecure = ctk.CTkCheckBox(self.adv_card, text="Skip SSL certificate verification",
                                        font=self.f_label, text_color=MUTED, fg_color=TEAL, hover_color=TEAL_HOVER,
                                        variable=self.insecure_var)
        self.insecure.grid(row=1, column=1, sticky="w", padx=12, pady=8)

        # Verify button inside Advanced Options Card
        self.verifybtn = ctk.CTkButton(self.adv_card, text="Verify a backup folder…", height=36, corner_radius=8,
                                       font=self.f_label, fg_color=WHITE, text_color=NAVY,
                                       border_color=BORDER, border_width=1, hover_color=LIGHT_GREY,
                                       command=self.run_verify)
        self.verifybtn.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 8))

        self.verify_cancelbtn = ctk.CTkButton(self.adv_card, text="Cancel", height=36, corner_radius=8, font=self.f_label,
                                              fg_color=WHITE, text_color=RED, border_color=RED, border_width=1,
                                              hover_color="#FBEAEA", command=self._cancel)

        # 4. Actions Row
        actions_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        actions_frame.pack(fill="x", padx=12, pady=(10, 6))
        actions_frame.grid_columnconfigure(0, weight=1)
        actions_frame.grid_columnconfigure(1, weight=1)
        
        self.runbtn = ctk.CTkButton(actions_frame, text="Back up my data", height=42, corner_radius=8, font=self.f_btn,
                                    fg_color=TEAL, hover_color=TEAL_HOVER, text_color=WHITE, command=self.run_backup)
        self.runbtn.grid(row=0, column=0, columnspan=2, sticky="ew", padx=0)
        
        self.cancelbtn = ctk.CTkButton(actions_frame, text="Cancel", height=42, corner_radius=8, font=self.f_btn,
                                       fg_color=WHITE, text_color=RED, border_color=RED, border_width=1,
                                       hover_color="#FBEAEA", command=self._cancel)

    # ============================ RESTORE TAB ============================
    def _build_restore_tab(self, tab):
        # Scrollable container for smaller screens
        self.scroll_restore = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.scroll_restore.pack(fill="both", expand=True)
        scroll = self.scroll_restore

        # Header description
        desc_label = ctk.CTkLabel(
            scroll, text="Upload a backup folder (made by the Backup tab) into a Gmail / Workspace account.",
            font=self.f_label, text_color=MUTED, wraplength=680, justify="left")
        desc_label.pack(anchor="w", padx=12, pady=(10, 6))

        # 1. Google Authentication Card
        auth_card = ctk.CTkFrame(scroll, fg_color=LIGHT_GREY, corner_radius=12, border_color=BORDER, border_width=1)
        auth_card.pack(fill="x", padx=12, pady=6)
        
        self._card_header(auth_card, "1. Google Authentication").pack(anchor="w", padx=12, pady=(10, 4))
        
        login_row = ctk.CTkFrame(auth_card, fg_color="transparent")
        login_row.pack(fill="x", padx=12, pady=4)
        
        self.google_login_btn = ctk.CTkButton(
            login_row, text="Sign in with Google", width=180, height=36, corner_radius=8, font=self.f_btn,
            fg_color=TEAL, hover_color=TEAL_HOVER, text_color=WHITE, command=self.run_google_login)
        self.google_login_btn.pack(side="left")
        
        self.google_status_lbl = ctk.CTkLabel(login_row, text="Not signed in.", font=self.f_label, text_color=MUTED)
        self.google_status_lbl.pack(side="left", padx=(15, 0))

        ev = ctk.CTkFrame(auth_card, fg_color="transparent")
        ev.pack(fill="x", padx=12, pady=(4, 10))
        self._cap(ev, "Verified Gmail / Workspace address").pack(anchor="w", pady=(0, 2))
        self.r_user = self._entry(ev)
        self.r_user.pack(fill="x")
        self.r_user.configure(placeholder_text="Will fill in automatically after Google Login", state="disabled")

        # 2. Restore Source Card
        src_card = ctk.CTkFrame(scroll, fg_color=LIGHT_GREY, corner_radius=12, border_color=BORDER, border_width=1)
        src_card.pack(fill="x", padx=12, pady=6)
        
        self._card_header(src_card, "2. Restore Source").pack(anchor="w", padx=12, pady=(10, 4))
        
        sv = ctk.CTkFrame(src_card, fg_color="transparent")
        sv.pack(fill="x", padx=12, pady=(4, 10))
        self._cap(sv, "Backup folder to upload").pack(anchor="w", pady=(0, 2))
        srow = ctk.CTkFrame(sv, fg_color="transparent")
        srow.pack(fill="x")
        srow.grid_columnconfigure(0, weight=1)
        self.r_src = self._entry(srow)
        self.r_src.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(srow, text="Browse", width=88, height=36, corner_radius=8, font=self.f_label, fg_color=LIGHT_BLUE,
                      text_color=NAVY, hover_color="#D7EAF2",
                      command=lambda: self._browse_into(self.r_src)).grid(row=0, column=1, padx=(8, 0))

        # 3. Advanced Settings
        self.r_adv_toggle_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self.r_adv_toggle_frame.pack(fill="x", padx=12, pady=4)
        
        self.r_show_advanced_var = tk.BooleanVar(value=False)
        self.r_show_advanced_switch = ctk.CTkSwitch(
            self.r_adv_toggle_frame, text="Show Advanced Settings", font=self.f_label,
            text_color=MUTED, progress_color=TEAL, variable=self.r_show_advanced_var,
            command=self._update_r_advanced_visibility)
        self.r_show_advanced_switch.pack(anchor="w")
        
        self.r_adv_card = ctk.CTkFrame(scroll, fg_color=LIGHT_GREY, corner_radius=12, border_color=BORDER, border_width=1)
        self.insecure_r = ctk.CTkCheckBox(
            self.r_adv_card, text="Skip SSL certificate verification", font=self.f_label,
            text_color=MUTED, fg_color=TEAL, hover_color=TEAL_HOVER, variable=self.insecure_var)
        self.insecure_r.pack(anchor="w", padx=12, pady=8)

        # 4. Actions Row
        actions_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        actions_frame.pack(fill="x", padx=12, pady=(10, 6))
        
        self.r_btn = ctk.CTkButton(actions_frame, text="Upload to Gmail", height=42, corner_radius=8, font=self.f_btn,
                                   fg_color=TEAL, hover_color=TEAL_HOVER, text_color=WHITE, command=self.run_restore)
        self.r_btn.pack(fill="x")

    # ---- shared progress + console ----
    def _build_progress_and_console(self):
        self.status = ctk.CTkLabel(self, text="Idle.", font=self.f_label, text_color=MUTED)
        self.status.grid(row=2, column=0, sticky="w", padx=22, pady=(2, 0))
        self.bar = ctk.CTkProgressBar(self, height=8, corner_radius=8, progress_color=TEAL, fg_color=BORDER)
        self.bar.set(0); self.bar.grid(row=3, column=0, sticky="ew", padx=14, pady=(4, 0))
        self.log = ctk.CTkTextbox(self, height=150, font=self.f_mono, corner_radius=10, fg_color=NAVY,
                                  text_color="#DDE6F0", border_width=0)
        self.log.grid(row=4, column=0, sticky="nsew", padx=14, pady=(10, 14))
        self.log.configure(state="disabled")

    # ---- helpers ----
    def _browse_into(self, entry):
        d = filedialog.askdirectory()
        if d:
            entry.delete(0, "end"); entry.insert(0, d)

    def _add_archive(self):
        for f in filedialog.askopenfilenames(title="Select your data-export .zip (all parts)",
                                             filetypes=[("Zip archives", "*.zip")]):
            if f and f not in self.archive_zips:
                self.archive_zips.append(f)
        n = len(self.archive_zips)
        self.archive_label.configure(text=("%d file(s) added" % n) if n else "none added")

    def _clear_archive(self):
        self.archive_zips = []; self.archive_label.configure(text="none added")

    def _write(self, t):
        self.log.configure(state="normal"); self.log.insert("end", t if t.endswith("\n") else t + "\n")
        self.log.see("end"); self.log.configure(state="disabled")

    def _busy(self, on, which="backup"):
        self.running = on
        if on:
            if which == "login":
                self.google_login_btn.configure(text="Authenticating…")
            elif which == "verify":
                self.verifybtn.configure(text="Verifying…")
                self.verifybtn.grid(row=2, column=0, columnspan=1, sticky="ew", padx=(12, 6), pady=(10, 8))
                self.verify_cancelbtn.configure(state="normal")
                self.verify_cancelbtn.grid(row=2, column=1, sticky="ew", padx=(6, 12), pady=(10, 8))
            elif which == "restore":
                self.r_btn.configure(text="Working…")
            else:
                self.runbtn.configure(text="Working…")
                self.runbtn.grid(row=0, column=0, columnspan=1, sticky="ew", padx=(0, 6))
                self.cancelbtn.configure(state="normal")
                self.cancelbtn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
            # lock every action button so two runs can't overlap
            for b in (self.runbtn, self.verifybtn, self.r_btn, self.google_login_btn):
                b.configure(state="disabled")
        else:
            self.runbtn.configure(state="normal", text="Back up my data")
            self.google_login_btn.configure(state="normal", text="Sign in with Google")
            self.r_btn.configure(state="normal", text="Upload to Gmail")
            self.verifybtn.configure(state="normal", text="Verify a backup folder…")
            self.cancelbtn.grid_forget()
            self.verify_cancelbtn.grid_forget()
            
            # restore actions grid layout
            self.runbtn.grid(row=0, column=0, columnspan=2, sticky="ew", padx=0)
            self.verifybtn.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 8))

    # ---- google login run ----
    def run_google_login(self):
        if self.running:
            return
        self._busy(True, "login")
        self.status.configure(text="Waiting for Google Login in browser...", text_color=MUTED)
        insecure = bool(self.insecure_var.get())
        threading.Thread(target=self._worker_oauth, args=(insecure,), daemon=True).start()

    def _worker_oauth(self, insecure):
        def log_fn(s): self.q.put(("log", s))
        try:
            res = gmail_upload.authenticate_google(log_fn=log_fn, insecure=insecure)
            if res:
                self.q.put(("oauth_done", res))
            else:
                self.q.put(("oauth_failed", None))
        except Exception as e:
            self.q.put(("log", f"\n[login error] {translate_error(e)}"))
            self.q.put(("oauth_failed", None))

    # ---- backup run ----
    def run_backup(self):
        if self.running:
            return
        user = self.user.get().strip(); pw = self.pw.get(); dest = self.dest.get().strip()
        port = self.port.get().strip(); dry = bool(self.dry.get())
        if "@" not in user: self._write("⚠  Enter your email address."); return
        if not pw: self._write("⚠  Enter your password."); return
        if not dest or not os.path.isdir(dest): self._write("⚠  Pick a valid folder to save into."); return
        if not port.isdigit(): self._write("⚠  Port must be a number (usually 993)."); return
        self.stop_event.clear(); self.bar.set(0); self._busy(True, "backup")
        self.current_op = "backup"
        self.status.configure(text="Connecting…", text_color=MUTED)
        
        # Reset transfer stats for progress/ETA tracking
        self.transfer_start_time = time.time()
        self.accumulated_bytes = 0
        
        args = dict(host=self.host.get().strip(), port=int(port), ssl=bool(self.ssl.get()), user=user,
                    password=pw, dest=dest, dry_run=dry, insecure=bool(self.insecure_var.get()),
                    make_mbox=bool(self.mbox.get()))
        dav = dict(host=self.caldav_host.get().strip() or "am1.myprofessionalmail.com",
                   insecure=bool(self.insecure_var.get())) if self.do_dav.get() else None
        threading.Thread(target=self._worker_backup,
                         args=(args, list(self.archive_zips), dav, bool(self.auto_verify.get())),
                         daemon=True).start()

    def _worker_backup(self, args, zips, dav, auto_verify):
        def log(s): self.q.put(("log", s))
        def prog(d, t, bytes_diff=0): self.q.put(("prog", (d, t, bytes_diff)))
        try:
            # backup -> verify backup -> if a file is corrupt/missing, redownload it
            res = backup(log=log, progress=prog, should_stop=self.stop_event.is_set, **args)
            if dav and not args["dry_run"] and not self.stop_event.is_set():
                log("\n— calendar, contacts & tasks (CalDAV / CardDAV) —")
                try:
                    caldav_backup.set_log(log)
                    d = caldav_backup.backup_dav(args["user"], args["password"], args["dest"],
                                                 caldav_host=dav["host"], do_contacts=True,
                                                 insecure=dav["insecure"], log_fn=log, progress=prog)
                    log("  -> events %d, tasks %d, contacts %d" % (d.get("events", 0), d.get("tasks", 0), d.get("contacts", 0)))
                except Exception as e:
                    log("  CalDAV/CardDAV error: %s" % translate_error(e))
            elif dav and args["dry_run"]:
                log("\n(Dry run: calendar/contacts/tasks pulled only on a real run.)")
            if zips and not self.stop_event.is_set() and res.get("dest"):
                log("\n— folding in your data export (Drive etc.) —")
                fold_in_data_export(zips, res["dest"], dry_run=args["dry_run"], log=log)
            if auto_verify and not args["dry_run"] and not self.stop_event.is_set() and res.get("dest"):
                try:
                    self.q.put(("phase", "verify"))
                    vres = verify_and_repair(log=log, progress=prog,
                                             should_stop=self.stop_event.is_set, **args)
                    res["verify"] = vres
                    res["ok"] = bool(res.get("ok")) and bool(vres.get("ok"))
                except Exception as e:
                    log("\n[verify error] %s" % translate_error(e))
            self.q.put(("done", res))
        except Exception as e:
            self.q.put(("log", "\n[error] %s" % translate_error(e))); self.q.put(("done", {"ok": False}))

    # ---- restore run ----
    def run_restore(self):
        if self.running:
            return
        user = self.r_user.get().strip(); src = self.r_src.get().strip()
        if "@" not in user: self._write("⚠  Enter the Gmail address."); return
        if not src or not os.path.isdir(src): self._write("⚠  Pick the backup folder to upload."); return
        if not self.google_tokens:
            self._write("⚠  Please sign in with Google first."); return
        self.stop_event.clear()
        self.bar.set(0); self._busy(True, "restore"); self.current_op = "restore"
        self.status.configure(text="Reading backup…", text_color=MUTED)
        
        # Reset transfer stats for progress/ETA tracking
        self.transfer_start_time = time.time()
        self.accumulated_bytes = 0
        
        threading.Thread(target=self._worker_restore, args=(user, self.google_tokens, src), daemon=True).start()

    def _worker_restore(self, user, tokens, src):
        def log(s): self.q.put(("log", s))
        def prog(d, t, bytes_diff=0): self.q.put(("prog", (d, t, bytes_diff)))
        try:
            log("\n— restore / upload to Gmail —")
            insecure = bool(self.insecure_var.get())
            res = gmail_upload.upload(user, tokens, src, log=log, progress=prog, should_stop=self.stop_event.is_set, insecure=insecure)
            self.q.put(("done_restore", res))
        except Exception as e:
            self.q.put(("log", "\n[error] %s" % translate_error(e))); self.q.put(("done_restore", {"ok": False}))

    def run_verify(self):
        if self.running:
            return
        start = self.last_backup_path or self.dest.get().strip() or os.path.expanduser("~")
        folder = filedialog.askdirectory(title="Pick the backup folder to verify", initialdir=start)
        if not folder:
            return
        self.stop_event.clear(); self.bar.set(0); self._busy(True, "verify"); self.current_op = "verify"
        self.status.configure(text="Verifying backup…", text_color=MUTED)
        self.transfer_start_time = time.time(); self.accumulated_bytes = 0
        threading.Thread(target=self._worker_verify, args=(folder,), daemon=True).start()

    def _worker_verify(self, folder):
        def log(s): self.q.put(("log", s))
        def prog(d, t, b=0): self.q.put(("prog", (d, t, b)))
        try:
            res = verify_backup(folder, log=log, progress=prog, should_stop=self.stop_event.is_set)
            self.q.put(("done_verify", res))
        except Exception as e:
            self.q.put(("log", "\n[error] %s" % translate_error(e)))
            self.q.put(("done_verify", {"ok": False}))

    def _cancel(self):
        self.stop_event.set(); self.status.configure(text="Stopping…", text_color=RED)

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._write(payload)
                elif kind == "prog":
                    d, t, bytes_diff = payload
                    self.accumulated_bytes += bytes_diff
                    self.bar.set(d / t if t else 0)
                    
                    elapsed = time.time() - self.transfer_start_time
                    speed_str = ""
                    eta_str = ""
                    
                    if elapsed > 0.2:
                        speed_mb = (self.accumulated_bytes / (1024 * 1024)) / elapsed
                        items_sec = d / elapsed
                        speed_str = f"Speed: {speed_mb:.2f} MB/s ({items_sec:.1f} items/s)"
                        
                        if items_sec > 0:
                            eta_secs = (t - d) / items_sec
                            eta_str = f"ETA: {int(eta_secs // 60)}m {int(eta_secs % 60)}s"
                        else:
                            eta_str = "ETA: Calibrating"
                            
                    # Determine progress verb from the current operation
                    op = getattr(self, "current_op", "backup")
                    if op == "restore":
                        verb = "Uploading"
                    elif op == "verify":
                        verb = "Verifying"
                    else:
                        verb = "Checking" if bool(self.dry.get()) else "Downloading"
                        
                    self.status.configure(
                        text=f"{verb} {d} / {t} items | {speed_str} | {eta_str}", 
                        text_color=MUTED
                    )
                elif kind == "phase":
                    self.current_op = payload
                elif kind == "oauth_done":
                    self._busy(False)
                    access_token, refresh_token, email = payload
                    self.google_tokens = (access_token, refresh_token)
                    self.google_status_lbl.configure(text=f"Signed in as {email}", text_color=GREEN)
                    self.r_user.configure(state="normal")
                    self.r_user.delete(0, "end")
                    self.r_user.insert(0, email)
                    self.r_user.configure(state="disabled")
                elif kind == "oauth_failed":
                    self._busy(False)
                    self.google_status_lbl.configure(text="Login failed. Please try again.", text_color=RED)
                elif kind == "done":
                    self._busy(False)
                    if payload.get("dest"):
                        self.last_backup_path = payload["dest"]
                        self.r_src.delete(0, "end")
                        self.r_src.insert(0, self.last_backup_path)
                    vres = payload.get("verify")
                    if payload.get("dry_run"):
                        self.status.configure(text="Dry run complete — email login OK.", text_color=GREEN)
                    elif payload.get("ok"):
                        self.bar.set(1)
                        if vres and vres.get("repaired"):
                            self.status.configure(
                                text="Done — verified, re-downloaded %d file(s) that failed the first check."
                                     % vres["repaired"], text_color=GREEN)
                        elif vres:
                            self.status.configure(text="Done — backup verified, all files intact.", text_color=GREEN)
                        else:
                            self.status.configure(text="Done — your backup is complete.", text_color=GREEN)
                    else:
                        if vres and not vres.get("ok"):
                            self.status.configure(
                                text="Backup finished but verification still found problems — check the log.",
                                text_color=RED)
                        else:
                            self.status.configure(text="Finished with problems — check the log.", text_color=RED)
                elif kind == "done_restore":
                    self._busy(False)
                    if payload.get("ok"):
                        self.bar.set(1)
                        self.status.configure(text="Done — restore completed successfully.", text_color=GREEN)
                    else:
                        self.status.configure(text="Restore finished with problems — check the log.", text_color=RED)
                elif kind == "done_verify":
                    self._busy(False)
                    if payload.get("stopped"):
                        self.status.configure(text="Verification stopped.", text_color=RED)
                    elif payload.get("ok"):
                        self.bar.set(1)
                        self.status.configure(text="Verified — all files intact.", text_color=GREEN)
                    else:
                        self.status.configure(text="Verification found problems — check the log.", text_color=RED)
        except queue.Empty:
            pass
        self.after(100, self._drain)


if __name__ == "__main__":
    App().mainloop()
