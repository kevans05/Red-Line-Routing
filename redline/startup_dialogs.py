"""Startup flow: SoftwareSetupDialog, LandingDialog, ProjectWizard and helpers."""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import os
import re

from .utils import (_center_window, _hover_btn, _styled_header,
                    _bind_url_open, _bind_filter_combobox)
from .net import _parse_request_headers_raw, _ps_grab_windows_cookies, _domain_from_url

# Inter-dialog imports (these modules have no upward dependency on startup_dialogs)
from .job_dialogs import DrawingEditDialog
from .export_wizard import CrowDialog
from .registry_dialogs import MaintenanceStandardDialog, EngineeringStandardDialog
from .search_dialogs import (DrawingSearchDialog, _show_fetch_options_dialog,
                             _BrowserCookieDialog, _DRAWING_SEARCH_AVAILABLE,
                             _ENG_STD_AVAILABLE)

class SoftwareSetupDialog(tk.Toplevel):
    """First-time global setup: collect base URLs."""
    def __init__(self, parent, app_config):
        super().__init__(parent)
        self.title("Red-Line-Routing — First Time Setup")
        self.resizable(False, False)
        self.result = None
        self._cfg_vars = {}
        self._headers_txt      = None   # ScrolledText for request headers, set in _build
        self._eng_headers_txt  = None   # ScrolledText for engineering-specific headers
        self._build(dict(app_config))
        self.resizable(True, True)
        _center_window(self)          # auto-size to content
        self.grab_set()
        self.wait_window()

    def _build(self, cfg):
        _styled_header(self, "Welcome to Red-Line-Routing",
                       "First-time setup — configure your organisation's base URLs")

        body = tk.Frame(self, bg="white"); body.pack(fill="both", expand=True, padx=24, pady=14)
        tk.Label(body,
                 text="These settings are global and apply to all projects.\n"
                      "You can leave fields blank and update them later via File → Software Settings.",
                 bg="white", justify="left", fg="#566573", font=("", 9)).pack(anchor="w", pady=(0, 14))

        sections = [
            ("Drawings",       [("drawing_search_url",   "Drawing Search URL"),
                                 ("drawing_download_url", "Drawing Download URL"),
                                ]),
            ("Aspen",          [("aspen_url",           "Aspen URL (future)")]),
            ("CROWs",          [("base_crow_url",       "Base CROW URL")]),
            ("Relay Settings", [("base_relay_url",      "Base Relay URL")]),
            ("Maintenance Standards", [
                ("base_maintenance_telecom_url",      "Base URL (Telecom)"),
                ("base_maintenance_transmission_url", "Base URL (Transmission)"),
            ]),
            ("Engineering Standards", [
                ("engineering_url",     "Engineering Standards URL"),
                ("engineering_api_url", "Engineering API URL"),
            ]),
        ]
        for sec, fields in sections:
            # Section header row
            sh = tk.Frame(body, bg="white"); sh.pack(fill="x", pady=(6, 4))
            tk.Frame(sh, bg="#2980b9", width=3).pack(side="left", fill="y")
            tk.Label(sh, text=sec, bg="white", fg="#1c2833",
                     font=("", 9, "bold"), padx=8, pady=2).pack(side="left", anchor="w")
            # Fields
            for key, label in fields:
                row = tk.Frame(body, bg="white"); row.pack(fill="x", pady=2)
                tk.Label(row, text=label + ":", bg="white", fg="#5d6d7e",
                         font=("", 9), width=26, anchor="e").pack(side="left")
                var = tk.StringVar(value=cfg.get(key, ""))
                self._cfg_vars[key] = var
                tk.Entry(row, textvariable=var, bg="#f4f6f7", relief="flat",
                         bd=1, highlightthickness=1, highlightbackground="#d5d8dc",
                         highlightcolor="#2980b9", font=("", 9)).pack(
                    side="left", fill="x", expand=True, padx=(6, 0), ipady=4)

        # Authentication / Request Headers
        auth_hdr_row = tk.Frame(body, bg="white"); auth_hdr_row.pack(fill="x", pady=(6, 4))
        tk.Frame(auth_hdr_row, bg="#2980b9", width=3).pack(side="left", fill="y")
        tk.Label(auth_hdr_row, text="Authentication / Request Headers", bg="white", fg="#1c2833",
                 font=("", 9, "bold"), padx=8, pady=2).pack(side="left", anchor="w")
        auth_body = tk.Frame(body, bg="white"); auth_body.pack(fill="x", pady=(0, 4))
        tk.Label(auth_body,
                 text="Headers sent with every download request. One per line as  Header-Name: value\n"
                      "Open browser DevTools (F12) → Network tab → copy the Cookie: and Referer: lines.",
                 bg="white", fg="#7f8c8d", font=("", 8), justify="left").pack(anchor="w", padx=4)
        self._headers_txt = scrolledtext.ScrolledText(auth_body, height=3, font=("Courier", 9), wrap="none")
        self._headers_txt.pack(fill="x", padx=4, pady=(2, 0))
        self._headers_txt.insert("1.0", cfg.get("request_headers", ""))
        grab_row = tk.Frame(auth_body, bg="white"); grab_row.pack(anchor="w", padx=4, pady=(4, 0))
        tk.Button(
            grab_row, text="🔑 Grab via Windows Auth",
            command=lambda: self._grab_cookies_win_auth(self._headers_txt),
            bg="#6c3483", fg="white", relief="flat", font=("", 8),
            cursor="hand2", activebackground="#7d3c98", activeforeground="white",
            padx=8, pady=3).pack(side="left")
        tk.Label(grab_row,
                 text="Uses your Windows domain login — requires Drawing Search URL, Drawing Download URL, and W3C Domain.",
                 bg="white", fg="#7f8c8d", font=("", 8)).pack(side="left", padx=8)
        # Engineering Standards Headers (optional per-server override)
        eng_hdr_row = tk.Frame(body, bg="white"); eng_hdr_row.pack(fill="x", pady=(6, 4))
        tk.Frame(eng_hdr_row, bg="#2980b9", width=3).pack(side="left", fill="y")
        tk.Label(eng_hdr_row, text="Engineering Standards Headers (optional override)", bg="white", fg="#1c2833",
                 font=("", 9, "bold"), padx=8, pady=2).pack(side="left", anchor="w")
        eng_body = tk.Frame(body, bg="white"); eng_body.pack(fill="x", pady=(0, 4))
        tk.Label(eng_body,
                 text="Leave blank to use the master headers above. Fill in only if engineering\n"
                      "standards are served from a different server with different auth credentials.",
                 bg="white", fg="#7f8c8d", font=("", 8), justify="left").pack(anchor="w", padx=4)
        self._eng_headers_txt = scrolledtext.ScrolledText(eng_body, height=3, font=("Courier", 9), wrap="none")
        self._eng_headers_txt.pack(fill="x", padx=4, pady=(2, 0))
        self._eng_headers_txt.insert("1.0", cfg.get("engineering_request_headers", ""))
        # Drawing Search — Fetch Options button (uses master auth from above)
        ds_hdr_row = tk.Frame(body, bg="white"); ds_hdr_row.pack(fill="x", pady=(6, 4))
        tk.Frame(ds_hdr_row, bg="#2980b9", width=3).pack(side="left", fill="y")
        tk.Label(ds_hdr_row, text="Drawing Search", bg="white", fg="#1c2833",
                 font=("", 9, "bold"), padx=8, pady=2).pack(side="left", anchor="w")

        fetch_row = tk.Frame(body, bg="white"); fetch_row.pack(fill="x", pady=(2, 6))
        self._fetch_btn = tk.Button(
            fetch_row, text="🔄 Fetch Drawing Options",
            command=self._fetch_drawing_options,
            bg="#2980b9", fg="white", relief="flat", font=("", 8),
            cursor="hand2", activebackground="#3498db", activeforeground="white",
            padx=8, pady=3)
        self._fetch_btn.pack(side="left")
        tk.Label(fetch_row,
                 text="Pulls live facility / type / subject lists from the search server.",
                 bg="white", fg="#7f8c8d", font=("", 8)).pack(side="left", padx=8)

        sep = tk.Frame(self, bg="#d5d8dc", height=1); sep.pack(fill="x", side="bottom")
        bf = tk.Frame(self, bg="#eaecee"); bf.pack(fill="x", side="bottom")
        tk.Label(bf, text="You can skip this and fill in URLs later.",
                 bg="#eaecee", fg="#aab7b8", font=("", 8)).pack(side="left", padx=12, pady=8)
        ttk.Button(bf, text="Skip for Now",    command=self._skip).pack(side="right", padx=(6, 12), pady=8)
        ttk.Button(bf, text="Save & Continue", command=self._save).pack(side="right", pady=8)

        # Make body scrollable — wrap it in a Canvas with a vertical scrollbar.
        # The canvas and scrollbar must be packed AFTER the footer (side="bottom" was
        # already packed above) so that the footer stays fixed.
        vsb = tk.Scrollbar(self, orient="vertical")
        vsb.pack(side="right", fill="y")
        canvas = tk.Canvas(self, bg="white", highlightthickness=0,
                           yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.configure(command=canvas.yview)

        # Re-parent body onto the canvas
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_body_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfigure(body_id, width=event.width)

        body.bind("<Configure>", _on_body_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        self.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>")
                  if e.widget is self else None)

    def _fetch_drawing_options(self):
        url  = self._cfg_vars.get("drawing_search_url", tk.StringVar()).get().strip()
        raw  = self._headers_txt.get("1.0", "end") if self._headers_txt else ""
        headers = _parse_request_headers_raw(raw)
        _show_fetch_options_dialog(self, url, headers)

    def _grab_cookies_win_auth(self, headers_widget):
        url          = self._cfg_vars.get("drawing_search_url",   tk.StringVar()).get().strip()
        download_url = self._cfg_vars.get("drawing_download_url", tk.StringVar()).get().strip()
        if not (url and download_url):
            messagebox.showwarning("Incomplete Setup",
                "Fill in Drawing Search URL and Drawing Download URL first.",
                parent=self)
            return
        domain = _domain_from_url(url)
        _BrowserCookieDialog(self, domain, headers_widget,
                             cookies_fn=lambda _: _ps_grab_windows_cookies(url))

    def _skip(self):
        self.result = {}; self.destroy()

    def _save(self):
        self.result = {k: v.get().strip() for k, v in self._cfg_vars.items()}
        if self._headers_txt:
            self.result["request_headers"] = self._headers_txt.get("1.0", "end").strip()
        if self._eng_headers_txt:
            self.result["engineering_request_headers"] = self._eng_headers_txt.get("1.0", "end").strip()
        self.destroy()


class LandingDialog(tk.Toplevel):
    """Welcome screen: open existing or start new project."""
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Red-Line-Routing")
        self.resizable(False, False)
        self.result = None
        self.protocol("WM_DELETE_WINDOW", lambda: self._choose("new_quick"))
        self._build()
        self.resizable(True, True)
        _center_window(self)          # auto-size to content
        self.grab_set()
        self.wait_window()

    def _build(self):
        # ── Header ────────────────────────────────────────────────
        hdr = tk.Frame(self, bg="#1c2833"); hdr.pack(fill="x")
        tk.Label(hdr, text="Red-Line-Routing", bg="#1c2833", fg="white",
                 font=("", 19, "bold"), padx=28, pady=20, anchor="w").pack(fill="x")
        tk.Label(hdr, text="All-in-one electrical job planner",
                 bg="#1c2833", fg="#7fb3c8", font=("", 10), padx=28, pady=0,
                 anchor="w").pack(fill="x")
        tk.Frame(hdr, bg="#1c2833", height=16).pack()

        # ── Body ──────────────────────────────────────────────────
        body = tk.Frame(self, bg="#eaecee"); body.pack(fill="both", expand=True)
        tk.Label(body, text="What would you like to do?", bg="#eaecee",
                 font=("", 11), fg="#2c3e50").pack(pady=(22, 10))

        def _action_card(parent, icon, title, subtitle, bg, hover, cmd):
            wrapper = tk.Frame(parent, bg="#eaecee")
            wrapper.pack(fill="x", padx=36, pady=5)
            fr = tk.Frame(wrapper, bg=bg, cursor="hand2")
            fr.pack(fill="x")
            # Left accent strip
            accent = tk.Frame(fr, bg=hover, width=6); accent.pack(side="left", fill="y")
            content = tk.Frame(fr, bg=bg); content.pack(side="left", fill="both",
                                                         expand=True, padx=16, pady=14)
            tk.Label(content, text=f"{icon}  {title}", bg=bg, fg="white",
                     font=("", 12, "bold"), anchor="w").pack(fill="x")
            tk.Label(content, text=subtitle, bg=bg, fg="#d6eaf8",
                     font=("", 9), anchor="w").pack(fill="x", pady=(2, 0))
            _hover_btn(fr, bg, hover)
            for w in [fr, accent, content] + list(content.winfo_children()):
                w.bind("<Button-1>", lambda _, c=cmd: c())

        _action_card(body, "📂", "Open Existing Project",
                     "Browse for a .redline file",
                     "#1a5276", "#21618c", lambda: self._choose("open"))
        _action_card(body, "✦", "Create New Project",
                     "Quick start or step-through setup wizard",
                     "#1e6b3c", "#1e8449", self._new_choice)

        tk.Label(body, text="Red-Line-Routing  —  Electrical Job Planner",
                 bg="#eaecee", fg="#aab7b8", font=("", 8)).pack(pady=(16, 0))

    def _new_choice(self):
        dlg = NewProjectChoiceDialog(self)
        if dlg.result: self._choose(dlg.result)

    def _choose(self, result):
        self.result = result; self.destroy()


class NewProjectChoiceDialog(tk.Toplevel):
    """Quick start vs wizard choice."""
    def __init__(self, parent):
        super().__init__(parent)
        self.title("New Project")
        self.resizable(False, False)
        self.result = None
        self._build()
        self.resizable(True, True)
        _center_window(self)          # auto-size to content
        self.grab_set()
        self.wait_window()

    def _build(self):
        _styled_header(self, "Create New Project", "Choose how you'd like to begin")

        body = tk.Frame(self, bg="#eaecee"); body.pack(fill="both", expand=True)

        def _card(parent, icon, title, subtitle, bg, hover, val):
            wrapper = tk.Frame(parent, bg="#eaecee")
            wrapper.pack(fill="x", padx=24, pady=5)
            fr = tk.Frame(wrapper, bg=bg, cursor="hand2")
            fr.pack(fill="x")
            accent = tk.Frame(fr, bg=hover, width=5); accent.pack(side="left", fill="y")
            inner = tk.Frame(fr, bg=bg); inner.pack(side="left", fill="both",
                                                      expand=True, padx=14, pady=12)
            tk.Label(inner, text=f"{icon}  {title}", bg=bg, fg="white",
                     font=("", 11, "bold"), anchor="w").pack(fill="x")
            tk.Label(inner, text=subtitle, bg=bg, fg="#d6eaf8",
                     font=("", 9), anchor="w").pack(fill="x", pady=(2, 0))
            _hover_btn(fr, bg, hover)
            for w in [fr, accent, inner] + list(inner.winfo_children()):
                w.bind("<Button-1>", lambda _, v=val: self._choose(v))

        _card(body, "⚡", "Quick Start",
              "Open a blank planner and start adding jobs straight away",
              "#2c3e50", "#3d5166", "new_quick")
        _card(body, "🧭", "Setup Wizard",
              "Step-by-step: drawings, relays, CROWs, and save location",
              "#6c2f8a", "#7d3c98", "new_wizard")

    def _choose(self, val):
        self.result = val; self.destroy()


class WizardRelayDialog(tk.Toplevel):
    """Add/edit a relay/device record in the wizard (includes Aspen settings tab)."""
    def __init__(self, parent, existing=None, app_config=None):
        super().__init__(parent)
        self.title("Edit Relay / Device" if existing else "Add Relay / Device")
        self.geometry("500x460")
        self.resizable(False, False)
        self.result = None
        self._cfg = app_config or {}
        self._build(existing or {})
        self.grab_set()
        self.wait_window()

    def _build(self, ex):
        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, padx=8, pady=8)

        self.vars = {
            "device_id":   tk.StringVar(value=ex.get("device_id", "")),
            "title":       tk.StringVar(value=ex.get("title", "")),
            "revision":    tk.StringVar(value=ex.get("revision", "")),
            "engineer":    tk.StringVar(value=ex.get("engineer", "")),
            "contact":     tk.StringVar(value=ex.get("contact", "")),
            "url":         tk.StringVar(value=ex.get("url","") or self._cfg.get("base_relay_url","")),
            "aspen_model": tk.StringVar(value=ex.get("aspen_model", "")),
            "aspen_url":   tk.StringVar(value=ex.get("aspen_url","") or self._cfg.get("aspen_url","")),
            "aspen_notes": tk.StringVar(value=ex.get("aspen_notes", "")),
        }

        # Tab 1: Device info
        df = ttk.Frame(nb, padding=10); nb.add(df, text="Device Info"); df.columnconfigure(1, weight=1)
        for r, (key, label) in enumerate([
                ("device_id","Device ID: *"), ("title","Title:"), ("revision","Revision:"),
                ("engineer","Engineer:"), ("contact","Contact (email/phone):"), ("url","URL:")]):
            ttk.Label(df, text=label).grid(row=r, column=0, sticky="e", padx=(0,6), pady=4)
            ttk.Entry(df, textvariable=self.vars[key], width=40).grid(row=r, column=1, sticky="ew", pady=4)

        # Tab 2: Aspen
        af = ttk.Frame(nb, padding=10); nb.add(af, text="Aspen Settings"); af.columnconfigure(1, weight=1)
        ttk.Label(af, text="Aspen integration is a future feature. Fill in details now to be ready.",
                  foreground="grey", wraplength=380, justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        for r, (key, label) in enumerate([
                ("aspen_model","Aspen Model Name:"), ("aspen_url","Aspen URL:"), ("aspen_notes","Notes:")]):
            ttk.Label(af, text=label).grid(row=r+1, column=0, sticky="e", padx=(0,6), pady=4)
            ttk.Entry(af, textvariable=self.vars[key], width=40).grid(row=r+1, column=1, sticky="ew", pady=4)

        bf = ttk.Frame(self); bf.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(bf, text="* Required", foreground="grey", font=("",8)).pack(side="left")
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bf, text="Save",   command=self._save).pack(side="right")

    def _save(self):
        if not self.vars["device_id"].get().strip():
            messagebox.showwarning("Required", "Device ID is required.", parent=self); return
        self.result = {k: v.get().strip() for k, v in self.vars.items()}
        self.destroy()


class ProjectWizard(tk.Toplevel):
    """Multi-step new-project wizard."""

    _STEPS = ["Project Info", "Drawings", "Relays & Devices", "CROWs",
              "Maintenance Stds", "Engineering Stds", "Summary"]

    _SUGGESTIONS = [
        ("Project Team",     "Add team members (engineers, technicians, supervisors) with roles and contacts."),
        ("Outage Window",    "Record the planned outage date, start time, and expected duration."),
        ("Safety Checklist", "Pre-work safety items: PPE requirements, isolation verification, grounding."),
        ("Job Templates",    "Save common job sequences as reusable templates for standard terminal work."),
        ("Protection Review","List protection devices that need before/after verification at re-energisation."),
    ]

    # Step accent colours: (sidebar-active, sidebar-done, content-strip)
    _STEP_COLORS = [
        ("#154360", "#1a5276", "#2980b9"),   # Project Info        — blue
        ("#0b3d2e", "#1b6b46", "#27ae60"),   # Drawings            — green
        ("#4a1f6a", "#6c3483", "#8e44ad"),   # Relays              — purple
        ("#6e2706", "#943126", "#e74c3c"),   # CROWs               — red
        ("#1a3a2a", "#1e6645", "#1abc9c"),   # Maintenance Stds    — teal
        ("#2e1a00", "#7d4a00", "#e67e22"),   # Engineering Stds    — orange
        ("#17202a", "#273746", "#566573"),   # Summary             — slate
    ]

    def __init__(self, parent, app_config=None):
        super().__init__(parent)
        self.title("New Project Wizard")
        self.resizable(True, True)
        self.minsize(720, 540)
        self.result = None
        self.app_config = app_config or {}
        self._step = 0
        self.wiz_vars = {}
        self.wiz_notes_widget = None
        self.wiz_drawings   = {}
        self.wiz_relays     = {}
        self.wiz_crows      = []
        self.wiz_maint_stds = {}
        self.wiz_eng_stds   = {}
        self._build()
        # Maximize on open (platform-safe)
        try:
            self.attributes("-zoomed", True)   # Linux/X11
        except Exception:
            try:
                self.state("zoomed")           # Windows
            except Exception:
                _center_window(self, 900, 680)
        self.grab_set()
        self.wait_window()

    def _build(self):
        # ── Sidebar ───────────────────────────────────────────────
        self._sidebar = tk.Frame(self, bg="#1c2833", width=195)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        tk.Label(self._sidebar, text="New Project", bg="#1c2833", fg="white",
                 font=("", 12, "bold"), padx=18, pady=18, anchor="w").pack(fill="x")
        tk.Frame(self._sidebar, bg="#2e4053", height=1).pack(fill="x")

        self._step_widgets = []
        for i, name in enumerate(self._STEPS):
            row = tk.Frame(self._sidebar, bg="#1c2833"); row.pack(fill="x")
            # Number badge
            badge_bg, _, _ = self._STEP_COLORS[i]
            num_cv = tk.Canvas(row, width=30, height=30, bg="#1c2833",
                               highlightthickness=0)
            num_cv.pack(side="left", padx=(12, 0), pady=6)
            num_cv.create_oval(3, 3, 27, 27, fill="#2e4053", outline="")
            num_cv.create_text(15, 15, text=str(i+1), fill="#7f8c8d",
                               font=("", 9, "bold"), tags="txt")
            lbl = tk.Label(row, text=name, bg="#1c2833", fg="#7f8c8d",
                           font=("", 9), anchor="w", padx=8)
            lbl.pack(side="left", fill="x", expand=True, ipady=6)
            self._step_widgets.append((row, num_cv, lbl))

        # ── Right side ────────────────────────────────────────────
        right = tk.Frame(self, bg="#f5f6fa"); right.pack(side="right", fill="both", expand=True)

        # Footer nav — pack BEFORE content so pack(expand=True) doesn't swallow it.
        # tk's pack geometry manager allocates remaining space to widgets with
        # expand=True in the order they were packed.  By packing the footer first
        # (side="bottom") and the content second (expand=True), the footer always
        # gets its natural height and the content fills whatever is left above it.
        tk.Frame(right, bg="#d5d8dc", height=1).pack(fill="x", side="bottom")
        nav = tk.Frame(right, bg="#eaecee"); nav.pack(fill="x", side="bottom")
        ttk.Button(nav, text="Cancel", command=self.destroy).pack(side="left", padx=12, pady=10)
        self._finish_btn = tk.Button(nav, text="  Create Project ✓  ", bg="#27ae60", fg="white",
                                     font=("", 9, "bold"), relief="flat", cursor="hand2",
                                     activebackground="#2ecc71", activeforeground="white",
                                     command=self._finish)
        self._next_btn  = ttk.Button(nav, text="Next  →", command=self._next)
        self._back_btn  = ttk.Button(nav, text="←  Back", command=self._back)
        self._back_btn.pack(side="right", padx=(0, 12), pady=10)
        self._next_btn.pack(side="right", padx=4, pady=10)
        self._finish_btn.pack(side="right", padx=(0, 12), pady=8)

        # Accent strip + content — packed AFTER footer
        self._accent_strip = tk.Frame(right, height=4, bg="#2980b9")
        self._accent_strip.pack(fill="x", side="top")
        self._content = tk.Frame(right, bg="#f5f6fa")
        self._content.pack(fill="both", expand=True, side="top")

        self._frames = []
        for _ in range(len(self._STEPS)):
            outer = tk.Frame(self._content, bg="#f5f6fa")
            self._frames.append(outer)

        def _inner(outer):
            f = tk.Frame(outer, bg="#f5f6fa")
            f.pack(fill="both", expand=True, padx=20, pady=14)
            return f

        self._build_step_info(       _inner(self._frames[0]))
        self._build_step_drawings(   _inner(self._frames[1]))
        self._build_step_relays(     _inner(self._frames[2]))
        self._build_step_crows(      _inner(self._frames[3]))
        self._build_step_maint_stds( _inner(self._frames[4]))
        self._build_step_eng_stds(   _inner(self._frames[5]))
        self._build_step_summary(    _inner(self._frames[6]))

        self._show_step(0)

    def _show_step(self, idx):
        for f in self._frames: f.pack_forget()
        self._frames[idx].pack(fill="both", expand=True)
        self._step = idx
        is_last = idx == len(self._STEPS) - 1

        _, active_bg, strip_col = self._STEP_COLORS[idx]
        self._accent_strip.configure(bg=strip_col)

        for i, (row, num_cv, lbl) in enumerate(self._step_widgets):
            _, act, _ = self._STEP_COLORS[i]
            done_bg   = self._STEP_COLORS[i][1]
            if i == idx:
                row.configure(bg=act); lbl.configure(bg=act, fg="white")
                num_cv.configure(bg=act)
                num_cv.itemconfigure("txt", fill="white")
                num_cv.delete("oval"); num_cv.create_oval(3,3,27,27,fill=strip_col,outline="",tags="oval")
                num_cv.tag_raise("txt")
            elif i < idx:
                row.configure(bg="#1b4332"); lbl.configure(bg="#1b4332", fg="#a9dfbf")
                num_cv.configure(bg="#1b4332")
                num_cv.itemconfigure("txt", fill="white")
                num_cv.delete("oval"); num_cv.create_oval(3,3,27,27,fill="#27ae60",outline="",tags="oval")
                num_cv.tag_raise("txt")
                num_cv.delete("check"); num_cv.create_text(15,15,text="✓",fill="white",
                    font=("",9,"bold"),tags="check")
            else:
                row.configure(bg="#1c2833"); lbl.configure(bg="#1c2833", fg="#7f8c8d")
                num_cv.configure(bg="#1c2833")
                num_cv.itemconfigure("txt", fill="#7f8c8d")
                num_cv.delete("oval"); num_cv.create_oval(3,3,27,27,fill="#2e4053",outline="",tags="oval")
                num_cv.tag_raise("txt")
                num_cv.delete("check")

        self._back_btn.configure(state="normal" if idx > 0 else "disabled")
        if is_last:
            self._next_btn.pack_forget(); self._finish_btn.pack(side="right", padx=(0,6), pady=8)
            self._refresh_summary()
        else:
            self._finish_btn.pack_forget(); self._next_btn.pack(side="right", padx=4, pady=10)

    def _next(self):
        if self._validate(): self._show_step(self._step + 1)

    def _back(self):
        self._show_step(self._step - 1)

    def _validate(self):
        if self._step == 0:
            if not self.wiz_vars.get("project_name", tk.StringVar()).get().strip():
                messagebox.showwarning("Required", "Project Name is required.", parent=self); return False
            if not self.wiz_vars.get("save_location", tk.StringVar()).get().strip():
                messagebox.showwarning("Required", "Please choose a save location.", parent=self); return False
        return True

    # ── Step 1 ─────────────────────────────────────────────────────
    def _build_step_info(self, parent):
        tk.Label(parent, text="Project Information", bg="#f5f6fa", fg="#1c2833",
                 font=("", 13, "bold")).pack(anchor="w", pady=(0, 2))
        tk.Label(parent, text="Fill in the core project details. Fields marked * are required.",
                 bg="#f5f6fa", fg="#85929e").pack(anchor="w", pady=(0, 12))

        f = ttk.Frame(parent); f.pack(fill="x"); f.columnconfigure(1, weight=1)
        row = [0]
        def _field(key, label, hint=""):
            ttk.Label(f, text=label).grid(row=row[0], column=0, sticky="e", padx=(0,8), pady=3)
            var = tk.StringVar(); self.wiz_vars[key] = var
            ttk.Entry(f, textvariable=var, width=44).grid(row=row[0], column=1, sticky="ew", pady=3)
            row[0] += 1
            if hint:
                ttk.Label(f, text=hint, foreground="grey", font=("",8)).grid(
                    row=row[0], column=0, columnspan=2, sticky="w", pady=(0,2)); row[0] += 1

        _field("project_name", "Project Name: *")
        _field("site_name",    "Site Name:")
        _field("site_id",      "Site ID (XXXX):", "4-char site ID used in drawing name convention XXXX-YZZ-IIII-N")

        # Save location with Browse button
        ttk.Label(f, text="Save Location: *").grid(row=row[0], column=0, sticky="e", padx=(0,8), pady=3)
        lf = ttk.Frame(f); lf.grid(row=row[0], column=1, sticky="ew", pady=3); lf.columnconfigure(0, weight=1)
        self.wiz_vars["save_location"] = tk.StringVar()
        ttk.Entry(lf, textvariable=self.wiz_vars["save_location"]).grid(row=0, column=0, sticky="ew")
        ttk.Button(lf, text="Browse…", command=self._browse_location).grid(row=0, column=1, padx=(4,0))
        row[0] += 1

        ttk.Label(f, text="Notes:").grid(row=row[0], column=0, sticky="ne", padx=(0,8), pady=(6,0))
        self.wiz_notes_widget = scrolledtext.ScrolledText(f, height=5, wrap="word", font=("",9))
        self.wiz_notes_widget.grid(row=row[0], column=1, sticky="ew", pady=(6,0))

        tk.Label(parent, text="* Required", bg="#f5f6fa", fg="#aab7b8",
                 font=("", 8)).pack(anchor="w", pady=(6, 0))

    def _browse_location(self):
        d = filedialog.askdirectory(title="Choose save location", parent=self)
        if d: self.wiz_vars["save_location"].set(d)

    # ── Step 2 ─────────────────────────────────────────────────────
    def _build_step_drawings(self, parent):
        tk.Label(parent, text="Project Drawings", bg="#f5f6fa", fg="#1c2833",
                 font=("", 13, "bold")).pack(anchor="w", pady=(0, 2))
        tk.Label(parent,
                 text="Add drawings for this project (optional — you can add more later).\n"
                      "Naming convention: XXXX-YZZ-IIII-N  (Site—Type—Subject—Serial—Sheet)",
                 bg="#f5f6fa", fg="#85929e", justify="left").pack(anchor="w", pady=(0, 8))

        tb = ttk.Frame(parent); tb.pack(fill="x", pady=(0, 4))
        ttk.Button(tb, text="+ Add Drawing", command=self._wiz_add_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",          command=self._wiz_edit_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Delete",        command=self._wiz_del_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="\U0001f50d Search Drawings…", command=self._wiz_search_drawings).pack(side="left", padx=(10,2))

        fr = ttk.Frame(parent); fr.pack(fill="both", expand=True)
        cols = ("Drawing","Title","Rev","URL")
        self.wiz_drw_tree = ttk.Treeview(fr, columns=cols, show="headings")
        for col, w in [("Drawing",140),("Title",160),("Rev",55),("URL",220)]:
            self.wiz_drw_tree.heading(col, text=col)
            self.wiz_drw_tree.column(col, width=w, stretch=(col=="URL"))
        vsb = ttk.Scrollbar(fr, orient="vertical", command=self.wiz_drw_tree.yview)
        self.wiz_drw_tree.configure(yscrollcommand=vsb.set)
        self.wiz_drw_tree.pack(side="left", fill="both", expand=True); vsb.pack(side="right", fill="y")
        self.wiz_drw_tree.bind("<Double-1>", lambda _: self._wiz_edit_drawing())

    def _wiz_add_drawing(self):
        dlg = DrawingEditDialog(self, base_url=self.app_config.get("drawing_download_url",""),
                                app_config=self.app_config)
        if dlg.result:
            n = dlg.result["name"]
            self.wiz_drawings[n] = {k: dlg.result[k] for k in ("title","rev","url","notes")}
            self._wiz_refresh_drawings()

    def _wiz_edit_drawing(self):
        sel = self.wiz_drw_tree.selection()
        if not sel: return
        n = sel[0]; info = self.wiz_drawings.get(n, {})
        dlg = DrawingEditDialog(self, existing={"name":n,**info},
                                base_url=self.app_config.get("drawing_download_url",""),
                                app_config=self.app_config)
        if dlg.result:
            old = dlg.result.get("old_name"); new = dlg.result["name"]
            if old and old != new: self.wiz_drawings.pop(old, None)
            self.wiz_drawings[new] = {k: dlg.result[k] for k in ("title","rev","url","notes")}
            self._wiz_refresh_drawings()

    def _wiz_search_drawings(self):
        dlg = DrawingSearchDialog(self, self.app_config, multi_select=True)
        for r in dlg.selected:
            self.wiz_drawings[r.drawing_number] = {
                "title": r.title, "rev": r.revision,
                "url": r.document_url, "notes": "",
            }
        if dlg.selected:
            self._wiz_refresh_drawings()

    def _wiz_del_drawing(self):
        sel = self.wiz_drw_tree.selection()
        if not sel: return
        if messagebox.askyesno("Delete", f"Remove '{sel[0]}'?", parent=self):
            self.wiz_drawings.pop(sel[0], None); self._wiz_refresh_drawings()

    def _wiz_refresh_drawings(self):
        for iid in self.wiz_drw_tree.get_children(): self.wiz_drw_tree.delete(iid)
        for n, i in sorted(self.wiz_drawings.items()):
            self.wiz_drw_tree.insert("","end",iid=n,
                values=(n,i.get("title",""),i.get("rev",""),i.get("url","")))

    # ── Step 3 ─────────────────────────────────────────────────────
    def _build_step_relays(self, parent):
        tk.Label(parent, text="Relays & Devices", bg="#f5f6fa", fg="#1c2833",
                 font=("", 13, "bold")).pack(anchor="w", pady=(0, 2))
        tk.Label(parent,
                 text="Add relay protection devices (optional — you can add more in Relay Settings later).\n"
                      "All relays are devices. Aspen settings can be attached to each relay record.",
                 bg="#f5f6fa", fg="#85929e", justify="left").pack(anchor="w", pady=(0, 8))

        tb = ttk.Frame(parent); tb.pack(fill="x", pady=(0, 4))
        ttk.Button(tb, text="+ Add",  command=self._wiz_add_relay).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",   command=self._wiz_edit_relay).pack(side="left", padx=2)
        ttk.Button(tb, text="Delete", command=self._wiz_del_relay).pack(side="left", padx=2)

        fr = ttk.Frame(parent); fr.pack(fill="both", expand=True)
        cols = ("Device ID","Title","Rev","Engineer","Aspen")
        self.wiz_rly_tree = ttk.Treeview(fr, columns=cols, show="headings")
        for col, w in [("Device ID",100),("Title",155),("Rev",55),("Engineer",120),("Aspen",70)]:
            self.wiz_rly_tree.heading(col, text=col)
            self.wiz_rly_tree.column(col, width=w, stretch=(col=="Title"))
        vsb = ttk.Scrollbar(fr, orient="vertical", command=self.wiz_rly_tree.yview)
        self.wiz_rly_tree.configure(yscrollcommand=vsb.set)
        self.wiz_rly_tree.pack(side="left", fill="both", expand=True); vsb.pack(side="right", fill="y")
        self.wiz_rly_tree.bind("<Double-1>", lambda _: self._wiz_edit_relay())

    def _wiz_add_relay(self):
        dlg = WizardRelayDialog(self, app_config=self.app_config)
        if dlg.result:
            dev = dlg.result["device_id"]
            self.wiz_relays[dev] = {k: v for k, v in dlg.result.items() if k != "device_id"}
            self._wiz_refresh_relays()

    def _wiz_edit_relay(self):
        sel = self.wiz_rly_tree.selection()
        if not sel: return
        dev = sel[0]; info = self.wiz_relays.get(dev, {})
        dlg = WizardRelayDialog(self, existing={"device_id":dev,**info}, app_config=self.app_config)
        if dlg.result:
            old = dev; new = dlg.result["device_id"]
            if old != new: self.wiz_relays.pop(old, None)
            self.wiz_relays[new] = {k: v for k, v in dlg.result.items() if k != "device_id"}
            self._wiz_refresh_relays()

    def _wiz_del_relay(self):
        sel = self.wiz_rly_tree.selection()
        if not sel: return
        if messagebox.askyesno("Delete", f"Remove '{sel[0]}'?", parent=self):
            self.wiz_relays.pop(sel[0], None); self._wiz_refresh_relays()

    def _wiz_refresh_relays(self):
        for iid in self.wiz_rly_tree.get_children(): self.wiz_rly_tree.delete(iid)
        for dev, info in sorted(self.wiz_relays.items()):
            aspen = "✓" if info.get("aspen_model") or info.get("aspen_url") else "—"
            self.wiz_rly_tree.insert("","end",iid=dev,
                values=(dev,info.get("title",""),info.get("revision",""),info.get("engineer",""),aspen))

    # ── Step 4 ─────────────────────────────────────────────────────
    def _build_step_crows(self, parent):
        tk.Label(parent, text="CROW / Outage Records", bg="#f5f6fa", fg="#1c2833",
                 font=("", 13, "bold")).pack(anchor="w", pady=(0, 2))
        tk.Label(parent,
                 text="Attach any CROW outage records to this project (optional).\n"
                      "You can add and edit CROWs at any time in the CROW tab.",
                 bg="#f5f6fa", fg="#85929e", justify="left").pack(anchor="w", pady=(0, 8))

        tb = ttk.Frame(parent); tb.pack(fill="x", pady=(0, 4))
        ttk.Button(tb, text="+ Add CROW", command=self._wiz_add_crow).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",       command=self._wiz_edit_crow).pack(side="left", padx=2)
        ttk.Button(tb, text="Remove",     command=self._wiz_del_crow).pack(side="left", padx=2)

        fr = ttk.Frame(parent); fr.pack(fill="both", expand=True)
        cols = ("Outage Number","URL")
        self.wiz_crow_tree = ttk.Treeview(fr, columns=cols, show="headings")
        self.wiz_crow_tree.heading("Outage Number", text="Outage Number")
        self.wiz_crow_tree.heading("URL", text="URL")
        self.wiz_crow_tree.column("Outage Number", width=160, stretch=False)
        self.wiz_crow_tree.column("URL", width=380)
        vsb = ttk.Scrollbar(fr, orient="vertical", command=self.wiz_crow_tree.yview)
        self.wiz_crow_tree.configure(yscrollcommand=vsb.set)
        self.wiz_crow_tree.pack(side="left", fill="both", expand=True); vsb.pack(side="right", fill="y")
        self.wiz_crow_tree.bind("<Double-1>", lambda _: self._wiz_edit_crow())

    def _wiz_add_crow(self):
        dlg = CrowDialog(self, base_url=self.app_config.get("base_crow_url",""))
        if dlg.result: self.wiz_crows.append(dlg.result); self._wiz_refresh_crows()

    def _wiz_edit_crow(self):
        sel = self.wiz_crow_tree.selection()
        if not sel: return
        idx = self.wiz_crow_tree.index(sel[0])
        dlg = CrowDialog(self, existing=self.wiz_crows[idx])
        if dlg.result: self.wiz_crows[idx] = dlg.result; self._wiz_refresh_crows()

    def _wiz_del_crow(self):
        sel = self.wiz_crow_tree.selection()
        if not sel: return
        self.wiz_crows.pop(self.wiz_crow_tree.index(sel[0])); self._wiz_refresh_crows()

    def _wiz_refresh_crows(self):
        for iid in self.wiz_crow_tree.get_children(): self.wiz_crow_tree.delete(iid)
        for c in self.wiz_crows:
            self.wiz_crow_tree.insert("","end",values=(c.get("outage_number",""),c.get("url","")))

    # ── Step 5 ─────────────────────────────────────────────────────
    def _build_step_maint_stds(self, parent):
        tk.Label(parent, text="Maintenance Standards", bg="#f5f6fa", fg="#1c2833",
                 font=("", 13, "bold")).pack(anchor="w", pady=(0, 2))
        tk.Label(parent,
                 text="Add applicable maintenance standards for this project (optional).\n"
                      "You can manage these at any time in the Maintenance Standards tab.",
                 bg="#f5f6fa", fg="#85929e", justify="left").pack(anchor="w", pady=(0, 8))

        tb = ttk.Frame(parent); tb.pack(fill="x", pady=(0, 4))
        ttk.Button(tb, text="+ Add",  command=self._wiz_add_maint).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",   command=self._wiz_edit_maint).pack(side="left", padx=2)
        ttk.Button(tb, text="Delete", command=self._wiz_del_maint).pack(side="left", padx=2)

        fr = ttk.Frame(parent); fr.pack(fill="both", expand=True)
        cols = ("Standard ID", "Title", "Rev", "URL (Telecom)", "URL (Transmission)", "Notes")
        self.wiz_maint_tree = ttk.Treeview(fr, columns=cols, show="headings")
        for col, w, stretch in [
            ("Standard ID", 110, False), ("Title", 160, True),
            ("Rev", 55, False), ("URL (Telecom)", 160, False),
            ("URL (Transmission)", 160, False), ("Notes", 140, False),
        ]:
            self.wiz_maint_tree.heading(col, text=col)
            self.wiz_maint_tree.column(col, width=w, stretch=stretch)
        vsb = ttk.Scrollbar(fr, orient="vertical", command=self.wiz_maint_tree.yview)
        self.wiz_maint_tree.configure(yscrollcommand=vsb.set)
        self.wiz_maint_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.wiz_maint_tree.bind("<Double-1>", lambda _: self._wiz_edit_maint())

    def _wiz_add_maint(self):
        dlg = MaintenanceStandardDialog(
            self,
            base_url_telecom=self.app_config.get("base_maintenance_telecom_url", ""),
            base_url_transmission=self.app_config.get("base_maintenance_transmission_url", ""),
        )
        if dlg.result:
            sid = dlg.result["standard_id"]
            self.wiz_maint_stds[sid] = {k: v for k, v in dlg.result.items() if k != "standard_id"}
            self._wiz_refresh_maint()

    def _wiz_edit_maint(self):
        sel = self.wiz_maint_tree.selection()
        if not sel: return
        sid = sel[0]; info = self.wiz_maint_stds.get(sid, {})
        dlg = MaintenanceStandardDialog(
            self,
            existing={"standard_id": sid, **info},
            base_url_telecom=self.app_config.get("base_maintenance_telecom_url", ""),
            base_url_transmission=self.app_config.get("base_maintenance_transmission_url", ""),
        )
        if dlg.result:
            old = sid; new = dlg.result["standard_id"]
            if old != new: self.wiz_maint_stds.pop(old, None)
            self.wiz_maint_stds[new] = {k: v for k, v in dlg.result.items() if k != "standard_id"}
            self._wiz_refresh_maint()

    def _wiz_del_maint(self):
        sel = self.wiz_maint_tree.selection()
        if not sel: return
        if messagebox.askyesno("Delete", f"Remove '{sel[0]}'?", parent=self):
            self.wiz_maint_stds.pop(sel[0], None); self._wiz_refresh_maint()

    def _wiz_refresh_maint(self):
        for iid in self.wiz_maint_tree.get_children(): self.wiz_maint_tree.delete(iid)
        for sid, info in sorted(self.wiz_maint_stds.items()):
            self.wiz_maint_tree.insert("", "end", iid=sid, values=(
                sid, info.get("title", ""), info.get("revision", ""),
                info.get("url_telecom", ""), info.get("url_transmission", ""),
                info.get("notes", ""),
            ))

    # ── Step 6 ─────────────────────────────────────────────────────
    def _build_step_eng_stds(self, parent):
        tk.Label(parent, text="Engineering Standards", bg="#f5f6fa", fg="#1c2833",
                 font=("", 13, "bold")).pack(anchor="w", pady=(0, 2))
        tk.Label(parent,
                 text="Add applicable engineering standards for this project (optional).\n"
                      "You can manage these at any time in the Engineering Standards tab.",
                 bg="#f5f6fa", fg="#85929e", justify="left").pack(anchor="w", pady=(0, 8))

        tb = ttk.Frame(parent); tb.pack(fill="x", pady=(0, 4))
        ttk.Button(tb, text="+ Add",  command=self._wiz_add_eng).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",   command=self._wiz_edit_eng).pack(side="left", padx=2)
        ttk.Button(tb, text="Delete", command=self._wiz_del_eng).pack(side="left", padx=2)

        fr = ttk.Frame(parent); fr.pack(fill="both", expand=True)
        cols = ("Standard ID", "Title", "Rev", "Type", "URL", "Notes")
        self.wiz_eng_tree = ttk.Treeview(fr, columns=cols, show="headings")
        for col, w, stretch in [
            ("Standard ID", 110, False), ("Title", 160, True),
            ("Rev", 55, False), ("Type", 100, False),
            ("URL", 200, False), ("Notes", 140, False),
        ]:
            self.wiz_eng_tree.heading(col, text=col)
            self.wiz_eng_tree.column(col, width=w, stretch=stretch)
        vsb = ttk.Scrollbar(fr, orient="vertical", command=self.wiz_eng_tree.yview)
        self.wiz_eng_tree.configure(yscrollcommand=vsb.set)
        self.wiz_eng_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.wiz_eng_tree.bind("<Double-1>", lambda _: self._wiz_edit_eng())

    def _wiz_add_eng(self):
        dlg = EngineeringStandardDialog(self)
        if dlg.result:
            sid = dlg.result["standard_id"]
            self.wiz_eng_stds[sid] = {
                "title":         dlg.result.get("title", ""),
                "revision":      dlg.result.get("revision", ""),
                "standard_type": dlg.result.get("standard_type", ""),
                "url":           dlg.result.get("url", ""),
                "notes":         dlg.result.get("notes", ""),
            }
            self._wiz_refresh_eng()

    def _wiz_edit_eng(self):
        sel = self.wiz_eng_tree.selection()
        if not sel: return
        sid = sel[0]; info = self.wiz_eng_stds.get(sid, {})
        dlg = EngineeringStandardDialog(self, existing={"standard_id": sid, **info})
        if dlg.result:
            old = sid; new = dlg.result["standard_id"]
            if old != new: self.wiz_eng_stds.pop(old, None)
            self.wiz_eng_stds[new] = {
                "title":         dlg.result.get("title", ""),
                "revision":      dlg.result.get("revision", ""),
                "standard_type": dlg.result.get("standard_type", ""),
                "url":           dlg.result.get("url", ""),
                "notes":         dlg.result.get("notes", ""),
            }
            self._wiz_refresh_eng()

    def _wiz_del_eng(self):
        sel = self.wiz_eng_tree.selection()
        if not sel: return
        if messagebox.askyesno("Delete", f"Remove '{sel[0]}'?", parent=self):
            self.wiz_eng_stds.pop(sel[0], None); self._wiz_refresh_eng()

    def _wiz_refresh_eng(self):
        for iid in self.wiz_eng_tree.get_children(): self.wiz_eng_tree.delete(iid)
        for sid, info in sorted(self.wiz_eng_stds.items()):
            self.wiz_eng_tree.insert("", "end", iid=sid, values=(
                sid, info.get("title", ""), info.get("revision", ""),
                info.get("standard_type", ""), info.get("url", ""),
                info.get("notes", ""),
            ))

    # ── Step 7 ─────────────────────────────────────────────────────
    def _build_step_summary(self, parent):
        tk.Label(parent, text="Summary", bg="#f5f6fa", fg="#1c2833",
                 font=("", 13, "bold")).pack(anchor="w", pady=(0, 2))
        self._summary_text = scrolledtext.ScrolledText(
            parent, height=8, font=("Courier", 9), state="disabled", wrap="word")
        self._summary_text.pack(fill="x", pady=(0, 10))

        sf = ttk.LabelFrame(parent, text="Suggested future additions", padding=8)
        sf.pack(fill="both", expand=True)
        for title, desc in self._SUGGESTIONS:
            rf = ttk.Frame(sf); rf.pack(fill="x", pady=2)
            ttk.Label(rf, text=f"● {title}", font=("", 9, "bold")).pack(anchor="w")
            ttk.Label(rf, text=f"   {desc}", foreground="grey", font=("", 8),
                      wraplength=500, justify="left").pack(anchor="w")

    def _refresh_summary(self):
        proj = self.wiz_vars.get("project_name",  tk.StringVar()).get().strip() or "(unnamed)"
        site = self.wiz_vars.get("site_name",      tk.StringVar()).get().strip() or "—"
        sid  = self.wiz_vars.get("site_id",        tk.StringVar()).get().strip() or "—"
        loc  = self.wiz_vars.get("save_location",  tk.StringVar()).get().strip() or "—"
        lines = [
            f"Project:              {proj}",
            f"Site:                 {site}  (ID: {sid})",
            f"Save folder:          {loc}/{proj}",
            f"",
            f"Drawings:             {len(self.wiz_drawings)} added",
            f"Relay records:        {len(self.wiz_relays)} added",
            f"CROWs:                {len(self.wiz_crows)} added",
            f"Maintenance stds:     {len(self.wiz_maint_stds)} added",
            f"Engineering stds:     {len(self.wiz_eng_stds)} added",
            f"",
            f"Click 'Create Project' to build the folder structure and save.",
        ]
        self._summary_text.configure(state="normal")
        self._summary_text.delete("1.0","end")
        self._summary_text.insert("1.0","\n".join(lines))
        self._summary_text.configure(state="disabled")

    def _finish(self):
        if not self._validate() and self._step != len(self._STEPS) - 1:
            self._show_step(0); return
        proj = self.wiz_vars.get("project_name", tk.StringVar()).get().strip()
        loc  = self.wiz_vars.get("save_location",tk.StringVar()).get().strip()
        if not proj or not loc:
            messagebox.showwarning("Required",
                "Project Name and Save Location are required.", parent=self)
            self._show_step(0); return
        self.result = {
            "project_name":       proj,
            "site_name":          self.wiz_vars.get("site_name",    tk.StringVar()).get().strip(),
            "site_id":            self.wiz_vars.get("site_id",      tk.StringVar()).get().strip(),
            "save_location":      loc,
            "notes":              self.wiz_notes_widget.get("1.0","end").strip() if self.wiz_notes_widget else "",
            "drawings":           dict(self.wiz_drawings),
            "relays":             dict(self.wiz_relays),
            "crows":              list(self.wiz_crows),
            "maint_stds":         dict(self.wiz_maint_stds),
            "eng_stds":           dict(self.wiz_eng_stds),
        }
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Main application
# ──────────────────────────────────────────────────────────────────

