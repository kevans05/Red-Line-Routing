"""RedLineApp — the main application window."""
# stdlib
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext, simpledialog
import tkinter.font as tkfont
import json
import os
import subprocess
import sys
import re
import webbrowser
from copy import deepcopy
from datetime import datetime
import threading
import urllib.request
import urllib.error
import urllib.parse
import queue
import glob
import shutil
import sqlite3
import tempfile
import zipfile
import ctypes
import ctypes.wintypes
import base64

# Ensure project root (parent of redline/) is on sys.path so that
# sibling packages (drawing_search/, pypdf/) are always importable.
_proj_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _proj_dir not in sys.path:
    sys.path.insert(0, _proj_dir)

try:
    from drawing_search import (DrawingSearchClient, SearchParams,
                                DrawingResult, PagedResults, DrawingSearchCache,
                                DRAWING_TYPES, DRAWING_SUBJECTS, FACILITIES,
                                load_cached_options, save_cached_options,
                                fetch_form_options)
    _DRAWING_SEARCH_AVAILABLE = True
    import inspect as _insp
    _DSC_HAS_COOKIE_CB = "on_cookie_update" in _insp.signature(
        DrawingSearchClient.__init__).parameters
    _DSC_HAS_DOWNLOAD_URL = "download_url" in _insp.signature(
        DrawingSearchClient.__init__).parameters
    del _insp
except ImportError:
    _DRAWING_SEARCH_AVAILABLE = False
    _DSC_HAS_COOKIE_CB = False
    _DSC_HAS_DOWNLOAD_URL = False

try:
    from engineering_standards import (EngineeringStandardsClient,
                                       EngineeringStandardsCache,
                                       EngineeringSeries, EngineeringStandard)
    _ENG_STD_AVAILABLE = True
except ImportError:
    _ENG_STD_AVAILABLE = False

try:
    from pts_parser import (parse_pts as _parse_pts,
                            row_key as _pts_row_key,
                            write_completions as _pts_write_completions)
    _PTS_PARSER_AVAILABLE = True
except ImportError:
    _PTS_PARSER_AVAILABLE = False
    def _pts_row_key(entry): return ""
    def _pts_write_completions(*a, **kw): return False, "pts_parser not available"

from .models import (
    _empty_tailboard_refs, empty_endpoint, empty_protection,
    empty_job, _get_prot_drawings, JOB_TYPE_SHORT,
)
from .formatting import (
    W, _bar, _ep_block, _prot_block, _std_list,
    format_job, _ROW_STYLE, _ROW_BORDER, _esc,
)
from .utils import (
    is_h_type_drawing, _treeview_strike_font, _drawing_subdir,
    _archive_existing, _archive_revision, _snapshot_file, _file_url_to_path,
    _center_window, _hover_btn, _styled_header,
    _StickyRedirectHandler, _ComboFilterHelper,
    _bind_search_combobox, _bind_filter_combobox, _bind_url_open,
    _open_file, _reveal_file, _iter_project_files,
)
from .db import _AppDB, _GlobalDrawingCache
from .net import (
    _parse_request_headers_raw, _parse_cookies_from_headers,
    _update_cookie_in_headers, _fmt_phone, _domain_from_url,
    _dpapi_decrypt, _decrypt_cookie_aes_gcm,
    _grab_browser_cookies, _ps_grab_windows_cookies,
)
from .html_export import (
    _EW_PAGE_SIZES, _EW_PAGE_DIMS,
    _PDF_EXTS, _DOC_EXTS, _EMBED_EXTS,
    _ew_url_cell, _ew_full_html, _ew_with_id, _ew_cover,
    _ew_work_orders, _ew_drawings_reg, _ew_relay_reg,
    _ew_standards, _ew_qr_sheet, _ew_embedded_files,
    _TABLET_SECTION_DIRS, _TABLET_PKG_FORMAT, _TABLET_PKG_VERSION,
    _build_tablet_zip,
)
from .pdf_export import (
    _PYPDF_AVAILABLE, _PYPDF_ERROR,
    _HELV_W, _ptw, _ptrunc, _pwrap, _penc,
    _PDFPage, _SimplePDFBuilder,
    _ep_flat, _prot_flat,
    _pdf_cover, _pdf_section_table, _pdf_work_orders,
    _pdf_drawings_reg, _pdf_relay_reg, _pdf_standards,
    _merge_pdfs_bytes, _CONVERTIBLE_EXTS,
    _txt_to_pdf_bytes, _docx_to_pdf_bytes, _convert_file_to_pdf,
    _collect_pdfs, _build_print_pdf,
)
from .job_dialogs import (
    DrawingAwareFrame, EndpointFrame, DrawingEntryDialog,
    IsoPointDialog, MultiDrawingFrame, MultiIsoFrame,
    ProtectionFrame, JobDialog, DrawingEditDialog,
)
from .export_wizard import ExportWizard, CrowDialog
from .registry_dialogs import (
    RelaySettingDialog, MaintenanceStandardDialog,
    EngineeringStandardDialog, StandardsLibraryDialog,
)
from .pts_dialogs import _PTSImportDialog, _PTSCompletionDialog
from .search_dialogs import (
    DrawingSearchDialog,
    _show_search_error_dialog, _show_fetch_options_dialog,
    CtrlRoomDeskEditDialog, CtrlRoomDesksManagerDialog,
    _BrowserCookieDialog, _EngineeringBrowseDialog,
)
from .startup_dialogs import (
    SoftwareSetupDialog, LandingDialog, NewProjectChoiceDialog,
    WizardRelayDialog, ProjectWizard,
)

class RedLineApp(tk.Tk):
    TYPE_FG = {"REMOVE":"#c0392b","ADD":"#1a7a3c","MOVE":"#1a5a99",
               "BLOCK":"#d35400","UNBLOCK":"#16a085","TESTING":"#6c3483",
               "ISOLATION":"#1a6b8a","CR_PROT":"#1a5276",
               "DEVICE ADD":"#117a65","DEVICE REMOVE":"#784212"}

    def __init__(self):
        super().__init__()
        self.title("Red-Line-Routing")
        self.minsize(900, 600)
        # Start maximised; fall back to a safe fixed size on headless/CI
        try:
            self.update_idletasks()
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            w = max(1000, int(sw * 0.90))
            h = max(650,  int(sh * 0.90))
            x = (sw - w) // 2
            y = max(0, (sh - h) // 2 - 20)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            self.geometry("1080x720")
        self.jobs = []
        self.drawing_registry = {}
        self.current_file = None
        self.project_folder = None
        # Flat value history for autocomplete dropdowns
        self.history = {"device": [], "location": [], "pin": [], "panel": [], "wire": []}
        # Full endpoint dicts for context-aware suggestions
        self.ep_history = []
        self.title_page = {"notes": "", "crows": []}
        self.relay_registry = {}          # keyed by device_id
        self.maintenance_standards_registry = {}  # keyed by standard_id
        self.engineering_standards_registry = {}  # keyed by standard_id
        self.pts_files = {}               # {filename: {uploaded_at, standard_ids, completions}}
        self._pts_entries_cache = {}      # row_key → entry dict, populated by _refresh_pts_results
        self.tailboard_refs = _empty_tailboard_refs()
        self._dirty = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._app_db = _AppDB()           # ~/.redlinerouting.db — settings, standards library, drawing cache
        self._drawing_cache = _GlobalDrawingCache(self._app_db)
        self.app_config = self._load_app_config()
        self._build_menu()
        self._build_ui()
        self.after_idle(self._startup_flow)
        self._schedule_drawing_cache_refresh()
        self._schedule_engineering_cache_refresh()

    # ── Startup flow ─────────────────────────────────────────────

    def _startup_flow(self):
        """Run once after the UI is ready: first-time setup → landing dialog.

        Sequence:
          1. If this is the first run (setup_complete absent), show SoftwareSetupDialog
             to collect global base-URLs and write them to ~/.redlinerouting.json.
          2. Always show LandingDialog so the user can open an existing project,
             start a quick blank plan, or run the full wizard.
          3. Dispatch based on the user's choice (open / wizard / quick-start).
        """
        if not self.app_config.get("setup_complete"):
            dlg = SoftwareSetupDialog(self, self.app_config)
            if dlg.result is not None:
                self.app_config.update(dlg.result)
                self.app_config["setup_complete"] = True
                self._save_app_config()

        dlg = LandingDialog(self)
        choice = dlg.result or "new_quick"

        if choice == "open":
            self._open()
        elif choice == "new_wizard":
            wiz = ProjectWizard(self, app_config=self.app_config)
            if wiz.result:
                self._apply_wizard_result(wiz.result)
        # "new_quick" → blank plan, nothing to do

    def _apply_wizard_result(self, result):
        """Apply wizard output: populate app state, create folder structure, save."""
        self.project_var.set(result["project_name"])
        self.drawing_registry               = result.get("drawings",   {})
        self.relay_registry                 = result.get("relays",     {})
        self.maintenance_standards_registry = result.get("maint_stds", {})
        self.engineering_standards_registry = result.get("eng_stds",   {})
        self.title_page = {
            "notes": result.get("notes", ""),
            "crows": result.get("crows", []),
        }
        self.tailboard_refs = _empty_tailboard_refs()

        proj = result["project_name"]
        safe = "".join(c if c not in r'<>:"/\|?*' else "_" for c in proj) if proj else "RedLine_Plan"
        folder = os.path.join(result["save_location"], safe)
        try:
            os.makedirs(folder, exist_ok=True)
            for sub in ("Drawings", "Relay Settings", "Maintenance Standards", "Engineering Standards",
                        "CROW Outage", "Other",
                        os.path.join("Tailboards", "Completed"),
                        os.path.join("Safety Documents", "Completed"),
                        "Other Documents"):
                os.makedirs(os.path.join(folder, sub), exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Error", f"Could not create project folder:\n{exc}"); return

        self.project_folder = folder
        self._schedule_tailboard_check()
        path = os.path.join(folder, safe + ".redline")
        self.current_file = path

        self.title_notes.delete("1.0", "end")
        self.title_notes.insert("1.0", result.get("notes", ""))
        self._refresh_list()
        self._refresh_drawings_list()
        self._refresh_relay_list()
        self._refresh_crows()
        self._refresh_maintenance_list()
        self._refresh_engineering_list()
        self._write(path)
        messagebox.showinfo("Project Created",
            f"'{proj}' created at:\n{folder}\n\nYou're ready to start adding jobs.")

    # ──────────────────────────────────────────────────────────────────
    # History management
    # ──────────────────────────────────────────────────────────────────

    def _add_to_history(self, key, value):
        # Most-recently-used order: remove the value if it already exists, then
        # re-insert at position 0 so the dropdown always leads with recent entries.
        value = value.strip()
        if not value:
            return
        lst = self.history.setdefault(key, [])
        if value in lst:
            lst.remove(value)
        lst.insert(0, value)
        # cap at 60 entries per key
        self.history[key] = lst[:60]

    def _collect_history(self, job):
        """Extract device/location/pin/panel/wire values and full endpoint dicts into history."""
        for ep_key in ("start", "end", "add_start", "add_end"):
            ep = job.get(ep_key) or {}
            if any(ep.get(k) for k in ("device","location","pin","panel","drawing")):
                self.ep_history.insert(0, dict(ep))
                self.ep_history = self.ep_history[:400]
            for k in ("device", "location", "pin", "panel"):
                self._add_to_history(k, ep.get(k, ""))
        for wire_key in ("wire", "add_wire"):
            self._add_to_history("wire", job.get(wire_key, ""))

    def _rebuild_history(self):
        """Rebuild flat history and ep_history from all loaded jobs."""
        self.ep_history = []
        for job in self.jobs:
            self._collect_history(job)

    # ── Device registry ───────────────────────────────────────────
    # Devices live in self.history["device"] so they persist with the project
    # and automatically populate the Device field autocomplete in every job dialog.

    def _refresh_device_list(self):
        """Repopulate the Devices listbox from history."""
        if not hasattr(self, "device_lb"):
            return
        self.device_lb.delete(0, "end")
        for name in sorted(self.history.get("device", []), key=str.lower):
            self.device_lb.insert("end", name)

    def _add_device(self):
        name = simpledialog.askstring("Add Device", "Device name:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        existing = self.history.setdefault("device", [])
        if name not in existing:
            existing.insert(0, name)
            self._refresh_device_list()

    def _edit_device(self):
        sel = self.device_lb.curselection()
        if not sel:
            return
        old = self.device_lb.get(sel[0])
        new = simpledialog.askstring("Edit Device", "Device name:", initialvalue=old, parent=self)
        if not new or not new.strip() or new.strip() == old:
            return
        new = new.strip()
        devices = self.history.setdefault("device", [])
        if old in devices:
            idx = devices.index(old)
            devices[idx] = new
        self._refresh_device_list()

    def _remove_device(self):
        sel = self.device_lb.curselection()
        if not sel:
            return
        name = self.device_lb.get(sel[0])
        # Warn if jobs still reference this device so the user doesn't lose autocomplete accidentally
        in_use = sum(
            1 for job in self.jobs
            for ep_key in ("start", "end", "add_start", "add_end")
            if isinstance(job.get(ep_key), dict) and job[ep_key].get("device") == name
        )
        if in_use:
            if not messagebox.askyesno(
                    "Device in use",
                    f"'{name}' is referenced in {in_use} job(s).\n"
                    "Remove from the device list anyway?", parent=self):
                return
        devices = self.history.setdefault("device", [])
        if name in devices:
            devices.remove(name)
        self._refresh_device_list()

    # ── Menu ──────────────────────────────────────────────────────

    def _build_menu(self):
        mb = tk.Menu(self)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label="New",                    command=self._new_plan,     accelerator="Ctrl+N")
        fm.add_command(label="Open…",                  command=self._open,         accelerator="Ctrl+O")
        fm.add_command(label="Save",                   command=self._save,         accelerator="Ctrl+S")
        fm.add_command(label="Save As…",               command=self._save_as)
        fm.add_separator()
        fm.add_command(label="Export Wizard…",         command=self._open_export_wizard, accelerator="Ctrl+E")
        fm.add_separator()
        fm.add_command(label="Software Settings…",       command=self._open_software_settings)
        fm.add_command(label="Control Room Desks…",      command=self._open_ctrl_room_desks)
        fm.add_separator()
        fm.add_command(label="Quit",command=self.quit, accelerator="Ctrl+Q")
        mb.add_cascade(label="File",menu=fm)
        self.config(menu=mb)
        self.bind("<Control-n>", lambda _: self._new_plan())
        self.bind("<Control-o>", lambda _: self._open())
        self.bind("<Control-s>", lambda _: self._save())
        self.bind("<Control-e>", lambda _: self._open_export_wizard())
        self.bind("<Control-q>", lambda _: self.quit())

    # ── Unsaved-changes tracking ──────────────────────────────────

    def _mark_dirty(self):
        """Mark the project as having unsaved changes."""
        if not self.project_folder:
            return  # no project open yet — nothing to dirty
        if not self._dirty:
            self._dirty = True
            t = self.title()
            if not t.startswith("● "):
                self.title("● " + t)

    def _mark_clean(self):
        """Clear the unsaved-changes flag."""
        self._dirty = False
        t = self.title()
        if t.startswith("● "):
            self.title(t[2:])

    def _check_unsaved(self):
        """If dirty, ask whether to save. Returns True to proceed, False to abort."""
        if not self._dirty:
            return True
        ans = messagebox.askyesnocancel(
            "Unsaved Changes",
            "You have unsaved changes.\n\nSave before continuing?",
            parent=self,
        )
        if ans is True:
            self._save()
            return not self._dirty   # abort if save itself was cancelled/failed
        if ans is False:
            return True              # discard changes and proceed
        return False                 # Cancel — do nothing

    def _on_close(self):
        """WM_DELETE_WINDOW handler — prompt to save if needed."""
        if self._check_unsaved():
            self.destroy()

    def _build_ui(self):
        # ── Dark branded header bar ─────────────────────────────────
        hdr = tk.Frame(self, bg="#1c2833"); hdr.pack(fill="x")

        tk.Label(hdr, text="Red-Line-Routing", bg="#1c2833", fg="white",
                 font=("", 11, "bold"), padx=14).pack(side="left", ipady=7)
        tk.Frame(hdr, bg="#2e4053", width=1).pack(side="left", fill="y", padx=6, pady=5)

        tk.Label(hdr, text="Project:", bg="#1c2833", fg="#85929e",
                 font=("", 9), padx=2).pack(side="left")
        self.project_var = tk.StringVar()
        tk.Entry(hdr, textvariable=self.project_var, width=22,
                 bg="#2e4053", fg="white", insertbackground="white",
                 relief="flat", font=("", 9), bd=0).pack(side="left", padx=(3, 0), ipady=5)
        tk.Frame(hdr, bg="#2e4053", width=1).pack(side="left", fill="y", padx=10, pady=5)

        # Mode toggle — styled buttons, active state updated in _update_mode_buttons
        self.mode_var = tk.StringVar(value="planner")
        self._mode_btns = {}
        for text, val, act_bg in [("  Planner  ", "planner", "#2980b9"),
                                   ("  Implementation  ", "impl", "#27ae60")]:
            btn = tk.Button(hdr, text=text, bg="#1c2833", fg="#7f8c8d",
                            relief="flat", padx=4, bd=0, cursor="hand2", font=("", 9),
                            activebackground=act_bg, activeforeground="white",
                            command=lambda v=val: self._set_mode(v))
            btn.pack(side="left", padx=2, ipady=5)
            self._mode_btns[val] = (btn, act_bg)

        # Right: export wizard button
        rf = tk.Frame(hdr, bg="#1c2833"); rf.pack(side="right", padx=6)
        tk.Button(rf, text="  Export Wizard  ", bg="#27ae60", fg="white",
                  relief="flat", padx=7, bd=0, cursor="hand2", font=("", 9, "bold"),
                  activebackground="#2ecc71", activeforeground="white",
                  command=self._open_export_wizard).pack(side="left", padx=2, ipady=4, pady=6)

        self._update_mode_buttons("planner")
        tk.Frame(self, bg="#2980b9", height=2).pack(fill="x")

        # Status bar (always at bottom, packed before main area)
        self.status_var = tk.StringVar(value="Ready  —  no jobs loaded")
        _sb = tk.Frame(self, relief="sunken", bd=1, bg="#f0f0f0")
        _sb.pack(fill="x", side="bottom")
        ttk.Label(_sb, textvariable=self.status_var,
                  anchor="w", padding=(4, 1), background="#f0f0f0").pack(
            side="left", fill="x", expand=True)

        # Low-bandwidth toggle — session only, not saved; disables all auto cache refresh
        self._low_bw = tk.BooleanVar(value=False)
        def _on_low_bw():
            if self._low_bw.get():
                self._cancel_cache_refresh()
                self._low_bw_btn.config(bg="#f39c12", fg="white",
                                        relief="solid", text="Low BW  ON")
            else:
                self._low_bw_btn.config(bg="#f0f0f0", fg="#555",
                                        relief="flat", text="Low BW")
        self._low_bw_btn = tk.Button(
            _sb, text="Low BW", bg="#f0f0f0", fg="#555",
            relief="flat", bd=0, font=("", 8), cursor="hand2",
            padx=4, pady=1,
            command=lambda: [self._low_bw.set(not self._low_bw.get()), _on_low_bw()])
        self._low_bw_btn.pack(side="right", padx=(0, 6))

        # Cache activity chip — hidden until a background fetch is running
        self._cache_chip = tk.Frame(_sb, bg="#d6eaf8", padx=4, pady=1)
        self._cache_chip_lbl = tk.Label(self._cache_chip, text="", bg="#d6eaf8",
                                        fg="#1a5276", font=("", 8))
        self._cache_chip_lbl.pack(side="left")
        tk.Button(self._cache_chip, text="✕", bg="#d6eaf8", fg="#1a5276",
                  relief="flat", bd=0, font=("", 8), cursor="hand2",
                  command=self._cancel_cache_refresh).pack(side="left", padx=(4, 0))
        # chip starts hidden; shown by _set_cache_activity()

        # Cancel events for background refresh threads
        import threading as _threading
        self._draw_cache_cancel  = _threading.Event()
        self._eng_cache_cancel   = _threading.Event()

        # ── Planner mode frame ─────────────────────────────────────
        self.planner_frame = ttk.Frame(self)
        self.planner_frame.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        nb = ttk.Notebook(self.planner_frame)
        nb.pack(fill="both", expand=True)
        wt = ttk.Frame(nb); nb.add(wt, text="  Work Order  ");       self._build_work_tab(wt)
        dt = ttk.Frame(nb); nb.add(dt, text="  Project Drawings  "); self._build_drawings_tab(dt)
        rt = ttk.Frame(nb); nb.add(rt, text="  Relay Settings  ");   self._build_relay_settings_tab(rt)
        mt = ttk.Frame(nb); nb.add(mt, text="  Maintenance Standards  "); self._build_maintenance_tab(mt)
        et = ttk.Frame(nb); nb.add(et, text="  Engineering Standards  "); self._build_engineering_tab(et)
        pt = ttk.Frame(nb); nb.add(pt, text="  PTS  ");              self._build_pts_tab(pt)
        ct = ttk.Frame(nb); nb.add(ct, text="  CROW  ");             self._build_title_tab(ct)
        tbt = ttk.Frame(nb); nb.add(tbt, text="  Tailboards  ");      self._build_tailboards_tab(tbt)
        sdt = ttk.Frame(nb); nb.add(sdt, text="  Safety Documents  "); self._build_safety_tab(sdt)
        odt = ttk.Frame(nb); nb.add(odt, text="  Other Documents  ");  self._build_other_docs_tab(odt)

        # ── Implementation mode frame (hidden initially) ────────────
        self.impl_frame = ttk.Frame(self)
        self._build_impl_view(self.impl_frame)

    def _build_work_tab(self, parent):
        # Job-type buttons
        tb1 = ttk.Frame(parent, padding=(4, 4, 4, 2)); tb1.pack(fill="x")
        for label, jtype, color in [
                ("+ Remove Wire", "REMOVE", "#c0392b"), ("+ Add Wire", "ADD", "#27ae60"),
                ("+ Move Wire", "MOVE", "#1a5a99"),
                ("+ Install Device", "DEVICE ADD", "#117a65"),
                ("+ Remove Device", "DEVICE REMOVE", "#784212"),
                ("+ Block", "BLOCK", "#d35400"), ("+ Restore", "UNBLOCK", "#16a085"),
                ("+ Testing", "TESTING", "#6c3483"),
                ("+ Isolation", "ISOLATION", "#1a6b8a"),
                ("+ CR Protection", "CR_PROT", "#1a5276")]:
            tk.Button(tb1, text=label, fg="white", bg=color, relief="flat", padx=7, pady=3,
                      cursor="hand2", command=lambda t=jtype: self._add_job(t)).pack(side="left", padx=2)
        nf = ttk.Frame(tb1); nf.pack(side="right")
        for label, cmd in [("↑ Up", self._move_up), ("↓ Down", self._move_down),
                            ("Edit", self._edit_job), ("Duplicate", self._duplicate_job),
                            ("Delete", self._delete_job)]:
            ttk.Button(nf, text=label, command=cmd).pack(side="left", padx=2)

        tb2 = ttk.Frame(parent, padding=(4, 0, 4, 2)); tb2.pack(fill="x")
        ttk.Button(tb2, text="Swap ↔ Start/End", command=self._swap_endpoints).pack(side="left", padx=2)
        ttk.Button(tb2, text="Auto-Group by Device", command=self._auto_group,
                   state="disabled").pack(side="left", padx=2)

        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=4, pady=4)

        # ── Left pane: Devices panel + Work Order list ─────────────
        left_pane = ttk.Frame(pw); pw.add(left_pane, weight=1)

        # Devices panel — registered device names that feed all endpoint autocompletes
        dev_lf = ttk.LabelFrame(left_pane, text="Devices", padding=(4, 2))
        dev_lf.pack(fill="x", pady=(0, 4))

        dev_inner = ttk.Frame(dev_lf); dev_inner.pack(fill="x")
        self.device_lb = tk.Listbox(dev_inner, height=4, selectmode="browse",
                                    font=("", 9), activestyle="none",
                                    relief="flat", borderwidth=1,
                                    bg="white", fg="#1c2833",
                                    selectbackground="#2980b9", selectforeground="white")
        dev_vsb = ttk.Scrollbar(dev_inner, orient="vertical", command=self.device_lb.yview)
        self.device_lb.configure(yscrollcommand=dev_vsb.set)
        self.device_lb.pack(side="left", fill="both", expand=True)
        dev_vsb.pack(side="right", fill="y")
        self.device_lb.bind("<Double-1>", lambda _: self._edit_device())

        dev_btns = ttk.Frame(dev_lf); dev_btns.pack(fill="x", pady=(4, 0))
        ttk.Button(dev_btns, text="+ Add Device",    command=self._add_device).pack(side="left", padx=2)
        ttk.Button(dev_btns, text="Edit",            command=self._edit_device).pack(side="left", padx=2)
        ttk.Button(dev_btns, text="Remove",          command=self._remove_device).pack(side="left", padx=2)
        ttk.Label(dev_btns, text="Drives autocomplete in all jobs",
                  foreground="grey", font=("", 8)).pack(side="left", padx=6)

        # Work Order job list
        lf = ttk.LabelFrame(left_pane, text="Work Order", padding=4)
        lf.pack(fill="both", expand=True)
        cols = ("Done","Seq","Type","Description")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("Done",text="✓"); self.tree.heading("Seq",text="#")
        self.tree.heading("Type",text="Type"); self.tree.heading("Description",text="Description")
        self.tree.column("Done",width=30,stretch=False,anchor="center")
        self.tree.column("Seq",width=35,stretch=False); self.tree.column("Type",width=110,stretch=False)
        self.tree.column("Description",width=230)
        for t,fg in self.TYPE_FG.items(): self.tree.tag_configure(t, foreground=fg)
        _strike = _treeview_strike_font()
        self.tree.tag_configure("COMPLETED", foreground="#aaaaaa", font=_strike)
        vsb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True); vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _: self._edit_job())
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Delete>", lambda _: self._delete_job())

        # ── Right pane: Job Preview ─────────────────────────────────
        pf = ttk.LabelFrame(pw, text="Job Preview", padding=4); pw.add(pf, weight=2)
        self.preview = scrolledtext.ScrolledText(pf, font=("Courier",9), state="disabled", wrap="none")
        self.preview.pack(fill="both", expand=True)

    def _build_drawings_tab(self, parent):
        tb = ttk.Frame(parent, padding=(4,4)); tb.pack(fill="x")
        ttk.Button(tb, text="+ Add Drawing", command=self._add_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",          command=self._edit_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Delete",        command=self._delete_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="\U0001f50d Search…", command=self._search_drawings).pack(side="left", padx=2)
        ttk.Button(tb, text="Scan Jobs →",   command=self._scan_and_refresh).pack(side="left", padx=(10,2))
        ttk.Button(tb, text="Drawing Index",   command=self._show_drawing_index).pack(side="left", padx=2)
        ttk.Button(tb, text="⬇ Download All",  command=self._download_drawings).pack(side="left", padx=(10,2))
        ttk.Button(tb, text="🖨 Print Selected", command=self._print_selected_drawings).pack(side="left", padx=2)
        ttk.Button(tb, text="🖨 Print All",      command=self._print_all_drawings).pack(side="left", padx=2)
        ttk.Label(tb, text="Click to open  ·  Ctrl+click to force web  ·  Double-click to edit",
                  foreground="grey").pack(side="left", padx=8)
        frame = ttk.Frame(parent); frame.pack(fill="both", expand=True, padx=4, pady=(0,4))
        cols = ("Drawing","Title","Revision","Local","URL","Notes")
        self.drawings_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.drawings_tree.heading("Drawing",text="Drawing"); self.drawings_tree.heading("Title",text="Title")
        self.drawings_tree.heading("Revision",text="Rev")
        self.drawings_tree.heading("Local",text="Local")
        self.drawings_tree.heading("URL",text="Drawing URL"); self.drawings_tree.heading("Notes",text="Notes")
        self.drawings_tree.column("Drawing",width=140,stretch=False); self.drawings_tree.column("Title",width=160,stretch=False)
        self.drawings_tree.column("Revision",width=44,stretch=False)
        self.drawings_tree.column("Local",width=60,stretch=False,anchor="center")
        self.drawings_tree.column("URL",width=280); self.drawings_tree.column("Notes",width=160)
        self.drawings_tree.tag_configure("downloaded", foreground="#1a7a30")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.drawings_tree.yview)
        self.drawings_tree.configure(yscrollcommand=vsb.set)
        self.drawings_tree.pack(side="left", fill="both", expand=True); vsb.pack(side="right", fill="y")
        self.drawings_tree.bind("<Button-1>", self._on_drawings_click)
        self.drawings_tree.bind("<Double-1>", lambda _: self._edit_drawing())

    # ── Drawing registry CRUD ─────────────────────────────────────

    def _search_drawings(self):
        dlg = DrawingSearchDialog(self, self.app_config, multi_select=True,
                                  proj_cache=self._drawing_cache,
                                  persist_fn=self._save_app_config)
        for r in dlg.selected:
            if r.drawing_number not in self.drawing_registry:
                self.drawing_registry[r.drawing_number] = {
                    "title": r.title, "rev": r.revision,
                    "url": r.document_url, "notes": "",
                }
        if dlg.selected:
            self._refresh_drawings_list()
            if self.current_file:
                self._write(self.current_file)

    def _refresh_drawings_list(self):
        for iid in self.drawings_tree.get_children(): self.drawings_tree.delete(iid)
        for name in sorted(self.drawing_registry.keys()):
            info = self.drawing_registry[name]
            downloaded = bool(self._find_drawing_files({name}))
            local_lbl = "✓ local" if downloaded else "—"
            tags = ("downloaded",) if downloaded else ()
            self.drawings_tree.insert("","end",iid=name, tags=tags,
                values=(name,info.get("title",""),info.get("rev",""),
                        local_lbl,info.get("url",""),info.get("notes","")))
        self._mark_dirty()

    def _add_drawing(self):
        dlg = DrawingEditDialog(self, base_url=self.app_config.get("drawing_download_url",""),
                                app_config=self.app_config,
                                proj_cache=self._drawing_cache)
        if dlg.result:
            name = dlg.result["name"]
            self.drawing_registry[name] = {"title":dlg.result["title"],"rev":dlg.result["rev"],"url":dlg.result["url"],"notes":dlg.result["notes"]}
            self._refresh_drawings_list()

    def _edit_drawing(self):
        sel = self.drawings_tree.selection()
        if not sel: messagebox.showinfo("Select","Please select a drawing to edit."); return
        name = sel[0]; info = self.drawing_registry.get(name,{})
        dlg = DrawingEditDialog(self, existing={"name":name,**info},
                                base_url=self.app_config.get("drawing_download_url",""),
                                app_config=self.app_config,
                                proj_cache=self._drawing_cache)
        if dlg.result:
            old = dlg.result.get("old_name"); new_name = dlg.result["name"]
            if old and old != new_name and old in self.drawing_registry: del self.drawing_registry[old]
            self.drawing_registry[new_name] = {"title":dlg.result["title"],"rev":dlg.result["rev"],"url":dlg.result["url"],"notes":dlg.result["notes"]}
            self._refresh_drawings_list()

    def _delete_drawing(self):
        sel = self.drawings_tree.selection()
        if not sel: messagebox.showinfo("Select","Please select a drawing to delete."); return
        name = sel[0]
        if messagebox.askyesno("Delete Drawing",f"Remove '{name}' from the registry?"):
            self.drawing_registry.pop(name,None); self._refresh_drawings_list()

    def _scan_jobs_for_drawings(self):
        def _reg(ep):
            name = ep.get("drawing","").strip()
            if not name: return
            if name not in self.drawing_registry:
                self.drawing_registry[name] = {"title":"","rev":"","url":"","notes":""}
            rec = self.drawing_registry[name]
            if ep.get("drawing_rev") and not rec.get("rev"): rec["rev"] = ep["drawing_rev"]
            if ep.get("drawing_url") and not rec.get("url"): rec["url"] = ep["drawing_url"]
        for job in self.jobs:
            for k in ("start","end","add_start","add_end"):
                if job.get(k): _reg(job[k])
            if job.get("protection"):
                for d in _get_prot_drawings(job["protection"]): _reg(d)

    def _scan_and_refresh(self):
        self._scan_jobs_for_drawings(); self._refresh_drawings_list()
        self.status_var.set(f"Registry updated — {len(self.drawing_registry)} drawing(s).")

    def _on_drawings_click(self, event):
        if self.drawings_tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.drawings_tree.identify_row(event.y)
        if not row:
            return
        ctrl = bool(event.state & 0x4)
        url = self.drawing_registry.get(row, {}).get("url", "").strip()
        files = self._find_drawing_files({row})
        if ctrl:
            # Force-open web version
            if url:
                webbrowser.open(url)
        elif files:
            _open_file(files[0][1])
        elif url:
            webbrowser.open(url)

    def _show_drawing_index(self):
        """Dialog: which drawings appear in which job steps (cross-reference)."""
        index = {}
        for i, job in enumerate(self.jobs):
            step = f"#{i+1} {job.get('description','') or job['type']}"
            seen_in_step = []
            for ep_key in ("start", "end", "add_start", "add_end"):
                d = (job.get(ep_key) or {}).get("drawing", "").strip()
                if d and d not in seen_in_step:
                    seen_in_step.append(d)
            for d_info in _get_prot_drawings(job.get("protection") or {}):
                d = d_info.get("drawing", "").strip()
                if d and d not in seen_in_step:
                    seen_in_step.append(d)
            for d in seen_in_step:
                index.setdefault(d, []).append(step)
        if not index:
            messagebox.showinfo("Drawing Index", "No drawings are referenced in any job step.")
            return
        win = tk.Toplevel(self)
        win.title("Drawing Cross-Reference")
        win.geometry("740x440")
        ttk.Label(win, text="Drawing → Job steps that reference it",
                  font=("", 10, "bold"), padding=(8, 6)).pack(anchor="w")
        fr = ttk.Frame(win)
        fr.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        cols = ("Drawing", "Steps")
        tree = ttk.Treeview(fr, columns=cols, show="headings")
        tree.heading("Drawing", text="Drawing")
        tree.heading("Steps",   text="Steps")
        tree.column("Drawing", width=200, stretch=False)
        tree.column("Steps",   width=500)
        vsb = ttk.Scrollbar(fr, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        for drw in sorted(index.keys()):
            tree.insert("", "end", values=(drw, ",  ".join(index[drw])))
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(4, 8))

    def _download_drawings(self):
        """Download all drawings with URLs to the project's Drawings/ subfolder."""
        if not self.project_folder:
            messagebox.showinfo("Save First",
                "Please save the project first so the Drawings folder location is known.")
            return
        targets = [(name, info["url"]) for name, info in self.drawing_registry.items()
                   if info.get("url", "").strip()]
        if not targets:
            messagebox.showinfo("No URLs", "No drawing URLs are set in the registry."); return
        self._download_with_progress(
            "Downloading Drawings",
            targets,
            os.path.join(self.project_folder, "Drawings"),
            organize=True,
        )

    # ── Relay Settings CRUD ───────────────────────────────────────

    def _refresh_relay_list(self):
        for iid in self.relay_tree.get_children(): self.relay_tree.delete(iid)
        for dev_id, info in sorted(self.relay_registry.items()):
            self.relay_tree.insert("", "end", iid=dev_id, values=(
                dev_id,
                info.get("title",    ""),
                info.get("revision", ""),
                info.get("engineer", ""),
                info.get("contact",  ""),
                info.get("url",      ""),
                info.get("wo_device",""),
            ))
        self._mark_dirty()

    def _wo_device_list(self):
        """Collect unique device names from all work order jobs."""
        seen = set()
        devs = []
        for job in self.jobs:
            for ep_key in ("start", "end", "add_start", "add_end"):
                d = (job.get(ep_key) or {}).get("device", "").strip()
                if d and d not in seen:
                    seen.add(d); devs.append(d)
        return sorted(devs)

    def _add_relay(self):
        dlg = RelaySettingDialog(self, wo_devices=self._wo_device_list(),
                                 base_url=self.app_config.get("base_relay_url", ""))
        if dlg.result:
            dev_id = dlg.result["device_id"]
            self.relay_registry[dev_id] = {
                k: v for k, v in dlg.result.items()
                if k not in ("device_id", "import_file")}
            self._relay_import_file(dlg.result.get("import_file"), dev_id)
            self._refresh_relay_list()

    def _edit_relay(self):
        sel = self.relay_tree.selection()
        if not sel: messagebox.showinfo("Select", "Please select a relay record to edit."); return
        dev_id = sel[0]; info = self.relay_registry.get(dev_id, {})
        dlg = RelaySettingDialog(self, existing={"device_id": dev_id, **info},
                                 wo_devices=self._wo_device_list(),
                                 base_url=self.app_config.get("base_relay_url", ""))
        if dlg.result:
            old_id = dev_id; new_id = dlg.result["device_id"]
            if old_id != new_id and old_id in self.relay_registry:
                del self.relay_registry[old_id]
            self.relay_registry[new_id] = {
                k: v for k, v in dlg.result.items()
                if k not in ("device_id", "import_file")}
            self._relay_import_file(dlg.result.get("import_file"), new_id)
            self._refresh_relay_list()

    def _relay_import_file(self, src_path, dev_id):
        """Copy src_path into Relay Settings/ using dev_id as the base name."""
        if not src_path or not os.path.isfile(src_path):
            return
        if not self.project_folder:
            messagebox.showinfo("Save Project First",
                "Import will be available after you save the project.\n"
                f"File to import: {src_path}")
            return
        dest_dir = os.path.join(self.project_folder, "Relay Settings")
        os.makedirs(dest_dir, exist_ok=True)
        ext      = os.path.splitext(src_path)[1] or ".txt"
        dest     = os.path.join(dest_dir, f"{dev_id}{ext}")
        try:
            shutil.copy2(src_path, dest)
        except Exception as exc:
            messagebox.showerror("Import Failed", str(exc))

    def _delete_relay(self):
        sel = self.relay_tree.selection()
        if not sel: messagebox.showinfo("Select", "Please select a relay record to delete."); return
        dev_id = sel[0]
        if messagebox.askyesno("Delete", f"Remove relay record '{dev_id}'?"):
            self.relay_registry.pop(dev_id, None); self._refresh_relay_list()

    def _on_relay_ctrl_click(self, event):
        row = self.relay_tree.identify_row(event.y)
        if not row: return
        url = self.relay_registry.get(row, {}).get("url", "").strip()
        if url: webbrowser.open(url)

    def _download_relay_settings(self):
        if not self.project_folder:
            messagebox.showinfo("Save First",
                "Please save the project first so the Relay Settings folder location is known."); return
        targets = [(dev_id, info["url"]) for dev_id, info in self.relay_registry.items()
                   if info.get("url", "").strip()]
        if not targets:
            messagebox.showinfo("No URLs", "No relay setting URLs are set."); return
        self._download_with_progress(
            "Downloading Relay Settings",
            targets,
            os.path.join(self.project_folder, "Relay Settings"),
        )

    def _parse_request_headers(self):
        """Parse request_headers from app_config into a dict.

        Reads self.app_config.get("request_headers", ""), parses it as
        Header-Name: value lines (one per line, skips blanks and lines
        without ':'), and returns a dict with whitespace-stripped keys/values.
        """
        raw = self.app_config.get("request_headers", "")
        headers = {}
        for line in raw.splitlines():
            if ":" not in line:
                continue
            line = line.strip()
            if not line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if key:
                headers[key] = value
        return headers

    def _download_with_progress(self, title, targets, dest_dir, organize=False, extra_headers=None, on_complete=None, archive_revisions=False):
        """Shared download engine with thread-safe progress dialog.

        Uses a queue.Queue so the worker thread never touches tkinter directly —
        all widget updates happen on the main thread via after() polling.
        archive_revisions: move an existing file of the same name into
        Archive/ (timestamped) before the new copy is written, so living
        documents keep their revision history.
        """
        os.makedirs(dest_dir, exist_ok=True)

        # ── Dialog ────────────────────────────────────────────────
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.resizable(True, False)
        dlg.grab_set()

        hdr = tk.Frame(dlg, bg="#1c2833"); hdr.pack(fill="x")
        tk.Label(hdr, text=title, bg="#1c2833", fg="white",
                 font=("", 11, "bold"), padx=14, pady=10).pack(side="left")
        tk.Label(hdr, text=f"{len(targets)} file(s)", bg="#1c2833", fg="#85929e",
                 font=("", 9), padx=8).pack(side="right", pady=10)

        body = ttk.Frame(dlg, padding=(14, 10, 14, 4)); body.pack(fill="both", expand=True)

        # Current file label
        cur_lbl = tk.StringVar(value="Waiting…")
        ttk.Label(body, text="File:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=2)
        ttk.Label(body, textvariable=cur_lbl, foreground="#2980b9",
                  font=("", 9, "bold"), wraplength=430,
                  anchor="w").grid(row=0, column=1, sticky="w", pady=2)

        # Destination label
        dest_lbl = tk.StringVar(value="")
        ttk.Label(body, text="Saving to:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=2)
        ttk.Label(body, textvariable=dest_lbl, foreground="#566573",
                  font=("", 8), wraplength=430,
                  anchor="w").grid(row=1, column=1, sticky="w", pady=2)

        # Progress bar + counter
        bar = ttk.Progressbar(body, length=500, maximum=len(targets))
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        cnt_lbl = tk.StringVar(value=f"0 / {len(targets)}")
        ttk.Label(body, textvariable=cnt_lbl, foreground="grey",
                  font=("", 8), anchor="e").grid(row=3, column=0, columnspan=2, sticky="e")
        body.columnconfigure(1, weight=1)

        # Scrollable log
        ttk.Label(body, text="Log:", font=("", 8)).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 2))
        log = scrolledtext.ScrolledText(body, height=8, font=("Courier", 8),
                                        state="disabled", wrap="word",
                                        bg="#1c2833", fg="#ecf0f1",
                                        insertbackground="white")
        log.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=(0, 4))
        log.tag_configure("ok",   foreground="#27ae60")
        log.tag_configure("err",  foreground="#e74c3c")
        log.tag_configure("info", foreground="#85929e")
        body.rowconfigure(5, weight=1)

        # Footer
        tk.Frame(dlg, bg="#d5d8dc", height=1).pack(fill="x")
        bf = ttk.Frame(dlg, padding=(14, 8)); bf.pack(fill="x")
        def _on_close():
            dlg.destroy()
            if on_complete:
                on_complete()
        close_btn = ttk.Button(bf, text="Close", state="disabled", command=_on_close)
        close_btn.pack(side="right")
        cancel_flag = [False]
        ttk.Button(bf, text="Cancel",
                   command=lambda: cancel_flag.__setitem__(0, True)).pack(side="right", padx=6)
        folder_btn = ttk.Button(bf, text="Open Folder",
                                command=lambda: _open_file(dest_dir))
        folder_btn.pack(side="left")

        _center_window(dlg, 560, 420)

        # ── Worker thread ─────────────────────────────────────────
        q = queue.Queue()
        _VALID_EXTS = {".pdf", ".png", ".jpg", ".jpeg",
                       ".tif", ".tiff", ".svg", ".dwg", ".dxf"}

        def _run():
            ok = 0
            for i, (name, url) in enumerate(targets):
                if cancel_flag[0]:
                    q.put(("log", f"Cancelled after {i} of {len(targets)} file(s).", "info"))
                    break

                ext = os.path.splitext(url.split("?")[0])[-1].lower()
                if ext not in _VALID_EXTS:
                    ext = ".pdf"
                sub_dir = _drawing_subdir(dest_dir, name) if organize else dest_dir
                os.makedirs(sub_dir, exist_ok=True)
                dest = os.path.join(sub_dir, name + ext)
                q.put(("progress", i, name, dest))

                try:
                    if url.lower().startswith("file://"):
                        local_path = _file_url_to_path(url)
                        with open(local_path, "rb") as fh:
                            data = fh.read()
                    else:
                        _p = urllib.parse.urlparse(url)
                        hdrs = {
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
                            ),
                            "Accept":  "*/*",
                            "Referer": f"{_p.scheme}://{_p.netloc}",
                            **self._parse_request_headers(),
                        }
                        if extra_headers:
                            hdrs.update(extra_headers)
                        req = urllib.request.Request(url, headers=hdrs)
                        # Preserve auth headers across redirects (e.g. SharePoint SSO)
                        _opener = urllib.request.build_opener(_StickyRedirectHandler())
                        with _opener.open(req, timeout=30) as resp:
                            data = resp.read()
                    if organize:
                        archived = _archive_existing(sub_dir, name)
                    elif archive_revisions:
                        archived = 1 if _archive_revision(sub_dir, os.path.basename(dest)) else 0
                    else:
                        archived = 0
                    with open(dest, "wb") as fh:
                        fh.write(data)
                    kb = len(data) // 1024
                    rel = os.path.relpath(dest, dest_dir)
                    arch_note = f"  [{archived} archived]" if archived else ""
                    q.put(("log", f"✓  {name}  ({kb} KB)  →  {rel}{arch_note}", "ok"))
                    ok += 1
                except urllib.error.HTTPError as exc:
                    try:
                        _b = exc.read().decode("utf-8", errors="replace")
                        _b = re.sub(r"<[^>]+>", " ", _b)
                        _b = re.sub(r"\s+", " ", _b).strip()[:300]
                    except Exception:
                        _b = ""
                    msg = f"✗  {name}: HTTP {exc.code} {exc.reason}  [{url}]"
                    if _b:
                        msg += f"\n     Server: {_b}"
                    if exc.code == 401:
                        msg += "\n  → Tip: add a Cookie or Authorization header in File → Software Settings"
                    elif exc.code == 403:
                        msg += "\n  → Tip: server denied access — confirm the Cookie is current for this server (Software Settings → Engineering Standards Headers)"
                    q.put(("log", msg, "err"))
                except urllib.error.URLError as exc:
                    q.put(("log",
                           f"✗  {name}: Cannot reach server — {exc.reason}", "err"))
                except OSError as exc:
                    q.put(("log",
                           f"✗  {name}: File write error — {exc}", "err"))
                except Exception as exc:
                    q.put(("log", f"✗  {name}: {exc}", "err"))

            q.put(("done", ok))

        # ── Main-thread poller ────────────────────────────────────
        def _poll():
            try:
                while True:
                    item = q.get_nowait()
                    kind = item[0]
                    if kind == "progress":
                        _, i, name, dest = item
                        cur_lbl.set(f"{i + 1} of {len(targets)}:  {name}")
                        dest_lbl.set(dest)
                        bar["value"] = i
                        cnt_lbl.set(f"{i} / {len(targets)}")
                    elif kind == "log":
                        _, msg, tag = item
                        log.configure(state="normal")
                        log.insert("end", msg + "\n", tag)
                        log.see("end")
                        log.configure(state="disabled")
                    elif kind == "done":
                        ok = item[1]
                        bar["value"] = len(targets)
                        cnt_lbl.set(f"{len(targets)} / {len(targets)}")
                        cur_lbl.set("Complete")
                        dest_lbl.set(f"All files saved to: {dest_dir}")
                        log.configure(state="normal")
                        log.insert("end",
                                   f"\n─── {ok} downloaded, "
                                   f"{len(targets) - ok} failed ───\n", "info")
                        log.see("end")
                        log.configure(state="disabled")
                        close_btn.configure(state="normal")
                        return
            except queue.Empty:
                pass
            dlg.after(80, _poll)

        threading.Thread(target=_run, daemon=True).start()
        dlg.after(80, _poll)

    # ── Software / Global Settings ────────────────────────────────

    def _load_app_config(self):
        return self._app_db.load_config()

    def _save_app_config(self):
        try:
            self._app_db.save_config(self.app_config)
        except Exception as exc:
            messagebox.showerror("Settings Error", f"Could not save software settings:\n{exc}")

    # ── Cross-project standards library ───────────────────────────
    # Every standard added to any project is remembered globally in
    # ~/.redlinerouting.db so new projects can pull it from the
    # library instead of re-entering it.

    def _remember_standard(self, kind, sid, info):
        """Merge one standard into the global library."""
        if not sid:
            return
        try:
            self._app_db.put_standard(kind, sid, info)
        except sqlite3.Error:
            pass

    def _remember_all_standards(self):
        """Sweep both project registries into the global library."""
        for kind, reg in (("maintenance", self.maintenance_standards_registry),
                          ("engineering", self.engineering_standards_registry)):
            for sid, info in reg.items():
                self._remember_standard(kind, sid, info)

    def _forget_standard(self, kind, sid):
        try:
            self._app_db.delete_standard(kind, sid)
        except sqlite3.Error:
            pass

    def _standards_library(self, kind):
        return self._app_db.get_standards(kind)

    def _open_software_settings(self):
        dlg = tk.Toplevel(self)
        dlg.title("Software Settings")
        dlg.resizable(True, True)
        dlg.grab_set()

        # Scrollable content area
        _vsb = ttk.Scrollbar(dlg, orient="vertical")
        _vsb.pack(side="right", fill="y")
        _canvas = tk.Canvas(dlg, highlightthickness=0, yscrollcommand=_vsb.set)
        _canvas.pack(side="left", fill="both", expand=True)
        _vsb.configure(command=_canvas.yview)

        f = ttk.Frame(_canvas, padding=14)
        _cwin = _canvas.create_window((0, 0), window=f, anchor="nw")

        def _on_f_cfg(event):
            _canvas.configure(scrollregion=_canvas.bbox("all"))

        def _on_canvas_cfg(event):
            _canvas.itemconfigure(_cwin, width=event.width)

        f.bind("<Configure>", _on_f_cfg)
        _canvas.bind("<Configure>", _on_canvas_cfg)
        _canvas.bind_all(
            "<MouseWheel>",
            lambda e: _canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"),
        )
        dlg.bind("<Destroy>", lambda e: _canvas.unbind_all("<MouseWheel>")
                 if e.widget is dlg else None)

        cfg_vars = {}

        # ── Drawings section (collapsible) ───────────────────────────
        draw_fields = [
            ("drawing_search_url",         "Drawing Search URL:",         "Base URL for the corporate drawing search server"),
            ("drawing_download_url",       "Drawing Download URL:",       "Direct download base URL for drawings"),
            ("drawing_cache_refresh_hours","Drawing Cache Refresh (hrs):","How many hours before a cached drawing search result is re-fetched (default 4)"),
        ]
        _draw_open = tk.BooleanVar(value=True)

        draw_hdr = ttk.Frame(f)
        draw_hdr.pack(fill="x", pady=(0, 0))
        _draw_caret = ttk.Label(draw_hdr, text="▼ Drawings", cursor="hand2", font=("", 9, "bold"))
        _draw_caret.pack(side="left", pady=(4, 2))

        draw_body = ttk.LabelFrame(f, padding=8)
        draw_body.pack(fill="x", pady=(0, 8))
        draw_body.columnconfigure(1, weight=1)

        def _toggle_drawings(e=None):
            if _draw_open.get():
                draw_body.pack_forget()
                _draw_caret.config(text="▶ Drawings")
                _draw_open.set(False)
            else:
                draw_body.pack(fill="x", pady=(0, 8), after=draw_hdr)
                _draw_caret.config(text="▼ Drawings")
                _draw_open.set(True)
            f.update_idletasks()
            _canvas.configure(scrollregion=_canvas.bbox("all"))

        draw_hdr.bind("<Button-1>", _toggle_drawings)
        _draw_caret.bind("<Button-1>", _toggle_drawings)

        for r, (key, label, hint) in enumerate(draw_fields):
            ttk.Label(draw_body, text=label).grid(row=r*2, column=0, sticky="e", padx=(0,6), pady=3)
            var = tk.StringVar(value=self.app_config.get(key, ""))
            cfg_vars[key] = var
            ttk.Entry(draw_body, textvariable=var, width=52).grid(row=r*2, column=1, sticky="ew", pady=3)
            ttk.Label(draw_body, text=hint, foreground="grey", font=("",8)).grid(
                row=r*2+1, column=0, columnspan=2, sticky="w", pady=(0,2))

        n_draw = len(draw_fields)
        ttk.Separator(draw_body, orient="horizontal").grid(
            row=n_draw*2, column=0, columnspan=2, sticky="ew", pady=(8, 4))

        headers_txt = scrolledtext.ScrolledText(draw_body, height=4, font=("Courier", 9), wrap="none")
        headers_txt.grid(row=n_draw*2+1, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        headers_txt.insert("1.0", self.app_config.get("request_headers", ""))

        def _do_win_auth_cookies():
            url          = cfg_vars.get("drawing_search_url",   tk.StringVar()).get().strip()
            download_url = cfg_vars.get("drawing_download_url", tk.StringVar()).get().strip()
            if not (url and download_url):
                messagebox.showwarning("Incomplete Setup",
                    "Fill in Drawing Search URL and Drawing Download URL first.",
                    parent=dlg)
                return
            domain = _domain_from_url(url)
            _BrowserCookieDialog(dlg, domain, headers_txt,
                                 cookies_fn=lambda _: _ps_grab_windows_cookies(url))

        def _do_fetch_options():
            url = cfg_vars.get("drawing_search_url", tk.StringVar()).get().strip()
            if not url:
                messagebox.showwarning("No URL",
                    "Fill in Drawing Search URL first.", parent=dlg)
                return
            headers = _parse_request_headers_raw(headers_txt.get("1.0", "end"))
            _show_fetch_options_dialog(dlg, url, headers)

        btn_row = ttk.Frame(draw_body)
        btn_row.grid(row=n_draw*2+2, column=0, columnspan=2, sticky="w", pady=(0, 2))
        ttk.Button(btn_row, text="🔑 Grab via Windows Auth",
                   command=_do_win_auth_cookies).pack(side="left")
        ttk.Button(btn_row, text="🔄 Fetch Drawing Options",
                   command=_do_fetch_options).pack(side="left", padx=(6, 0))
        ttk.Label(btn_row, text="Windows Auth requires Drawing Search URL and Drawing Download URL.",
                  foreground="grey", font=("", 8)).pack(side="left", padx=8)

        # ── Engineering Standards section (collapsible) ───────────────
        eng_fields = [
            ("engineering_url",                "Engineering Standards URL:",        "Web app URL — used for Windows Auth cookie grab"),
            ("engineering_api_url",            "Engineering API URL:",              "API base URL for series/sections data (e.g. https://host/esv4)"),
            ("engineering_cache_refresh_hours","Engineering Cache Refresh (hrs):",  "How many hours before cached standards are re-fetched (default 4)"),
        ]
        _eng_open = tk.BooleanVar(value=True)

        eng_hdr = ttk.Frame(f)
        eng_hdr.pack(fill="x", pady=(0, 0))
        _eng_caret = ttk.Label(eng_hdr, text="▼ Engineering Standards", cursor="hand2", font=("", 9, "bold"))
        _eng_caret.pack(side="left", pady=(4, 2))

        eng_body = ttk.LabelFrame(f, padding=8)
        eng_body.pack(fill="x", pady=(0, 8))
        eng_body.columnconfigure(1, weight=1)

        def _toggle_eng(e=None):
            if _eng_open.get():
                eng_body.pack_forget()
                _eng_caret.config(text="▶ Engineering Standards")
                _eng_open.set(False)
            else:
                eng_body.pack(fill="x", pady=(0, 8), after=eng_hdr)
                _eng_caret.config(text="▼ Engineering Standards")
                _eng_open.set(True)
            f.update_idletasks()
            _canvas.configure(scrollregion=_canvas.bbox("all"))

        eng_hdr.bind("<Button-1>", _toggle_eng)
        _eng_caret.bind("<Button-1>", _toggle_eng)

        for r, (key, label, hint) in enumerate(eng_fields):
            ttk.Label(eng_body, text=label).grid(row=r*2, column=0, sticky="e", padx=(0,6), pady=3)
            var = tk.StringVar(value=self.app_config.get(key, ""))
            cfg_vars[key] = var
            ttk.Entry(eng_body, textvariable=var, width=52).grid(row=r*2, column=1, sticky="ew", pady=3)
            ttk.Label(eng_body, text=hint, foreground="grey", font=("",8)).grid(
                row=r*2+1, column=0, columnspan=2, sticky="w", pady=(0,2))

        n_eng = len(eng_fields)
        ttk.Separator(eng_body, orient="horizontal").grid(
            row=n_eng*2, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Label(eng_body, text="Override headers (leave blank to use Drawing headers above):",
                  foreground="grey", font=("", 8)).grid(
            row=n_eng*2+1, column=0, columnspan=2, sticky="w", pady=(0, 2))

        eng_headers_txt = scrolledtext.ScrolledText(eng_body, height=3, font=("Courier", 9), wrap="none")
        eng_headers_txt.grid(row=n_eng*2+2, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        eng_headers_txt.insert("1.0", self.app_config.get("engineering_request_headers", ""))

        def _do_eng_win_auth():
            import re as _re
            # Prefer the API URL so we grab cookies for the /esv4/ path.
            # The web-app URL (/es/browse) may return cookies scoped to a
            # different path that the /esv4/ API doesn't accept.
            api_raw = cfg_vars.get("engineering_api_url", tk.StringVar()).get().strip()
            api_raw = _re.sub(r'/(sections|series)([?/].*)?$', '', api_raw).rstrip('/')
            grab_url = api_raw or cfg_vars.get("engineering_url", tk.StringVar()).get().strip()
            if not grab_url:
                messagebox.showwarning("Incomplete Setup",
                    "Fill in the Engineering API URL first.",
                    parent=dlg)
                return
            # Grab against the series endpoint so IIS issues a cookie for the /esv4 path
            grab_target = grab_url.rstrip('/') + "/series?group=00all"
            domain = _domain_from_url(grab_url)
            _BrowserCookieDialog(dlg, domain, eng_headers_txt,
                                 cookies_fn=lambda _: _ps_grab_windows_cookies(grab_target))

        eng_btn_row = ttk.Frame(eng_body)
        eng_btn_row.grid(row=n_eng*2+3, column=0, columnspan=2, sticky="w", pady=(0, 2))
        ttk.Button(eng_btn_row, text="🔑 Grab via Windows Auth",
                   command=_do_eng_win_auth).pack(side="left")
        ttk.Label(eng_btn_row,
                  text="Requires Engineering Standards URL to be filled in.",
                  foreground="grey", font=("", 8)).pack(side="left", padx=8)

        # ── All other URL sections ────────────────────────────────────
        other_sections = [
            ("Aspen", [
                ("aspen_url",         "Aspen URL:",         "Base URL for Aspen (future use)"),
            ]),
            ("CROWs", [
                ("base_crow_url",     "Base CROW URL:",     "Used to pre-fill URLs when adding CROWs"),
            ]),
            ("Relay Settings", [
                ("base_relay_url",    "Base Relay URL:",    "Used to pre-fill URLs when adding relay settings"),
            ]),
            ("Maintenance Standards", [
                ("base_maintenance_telecom_url",      "Base URL (Telecom):",      "Pre-fills Telecom URL when adding maintenance standards"),
                ("base_maintenance_transmission_url", "Base URL (Transmission):", "Pre-fills Transmission URL when adding maintenance standards"),
            ]),
            ("Tailboard", [
                ("tailboard_url",  "Tailboard URL:",  "Pre-fills the Tailboard Template URL on the Tailboards tab"),
                ("crew_email",     "Crew Email(s):",  "Default recipients when emailing a completed tailboard (comma-separated)"),
            ]),
        ]

        for section_name, fields in other_sections:
            lf = ttk.LabelFrame(f, text=section_name, padding=8)
            lf.pack(fill="x", pady=(0, 8))
            lf.columnconfigure(1, weight=1)
            for r, (key, label, hint) in enumerate(fields):
                ttk.Label(lf, text=label).grid(row=r*2,   column=0, sticky="e", padx=(0,6), pady=3)
                var = tk.StringVar(value=self.app_config.get(key, ""))
                cfg_vars[key] = var
                ttk.Entry(lf, textvariable=var, width=52).grid(row=r*2, column=1, sticky="ew", pady=3)
                ttk.Label(lf, text=hint, foreground="grey", font=("",8)).grid(
                    row=r*2+1, column=0, columnspan=2, sticky="w", pady=(0,2))

        tb_hdrs_lf = ttk.LabelFrame(f, text="Tailboard Site Headers (optional override)", padding=8)
        tb_hdrs_lf.pack(fill="x", pady=(0, 8))
        ttk.Label(tb_hdrs_lf,
                  text="Auth for the internal site hosting the tailboard template, HBR, LOA and\n"
                       "Safety Practice Regulations. Leave blank to use the master headers above.",
                  foreground="grey", font=("", 8), justify="left").pack(anchor="w", pady=(0, 4))
        tb_headers_txt = scrolledtext.ScrolledText(tb_hdrs_lf, height=3, font=("Courier", 9), wrap="none")
        tb_headers_txt.pack(fill="x")
        tb_headers_txt.insert("1.0", self.app_config.get("tailboard_request_headers", ""))

        def _do_grab_tb_cookies():
            url = cfg_vars.get("tailboard_url", tk.StringVar()).get().strip()
            if not url:
                # Fall back to any reference-document URL from the open project
                for k in ("tailboard", "hbr", "loa", "safety_regs"):
                    url = self.tailboard_refs.get(k, {}).get("url", "").strip()
                    if url:
                        break
            domain = _domain_from_url(url) if url else ""
            if not domain:
                messagebox.showwarning("No URL",
                    "Set the Tailboard URL above (or a reference document URL on the "
                    "Tailboards tab) first so the domain is known.",
                    parent=dlg)
                return
            _BrowserCookieDialog(dlg, domain, tb_headers_txt)

        tb_grab_row = ttk.Frame(tb_hdrs_lf); tb_grab_row.pack(anchor="w", pady=(4, 0))
        ttk.Button(tb_grab_row, text="🍪 Grab from Browser",
                   command=_do_grab_tb_cookies).pack(side="left")
        ttk.Label(tb_grab_row,
                  text="Reads cookies for the tailboard site from your running Edge / Chrome session.",
                  foreground="grey", font=("", 8)).pack(side="left", padx=8)

        bf = ttk.Frame(f); bf.pack(fill="x", pady=(10, 0))
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)

        def _save():
            self.app_config.update({k: v.get().strip() for k, v in cfg_vars.items()})
            self.app_config["request_headers"] = headers_txt.get("1.0", "end").strip()
            self.app_config["engineering_request_headers"] = eng_headers_txt.get("1.0", "end").strip()
            self.app_config["tailboard_request_headers"] = tb_headers_txt.get("1.0", "end").strip()
            self._save_app_config()
            dlg.destroy()

        ttk.Button(bf, text="Save", command=_save).pack(side="right")

    def _open_ctrl_room_desks(self):
        CtrlRoomDesksManagerDialog(self, self._app_db)

    # ── Title page tab ────────────────────────────────────────────

    def _build_title_tab(self, parent):
        f = ttk.Frame(parent, padding=10)
        f.pack(fill="both", expand=True)
        # Project name (shared with main toolbar)
        pf = ttk.LabelFrame(f, text="Project", padding=6)
        pf.pack(fill="x", pady=(0, 8))
        ttk.Label(pf, text="Project Name:").grid(row=0, column=0, sticky="e", padx=(0, 6))
        ttk.Entry(pf, textvariable=self.project_var, width=52).grid(
            row=0, column=1, sticky="ew")
        pf.columnconfigure(1, weight=1)
        # Notes
        nf = ttk.LabelFrame(f, text="Notes", padding=6)
        nf.pack(fill="x", pady=(0, 8))
        self.title_notes = scrolledtext.ScrolledText(nf, height=5, wrap="word", font=("", 9))
        self.title_notes.pack(fill="x")
        self.title_notes.insert("1.0", self.title_page.get("notes", ""))
        self.title_notes.bind("<<Modified>>",
            lambda e: (self._mark_dirty(), self.title_notes.edit_modified(False)))
        # CROWs
        cf = ttk.LabelFrame(f, text="CROWs (Outage Records)", padding=6)
        cf.pack(fill="both", expand=True)
        ctb = ttk.Frame(cf)
        ctb.pack(fill="x", pady=(0, 4))
        ttk.Button(ctb, text="+ Add CROW", command=self._add_crow).pack(side="left", padx=2)
        ttk.Button(ctb, text="Edit",       command=self._edit_crow).pack(side="left", padx=2)
        ttk.Button(ctb, text="Remove",     command=self._remove_crow).pack(side="left", padx=2)
        crow_fr = ttk.Frame(cf)
        crow_fr.pack(fill="both", expand=True)
        ccols = ("Outage Number", "URL", "Files")
        self.crow_tree = ttk.Treeview(crow_fr, columns=ccols, show="headings", height=6)
        self.crow_tree.heading("Outage Number", text="Outage Number")
        self.crow_tree.heading("URL",           text="URL")
        self.crow_tree.heading("Files",         text="Files")
        self.crow_tree.column("Outage Number", width=160, stretch=False)
        self.crow_tree.column("URL",           width=360)
        self.crow_tree.column("Files",         width=80,  stretch=False)
        cvsb = ttk.Scrollbar(crow_fr, orient="vertical", command=self.crow_tree.yview)
        self.crow_tree.configure(yscrollcommand=cvsb.set)
        self.crow_tree.pack(side="left", fill="both", expand=True)
        cvsb.pack(side="right", fill="y")
        self.crow_tree.bind("<Double-1>", lambda _: self._edit_crow())
        self.crow_tree.bind("<Control-Button-1>", self._on_crow_ctrl_click)
        ttk.Label(cf, text="Ctrl+click a row to open its URL",
                  foreground="grey", font=("", 7)).pack(anchor="w")
        self._refresh_crows()

    def _refresh_crows(self):
        for iid in self.crow_tree.get_children():
            self.crow_tree.delete(iid)
        for crow in self.title_page.get("crows", []):
            n = len(crow.get("files", []))
            file_str = f"{n} file(s)" if n else ""
            self.crow_tree.insert("", "end", values=(
                crow.get("outage_number", ""), crow.get("url", ""), file_str))
        self._mark_dirty()

    def _add_crow(self):
        dlg = CrowDialog(self, base_url=self.app_config.get("base_crow_url", ""),
                         project_folder=self.project_folder or "")
        if dlg.result:
            self.title_page.setdefault("crows", []).append(dlg.result)
            self._refresh_crows()

    def _edit_crow(self):
        sel = self.crow_tree.selection()
        if not sel:
            return
        idx = self.crow_tree.index(sel[0])
        dlg = CrowDialog(self, existing=self.title_page.get("crows", [])[idx],
                         project_folder=self.project_folder or "")
        if dlg.result:
            self.title_page["crows"][idx] = dlg.result
            self._refresh_crows()

    def _remove_crow(self):
        sel = self.crow_tree.selection()
        if not sel:
            return
        self.title_page.get("crows", []).pop(self.crow_tree.index(sel[0]))
        self._refresh_crows()

    def _on_crow_ctrl_click(self, event):
        row_id = self.crow_tree.identify_row(event.y)
        if not row_id:
            return
        idx = self.crow_tree.index(row_id)
        crows = self.title_page.get("crows", [])
        if idx < len(crows):
            url = crows[idx].get("url", "").strip()
            if url:
                webbrowser.open(url)

    def _get_settings(self):
        return dict(self.app_config)

    def _get_settings_with_pts(self):
        """Settings dict with _pts_dir_hint so JobDialog can locate PTS files."""
        s = dict(self.app_config)
        pts_dir = self._pts_dir()
        if pts_dir:
            s["_pts_dir_hint"] = pts_dir
        return s

    def _build_relay_settings_tab(self, parent):
        tb = ttk.Frame(parent, padding=(4, 4)); tb.pack(fill="x")
        ttk.Button(tb, text="+ Add",        command=self._add_relay).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",         command=self._edit_relay).pack(side="left", padx=2)
        ttk.Button(tb, text="Delete",       command=self._delete_relay).pack(side="left", padx=2)
        ttk.Button(tb, text="⬇ Download All", command=self._download_relay_settings).pack(side="left", padx=(10,2))
        ttk.Button(tb, text="🖨 Print Selected", command=self._print_selected_relay).pack(side="left", padx=2)
        ttk.Button(tb, text="🖨 Print All",      command=self._print_all_relay).pack(side="left", padx=2)
        ttk.Label(tb, text="Relay protection settings records.  Ctrl+click a row to open its URL.",
                  foreground="grey").pack(side="left", padx=8)

        frame = ttk.Frame(parent); frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        cols = ("Device ID", "Title", "Rev", "Engineer", "Contact", "URL", "WO Device")
        self.relay_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.relay_tree.heading("Device ID", text="Device ID")
        self.relay_tree.heading("Title",     text="Title")
        self.relay_tree.heading("Rev",       text="Rev")
        self.relay_tree.heading("Engineer",  text="Engineer")
        self.relay_tree.heading("Contact",   text="Contact")
        self.relay_tree.heading("URL",       text="URL")
        self.relay_tree.heading("WO Device", text="WO Device")
        self.relay_tree.column("Device ID", width=100, stretch=False)
        self.relay_tree.column("Title",     width=150, stretch=False)
        self.relay_tree.column("Rev",       width=55,  stretch=False)
        self.relay_tree.column("Engineer",  width=120, stretch=False)
        self.relay_tree.column("Contact",   width=140, stretch=False)
        self.relay_tree.column("URL",       width=260)
        self.relay_tree.column("WO Device", width=100, stretch=False)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.relay_tree.yview)
        self.relay_tree.configure(yscrollcommand=vsb.set)
        self.relay_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.relay_tree.bind("<Double-1>",          lambda _: self._edit_relay())
        self.relay_tree.bind("<Control-Button-1>",  self._on_relay_ctrl_click)

    # ── Maintenance Standards tab ─────────────────────────────────

    def _build_maintenance_tab(self, parent):
        tb = ttk.Frame(parent, padding=(4, 4)); tb.pack(fill="x")
        ttk.Button(tb, text="+ Add",           command=self._add_maintenance).pack(side="left", padx=2)
        ttk.Button(tb, text="📚 From Library",  command=self._add_maintenance_from_library).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",            command=self._edit_maintenance).pack(side="left", padx=2)
        ttk.Button(tb, text="Delete",          command=self._delete_maintenance).pack(side="left", padx=2)
        ttk.Button(tb, text="⬇ Download All",  command=self._download_maintenance).pack(side="left", padx=(10, 2))
        ttk.Button(tb, text="🖨 Print Selected", command=self._print_selected_maintenance).pack(side="left", padx=2)
        ttk.Button(tb, text="🖨 Print All",      command=self._print_all_maintenance).pack(side="left", padx=2)
        ttk.Label(tb,
                  text="Maintenance standards records.  Ctrl+click a row to open its URL.",
                  foreground="grey").pack(side="left", padx=8)

        frame = ttk.Frame(parent); frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        cols = ("Standard ID", "Title", "Rev", "URL (Telecom)", "URL (Transmission)", "Notes")
        self.maint_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.maint_tree.heading("Standard ID",        text="Standard ID")
        self.maint_tree.heading("Title",              text="Title")
        self.maint_tree.heading("Rev",                text="Rev")
        self.maint_tree.heading("URL (Telecom)",      text="URL (Telecom)")
        self.maint_tree.heading("URL (Transmission)", text="URL (Transmission)")
        self.maint_tree.heading("Notes",              text="Notes")
        self.maint_tree.column("Standard ID",        width=120, stretch=False)
        self.maint_tree.column("Title",              width=180, stretch=False)
        self.maint_tree.column("Rev",                width=55,  stretch=False)
        self.maint_tree.column("URL (Telecom)",      width=220)
        self.maint_tree.column("URL (Transmission)", width=220)
        self.maint_tree.column("Notes",              width=180)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.maint_tree.yview)
        self.maint_tree.configure(yscrollcommand=vsb.set)
        self.maint_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.maint_tree.bind("<Double-1>",         lambda _: self._edit_maintenance())
        self.maint_tree.bind("<Control-Button-1>", self._on_maint_ctrl_click)

    def _refresh_maintenance_list(self):
        for iid in self.maint_tree.get_children(): self.maint_tree.delete(iid)
        for sid, info in sorted(self.maintenance_standards_registry.items()):
            self.maint_tree.insert("", "end", iid=sid, values=(
                sid,
                info.get("title",            ""),
                info.get("revision",         ""),
                info.get("url_telecom",      ""),
                info.get("url_transmission", ""),
                info.get("notes",            ""),
            ))
        self._mark_dirty()

    def _add_maintenance(self):
        dlg = MaintenanceStandardDialog(
            self,
            base_url_telecom=self.app_config.get("base_maintenance_telecom_url", ""),
            base_url_transmission=self.app_config.get("base_maintenance_transmission_url", ""),
        )
        if dlg.result:
            sid = dlg.result["standard_id"]
            self.maintenance_standards_registry[sid] = {
                k: v for k, v in dlg.result.items() if k != "standard_id"}
            self._remember_standard("maintenance", sid,
                                    self.maintenance_standards_registry[sid])
            self._refresh_maintenance_list()

    def _add_maintenance_from_library(self):
        lib = self._standards_library("maintenance")
        if not lib:
            messagebox.showinfo(
                "Library Empty",
                "No maintenance standards remembered yet.\n"
                "Standards are added to the library automatically as you "
                "add them to projects.")
            return
        dlg = StandardsLibraryDialog(self, "maintenance", "Maintenance Standards",
                                     lib, self.maintenance_standards_registry.keys())
        if dlg.result:
            self.maintenance_standards_registry.update(dlg.result)
            self._refresh_maintenance_list()
            self.status_var.set(f"Added {len(dlg.result)} standard(s) from library")

    def _edit_maintenance(self):
        sel = self.maint_tree.selection()
        if not sel: messagebox.showinfo("Select", "Please select a standard to edit."); return
        sid = sel[0]; info = self.maintenance_standards_registry.get(sid, {})
        dlg = MaintenanceStandardDialog(
            self,
            existing={"standard_id": sid, **info},
            base_url_telecom=self.app_config.get("base_maintenance_telecom_url", ""),
            base_url_transmission=self.app_config.get("base_maintenance_transmission_url", ""),
        )
        if dlg.result:
            old_id = sid; new_id = dlg.result["standard_id"]
            if old_id != new_id and old_id in self.maintenance_standards_registry:
                del self.maintenance_standards_registry[old_id]
            self.maintenance_standards_registry[new_id] = {
                k: v for k, v in dlg.result.items() if k != "standard_id"}
            self._remember_standard("maintenance", new_id,
                                    self.maintenance_standards_registry[new_id])
            self._refresh_maintenance_list()

    def _delete_maintenance(self):
        sel = self.maint_tree.selection()
        if not sel: messagebox.showinfo("Select", "Please select a standard to delete."); return
        sid = sel[0]
        if messagebox.askyesno("Delete", f"Remove maintenance standard '{sid}'?"):
            self.maintenance_standards_registry.pop(sid, None); self._refresh_maintenance_list()

    def _on_maint_ctrl_click(self, event):
        row = self.maint_tree.identify_row(event.y)
        if not row: return
        info = self.maintenance_standards_registry.get(row, {})
        url = info.get("url_telecom", "").strip() or info.get("url_transmission", "").strip()
        if url: webbrowser.open(url)

    def _download_maintenance(self):
        if not self.project_folder:
            messagebox.showinfo("Save First",
                "Please save the project first so the Maintenance Standards folder location is known."); return
        targets = []
        for sid, info in self.maintenance_standards_registry.items():
            for url_key, suffix in (("url_telecom", "_telecom"), ("url_transmission", "_transmission")):
                url = info.get(url_key, "").strip()
                if url:
                    targets.append((f"{sid}{suffix}", url))
        if not targets:
            messagebox.showinfo("No URLs", "No maintenance standard URLs are set."); return
        self._download_with_progress(
            "Downloading Maintenance Standards",
            targets,
            os.path.join(self.project_folder, "Maintenance Standards"),
        )

    # ── Engineering Standards tab ─────────────────────────────────

    def _build_engineering_tab(self, parent):
        tb = ttk.Frame(parent, padding=(4, 4)); tb.pack(fill="x")
        ttk.Button(tb, text="+ Add",           command=self._add_engineering).pack(side="left", padx=2)
        ttk.Button(tb, text="🔍 Browse",        command=self._browse_engineering).pack(side="left", padx=2)
        ttk.Button(tb, text="📚 From Library",  command=self._add_engineering_from_library).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",            command=self._edit_engineering).pack(side="left", padx=2)
        ttk.Button(tb, text="Delete",          command=self._delete_engineering).pack(side="left", padx=2)
        ttk.Button(tb, text="⬇ Download All",  command=self._download_engineering).pack(side="left", padx=(10, 2))
        ttk.Button(tb, text="🖨 Print Selected", command=self._print_selected_engineering).pack(side="left", padx=2)
        ttk.Button(tb, text="🖨 Print All",      command=self._print_all_engineering).pack(side="left", padx=2)
        ttk.Label(tb,
                  text="Engineering standards records.  Ctrl+click a row to open its URL.",
                  foreground="grey").pack(side="left", padx=8)

        frame = ttk.Frame(parent); frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        cols = ("Standard ID", "Title", "Rev", "Type", "URL", "Notes")
        self.eng_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.eng_tree.heading("Standard ID", text="Standard ID")
        self.eng_tree.heading("Title",       text="Title")
        self.eng_tree.heading("Rev",         text="Rev")
        self.eng_tree.heading("Type",        text="Type")
        self.eng_tree.heading("URL",         text="URL")
        self.eng_tree.heading("Notes",       text="Notes")
        self.eng_tree.column("Standard ID", width=120, stretch=False)
        self.eng_tree.column("Title",       width=180, stretch=False)
        self.eng_tree.column("Rev",         width=55,  stretch=False)
        self.eng_tree.column("Type",        width=100, stretch=False)
        self.eng_tree.column("URL",         width=320)
        self.eng_tree.column("Notes",       width=180)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.eng_tree.yview)
        self.eng_tree.configure(yscrollcommand=vsb.set)
        self.eng_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.eng_tree.bind("<Double-1>",         lambda _: self._edit_engineering())
        self.eng_tree.bind("<Control-Button-1>", self._on_eng_ctrl_click)

    def _refresh_engineering_list(self):
        self.eng_tree.tag_configure("pts", background="#dbeafe")
        for iid in self.eng_tree.get_children(): self.eng_tree.delete(iid)
        for sid, info in sorted(self.engineering_standards_registry.items()):
            tags = ("pts",) if info.get("pts_source") else ()
            self.eng_tree.insert("", "end", iid=sid, tags=tags, values=(
                sid,
                info.get("title",         ""),
                info.get("revision",      ""),
                info.get("standard_type", ""),
                info.get("url",           ""),
                info.get("notes",         ""),
            ))
        self._mark_dirty()

    def _add_engineering(self):
        dlg = EngineeringStandardDialog(self)
        if dlg.result:
            sid = dlg.result["standard_id"]
            self.engineering_standards_registry[sid] = {
                "title":         dlg.result.get("title", ""),
                "revision":      dlg.result.get("revision", ""),
                "standard_type": dlg.result.get("standard_type", ""),
                "url":           dlg.result.get("url", ""),
                "notes":         dlg.result.get("notes", ""),
            }
            self._remember_standard("engineering", sid,
                                    self.engineering_standards_registry[sid])
            self._refresh_engineering_list()

    def _add_engineering_from_library(self):
        lib = self._standards_library("engineering")
        if not lib:
            messagebox.showinfo(
                "Library Empty",
                "No engineering standards remembered yet.\n"
                "Standards are added to the library automatically as you "
                "add them to projects.")
            return
        dlg = StandardsLibraryDialog(self, "engineering", "Engineering Standards",
                                     lib, self.engineering_standards_registry.keys())
        if dlg.result:
            self.engineering_standards_registry.update(dlg.result)
            self._refresh_engineering_list()
            self.status_var.set(f"Added {len(dlg.result)} standard(s) from library")

    def _build_eng_client(self):
        """Build an EngineeringStandardsClient from current app config."""
        if not _ENG_STD_AVAILABLE:
            messagebox.showerror("Unavailable",
                "The engineering_standards package could not be imported.")
            return None
        api_url = (self.app_config.get("engineering_api_url", "")
                   or self.app_config.get("engineering_url", "")).strip()
        if not api_url:
            messagebox.showwarning("Setup Required",
                "Fill in the Engineering API URL in File → Software Settings first.")
            return None
        # Strip any trailing /sections or /series path (user may paste the full endpoint URL)
        import re as _re
        api_url = _re.sub(r'/(sections|series)([?/].*)?$', '', api_url).rstrip('/')
        headers = _parse_request_headers_raw(
            self.app_config.get("engineering_request_headers", "")
            or self.app_config.get("request_headers", "")
        )
        if not hasattr(self, "_eng_cache"):
            try:
                ttl = float(self.app_config.get("engineering_cache_refresh_hours", 4))
            except (TypeError, ValueError):
                ttl = 4.0
            self._eng_cache = EngineeringStandardsCache(ttl_hours=max(0.25, ttl))
            self._eng_cache_load_from_db()
        return EngineeringStandardsClient(
            base_url=api_url,
            headers=headers,
            cache=self._eng_cache,
        )

    def _browse_engineering(self):
        client = self._build_eng_client()
        if client is None:
            return
        dlg = _EngineeringBrowseDialog(self, client,
                                       on_loaded=self._eng_cache_persist_all)
        if dlg.result:
            added = 0
            for std in dlg.result:
                sid = std.standard_id
                if sid:
                    self.engineering_standards_registry[sid] = {
                        "title":         std.description,
                        "revision":      str(std.major_version) if std.major_version else "",
                        "standard_type": "",
                        "url":           std.url,
                        "notes":         "",
                    }
                    self._remember_standard("engineering", sid,
                                            self.engineering_standards_registry[sid])
                    added += 1
            if added:
                self._refresh_engineering_list()
                self._mark_dirty()
                self.status_var.set(f"Added {added} engineering standard(s) from browse")

    def _edit_engineering(self):
        sel = self.eng_tree.selection()
        if not sel: messagebox.showinfo("Select", "Please select a standard to edit."); return
        sid = sel[0]; info = self.engineering_standards_registry.get(sid, {})
        dlg = EngineeringStandardDialog(self, existing={"standard_id": sid, **info})
        if dlg.result:
            old_id = sid; new_id = dlg.result["standard_id"]
            if old_id != new_id and old_id in self.engineering_standards_registry:
                del self.engineering_standards_registry[old_id]
            self.engineering_standards_registry[new_id] = {
                "title":         dlg.result.get("title", ""),
                "revision":      dlg.result.get("revision", ""),
                "standard_type": dlg.result.get("standard_type", ""),
                "url":           dlg.result.get("url", ""),
                "notes":         dlg.result.get("notes", ""),
            }
            self._remember_standard("engineering", new_id,
                                    self.engineering_standards_registry[new_id])
            self._refresh_engineering_list()

    def _delete_engineering(self):
        sel = self.eng_tree.selection()
        if not sel: messagebox.showinfo("Select", "Please select a standard to delete."); return
        sid = sel[0]
        if messagebox.askyesno("Delete", f"Remove engineering standard '{sid}'?"):
            self.engineering_standards_registry.pop(sid, None); self._refresh_engineering_list()

    def _on_eng_ctrl_click(self, event):
        row = self.eng_tree.identify_row(event.y)
        if not row: return
        info = self.engineering_standards_registry.get(row, {})
        url = info.get("url", "").strip()
        if url: webbrowser.open(url)

    def _download_engineering(self):
        if not self.project_folder:
            messagebox.showinfo("Save First",
                "Please save the project first so the Engineering Standards folder location is known."); return
        targets = []
        for sid, info in self.engineering_standards_registry.items():
            url = info.get("url", "").strip()
            if url:
                targets.append((sid, url))
        if not targets:
            messagebox.showinfo("No URLs", "No engineering standard URLs are set."); return

        dest_dir = os.path.join(self.project_folder, "Engineering Standards")
        self._download_with_progress(
            "Downloading Engineering Standards",
            targets,
            dest_dir,
            extra_headers=self._build_engineering_headers(),
        )

    def _build_engineering_headers(self):
        """Return extra HTTP headers for engineering downloads.

        Uses engineering_request_headers if set; falls back to master headers.
        """
        eng_raw = self.app_config.get("engineering_request_headers", "").strip()
        if eng_raw:
            return _parse_request_headers_raw(eng_raw)
        return self._parse_request_headers()

    # ── PTS (Protection Test Sheet) tab ───────────────────────────

    def _pts_dir(self):
        """Path to the project PTS folder, or None if no project is open."""
        return os.path.join(self.project_folder, "PTS") if self.project_folder else None

    def _build_pts_tab(self, parent):
        # Toolbar
        tb = ttk.Frame(parent, padding=(4, 4, 4, 2)); tb.pack(fill="x")
        ttk.Button(tb, text="⬆ Upload PTS",
                   command=self._upload_pts).pack(side="left")
        ttk.Button(tb, text="↺ Re-import Standards",
                   command=self._pts_reimport_selected).pack(side="left", padx=(6, 0))
        ttk.Button(tb, text="👁 Open File",
                   command=self._pts_open_selected).pack(side="left", padx=(6, 0))
        ttk.Button(tb, text="⊞ Open Folder",
                   command=lambda: self._reveal_project_subfolder("PTS")).pack(side="left", padx=(6, 0))
        ttk.Button(tb, text="🗂 Old Revisions",
                   command=lambda: self._reveal_project_subfolder("PTS", "Archive")).pack(side="left", padx=(6, 0))
        ttk.Button(tb, text="✕ Remove",
                   command=self._pts_remove_selected).pack(side="left", padx=(6, 0))
        ttk.Label(tb,
                  text="  Upload .docx / .doc Protection Test Sheets.  "
                       "Standards are extracted and optionally imported to Engineering Standards.",
                  foreground="grey", font=("", 8)).pack(side="left", padx=8)

        # Vertical split: file list (top) / extracted-standards detail (bottom)
        pw = ttk.PanedWindow(parent, orient="vertical")
        pw.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # ── Top pane: PTS file list ──────────────────────────────
        top_f = ttk.Frame(pw); pw.add(top_f, weight=1)
        top_lf = ttk.LabelFrame(top_f, text="PTS Files", padding=4)
        top_lf.pack(fill="both", expand=True)
        ff = ttk.Frame(top_lf); ff.pack(fill="both", expand=True)
        fcols = ("Filename", "Uploaded", "Standards")
        self._pts_file_tree = ttk.Treeview(ff, columns=fcols,
                                           show="headings", height=5)
        self._pts_file_tree.heading("Filename",  text="Filename")
        self._pts_file_tree.heading("Uploaded",  text="Uploaded")
        self._pts_file_tree.heading("Standards", text="Standards")
        self._pts_file_tree.column("Filename",  width=380)
        self._pts_file_tree.column("Uploaded",  width=150, stretch=False)
        self._pts_file_tree.column("Standards", width=90,  stretch=False,
                                   anchor="center")
        fvsb = ttk.Scrollbar(ff, orient="vertical",
                              command=self._pts_file_tree.yview)
        self._pts_file_tree.configure(yscrollcommand=fvsb.set)
        self._pts_file_tree.pack(side="left", fill="both", expand=True)
        fvsb.pack(side="right", fill="y")
        self._pts_file_tree.bind("<<TreeviewSelect>>", self._pts_on_file_select)

        # ── Bottom pane: test steps detail ───────────────────────
        bot_f = ttk.Frame(pw); pw.add(bot_f, weight=2)
        self._pts_results_lf = ttk.LabelFrame(
            bot_f, text="PTS Tests — select a PTS file above", padding=4)
        self._pts_results_lf.pack(fill="both", expand=True)

        # Results toolbar
        rtb = ttk.Frame(self._pts_results_lf); rtb.pack(fill="x", pady=(0, 4))
        ttk.Button(rtb, text="✓ Mark Complete",
                   command=self._pts_complete_selected).pack(side="left")
        ttk.Button(rtb, text="✗ Clear",
                   command=self._pts_clear_completion_selected).pack(side="left", padx=(4, 0))
        ttk.Button(rtb, text="⟳ Write to Document",
                   command=self._pts_write_back_selected).pack(side="left", padx=(12, 0))

        rf = ttk.Frame(self._pts_results_lf); rf.pack(fill="both", expand=True)
        rcols = ("Section", "System", "Standards", "Status", "Tested By", "Date")
        self._pts_results_tree = ttk.Treeview(rf, columns=rcols,
                                              show="headings", height=10)
        self._pts_results_tree.heading("Section",   text="Section")
        self._pts_results_tree.heading("System",    text="System")
        self._pts_results_tree.heading("Standards", text="Standards")
        self._pts_results_tree.heading("Status",    text="✓")
        self._pts_results_tree.heading("Tested By", text="Tested By")
        self._pts_results_tree.heading("Date",      text="Date")
        self._pts_results_tree.column("Section",   width=200)
        self._pts_results_tree.column("System",    width=170)
        self._pts_results_tree.column("Standards", width=180)
        self._pts_results_tree.column("Status",    width=30,  stretch=False, anchor="center")
        self._pts_results_tree.column("Tested By", width=80,  stretch=False)
        self._pts_results_tree.column("Date",      width=90,  stretch=False)
        rvsb = ttk.Scrollbar(rf, orient="vertical",
                              command=self._pts_results_tree.yview)
        self._pts_results_tree.configure(yscrollcommand=rvsb.set)
        self._pts_results_tree.pack(side="left", fill="both", expand=True)
        rvsb.pack(side="right", fill="y")
        self._pts_results_tree.bind("<Double-1>", lambda e: self._pts_complete_selected())
        self._pts_entries_cache = {}   # row_key → entry dict, populated by _refresh_pts_results

    def _refresh_pts_tab(self):
        """Reload both panes of the PTS tab (no-op if tab not yet built)."""
        if hasattr(self, "_pts_file_tree"):
            self._refresh_pts_file_list()
            self._pts_clear_results()

    def _refresh_pts_file_list(self):
        for iid in self._pts_file_tree.get_children():
            self._pts_file_tree.delete(iid)
        for fname, meta in sorted(self.pts_files.items()):
            ts = meta.get("uploaded_at", "")
            try:
                ts = datetime.fromisoformat(ts).strftime("%Y-%m-%d  %H:%M")
            except Exception:
                pass
            n = len(meta.get("standard_ids", []))
            self._pts_file_tree.insert("", "end", iid=fname, values=(
                fname, ts, n if n else "—"
            ))

    def _pts_clear_results(self):
        if hasattr(self, "_pts_results_tree"):
            for iid in self._pts_results_tree.get_children():
                self._pts_results_tree.delete(iid)
        if hasattr(self, "_pts_entries_cache"):
            self._pts_entries_cache = {}
        if hasattr(self, "_pts_results_lf"):
            self._pts_results_lf.configure(
                text="PTS Tests — select a PTS file above")

    def _pts_active_filename(self):
        """Return the filename currently selected in the PTS file list, or None."""
        sel = self._pts_file_tree.selection()
        return sel[0] if sel else None

    def _pts_complete_selected(self):
        """Open the completion dialog for the selected result row."""
        fname = self._pts_active_filename()
        if not fname:
            return
        sel = self._pts_results_tree.selection()
        if not sel:
            return
        key   = sel[0]
        entry = getattr(self, "_pts_entries_cache", {}).get(key)
        if not entry:
            messagebox.showinfo("Re-parse Required",
                "Select the file in the list above to load entry details.",
                parent=self)
            return
        existing = self.pts_files.get(fname, {}).get("completions", {}).get(key)
        dlg = _PTSCompletionDialog(self, entry, existing=existing)
        if dlg.result is None:
            return
        self._pts_save_completion(fname, key, dlg.result or None)

    def _pts_clear_completion_selected(self):
        """Remove the completion record for the selected result row."""
        fname = self._pts_active_filename()
        if not fname:
            return
        sel = self._pts_results_tree.selection()
        if not sel:
            return
        key = sel[0]
        if not self.pts_files.get(fname, {}).get("completions", {}).get(key):
            return
        self._pts_save_completion(fname, key, None)

    def _pts_save_completion(self, fname, key, data):
        """Save or remove a completion record, then refresh the display.

        data : {tested_by, date, comment}  → save
               None                        → remove
        """
        meta = self.pts_files.setdefault(fname, {})
        comps = meta.setdefault("completions", {})
        if data:
            comps[key] = data
        else:
            comps.pop(key, None)
        self._mark_dirty()
        self._refresh_pts_results(fname)

    def _pts_write_back_selected(self):
        """Write all completions for the selected PTS file back into the .docx."""
        fname = self._pts_active_filename()
        if not fname:
            messagebox.showinfo("No File Selected",
                "Select a PTS file in the list above.", parent=self)
            return
        pts_dir = self._pts_dir()
        if not pts_dir:
            return
        fpath = os.path.join(pts_dir, fname)
        if not os.path.isfile(fpath):
            messagebox.showwarning("File Not Found",
                f"{fname} is not on disk. Re-upload the file first.", parent=self)
            return
        comps = self.pts_files.get(fname, {}).get("completions", {})
        if not comps:
            messagebox.showinfo("Nothing to Write",
                "No completions recorded for this file yet.", parent=self)
            return
        _archive_revision(pts_dir, fname)
        ok, err = _pts_write_completions(fpath, comps)
        if ok:
            messagebox.showinfo("Done",
                f"Completion data written to {fname}.\n"
                "The previous revision has been archived.", parent=self)
        else:
            messagebox.showerror("Write Failed", err or "Unknown error", parent=self)

    def _pts_on_file_select(self, event=None):
        sel = self._pts_file_tree.selection()
        if not sel:
            self._pts_clear_results()
            return
        self._refresh_pts_results(sel[0])

    def _refresh_pts_results(self, filename):
        for iid in self._pts_results_tree.get_children():
            self._pts_results_tree.delete(iid)
        self._pts_entries_cache = {}
        self._pts_results_tree.tag_configure("done", background="#d5f5e3")

        pts_dir = self._pts_dir()
        fpath = os.path.join(pts_dir, filename) if pts_dir else None
        completions = self.pts_files.get(filename, {}).get("completions", {})

        # File not on disk — fall back to stored IDs without section context
        if not (fpath and os.path.isfile(fpath)):
            ids = self.pts_files.get(filename, {}).get("standard_ids", [])
            for sid in ids:
                self._pts_results_tree.insert("", "end",
                    values=("", "", sid, "", "", ""))
            self._pts_results_lf.configure(
                text=f"PTS Tests — {filename}  ({len(ids)} IDs stored, file not on disk)")
            return

        if not _PTS_PARSER_AVAILABLE:
            self._pts_results_lf.configure(
                text=f"PTS Tests — {filename}  (pts_parser not available)")
            return

        try:
            result = _parse_pts(fpath)
        except Exception as exc:
            messagebox.showerror("Parse Error", str(exc), parent=self)
            return

        if result.get("error"):
            messagebox.showerror("Parse Error", result["error"], parent=self)
            return

        for entry in result["entries"]:
            key  = _pts_row_key(entry)
            comp = completions.get(key)
            self._pts_entries_cache[key] = entry

            sect = entry.get("section", "")
            subs = entry.get("subsection", "")
            sys_ = entry.get("system", "")
            display_sect = f"{sect}  ›  {subs}" if sect and subs else (sect or subs)
            stds = ",  ".join(entry.get("standard_ids", []))

            if comp:
                status    = "✓"
                tested_by = comp.get("tested_by", "")
                date      = comp.get("date", "")
                tags      = ("done",)
            else:
                status = "○"; tested_by = ""; date = ""; tags = ()

            self._pts_results_tree.insert(
                "", "end", iid=key, tags=tags,
                values=(display_sect, sys_, stds, status, tested_by, date))

        n_total = len(result["entries"])
        n_done  = sum(1 for e in result["entries"]
                      if _pts_row_key(e) in completions)
        self._pts_results_lf.configure(
            text=f"PTS Tests — {filename}  ({n_done}/{n_total} completed)")

    def _upload_pts(self):
        if not self.project_folder:
            messagebox.showinfo("Save Project First",
                "Save the project before uploading PTS files.", parent=self)
            return
        paths = filedialog.askopenfilenames(
            title="Upload Protection Test Sheet(s)",
            filetypes=[("Word Documents", "*.docx *.doc"), ("All files", "*.*")])
        if not paths:
            return

        pts_dir = self._pts_dir()
        os.makedirs(pts_dir, exist_ok=True)

        uploaded = []   # (filename, parse_result)
        for src in paths:
            base = os.path.basename(src)
            _archive_revision(pts_dir, base)
            try:
                shutil.copy2(src, os.path.join(pts_dir, base))
            except Exception as exc:
                messagebox.showwarning("Copy Failed",
                    f"Could not copy {base}:\n{exc}", parent=self)
                continue

            dest_path = os.path.join(pts_dir, base)
            if _PTS_PARSER_AVAILABLE:
                try:
                    result = _parse_pts(dest_path)
                except Exception as exc:
                    result = {"entries": [], "all_standard_ids": [], "error": str(exc)}
            else:
                result = {"entries": [], "all_standard_ids": [],
                          "error": "pts_parser module not found."}

            if result.get("error"):
                messagebox.showwarning(
                    "Parse Warning",
                    f"{base}:\n{result['error']}\n\n"
                    "The file was uploaded but standards could not be extracted.",
                    parent=self)

            self.pts_files[base] = {
                "uploaded_at":  datetime.now().isoformat(timespec="seconds"),
                "standard_ids": result["all_standard_ids"],
            }
            uploaded.append((base, result))

        if not uploaded:
            return

        self._refresh_pts_file_list()
        self._mark_dirty()

        # Select the first uploaded file so results pane populates
        if uploaded:
            first_fname = uploaded[0][0]
            if first_fname in [self._pts_file_tree.item(i)["values"][0]
                                for i in self._pts_file_tree.get_children()]:
                self._pts_file_tree.selection_set(first_fname)
                self._refresh_pts_results(first_fname)

        # Offer to import standards now
        all_new_ids: list = []
        for _, r in uploaded:
            for sid in r["all_standard_ids"]:
                if sid not in all_new_ids:
                    all_new_ids.append(sid)

        if all_new_ids:
            n_files = len(uploaded)
            msg = (f"Extracted {len(all_new_ids)} engineering standard ID(s) "
                   f"from {n_files} PTS file(s).\n\n"
                   "Import them to the Engineering Standards registry now?")
            if messagebox.askyesno("Import Standards", msg, parent=self):
                for fname, result in uploaded:
                    if result["all_standard_ids"]:
                        self._pts_do_import(fname, result["all_standard_ids"])

    def _pts_open_selected(self):
        sel = self._pts_file_tree.selection()
        if not sel:
            messagebox.showinfo("Select", "Select a PTS file first.", parent=self)
            return
        d = self._pts_dir()
        path = os.path.join(d, sel[0]) if d else None
        if path and os.path.isfile(path):
            _open_file(path)
        else:
            messagebox.showinfo("Not Found",
                f"{sel[0]} is no longer in the PTS folder.", parent=self)

    def _pts_remove_selected(self):
        sel = self._pts_file_tree.selection()
        if not sel:
            messagebox.showinfo("Select", "Select a PTS file to remove.", parent=self)
            return
        fname = sel[0]
        if not messagebox.askyesno(
                "Remove",
                f"Remove  {fname}  from the PTS list?\n\n"
                "The file will be moved to Archive/.  Any Engineering Standards "
                "it added to this project will remain (with their PTS-source tag).",
                parent=self):
            return
        d = self._pts_dir()
        if d:
            _archive_revision(d, fname)
        self.pts_files.pop(fname, None)
        self._refresh_pts_file_list()
        self._pts_clear_results()
        self._mark_dirty()

    def _pts_reimport_selected(self):
        sel = self._pts_file_tree.selection()
        if not sel:
            messagebox.showinfo("Select",
                "Select a PTS file to re-import standards from.", parent=self)
            return
        fname = sel[0]
        meta  = self.pts_files.get(fname, {})
        std_ids = meta.get("standard_ids", [])

        # If we have no IDs stored, try to re-parse the file
        if not std_ids:
            d = self._pts_dir()
            fpath = os.path.join(d, fname) if d else None
            if not (fpath and os.path.isfile(fpath)):
                messagebox.showinfo("File Not Found",
                    f"{fname} is not in the PTS folder and has no stored IDs.",
                    parent=self)
                return
            if not _PTS_PARSER_AVAILABLE:
                messagebox.showerror("Unavailable",
                    "pts_parser module is not available.", parent=self)
                return
            try:
                result  = _parse_pts(fpath)
                std_ids = result["all_standard_ids"]
                self.pts_files[fname]["standard_ids"] = std_ids
                self._refresh_pts_file_list()
                self._refresh_pts_results(fname)
                self._mark_dirty()
            except Exception as exc:
                messagebox.showerror("Parse Error", str(exc), parent=self)
                return

        if not std_ids:
            messagebox.showinfo("No Standards",
                f"No engineering standard IDs were found in {fname}.", parent=self)
            return

        self._pts_do_import(fname, std_ids)

    def _pts_do_import(self, filename, standard_ids):
        """Look up standard IDs via the engineering API then open the import dialog."""
        if not standard_ids:
            return

        client = self._build_eng_client() if _ENG_STD_AVAILABLE else None

        if client is None:
            # No API — import with ID only (blank title/URL)
            results = [
                {'id': sid, 'std': None,
                 'in_registry': sid in self.engineering_standards_registry}
                for sid in standard_ids
            ]
            self._pts_show_import_dialog(filename, results)
            return

        # Show a lightweight progress window while fetching the catalogue
        prog = tk.Toplevel(self)
        prog.title("Looking Up Standards…")
        prog.resizable(False, False)
        prog.grab_set()
        prog_lbl = ttk.Label(prog,
                             text="Fetching engineering standards catalogue…",
                             padding=(20, 16))
        prog_lbl.pack()
        bar = ttk.Progressbar(prog, mode="indeterminate", length=320)
        bar.pack(padx=20, pady=(0, 20))
        bar.start(10)
        _center_window(prog)

        def _on_progress(done, total, series_title):
            self.after(0, lambda: prog_lbl.config(
                text=f"Fetching {done}/{total}: {series_title[:55]}"))

        def _on_done(all_stds):
            # Build a normalised-key lookup so PTS IDs ("ES 62-M0300") match
            # API IDs ("ES 62-M0300 R00") regardless of revision suffix or
            # whitespace variations.
            id_prefix = re.compile(r'^(ES\s+\d+[A-Z]*-[A-Z]\d{4,})', re.IGNORECASE)

            def _norm_es(s):
                """Base ES ID: strip revision suffix, collapse whitespace, uppercase."""
                s = re.sub(r'\s+R\d+\S*\s*$', '', s.strip(), flags=re.IGNORECASE)
                return ' '.join(s.split()).upper()

            lookup: dict = {}
            for std in all_stds:
                m = id_prefix.match(std.standard_id)
                if m:
                    key = _norm_es(m.group(1))
                    prev = lookup.get(key)
                    if prev is None or std.major_version > prev.major_version:
                        lookup[key] = std

            results = [
                {
                    'id':          sid,
                    'std':         lookup.get(_norm_es(sid)),
                    'in_registry': sid in self.engineering_standards_registry,
                }
                for sid in standard_ids
            ]

            def _finish():
                try:
                    prog.destroy()
                except Exception:
                    pass
                self._eng_cache_persist_all()
                self._pts_show_import_dialog(filename, results)

            self.after(0, _finish)

        def _on_error(exc):
            def _finish():
                try:
                    prog.destroy()
                except Exception:
                    pass
                messagebox.showwarning(
                    "API Unavailable",
                    f"Could not fetch engineering standards:\n{exc}\n\n"
                    "Standards will be imported with ID only (blank title/URL).",
                    parent=self)
                fallback = [
                    {'id': sid, 'std': None,
                     'in_registry': sid in self.engineering_standards_registry}
                    for sid in standard_ids
                ]
                self._pts_show_import_dialog(filename, fallback)

            self.after(0, _finish)

        client.fetch_all_async(on_done=_on_done, on_error=_on_error,
                               on_progress=_on_progress)

    def _pts_show_import_dialog(self, filename, lookup_results):
        """Open _PTSImportDialog and apply the user's selections to the registry."""
        dlg = _PTSImportDialog(self, filename, lookup_results)
        if not dlg.result:
            return

        added = tagged = 0
        for row in dlg.result:
            sid    = row['id']
            std    = row['std']
            in_reg = row['in_registry']

            if in_reg:
                # Add PTS-source tag to the existing entry without overwriting data
                entry   = self.engineering_standards_registry.get(sid, {})
                sources = list(entry.get("pts_source", []))
                if filename not in sources:
                    sources.append(filename)
                entry["pts_source"] = sources
                self.engineering_standards_registry[sid] = entry
                tagged += 1
            else:
                self.engineering_standards_registry[sid] = {
                    "title":         std.description        if std else "",
                    "revision":      (str(std.major_version)
                                      if (std and std.major_version) else ""),
                    "standard_type": "",
                    "url":           std.url                if std else "",
                    "notes":         "",
                    "pts_source":    [filename],
                }
                self._remember_standard(
                    "engineering", sid,
                    self.engineering_standards_registry[sid])
                added += 1

        if added or tagged:
            self._refresh_engineering_list()
            self._mark_dirty()
            parts = []
            if added:  parts.append(f"{added} standard(s) added")
            if tagged: parts.append(f"{tagged} existing standard(s) tagged as PTS source")
            self.status_var.set("PTS import: " + ", ".join(parts))

    # ── Print helpers ─────────────────────────────────────────────

    def _open_file_for_print(self, path):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            messagebox.showerror("Open Error", str(exc))

    def _print_files_in_folder(self, folder):
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("No Files", f"Folder not found: {folder}"); return
        files = [os.path.join(folder, f) for f in os.listdir(folder)
                 if os.path.isfile(os.path.join(folder, f))]
        if not files:
            messagebox.showinfo("No Files", "No files found in folder."); return
        for path in files:
            self._open_file_for_print(path)

    def _print_files_for_key(self, folder, key):
        if not folder or not os.path.isdir(folder):
            messagebox.showinfo("No Files", f"Folder not found: {folder}"); return
        files = [os.path.join(folder, f) for f in os.listdir(folder)
                 if os.path.isfile(os.path.join(folder, f)) and f.startswith(key)]
        if not files:
            messagebox.showinfo("No Files", f"No downloaded files found for '{key}'."); return
        for path in files:
            self._open_file_for_print(path)

    def _print_selected(self, tree, subfolder, label):
        sel = tree.selection()
        if not sel: messagebox.showinfo("Select", f"Please select a {label}."); return
        folder = os.path.join(self.project_folder, subfolder) if self.project_folder else None
        self._print_files_for_key(folder, sel[0])

    def _print_all(self, subfolder):
        folder = os.path.join(self.project_folder, subfolder) if self.project_folder else None
        self._print_files_in_folder(folder)

    def _print_selected_drawings(self):
        self._print_selected(self.drawings_tree, "Drawings", "drawing")

    def _print_all_drawings(self):
        self._print_all("Drawings")

    def _print_selected_relay(self):
        self._print_selected(self.relay_tree, "Relay Settings", "relay record")

    def _print_all_relay(self):
        self._print_all("Relay Settings")

    def _print_selected_maintenance(self):
        self._print_selected(self.maint_tree, "Maintenance Standards", "maintenance standard")

    def _print_all_maintenance(self):
        self._print_all("Maintenance Standards")

    def _print_selected_engineering(self):
        self._print_selected(self.eng_tree, "Engineering Standards", "engineering standard")

    def _print_all_engineering(self):
        self._print_all("Engineering Standards")

    # ── Mode switching ────────────────────────────────────────────

    def _update_mode_buttons(self, active):
        for val, (btn, act_bg) in self._mode_btns.items():
            if val == active:
                btn.configure(bg=act_bg, fg="white")
            else:
                btn.configure(bg="#1c2833", fg="#7f8c8d")

    def _set_mode(self, mode):
        # Two modes share the same window; only one frame is visible at a time.
        # Planner mode: full tabbed notebook for editing jobs, drawings, relays, CROWs.
        # Implementation mode: step-by-step checklist view with reference file viewer.
        # Switching to impl refreshes both the job list and the downloaded-file tabs so
        # the engineer always sees up-to-date information when moving to the field.
        self.mode_var.set(mode)
        self._update_mode_buttons(mode)
        if mode == "planner":
            self.impl_frame.pack_forget()
            self.planner_frame.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        else:
            self.planner_frame.pack_forget()
            self.impl_frame.pack(fill="both", expand=True, padx=6, pady=(0, 4))
            self._refresh_impl_list()
            self._refresh_file_tabs()

    # ──────────────────────────────────────────────────────────────────
    # Implementation mode
    # ──────────────────────────────────────────────────────────────────

    def _build_impl_view(self, parent):
        # ── Safety / Tailboard toolbar ─────────────────────────────
        tb_bar = tk.Frame(parent, bg="#1c3a5a")
        tb_bar.pack(fill="x", padx=4, pady=(4, 0))
        tk.Label(tb_bar, text="⚠  SAFETY", bg="#1c3a5a", fg="#f39c12",
                 font=("", 9, "bold"), padx=8, pady=6).pack(side="left")
        ttk.Button(tb_bar, text="Tailboard ▸",
                   command=self._goto_tailboard_step).pack(side="left", padx=(0, 10), pady=4)
        self._tb_status_var = tk.StringVar(value="")
        tk.Label(tb_bar, textvariable=self._tb_status_var, bg="#1c3a5a",
                 fg="#85c1e9", font=("", 8)).pack(side="left")

        pw_main = ttk.PanedWindow(parent, orient="horizontal")
        pw_main.pack(fill="both", expand=True, padx=4, pady=4)

        # ── Left: step list ────────────────────────────────────────
        left = ttk.Frame(pw_main); pw_main.add(left, weight=1)
        ttk.Label(left, text="Work Order Steps", font=("", 9, "bold")).pack(
            anchor="w", padx=4, pady=(4, 2))
        cols = ("Done", "Seq", "Type", "Description")
        self.impl_tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        self.impl_tree.heading("Done",        text="✓")
        self.impl_tree.heading("Seq",         text="#")
        self.impl_tree.heading("Type",        text="Type")
        self.impl_tree.heading("Description", text="Description")
        self.impl_tree.column("Done",        width=30,  stretch=False, anchor="center")
        self.impl_tree.column("Seq",         width=35,  stretch=False)
        self.impl_tree.column("Type",        width=105, stretch=False)
        self.impl_tree.column("Description", width=190)
        for t, fg in self.TYPE_FG.items():
            self.impl_tree.tag_configure(t, foreground=fg)
        _strike = _treeview_strike_font()
        self.impl_tree.tag_configure("COMPLETED", foreground="#aaaaaa", font=_strike)
        ivsb = ttk.Scrollbar(left, orient="vertical", command=self.impl_tree.yview)
        self.impl_tree.configure(yscrollcommand=ivsb.set)
        self.impl_tree.pack(side="left", fill="both", expand=True)
        ivsb.pack(side="right", fill="y")
        self.impl_tree.bind("<<TreeviewSelect>>", self._on_impl_select)
        self.impl_tree.bind("<Button-1>",          self._on_impl_tree_click)

        # ── Right: details (top) + file viewer (bottom) ────────────
        pw_right = ttk.PanedWindow(pw_main, orient="vertical")
        pw_main.add(pw_right, weight=3)

        details_f = ttk.LabelFrame(pw_right, text="Step Details", padding=4)
        pw_right.add(details_f, weight=2)
        # Regular step detail text — shown for all steps except TAILBOARD
        self.impl_preview = scrolledtext.ScrolledText(
            details_f, font=("Courier", 9), state="disabled", wrap="none")
        self.impl_preview.pack(fill="both", expand=True)
        # Custom tailboard panel — built but hidden; swapped in when TAILBOARD is selected
        self.impl_tb_frame = ttk.Frame(details_f)
        self._build_tailboard_panel(self.impl_tb_frame)
        # Safety Documents panel — same swap mechanism
        self.impl_safety_frame = ttk.Frame(details_f)
        self._build_safety_panel(self.impl_safety_frame)

        viewer_f = ttk.LabelFrame(pw_right, text="Reference Files", padding=4)
        pw_right.add(viewer_f, weight=3)

        vf_top = ttk.Frame(viewer_f); vf_top.pack(fill="x", pady=(0, 4))
        ttk.Button(vf_top, text="⟳ Refresh", command=self._refresh_file_tabs).pack(side="left")
        self._drw_filter_var = tk.StringVar(value="step")
        self._drw_filter_btn = ttk.Button(vf_top, text="Show All",
                                           command=self._toggle_drawing_filter)
        self._drw_filter_btn.pack(side="left", padx=(6, 0))
        ttk.Label(vf_top, text="  click to preview  ·  double-click to open",
                  foreground="grey", font=("", 8)).pack(side="left", padx=8)

        # Mirrored Bits warning strip — hidden until a step with MB enabled is selected
        self._mb_warn_frame = tk.Frame(viewer_f, bg="#7d3c00")
        tk.Label(self._mb_warn_frame,
                 text="⚠️",
                 bg="#7d3c00", fg="#fdebd0", font=("", 14),
                 padx=6, pady=3).pack(side="left")
        tk.Label(self._mb_warn_frame,
                 text="MIRRORED BITS ENABLED — verify MB isolation before proceeding",
                 bg="#7d3c00", fg="#fdebd0", font=("", 10, "bold"),
                 padx=4, pady=3).pack(side="left")
        self._mb_remote_lbl = tk.Label(self._mb_warn_frame, text="",
                                        bg="#7d3c00", fg="#fad7a0", font=("", 9))
        self._mb_remote_lbl.pack(side="left")

        self.file_nb = ttk.Notebook(viewer_f)
        self.file_nb.pack(fill="both", expand=True)

        # ── Drawings tab: file list (left) + PDF preview (right) ──
        drw_tab = ttk.Frame(self.file_nb)
        self.file_nb.add(drw_tab, text="  Drawings  ")

        pw_drw = ttk.PanedWindow(drw_tab, orient="horizontal")
        pw_drw.pack(fill="both", expand=True)

        drw_list_f = ttk.Frame(pw_drw)
        pw_drw.add(drw_list_f, weight=1)
        self.impl_drw_lb = tk.Listbox(drw_list_f, selectmode="browse",
                                       font=("Courier", 9), activestyle="none",
                                       relief="flat", borderwidth=0)
        drw_vsb = ttk.Scrollbar(drw_list_f, orient="vertical",   command=self.impl_drw_lb.yview)
        drw_hsb = ttk.Scrollbar(drw_list_f, orient="horizontal", command=self.impl_drw_lb.xview)
        self.impl_drw_lb.configure(yscrollcommand=drw_vsb.set, xscrollcommand=drw_hsb.set)
        drw_vsb.pack(side="right", fill="y")
        drw_hsb.pack(side="bottom", fill="x")
        self.impl_drw_lb.pack(fill="both", expand=True)
        self.impl_drw_lb.bind("<<ListboxSelect>>", self._on_drw_select)
        self.impl_drw_lb.bind("<Double-1>",        self._on_drw_double_click)
        self._impl_drw_paths: list = []   # parallel full-path list for listbox items

        # Preview pane
        prev_f = ttk.Frame(pw_drw)
        pw_drw.add(prev_f, weight=2)

        nav_f = ttk.Frame(prev_f); nav_f.pack(fill="x", pady=(0, 2))
        self._pdf_page     = [1]    # current page number
        self._pdf_total    = [0]    # total pages (0 = unknown/single)
        self._pdf_cur_path = [None] # path being previewed
        self._pdf_photo    = [None] # PhotoImage ref (prevents GC)
        self._pdf_prev_btn = ttk.Button(nav_f, text="◀", width=3,
                                         command=lambda: self._pdf_nav(-1))
        self._pdf_prev_btn.pack(side="left")
        self._pdf_page_lbl = tk.StringVar(value="")
        ttk.Label(nav_f, textvariable=self._pdf_page_lbl,
                  width=14, anchor="center").pack(side="left", padx=4)
        self._pdf_next_btn = ttk.Button(nav_f, text="▶", width=3,
                                         command=lambda: self._pdf_nav(+1))
        self._pdf_next_btn.pack(side="left")
        ttk.Button(nav_f, text="Open ↗",
                   command=self._open_previewed_file).pack(side="right")

        canvas_f = ttk.Frame(prev_f); canvas_f.pack(fill="both", expand=True)
        self._pdf_canvas = tk.Canvas(canvas_f, bg="#2c2c2c")
        c_vsb = ttk.Scrollbar(canvas_f, orient="vertical",   command=self._pdf_canvas.yview)
        c_hsb = ttk.Scrollbar(canvas_f, orient="horizontal", command=self._pdf_canvas.xview)
        self._pdf_canvas.configure(yscrollcommand=c_vsb.set, xscrollcommand=c_hsb.set)
        c_vsb.pack(side="right", fill="y")
        c_hsb.pack(side="bottom", fill="x")
        self._pdf_canvas.pack(fill="both", expand=True)
        self._pdf_canvas.create_text(10, 10, anchor="nw", fill="#888",
            text="Select a drawing to preview", font=("", 9), tags=("hint",))

        # ── Relay Settings tab ─────────────────────────────────────
        rly_tab = ttk.Frame(self.file_nb)
        self.file_nb.add(rly_tab, text="  Relay Settings  ")
        self._build_relay_impl_tab(rly_tab)

        # ── Maintenance Standards tab ───────────────────────────────
        maint_tab = ttk.Frame(self.file_nb)
        self.file_nb.add(maint_tab, text="  Maintenance Standards  ")
        self._build_standards_impl_tab(maint_tab, "impl_maint_lb", "Maintenance Standards")

        # ── Engineering Standards tab ───────────────────────────────
        eng_tab = ttk.Frame(self.file_nb)
        self.file_nb.add(eng_tab, text="  Engineering Standards  ")
        self._build_standards_impl_tab(eng_tab, "impl_eng_lb", "Engineering Standards")

    def _build_file_listbox(self, parent, attr, subfolder):
        lb = tk.Listbox(parent, selectmode="browse", font=("Courier", 9),
                        activestyle="none", relief="flat", borderwidth=0)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=vsb.set)
        lb.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        lb.bind("<Double-1>", lambda e, l=lb, s=subfolder: self._open_impl_file(l, s))
        setattr(self, attr, lb)

    def _build_relay_impl_tab(self, parent):
        """Relay Settings tab — file list (left) + formatted setting preview (right)."""
        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=4, pady=4)

        # File list
        list_f = ttk.Frame(pw)
        pw.add(list_f, weight=1)
        hint = ttk.Label(list_f,
                         text="click to preview  ·  double-click to open  "
                              "·  import via Add/Edit relay in Planning",
                         foreground="grey", font=("", 8))
        hint.pack(anchor="w", padx=2, pady=(0, 2))
        lb_f = ttk.Frame(list_f)
        lb_f.pack(fill="both", expand=True)
        lb = tk.Listbox(lb_f, selectmode="browse", font=("Courier", 9),
                        activestyle="none", relief="flat", borderwidth=0)
        vsb = ttk.Scrollbar(lb_f, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=vsb.set)
        lb.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        lb.bind("<<ListboxSelect>>", self._on_rly_select)
        lb.bind("<Double-1>",        self._on_rly_double_click)
        self.impl_rly_lb = lb

        # Formatted preview
        prev_f = ttk.Frame(pw)
        pw.add(prev_f, weight=3)
        self.impl_rly_preview = scrolledtext.ScrolledText(
            prev_f, font=("Courier", 9), state="disabled", wrap="none")
        self.impl_rly_preview.tag_configure("section",
            foreground="#1a5276", font=("Courier", 9, "bold"))
        self.impl_rly_preview.tag_configure("key",   foreground="#117a65")
        self.impl_rly_preview.tag_configure("value", foreground="#2c3e50")
        self.impl_rly_preview.tag_configure("info",  foreground="#7f8c8d", font=("Courier", 8))
        self.impl_rly_preview.pack(fill="both", expand=True)

    def _build_standards_impl_tab(self, parent, lb_attr, subfolder):
        """Simple file-list tab for Maintenance/Engineering Standards in impl view."""
        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=4, pady=4)

        list_f = ttk.Frame(pw)
        pw.add(list_f, weight=1)
        ttk.Label(list_f,
                  text="double-click to open  ·  click to preview",
                  foreground="grey", font=("", 8)).pack(anchor="w", padx=2, pady=(0, 2))
        lb_f = ttk.Frame(list_f); lb_f.pack(fill="both", expand=True)
        lb = tk.Listbox(lb_f, selectmode="browse", font=("Courier", 9),
                        activestyle="none", relief="flat", borderwidth=0)
        vsb = ttk.Scrollbar(lb_f, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=vsb.set)
        lb.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        lb.bind("<Double-1>",        lambda e, l=lb, s=subfolder: self._open_impl_file(l, s))
        lb.bind("<<ListboxSelect>>", lambda e, l=lb, s=subfolder: self._preview_std_file(l, s))
        setattr(self, lb_attr, lb)

        prev_f = ttk.Frame(pw)
        pw.add(prev_f, weight=3)
        attr_prev = lb_attr.replace("_lb", "_preview")
        tv = scrolledtext.ScrolledText(prev_f, font=("Courier", 9), state="disabled", wrap="none")
        tv.pack(fill="both", expand=True)
        setattr(self, attr_prev, tv)

    def _preview_std_file(self, lb, subfolder):
        """Show plain text content of selected standards file in its preview pane."""
        sel = lb.curselection()
        if not sel: return
        fname = lb.get(sel[0])
        if not self.project_folder or fname.startswith("("): return
        path = os.path.join(self.project_folder, subfolder, fname)
        attr = "impl_maint_preview" if subfolder == "Maintenance Standards" else "impl_eng_preview"
        tv = getattr(self, attr, None)
        if tv is None or not os.path.isfile(path): return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception:
            content = "(binary or unreadable file — double-click to open externally)"
        tv.configure(state="normal")
        tv.delete("1.0", "end")
        tv.insert("1.0", content)
        tv.configure(state="disabled")

    def _on_rly_select(self, _=None):
        """Preview the selected relay setting file with nice formatting."""
        sel = self.impl_rly_lb.curselection()
        if not sel:
            return
        fname = self.impl_rly_lb.get(sel[0])
        if not self.project_folder or fname.startswith("("):
            return
        path = os.path.join(self.project_folder, "Relay Settings", fname)
        if not os.path.isfile(path):
            return
        self._render_relay_setting(path)

    def _on_rly_double_click(self, _=None):
        """Open the selected relay setting file in the system text editor."""
        sel = self.impl_rly_lb.curselection()
        if not sel:
            return
        fname = self.impl_rly_lb.get(sel[0])
        if not self.project_folder or fname.startswith("("):
            return
        path = os.path.join(self.project_folder, "Relay Settings", fname)
        if os.path.isfile(path):
            _open_file(path)

    def _render_relay_setting(self, path):
        """Parse and display a relay setting .txt file with coloured formatting.

        Supports the export format used by the relay settings tool:
          [SECTION]
          SETTINGNAME,"value"   or   KEY=value
        """
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except Exception as exc:
            self._rly_preview_plain(f"Cannot read file:\n{exc}")
            return

        tv = self.impl_rly_preview
        tv.configure(state="normal")
        tv.delete("1.0", "end")

        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                tv.insert("end", "\n")
                continue

            # Section header  [NAME]
            if stripped.startswith("[") and stripped.endswith("]"):
                tv.insert("end", "\n" + stripped + "\n", "section")
                tv.insert("end", "─" * len(stripped) + "\n", "info")
                continue

            # KEY,"value"  format (comma-quoted)
            if ',"' in stripped:
                key, _, rest = stripped.partition(',"')
                value = rest.rstrip('"')
                tv.insert("end", f"  {key:<30}", "key")
                tv.insert("end", f"  {value}\n", "value")
                continue

            # KEY=value  format
            if "=" in stripped:
                key, _, value = stripped.partition("=")
                tv.insert("end", f"  {key:<30}", "key")
                tv.insert("end", f"  {value}\n", "value")
                continue

            # Anything else — plain
            tv.insert("end", "  " + stripped + "\n")

        tv.configure(state="disabled")
        tv.see("1.0")

    def _rly_preview_plain(self, msg):
        tv = self.impl_rly_preview
        tv.configure(state="normal")
        tv.delete("1.0", "end")
        tv.insert("1.0", msg)
        tv.configure(state="disabled")

    def _import_relay_file(self):
        """Let the user pick any file and save it (optionally renamed) into Relay Settings/."""
        if not self.project_folder:
            messagebox.showinfo("Save Project First",
                "Save the project first so the Relay Settings folder location is known.")
            return
        src = filedialog.askopenfilename(
            title="Select relay setting file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not src:
            return

        dest_dir = os.path.join(self.project_folder, "Relay Settings")
        os.makedirs(dest_dir, exist_ok=True)

        suggested = os.path.basename(src)
        # Ask for a destination name
        dlg = tk.Toplevel(self)
        dlg.title("Save Relay Setting File")
        dlg.grab_set()
        dlg.resizable(False, False)

        f = ttk.Frame(dlg, padding=14); f.pack(fill="both", expand=True)
        ttk.Label(f, text="Source file:", foreground="grey", font=("", 8)
                  ).grid(row=0, column=0, sticky="e", padx=(0, 6), pady=2)
        ttk.Label(f, text=suggested, font=("Courier", 8)
                  ).grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(f, text="Save as:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=6)
        name_var = tk.StringVar(value=suggested)
        name_entry = ttk.Entry(f, textvariable=name_var, width=40)
        name_entry.grid(row=1, column=1, sticky="ew", pady=6)
        f.columnconfigure(1, weight=1)

        bf = ttk.Frame(dlg, padding=(14, 4)); bf.pack(fill="x")
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)

        def _save():
            dest_name = name_var.get().strip()
            if not dest_name:
                messagebox.showwarning("Name Required", "Enter a filename.", parent=dlg)
                return
            dest = os.path.join(dest_dir, dest_name)
            if os.path.exists(dest):
                if not messagebox.askyesno("Overwrite?",
                        f"{dest_name} already exists. Overwrite?", parent=dlg):
                    return
            try:
                shutil.copy2(src, dest)
            except Exception as exc:
                messagebox.showerror("Copy Failed", str(exc), parent=dlg)
                return
            dlg.destroy()
            self._refresh_file_tabs()
            self.file_nb.select(1)   # switch to Relay Settings tab

        ttk.Button(bf, text="Save", command=_save).pack(side="right")
        _center_window(dlg)
        name_entry.focus_set()
        name_entry.selection_range(0, "end")
        dlg.bind("<Return>", lambda _: _save())

    def _refresh_file_tabs(self):
        # Drawings: honour the current filter mode
        sel = self.impl_tree.selection()
        if (self._drw_filter_var.get() == "step"
                and sel and sel[0] not in ("__prep__", "__tailboard__", "__safety__")):
            idx = int(sel[0])
            if 0 <= idx < len(self.jobs):
                self._filter_drawings_to_job(self.jobs[idx])
                # fall through to refresh relay tab
            else:
                self._show_all_drawings()
        else:
            self._show_all_drawings()

        # Relay Settings tab (flat folder)
        lb = self.impl_rly_lb
        lb.delete(0, "end")
        self._rly_preview_plain("")   # clear preview
        if not self.project_folder:
            lb.insert("end", "(save project first to see files)")
            return
        folder = os.path.join(self.project_folder, "Relay Settings")
        if os.path.isdir(folder):
            files = sorted(f for f in os.listdir(folder) if not f.startswith("."))
            for fn in files:
                lb.insert("end", fn)
            if not files:
                lb.insert("end", "(no files yet — import via Add/Edit relay in Planning)")
        else:
            lb.insert("end", "(Relay Settings/ folder not found)")

        # Maintenance Standards and Engineering Standards tabs
        for lb_attr, subfolder, hint in [
            ("impl_maint_lb", "Maintenance Standards",
             "(no files yet — download via Maintenance Standards tab in Planning)"),
            ("impl_eng_lb",   "Engineering Standards",
             "(no files yet — download via Engineering Standards tab in Planning)"),
        ]:
            slb = getattr(self, lb_attr, None)
            if slb is None:
                continue
            slb.delete(0, "end")
            std_folder = os.path.join(self.project_folder, subfolder)
            if os.path.isdir(std_folder):
                files = sorted(f for f in os.listdir(std_folder)
                               if not f.startswith(".") and f != "Archive")
                for fn in files:
                    slb.insert("end", fn)
                if not files:
                    slb.insert("end", hint)
            else:
                slb.insert("end", f"({subfolder}/ folder not found)")

    def _job_has_mb(self, job):
        """Return True if this job's protection sub-dict has mb_enabled set."""
        return bool((job.get("protection") or {}).get("mb_enabled"))

    def _update_mb_warn(self, job=None):
        """Show or hide the MB warning strip in the Reference Files area."""
        if not hasattr(self, "_mb_warn_frame"):
            return
        if job and self._job_has_mb(job):
            prot   = job.get("protection") or {}
            remote = prot.get("mb_remote", "").strip()
            notes  = prot.get("mb_notes",  "").strip()
            detail = ""
            if remote:
                detail = f"  Remote: {remote}"
            if notes:
                detail += f"  ({notes})"
            self._mb_remote_lbl.configure(text=detail)
            self._mb_warn_frame.pack(fill="x", before=self.file_nb)
        else:
            self._mb_warn_frame.pack_forget()

    def _open_impl_file(self, lb, subfolder):
        sel = lb.curselection()
        if not sel: return
        fname = lb.get(sel[0])
        if fname.startswith("("): return
        path = os.path.join(self.project_folder, subfolder, fname)
        if os.path.exists(path): _open_file(path)

    def _refresh_impl_list(self):
        for iid in self.impl_tree.get_children(): self.impl_tree.delete(iid)

        # PREP briefing — always first
        self.impl_tree.insert("", "end", iid="__prep__",
            values=("▶", "", "PREP", "Project Briefing  —  CROWs · Drawings · Relay Settings"),
            tags=("PREP",))
        self.impl_tree.tag_configure("PREP", foreground="#2980b9", font=("", 9, "bold"))

        # TAILBOARD — always second (before work steps)
        tb_done = self.title_page.get("tailboard_done", False)
        tb_path = self._tailboard_template_path()
        tb_hint = ("Open template ↗" if tb_path else "tailboard-template.pdf not found beside script")
        self.impl_tree.insert("", "end", iid="__tailboard__",
            values=("☑" if tb_done else "☐", "", "TAILBOARD",
                    f"Complete tailboard before starting work  ·  {tb_hint}"),
            tags=("TAILBOARD", "COMPLETED") if tb_done else ("TAILBOARD",))
        self.impl_tree.tag_configure("TAILBOARD", foreground="#e67e22", font=("", 9, "bold"))

        # SAFETY DOCUMENTS — always third (before work steps)
        saf_done = self.title_page.get("safety_done", False)
        saf_hint = "Template: " + (self.app_config.get("safety_template_path","") or "none set — click to configure")
        self.impl_tree.insert("", "end", iid="__safety__",
            values=("☑" if saf_done else "☐", "", "SAFETY DOCS",
                    f"Safety documents  ·  {saf_hint}"),
            tags=("SAFETY", "COMPLETED") if saf_done else ("SAFETY",))
        self.impl_tree.tag_configure("SAFETY", foreground="#1a7a30", font=("", 9, "bold"))

        disp = JOB_TYPE_SHORT
        self.impl_tree.tag_configure("MB_WARN", foreground="#e59866")
        for i, job in enumerate(self.jobs):
            done   = job.get("completed", False)
            has_mb = self._job_has_mb(job)
            desc   = ("! " if has_mb else "") + job.get("description", "")
            base_tags = (job["type"],)
            if has_mb:
                base_tags = base_tags + ("MB_WARN",)
            tags = base_tags + ("COMPLETED",) if done else base_tags
            self.impl_tree.insert("", "end", iid=str(i),
                values=("☑" if done else "☐", i + 1,
                        disp.get(job["type"], job["type"]),
                        desc),
                tags=tags)

    def _on_impl_select(self, _=None):
        sel = self.impl_tree.selection()
        if not sel: return
        if sel[0] == "__tailboard__":
            self._update_mb_warn(None)
            self._show_tailboard_panel()
            return
        if sel[0] == "__safety__":
            self._update_mb_warn(None)
            self._show_safety_panel()
            return
        # All other rows: hide both custom panels, show text preview
        self.impl_tb_frame.pack_forget()
        self.impl_safety_frame.pack_forget()
        self.impl_preview.pack(fill="both", expand=True)
        if sel[0] == "__prep__":
            self._update_mb_warn(None)
            self._show_impl_prep()
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.jobs):
            job  = self.jobs[idx]
            text = format_job(idx, job)
            self.impl_preview.configure(state="normal")
            self.impl_preview.delete("1.0", "end")
            self.impl_preview.insert("1.0", text)
            self.impl_preview.configure(state="disabled")
            self._update_mb_warn(job)
            if self._drw_filter_var.get() == "step":
                self._filter_drawings_to_job(job)

    # ── Drawing filter helpers ─────────────────────────────────────

    def _toggle_drawing_filter(self):
        if self._drw_filter_var.get() == "step":
            self._drw_filter_var.set("all")
            self._drw_filter_btn.configure(text="Step Only")
            self._show_all_drawings()
        else:
            self._drw_filter_var.set("step")
            self._drw_filter_btn.configure(text="Show All")
            sel = self.impl_tree.selection()
            if sel and sel[0] not in ("__prep__", "__tailboard__", "__safety__"):
                idx = int(sel[0])
                if 0 <= idx < len(self.jobs):
                    self._filter_drawings_to_job(self.jobs[idx])
                    return
            self._show_all_drawings()

    def _job_drawing_names(self, job):
        """Return a set of all drawing name strings referenced in a job."""
        names = set()
        for ep_key in ("start", "end", "add_start", "add_end", "endpoint"):
            d = (job.get(ep_key) or {}).get("drawing", "").strip()
            if d:
                names.add(d)
        for d in (job.get("protection") or {}).get("drawings", []):
            if isinstance(d, dict):
                dname = d.get("drawing", "").strip()
            else:
                dname = str(d).strip()
            if dname:
                names.add(dname)
        return names

    def _find_drawing_files(self, drawing_names):
        """Find downloaded files for the given drawing names.
        Returns [(display_label, full_path)] searching the organised subfolders first,
        then falling back to a flat Drawings/ scan."""
        if not self.project_folder:
            return []
        base = os.path.join(self.project_folder, "Drawings")
        results = []
        found = set()
        for name in drawing_names:
            sub = _drawing_subdir(base, name)
            if os.path.isdir(sub):
                for fname in sorted(os.listdir(sub)):
                    fpath = os.path.join(sub, fname)
                    if os.path.isfile(fpath) and not fname.startswith("."):
                        stem = os.path.splitext(fname)[0]
                        if stem.lower().startswith(name.lower()):
                            rel = os.path.relpath(fpath, base)
                            results.append((rel, fpath))
                            found.add(name)
            # Flat fallback
            if name not in found and os.path.isdir(base):
                for fname in os.listdir(base):
                    fpath = os.path.join(base, fname)
                    if os.path.isfile(fpath):
                        if os.path.splitext(fname)[0].lower() == name.lower():
                            results.append((fname, fpath))
                            found.add(name)
        return results

    def _filter_drawings_to_job(self, job):
        """Show local files then URL entries for each drawing on this step."""
        self.impl_drw_lb.delete(0, "end")
        self._impl_drw_paths = []
        names = self._job_drawing_names(job)
        if not names:
            self.impl_drw_lb.insert("end", "(no drawings on this step)")
            return
        first_local = None
        for name in sorted(names):
            local = self._find_drawing_files({name})
            url   = self.drawing_registry.get(name, {}).get("url", "").strip()
            for label, path in local:
                if first_local is None:
                    first_local = len(self._impl_drw_paths)
                self.impl_drw_lb.insert("end", f"  {label}")
                self._impl_drw_paths.append(path)
            # Only show URL when no local file exists yet
            if not local and url:
                self.impl_drw_lb.insert("end", f"  ↗ {name}  [web]")
                self._impl_drw_paths.append(url)
            if not local and not url:
                self.impl_drw_lb.insert("end", f"  (not downloaded) {name}")
        if not self.impl_drw_lb.size():
            self.impl_drw_lb.insert("end", "(no drawings on this step)")
            return
        if first_local is not None:
            self.impl_drw_lb.selection_set(first_local)
            self._preview_file(self._impl_drw_paths[first_local])

    def _show_all_drawings(self):
        """List every local drawing file then URL entries from the registry."""
        self.impl_drw_lb.delete(0, "end")
        self._impl_drw_paths = []
        if not self.project_folder:
            self.impl_drw_lb.insert("end", "(save project first)")
            return
        base = os.path.join(self.project_folder, "Drawings")
        # Local files first
        local_count = 0
        if os.path.isdir(base):
            for root, dirs, files in os.walk(base):
                dirs[:] = sorted(d for d in dirs if d != "Archive")
                for fname in sorted(files):
                    if not fname.startswith("."):
                        fpath = os.path.join(root, fname)
                        self.impl_drw_lb.insert("end", os.path.relpath(fpath, base))
                        self._impl_drw_paths.append(fpath)
                        local_count += 1
        # URL-only entries: show only for registry drawings that have no local file
        url_entries = []
        for name, info in sorted(self.drawing_registry.items()):
            url = info.get("url", "").strip()
            if url and not self._find_drawing_files({name}):
                url_entries.append((name, url))
        if url_entries:
            if local_count:
                self.impl_drw_lb.insert("end", "── not yet downloaded ──")
                self._impl_drw_paths.append(None)   # sentinel — not openable
            for name, url in url_entries:
                self.impl_drw_lb.insert("end", f"  ↗ {name}  [web]")
                self._impl_drw_paths.append(url)
        if not local_count and not url_entries:
            self.impl_drw_lb.insert("end", "(no drawings downloaded yet — use ⬇ Download All)")

    # ── Drawing list click handlers ───────────────────────────────

    def _on_drw_select(self, _=None):
        sel = self.impl_drw_lb.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._impl_drw_paths):
            return
        entry = self._impl_drw_paths[idx]
        if entry is None:
            return   # separator row
        if entry.startswith("http"):
            self._pdf_canvas.delete("all")
            self._pdf_canvas.create_text(10, 10, anchor="nw", fill="#888",
                text=f"Web URL — double-click to open in browser:\n\n{entry}",
                font=("", 9), tags=("hint",), width=500)
            self._pdf_page_lbl.set("")
        else:
            self._preview_file(entry)

    def _on_drw_double_click(self, _=None):
        sel = self.impl_drw_lb.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._impl_drw_paths):
            return
        entry = self._impl_drw_paths[idx]
        if not entry:
            return
        if entry.startswith("http"):
            webbrowser.open(entry)
        elif os.path.exists(entry):
            _open_file(entry)

    # ── PDF / image preview ───────────────────────────────────────

    def _preview_file(self, path):
        if not path or not os.path.exists(path):
            return
        self._pdf_cur_path[0] = path
        self._pdf_page[0]     = 1
        ext = os.path.splitext(path)[-1].lower()
        if ext == ".pdf":
            self._render_and_show_pdf()
        elif ext in (".png", ".ppm", ".pgm", ".gif"):
            self._show_image_preview(path)
        else:
            self._pdf_canvas.delete("all")
            self._pdf_page_lbl.set("")
            self._pdf_canvas.create_text(
                10, 10, anchor="nw", fill="#888",
                text=f"No preview for {ext} files.\nDouble-click to open.",
                font=("", 9))

    def _render_and_show_pdf(self):
        """Start a background render of the current PDF page."""
        path = self._pdf_cur_path[0]
        page = self._pdf_page[0]
        self._pdf_canvas.delete("all")
        self._pdf_canvas.create_text(10, 10, anchor="nw", fill="#888",
            text=f"Rendering page {page}…", font=("", 9))
        self._pdf_prev_btn.configure(state="disabled")
        self._pdf_next_btn.configure(state="disabled")
        self._pdf_page_lbl.set(f"Page {page}")

        q: queue.Queue = queue.Queue()
        def _worker():
            q.put(self._render_pdf_page(path, page))
        threading.Thread(target=_worker, daemon=True).start()

        def _poll():
            if q.empty():
                self.after(80, _poll)
                return
            # Stale if the user navigated elsewhere while rendering
            if self._pdf_cur_path[0] != path or self._pdf_page[0] != page:
                return
            img, total = q.get()
            self._pdf_total[0] = total
            self._pdf_canvas.delete("all")
            if img:
                self._pdf_photo[0] = img
                self._pdf_canvas.create_image(0, 0, anchor="nw", image=img)
                self._pdf_canvas.configure(
                    scrollregion=(0, 0, img.width(), img.height()))
                lbl = f"Page {page}"
                if total > 1:
                    lbl += f" / {total}"
                self._pdf_page_lbl.set(lbl)
                self._pdf_prev_btn.configure(
                    state="normal" if page > 1 else "disabled")
                self._pdf_next_btn.configure(
                    state="normal" if total > 1 and page < total else "disabled")
            else:
                self._pdf_page_lbl.set("")
                self._pdf_canvas.create_text(
                    10, 10, anchor="nw", fill="#888",
                    text="PDF preview not available.\nDouble-click the file to open it.",
                    font=("", 9))
        self.after(80, _poll)

    def _find_poppler_bin(self, name):
        """Find a poppler binary: check beside the script first, then PATH."""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        for candidate in (name, name + ".exe"):
            p = os.path.join(script_dir, candidate)
            if os.path.isfile(p):
                return p
        return shutil.which(name)

    def _render_pdf_page(self, pdf_path, page=1):
        """Convert one PDF page to a tk.PhotoImage via pdftoppm.
        Returns (PhotoImage, total_pages) or (None, 0) if pdftoppm is absent."""
        pdftoppm = self._find_poppler_bin("pdftoppm")
        if not pdftoppm:
            return None, 0
        tmp_base = None
        try:
            # Page count via pdfinfo (optional — graceful if missing)
            total = 0
            pdfinfo = self._find_poppler_bin("pdfinfo")
            if pdfinfo:
                r = subprocess.run([pdfinfo, pdf_path],
                                   capture_output=True, text=True, timeout=5)
                for line in r.stdout.splitlines():
                    if line.lower().startswith("pages:"):
                        try:
                            total = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            pass
                        break

            fd, tmp_base = tempfile.mkstemp()
            os.close(fd)
            os.unlink(tmp_base)   # pdftoppm appends its own suffix

            subprocess.run(
                [pdftoppm, "-r", "120", "-f", str(page), "-l", str(page),
                 pdf_path, tmp_base],
                capture_output=True, timeout=20, check=True)

            ppm_files = sorted(glob.glob(tmp_base + "*.ppm"))
            if not ppm_files:
                return None, total
            img = tk.PhotoImage(file=ppm_files[0])
            return img, max(total, page)
        except Exception:
            return None, 0
        finally:
            if tmp_base:
                for f in glob.glob(tmp_base + "*.ppm"):
                    try:
                        os.unlink(f)
                    except Exception:
                        pass

    def _show_image_preview(self, path):
        """Display a PNG/PPM/GIF directly (no external tool needed)."""
        try:
            img = tk.PhotoImage(file=path)
            self._pdf_photo[0] = img
            self._pdf_canvas.delete("all")
            self._pdf_canvas.create_image(0, 0, anchor="nw", image=img)
            self._pdf_canvas.configure(
                scrollregion=(0, 0, img.width(), img.height()))
            self._pdf_page_lbl.set("")
            self._pdf_prev_btn.configure(state="disabled")
            self._pdf_next_btn.configure(state="disabled")
        except Exception:
            self._pdf_canvas.delete("all")
            self._pdf_canvas.create_text(10, 10, anchor="nw", fill="#888",
                text="Could not display image.", font=("", 9))

    def _pdf_nav(self, delta):
        if not self._pdf_cur_path[0]:
            return
        total   = self._pdf_total[0]
        new_pg  = self._pdf_page[0] + delta
        if new_pg < 1 or (total > 0 and new_pg > total):
            return
        self._pdf_page[0] = new_pg
        self._render_and_show_pdf()

    def _open_previewed_file(self):
        path = self._pdf_cur_path[0]
        if path and os.path.exists(path):
            _open_file(path)

    # ── Tailboard ─────────────────────────────────────────────────

    def _tailboard_dir(self):
        if not self.project_folder:
            return None
        return os.path.join(self.project_folder, "Tailboards")

    def _tailboard_template_path(self):
        """Return the tailboard template path.

        Prefers the project's downloaded/uploaded copy
        (Tailboards/Tailboard_Template.*) so the live version from the
        tailboard site wins; falls back to tailboard-template.pdf beside
        the script, or None if neither exists.
        """
        if self.project_folder:
            tb_dir = os.path.join(self.project_folder, "Tailboards")
            if os.path.isdir(tb_dir):
                for fname in sorted(os.listdir(tb_dir)):
                    if fname.startswith("Tailboard_Template") and \
                       os.path.isfile(os.path.join(tb_dir, fname)):
                        return os.path.join(tb_dir, fname)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(script_dir, "tailboard-template.pdf")
        return p if os.path.exists(p) else None

    def _schedule_tailboard_check(self):
        """Create Tailboards/Completed folder and refresh the status label."""
        tb_dir = self._tailboard_dir()
        if not tb_dir:
            return
        try:
            os.makedirs(os.path.join(tb_dir, "Completed"), exist_ok=True)
        except Exception:
            pass
        self._update_tailboard_status()

    def _update_tailboard_status(self):
        """Show the timestamp of the most recently saved tailboard in the toolbar."""
        if not hasattr(self, "_tb_status_var"):
            return
        tb_dir = self._tailboard_dir()
        completed_dir = os.path.join(tb_dir, "Completed") if tb_dir else None
        if completed_dir and os.path.isdir(completed_dir):
            files = sorted(
                (f for f in os.listdir(completed_dir) if not f.startswith(".")),
                reverse=True)
            if files:
                ts = files[0].replace("tailboard_", "").rsplit(".", 1)[0].replace("_", " ")
                self._tb_status_var.set(f"Last saved: {ts}")
                return
        self._tb_status_var.set("No tailboard saved yet")

    def _email_tailboard(self, file_path):
        """Show an email compose dialog and open the system mail client."""
        dlg = tk.Toplevel(self)
        dlg.title("Email Tailboard to Crew")
        dlg.grab_set()
        dlg.resizable(True, False)

        f = ttk.Frame(dlg, padding=14)
        f.pack(fill="both", expand=True)

        fname = os.path.basename(file_path)
        ttk.Label(f, text="File:", foreground="grey", font=("", 8)
                  ).grid(row=0, column=0, sticky="e", padx=(0,6), pady=3)
        ttk.Label(f, text=fname, font=("Courier", 9)
                  ).grid(row=0, column=1, sticky="w", pady=3)

        ttk.Label(f, text="To:").grid(row=1, column=0, sticky="e", padx=(0,6), pady=4)
        to_var = tk.StringVar(value=self.app_config.get("crew_email", ""))
        ttk.Entry(f, textvariable=to_var, width=52).grid(row=1, column=1, sticky="ew", pady=4)

        proj = self.project_var.get().strip() or "Project"
        ts   = fname.replace("tailboard_", "").replace(".pdf", "").replace("_", " ")
        ttk.Label(f, text="Subject:").grid(row=2, column=0, sticky="e", padx=(0,6), pady=4)
        subj_var = tk.StringVar(value=f"Tailboard — {proj} — {ts}")
        ttk.Entry(f, textvariable=subj_var, width=52).grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Label(f, text="Body:").grid(row=3, column=0, sticky="ne", padx=(0,6), pady=4)
        body_txt = tk.Text(f, height=5, width=52, wrap="word", font=("", 9))
        body_txt.grid(row=3, column=1, sticky="ew", pady=4)
        body_txt.insert("1.0",
            f"Hi team,\n\nPlease find the attached tailboard for {proj}.\n\n"
            f"File: {fname}\nPath: {file_path}")

        ttk.Label(f,
                  text="Tip: attach  " + fname + "  manually after your email client opens.",
                  foreground="grey", font=("", 8), wraplength=420, justify="left"
                  ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 4))

        f.columnconfigure(1, weight=1)

        bf = ttk.Frame(dlg, padding=(14, 6)); bf.pack(fill="x")
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)

        def _copy_path():
            dlg.clipboard_clear()
            dlg.clipboard_append(file_path)
        ttk.Button(bf, text="Copy File Path", command=_copy_path).pack(side="right", padx=4)

        def _open_client():
            to   = to_var.get().strip()
            subj = subj_var.get().strip()
            body = body_txt.get("1.0", "end").strip()
            if to:
                self.app_config["crew_email"] = to
                self._save_app_config()
            mailto = (f"mailto:{urllib.parse.quote(to)}"
                      f"?subject={urllib.parse.quote(subj)}"
                      f"&body={urllib.parse.quote(body)}")
            webbrowser.open(mailto)
            dlg.destroy()

        ttk.Button(bf, text="Open Email Client", command=_open_client).pack(side="right")
        _center_window(dlg)

    # ── Tailboard panel (rich custom detail view) ──────────────────

    def _build_tailboard_panel(self, parent):
        """Build the tailboard detail panel inside details_f (hidden until selected)."""
        # Template row — view only, never editable from the app
        tmpl_f = ttk.LabelFrame(parent, text="Template  (read-only — do not edit from here)", padding=6)
        tmpl_f.pack(fill="x", padx=4, pady=(4, 2))
        self._tb_tmpl_lbl = ttk.Label(tmpl_f, text="(loading…)", foreground="#2980b9",
                                       cursor="hand2", font=("", 9))
        self._tb_tmpl_lbl.pack(side="left", expand=True, fill="x")
        self._tb_tmpl_lbl.bind("<Button-1>", lambda _: self._open_tailboard_template())
        ttk.Button(tmpl_f, text="View ↗",
                   command=self._open_tailboard_template).pack(side="right")

        # Middle: sign-ons (left) + saved tailboards / revisions (right)
        mid_f = ttk.Frame(parent)
        mid_f.pack(fill="both", expand=True, padx=4, pady=2)

        so_f = ttk.LabelFrame(mid_f, text="Sign-ons", padding=4)
        so_f.pack(side="left", fill="both", expand=True, padx=(0, 4))

        so_tree_f = ttk.Frame(so_f)
        so_tree_f.pack(fill="both", expand=True)
        self._tb_signon_tree = ttk.Treeview(
            so_tree_f, columns=("Name", "Email"),
            show="headings", height=6, selectmode="browse")
        self._tb_signon_tree.heading("Name",  text="Name")
        self._tb_signon_tree.heading("Email", text="Email")
        self._tb_signon_tree.column("Name",  width=130)
        self._tb_signon_tree.column("Email", width=170)
        so_vsb = ttk.Scrollbar(so_tree_f, orient="vertical",
                                command=self._tb_signon_tree.yview)
        self._tb_signon_tree.configure(yscrollcommand=so_vsb.set)
        so_vsb.pack(side="right", fill="y")
        self._tb_signon_tree.pack(fill="both", expand=True)

        so_btn_f = ttk.Frame(so_f)
        so_btn_f.pack(fill="x", pady=(4, 0))
        ttk.Button(so_btn_f, text="+ Add",
                   command=self._tb_add_signon).pack(side="left")
        ttk.Button(so_btn_f, text="Remove",
                   command=self._tb_remove_signon).pack(side="left", padx=4)

        rv_f = ttk.LabelFrame(mid_f, text="Saved Tailboards", padding=4)
        rv_f.pack(side="left", fill="both", expand=True)

        self._tb_rev_lb = tk.Listbox(rv_f, font=("Courier", 8), selectmode="browse",
                                      activestyle="none", relief="flat", borderwidth=0)
        rv_vsb = ttk.Scrollbar(rv_f, orient="vertical",
                                command=self._tb_rev_lb.yview)
        self._tb_rev_lb.configure(yscrollcommand=rv_vsb.set)
        rv_vsb.pack(side="right", fill="y")
        self._tb_rev_lb.pack(fill="both", expand=True)
        self._tb_rev_lb.bind("<Double-1>", self._tb_open_revision)

        # Action buttons
        act_f = ttk.Frame(parent)
        act_f.pack(fill="x", padx=4, pady=(2, 4))
        ttk.Button(act_f, text="Save Tailboard",
                   command=self._save_tailboard_record).pack(side="left")
        ttk.Button(act_f, text="Email to Crew ✉",
                   command=self._tb_email_latest).pack(side="left", padx=6)

    def _show_tailboard_panel(self):
        """Swap step-details area to show the tailboard panel."""
        self.impl_preview.pack_forget()
        self.impl_safety_frame.pack_forget()
        self.impl_tb_frame.pack(fill="both", expand=True)
        self._refresh_tailboard_panel()

    def _goto_tailboard_step(self):
        """Select the TAILBOARD row and show the tailboard panel."""
        self.impl_tree.selection_set("__tailboard__")
        self.impl_tree.see("__tailboard__")
        self._show_tailboard_panel()

    def _refresh_tailboard_panel(self):
        """Update template label and revisions list to reflect current state."""
        tmpl = self._tailboard_template_path()
        if tmpl:
            self._tb_tmpl_lbl.configure(
                text=f"tailboard-template.pdf  —  {tmpl}",
                foreground="#2980b9")
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            self._tb_tmpl_lbl.configure(
                text=f"Not found — place tailboard-template.pdf beside wire_planner.py"
                     f"\n({script_dir})",
                foreground="#c0392b")
        self._refresh_tb_signons()
        self._refresh_tb_revisions()

    def _refresh_tb_signons(self):
        """Reload sign-on treeview from title_page data."""
        for iid in self._tb_signon_tree.get_children():
            self._tb_signon_tree.delete(iid)
        for entry in self.title_page.get("tailboard_signons", []):
            self._tb_signon_tree.insert("", "end",
                values=(entry.get("name", ""), entry.get("email", "")))

    def _refresh_tb_revisions(self):
        """Reload the saved tailboards listbox."""
        self._tb_rev_lb.delete(0, "end")
        tb_dir = self._tailboard_dir()
        completed_dir = os.path.join(tb_dir, "Completed") if tb_dir else None
        if not (completed_dir and os.path.isdir(completed_dir)):
            return
        files = sorted(
            (f for f in os.listdir(completed_dir) if not f.startswith(".")),
            reverse=True)
        for fn in files:
            self._tb_rev_lb.insert("end", fn)

    def _tb_open_revision(self, _=None):
        """Open the double-clicked revision file."""
        sel = self._tb_rev_lb.curselection()
        if not sel:
            return
        fn = self._tb_rev_lb.get(sel[0])
        tb_dir = self._tailboard_dir()
        if not tb_dir:
            return
        path = os.path.join(tb_dir, "Completed", fn)
        if os.path.exists(path):
            _open_file(path)

    def _tb_add_signon(self):
        """Show a small dialog and append a new sign-on entry."""
        dlg = tk.Toplevel(self)
        dlg.title("Add Sign-on")
        dlg.grab_set()
        dlg.resizable(False, False)

        f = ttk.Frame(dlg, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Name:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=4)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(f, textvariable=name_var, width=30)
        name_entry.grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(f, text="Email:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=4)
        email_var = tk.StringVar()
        ttk.Entry(f, textvariable=email_var, width=30).grid(row=1, column=1, sticky="ew", pady=4)
        f.columnconfigure(1, weight=1)

        bf = ttk.Frame(dlg, padding=(14, 4)); bf.pack(fill="x")
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)

        def _add():
            name  = name_var.get().strip()
            email = email_var.get().strip()
            if not name:
                messagebox.showwarning("Name Required", "Please enter a name.", parent=dlg)
                return
            signons = self.title_page.setdefault("tailboard_signons", [])
            signons.append({"name": name, "email": email})
            self._tb_signon_tree.insert("", "end", values=(name, email))
            dlg.destroy()

        ttk.Button(bf, text="Add", command=_add).pack(side="right")
        _center_window(dlg)
        name_entry.focus_set()
        dlg.bind("<Return>", lambda _: _add())

    def _tb_remove_signon(self):
        """Remove the selected sign-on from the treeview and title_page data."""
        sel = self._tb_signon_tree.selection()
        if not sel:
            return
        iid = sel[0]
        idx = self._tb_signon_tree.index(iid)
        signons = self.title_page.get("tailboard_signons", [])
        if 0 <= idx < len(signons):
            signons.pop(idx)
        self._tb_signon_tree.delete(iid)

    def _save_tailboard_record(self):
        """Save a timestamped JSON record (+ PDF copy) and offer to email crew."""
        tb_dir = self._tailboard_dir()
        if not tb_dir:
            messagebox.showinfo("Save Project First",
                "Save the project first so the Tailboards folder location is known.")
            return

        completed_dir = os.path.join(tb_dir, "Completed")
        os.makedirs(completed_dir, exist_ok=True)

        ts   = datetime.now().strftime("%Y-%m-%d_%H%M")
        base = f"tailboard_{ts}"

        # Copy the template PDF if available
        tmpl = self._tailboard_template_path()
        saved_pdf = None
        if tmpl:
            dst_pdf = os.path.join(completed_dir, f"{base}.pdf")
            try:
                shutil.copy2(tmpl, dst_pdf)
                saved_pdf = dst_pdf
            except Exception as exc:
                messagebox.showwarning("PDF Copy Failed",
                    f"Could not copy template PDF:\n{exc}\n\nSaving JSON record only.")

        # Always save a JSON record with sign-ons
        record = {
            "timestamp":  ts,
            "project":    self.project_var.get().strip(),
            "signons":    self.title_page.get("tailboard_signons", []),
        }
        json_path = os.path.join(completed_dir, f"{base}.json")
        try:
            with open(json_path, "w") as fh:
                json.dump(record, fh, indent=2)
        except Exception as exc:
            messagebox.showerror("Save Failed", str(exc))
            return

        self.title_page["tailboard_done"] = True
        self._refresh_impl_list()
        self.impl_tree.selection_set("__tailboard__")
        self._show_tailboard_panel()
        self._update_tailboard_status()

        saved_name = os.path.basename(saved_pdf) if saved_pdf else os.path.basename(json_path)
        if messagebox.askyesno("Tailboard Saved",
                f"Saved:  {saved_name}\n\nEmail this tailboard to the crew?"):
            self._email_tailboard(saved_pdf or json_path)

    def _open_tailboard_template(self):
        """Open the tailboard template in the system viewer."""
        tmpl = self._tailboard_template_path()
        if tmpl:
            _open_file(tmpl)
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            messagebox.showinfo("Template Not Found",
                f"Place  tailboard-template.pdf  beside  wire_planner.py:\n\n{script_dir}")

    def _tb_email_latest(self):
        """Email the most recently saved tailboard (or show a message if none exist)."""
        tb_dir = self._tailboard_dir()
        completed_dir = os.path.join(tb_dir, "Completed") if tb_dir else None
        if completed_dir and os.path.isdir(completed_dir):
            pdfs = sorted(
                (f for f in os.listdir(completed_dir)
                 if f.endswith(".pdf") and not f.startswith(".")),
                reverse=True)
            if pdfs:
                self._email_tailboard(os.path.join(completed_dir, pdfs[0]))
                return
        messagebox.showinfo("No Tailboard Saved",
            "Save a tailboard first using the 'Save Tailboard' button.")

    # ── Safety Documents (template + sign-ons, mirrors tailboard pattern) ──

    def _safety_dir(self):
        if not self.project_folder:
            return None
        return os.path.join(self.project_folder, "Safety Documents")

    def _safety_template_path(self):
        p = self.app_config.get("safety_template_path", "").strip()
        return p if p and os.path.isfile(p) else None

    def _build_safety_panel(self, parent):
        """Build the Safety Documents detail panel (hidden until __safety__ is selected)."""
        tmpl_f = ttk.LabelFrame(parent,
                                text="Template  (read-only — click Browse to change)",
                                padding=6)
        tmpl_f.pack(fill="x", padx=4, pady=(4, 2))
        self._saf_tmpl_lbl = ttk.Label(tmpl_f, text="(loading…)", foreground="#2980b9",
                                        cursor="hand2", font=("", 9))
        self._saf_tmpl_lbl.pack(side="left", expand=True, fill="x")
        self._saf_tmpl_lbl.bind("<Button-1>", lambda _: self._open_safety_template())
        bf_tmpl = ttk.Frame(tmpl_f); bf_tmpl.pack(side="right")
        ttk.Button(bf_tmpl, text="Browse…",
                   command=self._browse_safety_template).pack(side="left", padx=(0, 4))
        ttk.Button(bf_tmpl, text="View ↗",
                   command=self._open_safety_template).pack(side="left")

        mid_f = ttk.Frame(parent)
        mid_f.pack(fill="both", expand=True, padx=4, pady=2)

        so_f = ttk.LabelFrame(mid_f, text="Sign-ons", padding=4)
        so_f.pack(side="left", fill="both", expand=True, padx=(0, 4))

        so_tree_f = ttk.Frame(so_f)
        so_tree_f.pack(fill="both", expand=True)
        self._saf_signon_tree = ttk.Treeview(
            so_tree_f, columns=("Name", "Email"),
            show="headings", height=6, selectmode="browse")
        self._saf_signon_tree.heading("Name",  text="Name")
        self._saf_signon_tree.heading("Email", text="Email")
        self._saf_signon_tree.column("Name",  width=130)
        self._saf_signon_tree.column("Email", width=170)
        so_vsb = ttk.Scrollbar(so_tree_f, orient="vertical",
                                command=self._saf_signon_tree.yview)
        self._saf_signon_tree.configure(yscrollcommand=so_vsb.set)
        so_vsb.pack(side="right", fill="y")
        self._saf_signon_tree.pack(fill="both", expand=True)

        so_btn_f = ttk.Frame(so_f)
        so_btn_f.pack(fill="x", pady=(4, 0))
        ttk.Button(so_btn_f, text="+ Add",
                   command=self._saf_add_signon).pack(side="left")
        ttk.Button(so_btn_f, text="Remove",
                   command=self._saf_remove_signon).pack(side="left", padx=4)

        rv_f = ttk.LabelFrame(mid_f, text="Saved Documents", padding=4)
        rv_f.pack(side="left", fill="both", expand=True)

        self._saf_rev_lb = tk.Listbox(rv_f, font=("Courier", 8), selectmode="browse",
                                       activestyle="none", relief="flat", borderwidth=0)
        rv_vsb = ttk.Scrollbar(rv_f, orient="vertical",
                                command=self._saf_rev_lb.yview)
        self._saf_rev_lb.configure(yscrollcommand=rv_vsb.set)
        rv_vsb.pack(side="right", fill="y")
        self._saf_rev_lb.pack(fill="both", expand=True)
        self._saf_rev_lb.bind("<Double-1>", self._saf_open_revision)

        act_f = ttk.Frame(parent)
        act_f.pack(fill="x", padx=4, pady=(2, 4))
        ttk.Button(act_f, text="Save Safety Document",
                   command=self._save_safety_record).pack(side="left")

    def _show_safety_panel(self):
        self.impl_preview.pack_forget()
        self.impl_tb_frame.pack_forget()
        self.impl_safety_frame.pack(fill="both", expand=True)
        self._refresh_safety_panel()

    def _refresh_safety_panel(self):
        tmpl = self._safety_template_path()
        if tmpl:
            self._saf_tmpl_lbl.configure(text=tmpl, foreground="#2980b9")
        else:
            self._saf_tmpl_lbl.configure(
                text="No template set — click Browse… to select a PDF template",
                foreground="#c0392b")
        self._refresh_saf_signons()
        self._refresh_saf_revisions()

    def _refresh_saf_signons(self):
        for iid in self._saf_signon_tree.get_children():
            self._saf_signon_tree.delete(iid)
        for entry in self.title_page.get("safety_signons", []):
            self._saf_signon_tree.insert("", "end",
                values=(entry.get("name", ""), entry.get("email", "")))

    def _refresh_saf_revisions(self):
        self._saf_rev_lb.delete(0, "end")
        saf_dir = self._safety_dir()
        completed_dir = os.path.join(saf_dir, "Completed") if saf_dir else None
        if not (completed_dir and os.path.isdir(completed_dir)):
            return
        for fn in sorted(
                (f for f in os.listdir(completed_dir) if not f.startswith(".")),
                reverse=True):
            self._saf_rev_lb.insert("end", fn)

    def _saf_open_revision(self, _=None):
        sel = self._saf_rev_lb.curselection()
        if not sel: return
        saf_dir = self._safety_dir()
        if not saf_dir: return
        path = os.path.join(saf_dir, "Completed", self._saf_rev_lb.get(sel[0]))
        if os.path.exists(path): _open_file(path)

    def _saf_add_signon(self):
        dlg = tk.Toplevel(self); dlg.title("Add Sign-on"); dlg.grab_set()
        dlg.resizable(False, False)
        f = ttk.Frame(dlg, padding=14); f.pack(fill="both", expand=True)
        ttk.Label(f, text="Name:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=4)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(f, textvariable=name_var, width=30)
        name_entry.grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(f, text="Email:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=4)
        email_var = tk.StringVar()
        ttk.Entry(f, textvariable=email_var, width=30).grid(row=1, column=1, sticky="ew", pady=4)
        f.columnconfigure(1, weight=1)
        bf = ttk.Frame(dlg, padding=(14, 4)); bf.pack(fill="x")
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)
        def _add():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("Name Required", "Please enter a name.", parent=dlg)
                return
            self.title_page.setdefault("safety_signons", []).append(
                {"name": name, "email": email_var.get().strip()})
            self._saf_signon_tree.insert("", "end", values=(name, email_var.get().strip()))
            dlg.destroy()
        ttk.Button(bf, text="Add", command=_add).pack(side="right")
        _center_window(dlg); name_entry.focus_set()
        dlg.bind("<Return>", lambda _: _add())

    def _saf_remove_signon(self):
        sel = self._saf_signon_tree.selection()
        if not sel: return
        iid = sel[0]
        idx = self._saf_signon_tree.index(iid)
        signons = self.title_page.get("safety_signons", [])
        if 0 <= idx < len(signons):
            signons.pop(idx)
        self._saf_signon_tree.delete(iid)

    def _save_safety_record(self):
        saf_dir = self._safety_dir()
        if not saf_dir:
            messagebox.showinfo("Save Project First",
                "Save the project first so the Safety Documents folder is known.")
            return
        completed_dir = os.path.join(saf_dir, "Completed")
        os.makedirs(completed_dir, exist_ok=True)
        ts   = datetime.now().strftime("%Y-%m-%d_%H%M")
        base = f"safety_{ts}"
        tmpl = self._safety_template_path()
        saved_pdf = None
        if tmpl:
            dst = os.path.join(completed_dir, f"{base}.pdf")
            try:
                shutil.copy2(tmpl, dst); saved_pdf = dst
            except Exception as exc:
                messagebox.showwarning("PDF Copy Failed",
                    f"Could not copy template:\n{exc}\n\nSaving JSON record only.")
        record = {"timestamp": ts, "project": self.project_var.get().strip(),
                  "signons": self.title_page.get("safety_signons", [])}
        json_path = os.path.join(completed_dir, f"{base}.json")
        try:
            with open(json_path, "w") as fh:
                json.dump(record, fh, indent=2)
        except Exception as exc:
            messagebox.showerror("Save Failed", str(exc)); return
        self.title_page["safety_done"] = True
        self._refresh_impl_list()
        self.impl_tree.selection_set("__safety__")
        self._show_safety_panel()
        saved_name = os.path.basename(saved_pdf) if saved_pdf else os.path.basename(json_path)
        messagebox.showinfo("Saved", f"Safety document saved:\n{saved_name}")

    def _browse_safety_template(self):
        path = filedialog.askopenfilename(
            title="Select Safety Document Template",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if not path: return
        self.app_config["safety_template_path"] = path
        self._save_app_config()
        self._refresh_safety_panel()
        self._refresh_impl_list()

    def _open_safety_template(self):
        tmpl = self._safety_template_path()
        if tmpl:
            _open_file(tmpl)
        else:
            messagebox.showinfo("No Template",
                "No safety template configured.\nClick Browse… to select a PDF template.")

    # ── Safety Documents planning tab ─────────────────────────────

    # ── Generic document-folder helpers ────────────────────────────
    # The Safety Documents and Other Documents tabs are both a toolbar +
    # Listbox view over one project subfolder.  These helpers hold the single
    # implementation; each tab's named methods are thin delegates (same
    # pattern as _print_selected/_print_all on the registry tabs).

    def _make_doc_listbox(self, parent, frame_label, open_handler):
        """Build LabelFrame + scrollable Listbox; return the Listbox."""
        lf = ttk.LabelFrame(parent, text=frame_label, padding=4)
        lf.pack(fill="both", expand=True, padx=6, pady=4)
        lb_f = ttk.Frame(lf); lb_f.pack(fill="both", expand=True)
        lb = tk.Listbox(lb_f, selectmode="browse", font=("Courier", 9),
                        activestyle="none", relief="flat", borderwidth=0)
        vsb = ttk.Scrollbar(lb_f, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=vsb.set)
        lb.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        lb.bind("<Double-1>", open_handler)
        return lb

    def _refresh_doc_listbox(self, lb, dirpath, newest_first=False):
        """Reload a Listbox with the visible files of *dirpath*."""
        lb.delete(0, "end")
        if not (dirpath and os.path.isdir(dirpath)):
            return
        for fn in sorted(
                (f for f in os.listdir(dirpath)
                 if os.path.isfile(os.path.join(dirpath, f))
                 and not f.startswith(".")),
                reverse=newest_first):
            lb.insert("end", fn)

    def _open_doc_from_listbox(self, lb, dirpath):
        """Open the file selected in *lb* with the OS default handler."""
        sel = lb.curselection()
        if not (sel and dirpath):
            return
        path = os.path.join(dirpath, lb.get(sel[0]))
        if os.path.exists(path):
            _open_file(path)

    def _upload_docs_to(self, subparts, title, after=()):
        """Browse for documents, copy them into a project subfolder.

        If a file of the same name already exists it is moved into
        Archive/ with a timestamp first, so re-uploading an updated
        document keeps the superseded revision.

        subparts: path components under the project folder
        after:    callbacks run once the copies finish (refreshers)
        """
        if not self.project_folder:
            messagebox.showinfo("Save Project First",
                "Save the project before uploading documents.")
            return
        paths = filedialog.askopenfilenames(
            title=title,
            filetypes=[("PDF / Word / Text", "*.pdf *.docx *.doc *.txt"),
                       ("All files", "*.*")])
        if not paths:
            return
        dest = os.path.join(self.project_folder, *subparts)
        os.makedirs(dest, exist_ok=True)
        for src in paths:
            try:
                base = os.path.basename(src)
                _archive_revision(dest, base)
                shutil.copy2(src, os.path.join(dest, base))
            except Exception as exc:
                messagebox.showwarning("Copy Failed",
                    f"Could not copy {os.path.basename(src)}:\n{exc}")
        for cb in after:
            cb()

    def _reveal_project_subfolder(self, *parts):
        """Open a project subfolder in the OS file manager (created if needed)."""
        if not self.project_folder:
            messagebox.showinfo("Save Project First", "Save the project first.")
            return
        d = os.path.join(self.project_folder, *parts)
        os.makedirs(d, exist_ok=True)
        _reveal_file(d)

    def _open_for_editing(self, lb, dirpath):
        """Snapshot the selected file then open it for editing.

        Saves a pre-edit copy into Archive/ (timestamped, original kept
        in place) so the state before this editing session is preserved,
        then opens the file with the OS default application.
        """
        sel = lb.curselection()
        if not (sel and dirpath):
            return
        fname = lb.get(sel[0])
        fpath = os.path.join(dirpath, fname)
        if not os.path.isfile(fpath):
            messagebox.showinfo("File Not Found", f"{fname} was not found in the project folder.", parent=self)
            return
        _snapshot_file(dirpath, fname)
        _open_file(fpath)

    # ── Safety Documents tab ───────────────────────────────────────

    def _saf_completed_dir(self):
        saf = self._safety_dir()
        return os.path.join(saf, "Completed") if saf else None

    # ── Tailboards tab ────────────────────────────────────────────

    _TB_REF_DOCS = [
        ("tailboard",   "Tailboard Template"),
        ("hbr",         "Hazard Barrier Reference (HBR)"),
        ("loa",         "Limits of Approach (LOA)"),
        ("safety_regs", "Safety Practice Regulations"),
    ]
    _TB_REF_NAMES = {
        "tailboard":   "Tailboard_Template",
        "hbr":         "HBR",
        "loa":         "LOA",
        "safety_regs": "Safety_Practice_Regulations",
    }

    def _build_tailboards_tab(self, parent):
        """Tailboard reference documents tab in the planning notebook."""
        ref_lf = ttk.LabelFrame(parent, text="Reference Documents (living — superseded copies are archived)", padding=(8, 4, 8, 8))
        ref_lf.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(ref_lf, text="Ctrl+click a URL field to open it in the browser. "
                               "⬆ uploads a local copy as a backup when the site is unreachable.",
                  foreground="grey", font=("", 8)).pack(anchor="w", pady=(0, 4))

        self._tb_url_vars = {}
        for key, label in self._TB_REF_DOCS:
            row = ttk.Frame(ref_lf)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=34, anchor="w").pack(side="left")
            var = tk.StringVar(value=self.tailboard_refs.get(key, {}).get("url", ""))
            self._tb_url_vars[key] = var
            e = ttk.Entry(row, textvariable=var)
            e.pack(side="left", fill="x", expand=True, padx=(4, 4))
            _bind_url_open(e, var)
            ttk.Button(row, text="⬇ Download",
                       command=lambda k=key, v=var: self._tb_download(k, v.get().strip())).pack(side="left", padx=(0, 4))
            ttk.Button(row, text="⬆ Upload",
                       command=lambda k=key: self._tb_upload_ref(k)).pack(side="left", padx=(0, 4))
            ttk.Button(row, text="👁 Open",
                       command=lambda k=key: self._tb_open_local(k)).pack(side="left")
            var.trace_add("write", lambda *_, k=key: self._tb_url_changed(k))

        tb = ttk.Frame(parent, padding=(8, 4, 8, 2)); tb.pack(fill="x")
        ttk.Button(tb, text="⬆ Upload Document",
                   command=self._upload_tailboard_doc).pack(side="left")
        ttk.Button(tb, text="✏ Edit Selected",
                   command=self._tb_edit_doc).pack(side="left", padx=(6, 0))
        ttk.Button(tb, text="📋 Save to Completed",
                   command=self._tb_save_completed).pack(side="left", padx=(6, 0))
        ttk.Button(tb, text="⊞ Open Folder",
                   command=lambda: self._reveal_project_subfolder("Tailboards")).pack(side="left", padx=(6, 0))
        ttk.Button(tb, text="🗂 Old Revisions",
                   command=lambda: self._reveal_project_subfolder("Tailboards", "Archive")).pack(side="left", padx=(6, 0))
        ttk.Label(tb, text="  ✏ Edit snapshots the current version before opening",
                  foreground="grey", font=("", 8)).pack(side="left", padx=8)
        self._tb_lb = self._make_doc_listbox(parent, "Tailboard Files", self._tb_open_doc)
        self._refresh_tailboards_tab()

    def _tb_url_changed(self, key):
        if not hasattr(self, "_tb_url_vars"):
            return
        if key not in self.tailboard_refs:
            self.tailboard_refs[key] = {}
        self.tailboard_refs[key]["url"] = self._tb_url_vars[key].get().strip()
        self._mark_dirty()

    def _build_tailboard_headers(self):
        """Return extra HTTP headers for tailboard-site downloads.

        Uses tailboard_request_headers if set; falls back to master headers.
        """
        raw = self.app_config.get("tailboard_request_headers", "").strip()
        if raw:
            return _parse_request_headers_raw(raw)
        return self._parse_request_headers()

    def _tb_download(self, key, url):
        if not url:
            messagebox.showinfo("No URL", "Enter a URL for this document first.", parent=self)
            return
        if not self.project_folder:
            messagebox.showinfo("No Project", "Save the project first, then download.", parent=self)
            return
        dest = os.path.join(self.project_folder, "Tailboards")
        self._download_with_progress(
            "Downloading Reference Document",
            [(self._TB_REF_NAMES.get(key, key), url)],
            dest,
            extra_headers=self._build_tailboard_headers(),
            archive_revisions=True,
            on_complete=self._refresh_tailboards_tab,
        )

    def _tb_upload_ref(self, key):
        """Upload a local copy of a reference document (backup for when the site is down)."""
        if not self.project_folder:
            messagebox.showinfo("No Project", "Save the project first, then upload.", parent=self)
            return
        label = dict(self._TB_REF_DOCS).get(key, key)
        path = filedialog.askopenfilename(
            title=f"Upload {label}",
            filetypes=[("Documents", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff"),
                       ("All files", "*.*")])
        if not path:
            return
        tb_dir = os.path.join(self.project_folder, "Tailboards")
        os.makedirs(tb_dir, exist_ok=True)
        prefix = self._TB_REF_NAMES.get(key, key)
        # Retire any existing copy (any extension) into Archive/ first
        for fname in sorted(os.listdir(tb_dir)):
            if fname.startswith(prefix) and os.path.isfile(os.path.join(tb_dir, fname)):
                _archive_revision(tb_dir, fname)
        ext = os.path.splitext(path)[1].lower() or ".pdf"
        try:
            shutil.copy2(path, os.path.join(tb_dir, prefix + ext))
        except Exception as exc:
            messagebox.showerror("Upload Failed", str(exc), parent=self)
            return
        self._refresh_tailboards_tab()

    def _tb_open_local(self, key):
        """Open the locally downloaded copy of a reference document."""
        if not self.project_folder:
            return
        prefix = self._TB_REF_NAMES.get(key, key)
        tb_dir = os.path.join(self.project_folder, "Tailboards")
        if os.path.isdir(tb_dir):
            for fname in sorted(os.listdir(tb_dir)):
                if fname.startswith(prefix) and not fname.startswith("."):
                    _open_file(os.path.join(tb_dir, fname))
                    return
        messagebox.showinfo(
            "Not Downloaded",
            f"{prefix} has not been downloaded yet.\n"
            "Enter the URL above and click ⬇ Download.",
            parent=self,
        )

    def _refresh_tailboards_tab(self):
        if hasattr(self, "_tb_url_vars"):
            for key, var in self._tb_url_vars.items():
                url = self.tailboard_refs.get(key, {}).get("url", "")
                if not url and key == "tailboard":
                    # Pre-fill from the global Tailboard URL setting
                    url = self.app_config.get("tailboard_url", "")
                var.set(url)
        if hasattr(self, "_tb_lb"):
            tb_dir = os.path.join(self.project_folder, "Tailboards") if self.project_folder else ""
            self._refresh_doc_listbox(self._tb_lb, tb_dir)

    def _tb_edit_doc(self):
        if hasattr(self, "_tb_lb") and self.project_folder:
            self._open_for_editing(self._tb_lb,
                                   os.path.join(self.project_folder, "Tailboards"))

    def _tb_save_completed(self):
        """Copy the selected tailboard file to Tailboards/Completed/ as a timestamped record."""
        if not (hasattr(self, "_tb_lb") and self.project_folder):
            return
        sel = self._tb_lb.curselection()
        if not sel:
            messagebox.showinfo("Nothing Selected", "Select a file from the Tailboard Files list first.", parent=self)
            return
        fname = self._tb_lb.get(sel[0])
        src = os.path.join(self.project_folder, "Tailboards", fname)
        if not os.path.isfile(src):
            return
        completed = os.path.join(self.project_folder, "Tailboards", "Completed")
        os.makedirs(completed, exist_ok=True)
        stem, ext = os.path.splitext(fname)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest_name = f"{stem}_{stamp}{ext}"
        try:
            shutil.copy2(src, os.path.join(completed, dest_name))
        except Exception as exc:
            messagebox.showerror("Save Failed", str(exc), parent=self); return
        messagebox.showinfo("Saved",
            f"Saved to  Tailboards/Completed/{dest_name}", parent=self)

    def _upload_tailboard_doc(self):
        self._upload_docs_to(("Tailboards",), "Upload Tailboard Document",
                             after=(self._refresh_tailboards_tab,))

    def _tb_open_doc(self, _=None):
        if hasattr(self, "_tb_lb") and self.project_folder:
            self._open_doc_from_listbox(
                self._tb_lb, os.path.join(self.project_folder, "Tailboards"))

    # ── Safety Documents tab ──────────────────────────────────────

    def _build_safety_tab(self, parent):
        """Safety Documents management tab in the planning notebook."""
        tb = ttk.Frame(parent, padding=(4, 4, 4, 2)); tb.pack(fill="x")
        ttk.Button(tb, text="⬆ Upload Document",
                   command=self._upload_safety_doc).pack(side="left")
        ttk.Button(tb, text="✏ Edit Selected",
                   command=self._saf_edit_doc).pack(side="left", padx=(6, 0))
        ttk.Button(tb, text="⊞ Open Folder",
                   command=self._open_safety_folder).pack(side="left", padx=(6, 0))
        ttk.Button(tb, text="🗂 Old Revisions",
                   command=lambda: self._reveal_project_subfolder(
                       "Safety Documents", "Completed", "Archive")).pack(side="left", padx=(6, 0))
        ttk.Label(tb, text="  ✏ Edit snapshots before opening; re-uploading archives the old revision",
                  foreground="grey", font=("", 8)).pack(side="left", padx=8)
        self._saf_tab_lb = self._make_doc_listbox(
            parent, "Safety Documents / Completed", self._saf_tab_open)
        self._refresh_safety_tab()

    def _refresh_safety_tab(self):
        if hasattr(self, "_saf_tab_lb"):
            self._refresh_doc_listbox(self._saf_tab_lb, self._saf_completed_dir(),
                                      newest_first=True)

    def _saf_tab_open(self, _=None):
        self._open_doc_from_listbox(self._saf_tab_lb, self._saf_completed_dir())

    def _saf_edit_doc(self):
        if hasattr(self, "_saf_tab_lb"):
            self._open_for_editing(self._saf_tab_lb, self._saf_completed_dir())

    def _upload_safety_doc(self):
        after = [self._refresh_safety_tab]
        if hasattr(self, "_saf_rev_lb"):
            after.append(self._refresh_saf_revisions)
        self._upload_docs_to(("Safety Documents", "Completed"),
                             "Upload Safety Documents", after)

    def _open_safety_folder(self):
        self._reveal_project_subfolder("Safety Documents")

    # ── Other Documents tab ────────────────────────────────────────

    def _other_docs_dir(self):
        if not self.project_folder:
            return None
        return os.path.join(self.project_folder, "Other Documents")

    def _build_other_docs_tab(self, parent):
        """Other Documents tab — plain file upload and management."""
        tb = ttk.Frame(parent, padding=(4, 4, 4, 2)); tb.pack(fill="x")
        ttk.Button(tb, text="⬆ Upload Document(s)",
                   command=self._upload_other_doc).pack(side="left")
        ttk.Button(tb, text="✏ Edit Selected",
                   command=self._other_doc_edit).pack(side="left", padx=(6, 0))
        ttk.Button(tb, text="⊞ Open Folder",
                   command=self._open_other_docs_folder).pack(side="left", padx=(6, 0))
        ttk.Button(tb, text="✕ Remove Selected",
                   command=self._remove_other_doc).pack(side="left", padx=(6, 0))
        ttk.Label(tb, text="  ✏ Edit snapshots before opening",
                  foreground="grey", font=("", 8)).pack(side="left", padx=8)
        self._other_docs_lb = self._make_doc_listbox(
            parent, "Uploaded Documents", self._other_doc_open)
        self._refresh_other_docs_tab()

    def _refresh_other_docs_tab(self):
        if hasattr(self, "_other_docs_lb"):
            self._refresh_doc_listbox(self._other_docs_lb, self._other_docs_dir())

    def _other_doc_open(self, _=None):
        self._open_doc_from_listbox(self._other_docs_lb, self._other_docs_dir())

    def _other_doc_edit(self):
        if hasattr(self, "_other_docs_lb"):
            self._open_for_editing(self._other_docs_lb, self._other_docs_dir())

    def _upload_other_doc(self):
        self._upload_docs_to(("Other Documents",), "Upload Other Documents",
                             (self._refresh_other_docs_tab,))

    def _remove_other_doc(self):
        sel = self._other_docs_lb.curselection()
        d   = self._other_docs_dir()
        if not (sel and d):
            return
        fn = self._other_docs_lb.get(sel[0])
        if not messagebox.askyesno("Remove", f"Delete  {fn}  from Other Documents?"):
            return
        try:
            os.remove(os.path.join(d, fn))
        except Exception as exc:
            messagebox.showerror("Error", str(exc)); return
        self._refresh_other_docs_tab()

    def _open_other_docs_folder(self):
        self._reveal_project_subfolder("Other Documents")

    def _show_impl_prep(self):
        """Generate the project briefing shown when the PREP row is selected."""
        lines = []
        proj = self.project_var.get().strip() or "(unnamed project)"
        lines += [f"{'='*60}", f"  PROJECT BRIEFING", f"  {proj}", f"{'='*60}", ""]

        notes = self.title_notes.get("1.0", "end").strip()
        if notes:
            lines += ["NOTES", "-"*40, notes, ""]

        crows = self.title_page.get("crows", [])
        if crows:
            lines += ["CROW OUTAGE RECORDS", "-"*40]
            for c in crows:
                num = c.get("outage_number", "")
                url = c.get("url", "")
                lines.append(f"  {num:<20}  {url}")
            lines.append("")

        if self.drawing_registry:
            lines += ["DRAWINGS", "-"*40]
            for name, info in sorted(self.drawing_registry.items()):
                rev  = f"  Rev {info['rev']}" if info.get("rev") else ""
                titl = f"  {info['title']}"   if info.get("title") else ""
                url  = f"\n    {info['url']}" if info.get("url") else ""
                lines.append(f"  {name}{rev}{titl}{url}")
            lines.append("")

        if self.relay_registry:
            lines += ["RELAY / DEVICE SETTINGS", "-"*40]
            for dev_id, info in sorted(self.relay_registry.items()):
                eng     = f"  Eng: {info['engineer']}"         if info.get("engineer") else ""
                rev     = f"  Rev {info['revision']}"          if info.get("revision") else ""
                contact = f"\n    Phone/Contact: {info['contact']}" if info.get("contact") else ""
                url     = f"\n    {info['url']}"               if info.get("url") else ""
                lines.append(f"  {dev_id}{rev}{eng}{contact}{url}")
            lines.append("")

        # Collect all standards referenced in jobs
        maint_jobs = {}  # standard_id -> [job desc, ...]
        eng_jobs   = {}
        for i, job in enumerate(self.jobs):
            desc = job.get("description","") or f"Job #{i+1}"
            for ms in _std_list(job, "maintenance_standards"):
                maint_jobs.setdefault(ms, []).append(desc)
            for es in _std_list(job, "engineering_standards"):
                eng_jobs.setdefault(es, []).append(desc)

        if maint_jobs or self.maintenance_standards_registry:
            lines += ["MAINTENANCE STANDARDS", "-"*40]
            for sid, info in sorted(self.maintenance_standards_registry.items()):
                rev  = f"  Rev {info['revision']}" if info.get("revision") else ""
                url_t = f"\n    Telecom: {info['url_telecom']}" if info.get("url_telecom") else ""
                url_r = f"\n    Transmission: {info['url_transmission']}" if info.get("url_transmission") else ""
                jobs_ref = maint_jobs.get(sid, [])
                ref_str = f"\n    Jobs: {', '.join(jobs_ref)}" if jobs_ref else ""
                lines.append(f"  {sid}{rev}{url_t}{url_r}{ref_str}")
            lines.append("")

        if eng_jobs or self.engineering_standards_registry:
            lines += ["ENGINEERING STANDARDS", "-"*40]
            for sid, info in sorted(self.engineering_standards_registry.items()):
                rev  = f"  Rev {info.get('revision','')}" if info.get("revision") else ""
                url  = f"\n    URL: {info['url']}" if info.get("url") else ""
                stype = f"  ({info.get('standard_type','')})" if info.get("standard_type") else ""
                jobs_ref = eng_jobs.get(sid, [])
                ref_str = f"\n    Jobs: {', '.join(jobs_ref)}" if jobs_ref else ""
                lines.append(f"  {sid}{stype}{rev}{url}{ref_str}")
            lines.append("")

        lines += [f"{'─'*60}", f"  Total work order steps: {len(self.jobs)}", f"{'─'*60}"]

        self.impl_preview.configure(state="normal")
        self.impl_preview.delete("1.0", "end")
        self.impl_preview.insert("1.0", "\n".join(lines))
        self.impl_preview.configure(state="disabled")

    def _on_impl_tree_click(self, event):
        if self.impl_tree.identify_region(event.x, event.y) != "cell": return
        if self.impl_tree.identify_column(event.x) != "#1": return
        row = self.impl_tree.identify_row(event.y)
        if not row or row == "__prep__": return
        if row == "__tailboard__":
            self.title_page["tailboard_done"] = not self.title_page.get("tailboard_done", False)
            self._refresh_impl_list()
            self.impl_tree.selection_set("__tailboard__")
            self._on_impl_select()
            return
        if row == "__safety__":
            self.title_page["safety_done"] = not self.title_page.get("safety_done", False)
            self._refresh_impl_list()
            self.impl_tree.selection_set("__safety__")
            self._on_impl_select()
            return
        idx = int(row)
        if 0 <= idx < len(self.jobs):
            self.jobs[idx]["completed"] = not self.jobs[idx].get("completed", False)
            self._refresh_list()
            self.impl_tree.selection_set(str(idx))
            self._on_impl_select()

    # ── Job list ─────────────────────────────────────────────────

    def _refresh_list(self):
        for iid in self.tree.get_children(): self.tree.delete(iid)
        disp = JOB_TYPE_SHORT
        for i,job in enumerate(self.jobs):
            done = job.get("completed", False)
            tags = (job["type"], "COMPLETED") if done else (job["type"],)
            self.tree.insert("","end",iid=str(i),
                values=("☑" if done else "☐", i+1,
                        disp.get(job["type"],job["type"]), job.get("description","")),
                tags=tags)
        self._update_status()
        self._refresh_impl_list()
        self._refresh_device_list()
        self._mark_dirty()

    def _on_tree_click(self, event):
        """Toggle completed on click in the ☐/☑ Done column."""
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        row = self.tree.identify_row(event.y)
        if not row:
            return
        idx = int(row)
        if 0 <= idx < len(self.jobs):
            self.jobs[idx]["completed"] = not self.jobs[idx].get("completed", False)
            self._refresh_list()
            self.tree.selection_set(str(idx))
            self._on_select()

    def _update_status(self):
        n = len(self.jobs)
        if n == 0: self.status_var.set("No jobs"); return
        counts = {}
        for j in self.jobs: counts[j["type"]] = counts.get(j["type"],0)+1
        parts = "  |  ".join(f"{v} {k}" for k,v in counts.items())
        fname = os.path.basename(self.current_file) if self.current_file else "unsaved"
        self.status_var.set(f"{fname}    {n} job(s):  {parts}")

    def _selected_indices(self):
        return sorted(int(iid) for iid in self.tree.selection())

    def _selected_idx(self):
        idxs = self._selected_indices(); return idxs[0] if idxs else None

    def _on_select(self, _=None):
        idxs = self._selected_indices()
        if not idxs: return
        text = format_job(idxs[0], self.jobs[idxs[0]])
        self.preview.configure(state="normal"); self.preview.delete("1.0","end")
        self.preview.insert("1.0",text); self.preview.configure(state="disabled")

    # ── CRUD ─────────────────────────────────────────────────────

    def _add_job(self, job_type):
        dlg = JobDialog(self, job_type, registry=self.drawing_registry,
                        history=self.history, ep_history=self.ep_history, jobs=self.jobs,
                        settings=self._get_settings_with_pts(),
                        maintenance_standards=self.maintenance_standards_registry,
                        engineering_standards=self.engineering_standards_registry,
                        ctrl_desks=self._app_db.get_ctrl_desks(),
                        crows=self.title_page.get("crows", []),
                        pts_files=self.pts_files,
                        on_complete_pts=self._pts_save_completion)
        if dlg.result:
            self._collect_history(dlg.result)
            self.jobs.append(dlg.result); self._refresh_list(); self._refresh_drawings_list()
            idx = len(self.jobs)-1; self.tree.selection_set(str(idx)); self._on_select()

    def _edit_job(self):
        idx = self._selected_idx()
        if idx is None: messagebox.showinfo("Select a Job","Please select a job from the list."); return
        dlg = JobDialog(self, self.jobs[idx]["type"], existing=deepcopy(self.jobs[idx]),
                        registry=self.drawing_registry, history=self.history,
                        ep_history=self.ep_history, jobs=self.jobs,
                        settings=self._get_settings_with_pts(),
                        maintenance_standards=self.maintenance_standards_registry,
                        engineering_standards=self.engineering_standards_registry,
                        ctrl_desks=self._app_db.get_ctrl_desks(),
                        crows=self.title_page.get("crows", []),
                        pts_files=self.pts_files,
                        on_complete_pts=self._pts_save_completion)
        if dlg.result:
            self._collect_history(dlg.result)
            self.jobs[idx]=dlg.result; self._refresh_list(); self._refresh_drawings_list()
            self.tree.selection_set(str(idx)); self._on_select()

    def _duplicate_job(self):
        idx = self._selected_idx()
        if idx is None: messagebox.showinfo("Select a Job","Please select a job to duplicate."); return
        copy = deepcopy(self.jobs[idx]); desc = copy.get("description","")
        copy["description"] = f"{desc} (copy)" if desc else "(copy)"
        self.jobs.insert(idx+1,copy); self._refresh_list(); self.tree.selection_set(str(idx+1)); self._on_select()

    def _delete_job(self):
        idxs = self._selected_indices()
        if not idxs: messagebox.showinfo("Select a Job","Please select a job to delete."); return
        msg = f"Delete {len(idxs)} selected jobs?" if len(idxs)>1 else f"Delete Job #{idxs[0]+1}?"
        if messagebox.askyesno("Delete",msg):
            for idx in reversed(idxs): self.jobs.pop(idx)
            self._refresh_list(); self._refresh_drawings_list()
            self.preview.configure(state="normal"); self.preview.delete("1.0","end"); self.preview.configure(state="disabled")

    def _move_up(self):
        idx = self._selected_idx()
        if idx is None or idx==0: return
        self.jobs[idx-1],self.jobs[idx]=self.jobs[idx],self.jobs[idx-1]
        self._refresh_list(); self.tree.selection_set(str(idx-1)); self._on_select()

    def _move_down(self):
        idx = self._selected_idx()
        if idx is None or idx>=len(self.jobs)-1: return
        self.jobs[idx],self.jobs[idx+1]=self.jobs[idx+1],self.jobs[idx]
        self._refresh_list(); self.tree.selection_set(str(idx+1)); self._on_select()

    # ── Swap start ↔ end ─────────────────────────────────────────

    def _swap_endpoints(self):
        idx = self._selected_idx()
        if idx is None:
            messagebox.showinfo("Swap","Select a REMOVE or ADD job to swap its start and end."); return
        job = self.jobs[idx]
        if job["type"] not in ("REMOVE","ADD"):
            messagebox.showinfo("Swap","Only REMOVE and ADD jobs support swapping endpoints."); return
        job["start"], job["end"] = deepcopy(job["end"]), deepcopy(job["start"])
        self._refresh_list(); self.tree.selection_set(str(idx)); self._on_select()
        self.status_var.set(f"Job #{idx+1}: start and end endpoints swapped.")

    # ── Auto-group by device ──────────────────────────────────────

    def _auto_group(self):
        if not self.jobs:
            return

        def get_devices(job):
            devs = set()
            for k in ("start","end","add_start","add_end"):
                d = (job.get(k) or {}).get("device","").strip().upper()
                if d:
                    devs.add(d)
            return devs

        def group_pool(pool):
            """Greedy clustering: pull jobs with shared devices together."""
            grouped, used = [], set()
            for i, job in enumerate(pool):
                if i in used:
                    continue
                used.add(i)
                grouped.append(job)
                devs = get_devices(job)
                for j, job2 in enumerate(pool):
                    if j in used:
                        continue
                    if get_devices(job2) & devs:
                        used.add(j)
                        grouped.append(job2)
                        devs |= get_devices(job2)
            return grouped

        removes = [j for j in self.jobs if j["type"] == "REMOVE"]
        adds    = [j for j in self.jobs if j["type"] == "ADD"]
        others  = [j for j in self.jobs if j["type"] not in ("REMOVE","ADD")]

        self.jobs = group_pool(removes) + group_pool(adds) + others
        self._refresh_list()
        self.status_var.set(
            f"Auto-grouped: {len(removes)} remove(s) and {len(adds)} add(s) sorted by shared device.")

    # ──────────────────────────────────────────────────────────────────
    # Export (wizard)
    # ──────────────────────────────────────────────────────────────────

    def _open_export_wizard(self):
        ExportWizard(self, self)

    # ──────────────────────────────────────────────────────────────────
    # Save / Load
    # ──────────────────────────────────────────────────────────────────

    def _new_plan(self):
        if self.jobs and not messagebox.askyesno("New Plan","Discard current plan and start fresh?"): return
        self.jobs=[]; self.drawing_registry={}; self.relay_registry={}
        self.maintenance_standards_registry={}; self.engineering_standards_registry={}
        self.pts_files={}
        self.current_file=None; self.project_folder=None
        self.project_var.set("")
        self.history = {"device": [], "location": [], "pin": [], "panel": [], "wire": []}
        self.ep_history = []
        self.title_page = {"notes": "", "crows": []}
        self.title_notes.delete("1.0", "end")
        self.title("Red-Line-Routing")
        self._refresh_list(); self._refresh_drawings_list()
        self._refresh_relay_list(); self._refresh_maintenance_list()
        self._refresh_engineering_list(); self._refresh_crows()
        self._refresh_pts_tab()
        self.preview.configure(state="normal"); self.preview.delete("1.0","end")
        self.preview.configure(state="disabled")
        self.impl_preview.configure(state="normal"); self.impl_preview.delete("1.0","end")
        self.impl_preview.configure(state="disabled")

    def _open(self):
        # File format: a single JSON object with keys:
        #   project, title_page, drawing_registry, relay_settings, history, jobs
        # After loading we re-scan jobs to backfill any drawings that exist in job
        # endpoints but are missing from the registry (handles files saved by older
        # versions that lacked the registry), then rebuild ep_history from scratch.
        if not self._check_unsaved():
            return
        path = filedialog.askopenfilename(filetypes=[("Red-Line Plan","*.redline"),("Legacy wirePlan","*.wirePlan"),("JSON","*.json"),("All","*.*")])
        if not path: return
        try:
            with open(path,encoding="utf-8") as fh: data = json.load(fh)
            self.jobs = data.get("jobs",[]); self.project_var.set(data.get("project",""))
            self.drawing_registry = data.get("drawing_registry",{})
            self.relay_registry   = data.get("relay_settings",{})  # key kept as "relay_settings" for file compatibility
            self.maintenance_standards_registry = data.get("maintenance_standards", {})
            self.engineering_standards_registry = data.get("engineering_standards", {})
            self.pts_files = data.get("pts_files", {})
            self.history = data.get("history", {"device":[],"location":[],"pin":[],"panel":[],"wire":[]})
            # Older files carried a per-project drawing cache — fold it
            # into the global DB so nothing is lost, then ignore it.
            legacy_cache = data.get("drawing_search_cache", {})
            if legacy_cache:
                try: self._app_db.cache_merge_legacy(legacy_cache)
                except sqlite3.Error: pass
            self.title_page = data.get("title_page", {"notes": "", "crows": []})
            self.tailboard_refs = data.get("tailboard_refs", _empty_tailboard_refs())
            self.current_file = path
            self.project_folder = os.path.dirname(path)
            self._schedule_tailboard_check()
            self._scan_jobs_for_drawings()
            self._rebuild_history()
            self.title_notes.delete("1.0", "end")
            self.title_notes.insert("1.0", self.title_page.get("notes", ""))
            self._refresh_list(); self._refresh_drawings_list()
            self._refresh_relay_list(); self._refresh_maintenance_list()
            self._refresh_engineering_list(); self._refresh_crows()
            self._refresh_tailboards_tab(); self._refresh_safety_tab(); self._refresh_other_docs_tab()
            self._refresh_pts_tab()
            if self.mode_var.get() == "impl": self._refresh_file_tabs()
            proj = data.get("project","") or os.path.splitext(os.path.basename(path))[0]
            self.title(f"Red-Line-Routing — {proj}")
            self._mark_clean()
            self.after_idle(lambda: self._refresh_drawing_cache_bg(stale_only=True))
            self._remember_all_standards()
        except Exception as exc: messagebox.showerror("Open Error",str(exc))

    def _save(self):
        if not self.current_file: self._save_as()
        else: self._write(self.current_file)

    def _save_as(self):
        proj = self.project_var.get().strip()
        safe = "".join(c if c not in r'<>:"/\|?*' else "_" for c in proj) if proj else "RedLine_Plan"
        parent = filedialog.askdirectory(title="Choose where to create the project folder")
        if not parent: return
        folder = os.path.join(parent, safe)
        try:
            os.makedirs(folder, exist_ok=True)
            for sub in ("Drawings", "Relay Settings", "Maintenance Standards", "Engineering Standards",
                        "CROW Outage", "Other", "PTS",
                        os.path.join("Tailboards", "Completed"),
                        os.path.join("Safety Documents", "Completed"),
                        "Other Documents"):
                os.makedirs(os.path.join(folder, sub), exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Save Error", f"Could not create project folder:\n{exc}"); return
        self.project_folder = folder
        self._schedule_tailboard_check()
        path = os.path.join(folder, safe + ".redline")
        self.current_file = path
        self._write(path)

    def _write(self, path):
        # Serialise the entire project to a single JSON file (indent=2 for readability).
        # title_notes is a tk.Text widget so its content must be pulled out here rather
        # than being stored continuously in title_page["notes"].
        try:
            tp = dict(self.title_page)
            tp["notes"] = self.title_notes.get("1.0", "end").strip()
            with open(path,"w",encoding="utf-8") as fh:
                json.dump({"project":self.project_var.get().strip(),
                           "title_page":tp,
                           "drawing_registry":self.drawing_registry,
                           "relay_settings":self.relay_registry,           # key kept as "relay_settings" for file compatibility
                           "maintenance_standards":self.maintenance_standards_registry,
                           "engineering_standards":self.engineering_standards_registry,
                           "pts_files":self.pts_files,
                           "tailboard_refs":self.tailboard_refs,
                           "history":self.history,
                           "jobs":self.jobs},fh,indent=2)
            proj = self.project_var.get().strip() or os.path.splitext(os.path.basename(path))[0]
            self.title(f"Red-Line-Routing — {proj}")
            self._update_status()
            self._remember_all_standards()
            self._mark_clean()
        except Exception as exc: messagebox.showerror("Save Error",str(exc))

    # How often to look for stale cache entries, and how old an entry must
    # be before it is re-fetched. The age is configurable via the
    # drawing_cache_refresh_hours app setting.
    _CACHE_CHECK_INTERVAL_MS = 15 * 60 * 1000   # check every 15 minutes
    _CACHE_DEFAULT_MAX_AGE_H = 4.0              # refresh entries older than 4 h

    def _eng_cache_load_from_db(self):
        """Populate the in-memory engineering cache from the SQLite DB on first use."""
        if not hasattr(self, "_eng_cache"):
            return
        rows = self._app_db.eng_cache_load_all()
        for series_value, (cached_at, results_json) in rows.items():
            try:
                from engineering_standards.models import EngineeringStandard
                results = [EngineeringStandard(**r)
                           for r in json.loads(results_json)]
                with self._eng_cache._lock:
                    self._eng_cache._data[series_value] = (cached_at, results)
            except Exception:
                pass

    def _eng_cache_persist_all(self):
        """Flush every series currently in the in-memory cache to SQLite."""
        if not hasattr(self, "_eng_cache"):
            return
        with self._eng_cache._lock:
            keys = list(self._eng_cache._data.keys())
        for sv in keys:
            self._eng_cache_persist(sv)

    def _eng_cache_persist(self, series_value: str):
        """Flush one series from the in-memory cache to the SQLite DB."""
        if not hasattr(self, "_eng_cache"):
            return
        with self._eng_cache._lock:
            entry = self._eng_cache._data.get(series_value)
        if entry is None:
            return
        cached_at, results = entry
        try:
            results_json = json.dumps([r.__dict__ for r in results])
            self._app_db.eng_cache_put(series_value, results_json, cached_at)
        except Exception:
            pass

    def _set_cache_activity(self, label: str):
        """Show or hide the status-bar cache chip. Call from the main thread only."""
        if not hasattr(self, "_cache_chip"):
            return
        if label:
            self._cache_chip_lbl.config(text=f"↺ {label}")
            self._cache_chip.pack(side="right", padx=(0, 4), pady=1)
        else:
            self._cache_chip.pack_forget()

    def _cancel_cache_refresh(self):
        """Signal all running background cache refreshes to stop."""
        self._draw_cache_cancel.set()
        self._eng_cache_cancel.set()
        self._set_cache_activity("")
        self.status_var.set("Cache refresh cancelled.")

    def _cache_max_age_seconds(self):
        try:
            hours = float(self.app_config.get(
                "drawing_cache_refresh_hours", self._CACHE_DEFAULT_MAX_AGE_H))
        except (TypeError, ValueError):
            hours = self._CACHE_DEFAULT_MAX_AGE_H
        return max(0.25, hours) * 3600.0

    def _schedule_drawing_cache_refresh(self, first_delay_ms=30000):
        """Start the periodic stale-entry refresh loop (runs for app lifetime)."""
        def _tick():
            self._refresh_drawing_cache_bg(stale_only=True)
            self.after(self._CACHE_CHECK_INTERVAL_MS, _tick)
        self.after(first_delay_ms, _tick)

    def _refresh_drawing_cache_bg(self, stale_only=False):
        """Refresh cached drawing-search entries in a background thread.

        stale_only=True (periodic timer) re-fetches only entries older than
        the configured max age; stale_only=False (project open) refreshes
        everything. Skips silently when a refresh is already running or
        drawing search is not configured.
        """
        if getattr(self, "_low_bw", None) and self._low_bw.get():
            return
        if not _DRAWING_SEARCH_AVAILABLE:
            return
        if getattr(self, "_cache_refresh_running", False):
            return
        cache = self._drawing_cache
        if stale_only:
            raw = self._app_db.cache_stale_keys(self._cache_max_age_seconds())
            keys = [tuple(p) for p in (k.split("|") for k in raw)
                    if len(p) == 4]
        else:
            keys = list(cache.iter_keys())
        if not keys:
            return
        base_url = self.app_config.get("drawing_search_url", "").strip()
        if not base_url:
            return
        raw_hdrs = self.app_config.get("request_headers", "")
        cookies  = _parse_cookies_from_headers(raw_hdrs)
        extra    = _parse_request_headers_raw(raw_hdrs)
        extra.pop("Cookie", None)
        client   = DrawingSearchClient(base_url=base_url, cookies=cookies,
                                        extra_headers=extra or None)

        self._cache_refresh_running = True
        self._draw_cache_cancel.clear()
        total = len(keys)
        self.after(0, self._set_cache_activity,
                   f"Drawing cache  (0 / {total})")

        def _run():
            done = 0
            try:
                for i, (fac, typ, subj, state) in enumerate(keys):
                    if self._draw_cache_cancel.is_set():
                        break
                    try:
                        params = SearchParams(facility=fac, drawing_type=typ,
                                              drawing_subject=subj, state=state)
                        results = client.search_all_pages(params)
                        cache.put(params, results)
                        done += 1
                        self.after(0, self._set_cache_activity,
                                   f"Drawing cache  ({done} / {total})")
                    except Exception:
                        pass
            finally:
                self._cache_refresh_running = False
                cancelled = self._draw_cache_cancel.is_set()
                def _finish(d=done, c=cancelled):
                    self._set_cache_activity("")
                    if d or not c:
                        self.status_var.set(
                            f"Drawing cache refreshed — {d} search(es) updated"
                            + (" (cancelled)" if c else ""))
                self.after(0, _finish)

        threading.Thread(target=_run, daemon=True).start()

    # ── Engineering standards cache refresh ───────────────────────────

    _ENG_CACHE_CHECK_INTERVAL_MS = 15 * 60 * 1000  # check every 15 minutes
    _ENG_CACHE_DEFAULT_MAX_AGE_H = 4.0

    def _eng_cache_max_age_seconds(self):
        try:
            hours = float(self.app_config.get(
                "engineering_cache_refresh_hours", self._ENG_CACHE_DEFAULT_MAX_AGE_H))
        except (TypeError, ValueError):
            hours = self._ENG_CACHE_DEFAULT_MAX_AGE_H
        return max(0.25, hours) * 3600.0

    def _schedule_engineering_cache_refresh(self, first_delay_ms=45000):
        """Start the periodic stale-entry refresh loop for engineering standards."""
        def _tick():
            self._refresh_engineering_cache_bg()
            self.after(self._ENG_CACHE_CHECK_INTERVAL_MS, _tick)
        self.after(first_delay_ms, _tick)

    def _refresh_engineering_cache_bg(self):
        """Re-fetch stale engineering standards cache entries in a background thread."""
        if getattr(self, "_low_bw", None) and self._low_bw.get():
            return
        if not _ENG_STD_AVAILABLE:
            return
        if getattr(self, "_eng_cache_refresh_running", False):
            return
        cache = getattr(self, "_eng_cache", None)
        if cache is None:
            return
        stale = [sv for sv in cache._data if cache.is_stale(sv)]
        if not stale:
            return
        client = self._build_eng_client()
        if client is None:
            return
        self._eng_cache_refresh_running = True
        self._eng_cache_cancel.clear()
        total = len(stale)
        self.after(0, self._set_cache_activity,
                   f"Eng. standards  (0 / {total})")

        def _run():
            done = 0
            try:
                for series_value in stale:
                    if self._eng_cache_cancel.is_set():
                        break
                    try:
                        client.fetch_section(series_value)
                        self._eng_cache_persist(series_value)
                        done += 1
                        self.after(0, self._set_cache_activity,
                                   f"Eng. standards  ({done} / {total})")
                    except Exception:
                        pass
            finally:
                self._eng_cache_refresh_running = False
                cancelled = self._eng_cache_cancel.is_set()
                def _finish(d=done, c=cancelled):
                    self._set_cache_activity("")
                    if d or not c:
                        self.status_var.set(
                            f"Engineering standards cache refreshed — {d} series updated"
                            + (" (cancelled)" if c else ""))
                self.after(0, _finish)

        threading.Thread(target=_run, daemon=True).start()


