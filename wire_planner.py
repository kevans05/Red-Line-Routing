#!/usr/bin/env python3
"""
Red-Line-Routing
----------------
All-in-one electrical job planner: work orders, drawings, relay settings, CROWs.
Save/load plans as project folders with .redline JSON and organised subfolders.
Export Wizard builds print-ready PDF packages, hyperlinked HTML, and tablet output.
"""

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

# Ensure the directory containing wire_planner.py is on sys.path so that
# sibling packages (drawing_search/) are always importable, regardless of
# the working directory the app is launched from.
_app_dir = os.path.dirname(os.path.abspath(__file__))
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

try:
    from drawing_search import (DrawingSearchClient, SearchParams,
                                DrawingResult, PagedResults, DrawingSearchCache,
                                DRAWING_TYPES, DRAWING_SUBJECTS, FACILITIES,
                                load_cached_options, save_cached_options,
                                fetch_form_options)
    _DRAWING_SEARCH_AVAILABLE = True
except ImportError:
    _DRAWING_SEARCH_AVAILABLE = False


# ──────────────────────────────────────────────────────────────────
# Drawing-type helper
# ──────────────────────────────────────────────────────────────────

def is_h_type_drawing(name):
    """Return True if drawing type character Y == 'H'.
    Format: XXXX-YZZ-IIII-N  or  YZZ-IIII-N"""
    if not name:
        return False
    parts = name.strip().split("-")
    type_seg = parts[1] if len(parts) >= 4 else parts[0]
    return bool(type_seg) and type_seg[0].upper() == "H"


def _treeview_strike_font():
    """Return an overstrike font that matches the ttk Treeview row font exactly.
    Using tkfont.Font(overstrike=True) alone creates a default-sized font which
    looks wrong next to Treeview rows; this copies family/size/weight from the
    current ttk style so the strikethrough rows stay the same size."""
    try:
        fname = ttk.Style().lookup("Treeview", "font") or "TkDefaultFont"
        base  = tkfont.nametofont(fname)
    except Exception:
        base  = tkfont.nametofont("TkDefaultFont")
    info = base.actual()
    return tkfont.Font(family=info["family"], size=info["size"],
                       weight=info["weight"], slant=info["slant"],
                       overstrike=True)


def _drawing_subdir(base_dir, drawing_name):
    """Resolve the organised subfolder for a drawing name XXXX-YZZ-NNNNN-MMM.

    Folder layout:  base_dir / XXXX / YZZ / NNNNN /
    Falls back to base_dir for names that don't match the 4-part convention.
    """
    parts = drawing_name.strip().split("-")
    if len(parts) >= 3:
        facility  = parts[0]   # XXXX
        type_subj = parts[1]   # YZZ  (drawing type + subject combined)
        serial    = parts[2]   # NNNNN
        return os.path.join(base_dir, facility, type_subj, serial)
    return base_dir


def _archive_existing(folder, drawing_name):
    """Move files in *folder* whose stem starts with *drawing_name* into Archive/.

    Called before saving a fresh download so the old revision is preserved.
    Returns the number of files moved.
    """
    archive_dir = os.path.join(folder, "Archive")
    moved = 0
    try:
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            if not os.path.isfile(fpath):
                continue
            stem = os.path.splitext(fname)[0]
            if stem.lower().startswith(drawing_name.lower()):
                os.makedirs(archive_dir, exist_ok=True)
                shutil.move(fpath, os.path.join(archive_dir, fname))
                moved += 1
    except OSError:
        pass
    return moved


def _file_url_to_path(url: str) -> str:
    """Convert a file:// URL to a local filesystem path.

    Handles UNC network shares (file://server/share/path → \\\\server\\share\\path
    on Windows) and URL-encoded characters (%20 → space, etc.).
    """
    parsed = urllib.parse.urlparse(url)
    path = urllib.parse.unquote(parsed.path)
    if parsed.netloc:
        # Network share: file://server/share/path
        if os.name == "nt":
            return "\\\\" + parsed.netloc + path.replace("/", "\\")
        return "//" + parsed.netloc + path
    # Local path: file:///C:/... or file:///unix/path
    if os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]  # strip leading slash before drive letter
    return path


# ──────────────────────────────────────────────────────────────────
# Data helpers
# ──────────────────────────────────────────────────────────────────

def empty_endpoint():
    return {"device": "", "location": "", "pin": "", "panel": "",
            "drawing": "", "drawing_rev": "", "drawing_url": "", "drawing_cell": ""}


def empty_protection():
    return {"equipment": "", "location": "", "panel": "", "notes": "",
            "drawings": [], "iso_points": [], "mb_enabled": False, "mb_remote": "", "mb_notes": ""}


def empty_job(job_type="REMOVE"):
    if job_type in ("BLOCK", "UNBLOCK"):
        return {"type": job_type, "description": "", "protection": empty_protection()}
    if job_type in ("TESTING", "ISOLATION"):
        return {"type": job_type, "description": "", "notes": ""}
    if job_type == "CR_PROT":
        return {"type": "CR_PROT", "description": "", "desks": [], "crows": [], "notes": ""}
    if job_type in ("DEVICE ADD", "DEVICE REMOVE"):
        return {"type": job_type, "description": "",
                "endpoint": empty_endpoint(), "notes": ""}
    job = {"type": job_type, "description": "", "wire": "",
           "start": empty_endpoint(), "end": empty_endpoint()}
    if job_type == "MOVE":
        job["add_wire"] = ""
        job["add_start"] = empty_endpoint()
        job["add_end"] = empty_endpoint()
    return job


def _get_prot_drawings(prot):
    """Return the drawings list from a protection dict (handles old single-drawing format)."""
    drawings = prot.get("drawings", [])
    if not drawings and prot.get("drawing"):
        drawings = [{"drawing":      prot.get("drawing", ""),
                     "drawing_rev":  prot.get("drawing_rev", ""),
                     "drawing_url":  prot.get("drawing_url", ""),
                     "drawing_cell": prot.get("drawing_cell", "")}]
    return drawings


# ──────────────────────────────────────────────────────────────────
# Search-as-you-type helper (Google-style combobox filtering)
# ──────────────────────────────────────────────────────────────────

def _bind_search_combobox(combo, get_values_fn):
    """Attach live search filtering to a ttk.Combobox.

    Uses the same non-focus-stealing custom popup as _ComboFilterHelper so
    that typing does not dismiss the suggestion list on each keystroke.
    get_values_fn is a zero-argument callable returning the current candidate list.
    """
    _ComboFilterHelper(combo, get_values_fn)


def _bind_url_open(entry_widget, url_var):
    """Ctrl+click on a URL entry opens it in the browser."""
    def _open(event):
        url = url_var.get().strip()
        if url:
            webbrowser.open(url)
    entry_widget.bind("<Control-Button-1>", _open)


# ──────────────────────────────────────────────────────────────────
# Drawing-aware endpoint frame (with conditional Drawing Cell)
# ──────────────────────────────────────────────────────────────────

class DrawingAwareFrame(ttk.LabelFrame):
    """LabelFrame with autofill from registry, context-aware suggestions, and live search.

    How context-aware suggestions work:
      Each HISTORY_KEYS combobox is populated by _context_suggestions(), which scores
      candidate values by how many of the 'context' fields (see _CONTEXT_MAP) match what
      the user has already typed in the same form.  Matches rise to the top; the rest of
      the flat history follows below.  A green border (_refresh_highlights) indicates when
      context is active.

    ep_history: a list of full endpoint dicts (device/location/pin/panel/drawing) collected
      from every job saved so far.  It is the source for the co-occurrence scoring in
      _context_suggestions.  The flat per-key history (self.history) provides fallback values
      for fields with no context match.
    """

    FIELDS = []          # subclasses define
    HISTORY_KEYS = ()    # keys that get a history/context Combobox

    # Which fields provide context when building suggestions for each key
    _CONTEXT_MAP = {
        "location":     ["device"],
        "pin":          ["device"],
        "panel":        ["device", "location"],
        "drawing":      ["panel", "device"],
        "drawing_cell": ["device", "location", "pin", "drawing"],
    }

    def __init__(self, parent, title, registry=None, history=None, ep_history=None, **kwargs):
        super().__init__(parent, text=title, padding=6, **kwargs)
        self.vars = {}
        self.registry   = registry   if registry   is not None else {}
        self.history    = history    if history    is not None else {}
        # ep_history: list of full endpoint dicts used for co-occurrence scoring
        self.ep_history = ep_history if ep_history is not None else []
        self._drawing_combo = None
        self._cell_entry = None
        self._context_combos = {}   # key → wrapper tk.Frame (for green highlight border)
        self._build()

    def _context_suggestions(self, key):
        """Return an ordered suggestion list for *key*, boosting values that co-occur
        with whatever is already typed in context fields.

        Scoring algorithm:
          1. Walk ep_history (most-recent first).  For each past endpoint, count how many
             of the context fields (per _CONTEXT_MAP) match what the user has typed so far.
          2. Any value with score > 0 is promoted to the 'prioritized' bucket (in encounter
             order, deduplicated case-insensitively).
          3. Remaining values from the flat per-key history fill the 'rest' bucket.
          4. Return prioritized + rest so the combobox dropdown is ordered by relevance.
        """
        ctx_keys = self._CONTEXT_MAP.get(key, [])
        ctx_vals = {cf: self.vars[cf].get().strip()
                    for cf in ctx_keys if cf in self.vars}

        seen, prioritized, rest = set(), [], []
        for ep in self.ep_history:
            val = ep.get(key, "").strip()
            if not val:
                continue
            # Count how many context fields in this historical endpoint match the current form
            score = sum(1 for cf, cv in ctx_vals.items()
                        if cv and ep.get(cf, "").strip().lower() == cv.lower())
            lo = val.lower()
            if score > 0 and lo not in seen:
                seen.add(lo)
                prioritized.append(val)

        # Flat history provides fallback candidates not already boosted above
        for val in self.history.get(key, []):
            if val.lower() not in seen:
                seen.add(val.lower())
                rest.append(val)

        return prioritized + rest

    def _drawing_suggestions(self):
        """Return drawing names ordered by context (panel, device) then registry."""
        ctx_vals = {cf: self.vars[cf].get().strip()
                    for cf in ("panel", "device") if cf in self.vars}
        seen, prioritized = set(), []
        for ep in self.ep_history:
            d = ep.get("drawing", "").strip()
            if not d:
                continue
            score = sum(1 for cf, cv in ctx_vals.items()
                        if cv and ep.get(cf, "").strip().lower() == cv.lower())
            if score > 0 and d.lower() not in seen:
                seen.add(d.lower())
                prioritized.append(d)
        return prioritized + [n for n in sorted(self.registry.keys())
                               if n.lower() not in seen]

    def _build(self):
        for row_idx, (key, label) in enumerate(self.FIELDS):
            ttk.Label(self, text=label + ":").grid(
                row=row_idx, column=0, sticky="e", padx=(0, 4), pady=1)
            var = tk.StringVar()
            self.vars[key] = var

            if key == "drawing":
                wrap = tk.Frame(self, highlightthickness=0, bd=0)
                combo = ttk.Combobox(wrap, textvariable=var, width=26)
                combo["postcommand"] = lambda c=combo: c.__setitem__(
                    "values", self._drawing_suggestions())
                combo.bind("<<ComboboxSelected>>", self._on_drawing_selected)
                combo.bind("<FocusOut>", self._on_drawing_focusout)
                _bind_search_combobox(combo, self._drawing_suggestions)
                combo.pack(fill="both", expand=True)
                wrap.grid(row=row_idx, column=1, sticky="ew", pady=1)
                self._drawing_combo = combo
                self._context_combos[key] = wrap
                var.trace_add("write", lambda *_: self._update_cell_state())
            elif key == "drawing_cell":
                entry = ttk.Entry(self, textvariable=var, width=28)
                entry.grid(row=row_idx, column=1, sticky="ew", pady=1)
                self._cell_entry = entry
            elif key in self.HISTORY_KEYS:
                wrap = tk.Frame(self, highlightthickness=0, bd=0)
                combo = ttk.Combobox(wrap, textvariable=var, width=28)
                combo["postcommand"] = lambda k=key, c=combo: c.__setitem__(
                    "values", self._context_suggestions(k))
                _bind_search_combobox(combo, lambda k=key: self._context_suggestions(k))
                combo.pack(fill="both", expand=True)
                wrap.grid(row=row_idx, column=1, sticky="ew", pady=1)
                self._context_combos[key] = wrap
            else:
                entry = ttk.Entry(self, textvariable=var, width=28)
                entry.grid(row=row_idx, column=1, sticky="ew", pady=1)
                if key in ("drawing_rev", "drawing_url"):
                    entry.bind("<FocusOut>", self._on_detail_changed)

        self.columnconfigure(1, weight=1)
        # When any context-providing field changes, refresh the highlight borders
        _ctx_providers: set = set()
        for _deps in self._CONTEXT_MAP.values():
            _ctx_providers.update(_deps)
        for _pk in _ctx_providers:
            if _pk in self.vars:
                self.vars[_pk].trace_add("write",
                    lambda *_: self.after(30, self._refresh_highlights))

    def _update_cell_state(self):
        # Drawing Cell only applies to H-type drawings (e.g. protection/schematic sheets
        # with cell references).  For all other drawing types the field is meaningless, so
        # we disable it and clear any stale value to avoid carrying phantom data into exports.
        if self._cell_entry is None:
            return
        name = self.vars.get("drawing", tk.StringVar()).get().strip()
        if not name or is_h_type_drawing(name):
            self._cell_entry.configure(state="normal")
        else:
            self._cell_entry.configure(state="disabled")
            self.vars["drawing_cell"].set("")

    def _refresh_highlights(self):
        """Show a green border on context-aware combos when context fields are filled.

        The green border is a visual hint telling the user that the dropdown for this field
        is now personalised — values from past jobs that share the same device/panel/etc.
        will appear first.  No border means the dropdown shows plain history with no boosting.
        """
        ACTIVE = "#27ae60"
        for key, wrap in self._context_combos.items():
            if key == "drawing":
                ctx_fields = [f for f in ("panel", "device") if f in self.vars]
            else:
                ctx_fields = [f for f in self._CONTEXT_MAP.get(key, []) if f in self.vars]
            # Active only when there is ep_history to score against AND at least one
            # context field has a value to match on
            active = bool(self.ep_history and any(
                self.vars[f].get().strip() for f in ctx_fields))
            if active:
                wrap.configure(highlightthickness=2,
                               highlightbackground=ACTIVE,
                               highlightcolor=ACTIVE)
            else:
                wrap.configure(highlightthickness=0)

    def _update_drawing_list(self):
        if self._drawing_combo is not None:
            self._drawing_combo["values"] = self._drawing_suggestions()

    def _on_drawing_selected(self, _=None):
        self._autofill(self.vars["drawing"].get().strip())

    def _on_drawing_focusout(self, _=None):
        name = self.vars["drawing"].get().strip()
        if name:
            if name not in self.registry:
                self.registry[name] = {"rev": "", "url": "", "notes": ""}
            self._autofill(name)
            self._push(name)

    def _on_detail_changed(self, _=None):
        name = self.vars["drawing"].get().strip()
        if name:
            self._push(name)

    def _autofill(self, name):
        # Registry → form: populate rev/url from the shared drawing registry so the user
        # doesn't have to retype details already known from a previous job or the Drawings tab.
        # Only fills blank fields to avoid clobbering intentional overrides.
        if name in self.registry:
            rec = self.registry[name]
            if not self.vars["drawing_rev"].get():
                self.vars["drawing_rev"].set(rec.get("rev", ""))
            if not self.vars["drawing_url"].get():
                self.vars["drawing_url"].set(rec.get("url", ""))

    def _push(self, name):
        # Form → registry: write rev/url back to the shared drawing registry so that any
        # detail typed here propagates to the Drawings tab and future autofills.
        # Only non-empty values are written to avoid blanking existing registry entries.
        rev = self.vars["drawing_rev"].get().strip()
        url = self.vars["drawing_url"].get().strip()
        if name not in self.registry:
            self.registry[name] = {"rev": "", "url": "", "notes": ""}
        if rev:
            self.registry[name]["rev"] = rev
        if url:
            self.registry[name]["url"] = url

    def get(self):
        result = {k: v.get().strip() for k, v in self.vars.items()}
        if "drawing" in result and "drawing_cell" in result:
            if result["drawing"] and not is_h_type_drawing(result["drawing"]):
                result["drawing_cell"] = ""
        return result

    def set(self, data):
        for k, v in self.vars.items():
            v.set(data.get(k, ""))


class EndpointFrame(DrawingAwareFrame):
    FIELDS = [
        ("device",       "Device"),
        ("location",     "Location"),
        ("pin",          "Pin"),
        ("panel",        "Panel"),
        ("drawing",      "Drawing"),
        ("drawing_rev",  "Drawing Rev"),
        ("drawing_url",  "Drawing URL"),
        ("drawing_cell", "Drawing Cell"),
    ]
    HISTORY_KEYS = ("device", "location", "pin", "panel")


# ──────────────────────────────────────────────────────────────────
# Mini-dialog: enter one drawing for a protection step
# ──────────────────────────────────────────────────────────────────

class DrawingEntryDialog(tk.Toplevel):
    def __init__(self, parent, existing=None, registry=None, base_url=""):
        super().__init__(parent)
        self.title("Edit Drawing" if existing else "Add Drawing")
        self.result = None
        self.registry = registry if registry is not None else {}
        self._base_url = base_url
        self.resizable(False, False)
        self._build(existing or {})
        self.grab_set()
        self.wait_window()

    def _build(self, ex):
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Drawing:").grid(row=0, column=0, sticky="e", padx=(0, 4), pady=3)
        self.drawing_var = tk.StringVar(value=ex.get("drawing", ""))
        combo = ttk.Combobox(f, textvariable=self.drawing_var, width=30)
        combo["postcommand"] = lambda: combo.__setitem__("values", sorted(self.registry.keys()))
        combo.bind("<<ComboboxSelected>>", self._autofill)
        combo.bind("<FocusOut>", self._on_drawing_out)
        combo.grid(row=0, column=1, sticky="ew", pady=3)

        ttk.Label(f, text="Revision:").grid(row=1, column=0, sticky="e", padx=(0, 4), pady=3)
        self.rev_var = tk.StringVar(value=ex.get("drawing_rev", ""))
        rev_e = ttk.Entry(f, textvariable=self.rev_var, width=32)
        rev_e.bind("<FocusOut>", self._push)
        rev_e.grid(row=1, column=1, sticky="ew", pady=3)

        ttk.Label(f, text="Drawing URL:").grid(row=2, column=0, sticky="e", padx=(0, 4), pady=3)
        self.url_var = tk.StringVar(value=ex.get("drawing_url", "") or self._base_url)
        url_e = ttk.Entry(f, textvariable=self.url_var, width=32)
        url_e.bind("<FocusOut>", self._push)
        url_e.grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(f, text="Ctrl+click to open", foreground="grey",
                  font=("", 7)).grid(row=2, column=2, sticky="w", padx=(4, 0))
        _bind_url_open(url_e, self.url_var)

        ttk.Label(f, text="Drawing Cell:").grid(row=3, column=0, sticky="e", padx=(0, 4), pady=3)
        self.cell_var = tk.StringVar(value=ex.get("drawing_cell", ""))
        self._cell_entry = ttk.Entry(f, textvariable=self.cell_var, width=32)
        self._cell_entry.grid(row=3, column=1, sticky="ew", pady=3)
        f.columnconfigure(1, weight=1)

        self.drawing_var.trace_add("write", lambda *_: self._update_cell_state())
        self._update_cell_state()

        br = ttk.Frame(self)
        br.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(br, text="Cancel", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(br, text="Save",   command=self._save).pack(side="right", padx=2)
        self.geometry("380x218")

    def _update_cell_state(self):
        name = self.drawing_var.get().strip()
        if not name or is_h_type_drawing(name):
            self._cell_entry.configure(state="normal")
        else:
            self._cell_entry.configure(state="disabled")
            self.cell_var.set("")

    def _autofill(self, _=None):
        name = self.drawing_var.get().strip()
        if name in self.registry:
            rec = self.registry[name]
            if not self.rev_var.get():
                self.rev_var.set(rec.get("rev", ""))
            if not self.url_var.get():
                self.url_var.set(rec.get("url", ""))

    def _on_drawing_out(self, _=None):
        name = self.drawing_var.get().strip()
        if name:
            if name not in self.registry:
                self.registry[name] = {"rev": "", "url": "", "notes": ""}
            self._autofill()
            self._push()

    def _push(self, _=None):
        name = self.drawing_var.get().strip()
        if not name:
            return
        rev = self.rev_var.get().strip()
        url = self.url_var.get().strip()
        if name not in self.registry:
            self.registry[name] = {"rev": "", "url": "", "notes": ""}
        if rev:
            self.registry[name]["rev"] = rev
        if url:
            self.registry[name]["url"] = url

    def _save(self):
        name = self.drawing_var.get().strip()
        if not name:
            messagebox.showwarning("Required", "Drawing name is required.", parent=self)
            return
        self.result = {
            "drawing":      name,
            "drawing_rev":  self.rev_var.get().strip(),
            "drawing_url":  self.url_var.get().strip(),
            "drawing_cell": self.cell_var.get().strip() if is_h_type_drawing(name) else "",
        }
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Mini-dialog: enter one ISO/FT isolation point
# ──────────────────────────────────────────────────────────────────

class IsoPointDialog(tk.Toplevel):
    """Dialog for a single ISO or FT isolation point entry."""
    def __init__(self, parent, existing=None):
        super().__init__(parent)
        self.title("Edit Isolation Point" if existing else "Add Isolation Point")
        self.result = None
        self.resizable(False, False)
        self._build(existing or {})
        self.grab_set()
        self.wait_window()

    def _build(self, ex):
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Type:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=4)
        self.type_var = tk.StringVar(value=ex.get("iso_type", "ISO"))
        type_combo = ttk.Combobox(f, textvariable=self.type_var, width=8,
                                  values=["ISO", "FT"], state="readonly")
        type_combo.grid(row=0, column=1, sticky="w", pady=4)

        ttk.Label(f, text="Reference:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=4)
        self.ref_var = tk.StringVar(value=ex.get("reference", ""))
        ttk.Entry(f, textvariable=self.ref_var, width=34).grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(f, text="Equipment:").grid(row=2, column=0, sticky="e", padx=(0, 6), pady=4)
        self.equip_var = tk.StringVar(value=ex.get("equipment", ""))
        ttk.Entry(f, textvariable=self.equip_var, width=34).grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Label(f, text="Notes:").grid(row=3, column=0, sticky="e", padx=(0, 6), pady=4)
        self.notes_var = tk.StringVar(value=ex.get("notes", ""))
        ttk.Entry(f, textvariable=self.notes_var, width=34).grid(row=3, column=1, sticky="ew", pady=4)

        f.columnconfigure(1, weight=1)

        hint = ttk.Label(f, text='e.g. Type=ISO  Reference="29L-1 54"  Equipment="2L79 OUT308"',
                         foreground="grey", font=("", 8))
        hint.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        br = ttk.Frame(self)
        br.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(br, text="Cancel", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(br, text="Save",   command=self._save).pack(side="right", padx=2)
        self.geometry("420x230")

    def _save(self):
        ref = self.ref_var.get().strip()
        if not ref:
            messagebox.showwarning("Required", "Reference is required.", parent=self)
            return
        self.result = {
            "iso_type":  self.type_var.get().strip(),
            "reference": ref,
            "equipment": self.equip_var.get().strip(),
            "notes":     self.notes_var.get().strip(),
        }
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Multiple-drawings list widget (for Block/Unblock)
# ──────────────────────────────────────────────────────────────────

class MultiDrawingFrame(ttk.LabelFrame):
    def __init__(self, parent, registry=None, base_drawing_url="", **kwargs):
        super().__init__(parent, text="Drawings", padding=4, **kwargs)
        self.registry = registry if registry is not None else {}
        self.base_drawing_url = base_drawing_url
        self._drawings = []
        self._build()

    def _build(self):
        cols = ("Drawing", "Rev", "Cell", "URL")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=3)
        self.tree.heading("Drawing", text="Drawing")
        self.tree.heading("Rev",     text="Rev")
        self.tree.heading("Cell",    text="Cell")
        self.tree.heading("URL",     text="URL")
        self.tree.column("Drawing", width=140, stretch=False)
        self.tree.column("Rev",     width=46,  stretch=False)
        self.tree.column("Cell",    width=46,  stretch=False)
        self.tree.column("URL",     width=240)
        self.tree.pack(fill="x", expand=False)
        self.tree.bind("<Double-1>", lambda _: self._edit())

        bf = ttk.Frame(self)
        bf.pack(fill="x", pady=(3, 0))
        ttk.Button(bf, text="+ Add Drawing", command=self._add).pack(side="left", padx=2)
        ttk.Button(bf, text="Edit",          command=self._edit).pack(side="left", padx=2)
        ttk.Button(bf, text="Remove",        command=self._remove).pack(side="left", padx=2)

    def _refresh(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for d in self._drawings:
            self.tree.insert("", "end", values=(
                d.get("drawing", ""), d.get("drawing_rev", ""),
                d.get("drawing_cell", ""), d.get("drawing_url", "")))

    def _add(self):
        dlg = DrawingEntryDialog(self, registry=self.registry, base_url=self.base_drawing_url)
        if dlg.result:
            self._drawings.append(dlg.result)
            self._refresh()

    def _edit(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        dlg = DrawingEntryDialog(self, existing=self._drawings[idx], registry=self.registry)
        if dlg.result:
            self._drawings[idx] = dlg.result
            self._refresh()

    def _remove(self):
        sel = self.tree.selection()
        if not sel:
            return
        self._drawings.pop(self.tree.index(sel[0]))
        self._refresh()

    def get(self):
        return list(self._drawings)

    def set(self, drawings_list):
        self._drawings = list(drawings_list or [])
        self._refresh()


# ──────────────────────────────────────────────────────────────────
# Multiple ISO/FT points widget (for Block/Unblock)
# ──────────────────────────────────────────────────────────────────

class MultiIsoFrame(ttk.LabelFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, text="Isolation / Field Termination Points", padding=4, **kwargs)
        self._points = []
        self._build()

    def _build(self):
        cols = ("Type", "Reference", "Equipment", "Notes")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=3)
        self.tree.heading("Type",      text="Type")
        self.tree.heading("Reference", text="Reference")
        self.tree.heading("Equipment", text="Equipment")
        self.tree.heading("Notes",     text="Notes")
        self.tree.column("Type",      width=44,  stretch=False)
        self.tree.column("Reference", width=120, stretch=False)
        self.tree.column("Equipment", width=130, stretch=False)
        self.tree.column("Notes",     width=200)
        self.tree.pack(fill="x", expand=False)
        self.tree.bind("<Double-1>", lambda _: self._edit())

        bf = ttk.Frame(self)
        bf.pack(fill="x", pady=(3, 0))
        ttk.Button(bf, text="+ Add ISO/FT Point", command=self._add).pack(side="left", padx=2)
        ttk.Button(bf, text="Edit",               command=self._edit).pack(side="left", padx=2)
        ttk.Button(bf, text="Remove",             command=self._remove).pack(side="left", padx=2)

    def _refresh(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for p in self._points:
            self.tree.insert("", "end", values=(
                p.get("iso_type", "ISO"), p.get("reference", ""),
                p.get("equipment", ""),  p.get("notes", "")))

    def _add(self):
        dlg = IsoPointDialog(self)
        if dlg.result:
            self._points.append(dlg.result)
            self._refresh()

    def _edit(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        dlg = IsoPointDialog(self, existing=self._points[idx])
        if dlg.result:
            self._points[idx] = dlg.result
            self._refresh()

    def _remove(self):
        sel = self.tree.selection()
        if not sel:
            return
        self._points.pop(self.tree.index(sel[0]))
        self._refresh()

    def get(self):
        return list(self._points)

    def set(self, points_list):
        self._points = list(points_list or [])
        self._refresh()


# ──────────────────────────────────────────────────────────────────
# Protection frame (Block/Unblock: equipment + drawings + ISO points)
# ──────────────────────────────────────────────────────────────────

class ProtectionFrame(ttk.LabelFrame):
    def __init__(self, parent, title, registry=None, job_type="BLOCK", base_drawing_url="", **kwargs):
        super().__init__(parent, text=title, padding=6, **kwargs)
        self.registry = registry if registry is not None else {}
        self.job_type = job_type
        self.base_drawing_url = base_drawing_url
        self.vars = {}
        self._build()

    def _build(self):
        fields = [("equipment","Equipment"),("location","Location"),
                  ("panel","Panel"),("notes","Notes")]
        for row, (key, label) in enumerate(fields):
            ttk.Label(self, text=label+":").grid(row=row, column=0, sticky="e", padx=(0,4), pady=1)
            var = tk.StringVar()
            self.vars[key] = var
            ttk.Entry(self, textvariable=var, width=36).grid(row=row, column=1, sticky="ew", pady=1)
        n = len(fields)
        self.multi_draw = MultiDrawingFrame(self, registry=self.registry, base_drawing_url=self.base_drawing_url)
        self.multi_draw.grid(row=n, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        self.multi_iso = MultiIsoFrame(self)
        self.multi_iso.grid(row=n+1, column=0, columnspan=2, sticky="ew", pady=(4, 2))

        # ── Mirrored Bit section ──────────────────────────────────
        mb_outer = ttk.LabelFrame(self, text="Mirrored Bit (MB)", padding=4)
        mb_outer.grid(row=n+2, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        self.mb_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(mb_outer, text="This protection uses a Mirrored Bit scheme",
                        variable=self.mb_var,
                        command=self._update_mb_state).grid(row=0, column=0, columnspan=2,
                                                            sticky="w", pady=(0, 2))

        ttk.Label(mb_outer, text="Remote relay / device:").grid(
            row=1, column=0, sticky="e", padx=(0, 4), pady=2)
        self.mb_remote_var = tk.StringVar()
        ttk.Entry(mb_outer, textvariable=self.mb_remote_var, width=32).grid(
            row=1, column=1, sticky="ew", pady=2)

        ttk.Label(mb_outer, text="Action / notes:").grid(
            row=2, column=0, sticky="e", padx=(0, 4), pady=2)
        self.mb_notes_var = tk.StringVar()
        ttk.Entry(mb_outer, textvariable=self.mb_notes_var, width=32).grid(
            row=2, column=1, sticky="ew", pady=2)
        ttk.Label(mb_outer, text='e.g. "block input", "block comms", "disable channel 1"',
                  foreground="grey", font=("", 8)).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=(0, 4), pady=(0, 2))
        mb_outer.columnconfigure(1, weight=1)

        # Warning banner (visible only when MB checkbox is ticked)
        action = "BLOCK MB INPUT" if self.job_type == "BLOCK" else "UNBLOCK MB INPUT"
        self._mb_banner = tk.Frame(mb_outer, bg="#fff3cd", relief="solid", bd=1)
        self._mb_banner.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._mb_banner_label = tk.Label(
            self._mb_banner,
            text=f"⚠  Also required:  {action}  on the remote relay",
            bg="#fff3cd", fg="#7d4e00",
            font=("", 10, "bold"),
            pady=5, padx=8)
        self._mb_banner_label.pack(fill="x")
        self._mb_banner.grid_remove()   # hidden until checkbox ticked

        self.columnconfigure(1, weight=1)

    def _update_mb_state(self, *_):
        if self.mb_var.get():
            self._mb_banner.grid()
        else:
            self._mb_banner.grid_remove()

    def get(self):
        result = {k: v.get().strip() for k, v in self.vars.items()}
        result["drawings"]   = self.multi_draw.get()
        result["iso_points"] = self.multi_iso.get()
        result["mb_enabled"] = self.mb_var.get()
        result["mb_remote"]  = self.mb_remote_var.get().strip()
        result["mb_notes"]   = self.mb_notes_var.get().strip()
        return result

    def set(self, data):
        for k, v in self.vars.items():
            v.set(data.get(k, ""))
        self.multi_draw.set(_get_prot_drawings(data))
        self.multi_iso.set(data.get("iso_points", []))
        self.mb_var.set(bool(data.get("mb_enabled", False)))
        self.mb_remote_var.set(data.get("mb_remote", ""))
        self.mb_notes_var.set(data.get("mb_notes", ""))
        self._update_mb_state()


# ──────────────────────────────────────────────────────────────────
# Job dialog
# ──────────────────────────────────────────────────────────────────

class JobDialog(tk.Toplevel):
    TYPE_COLOR = {"REMOVE":"#c0392b","ADD":"#27ae60","MOVE":"#2980b9",
                  "BLOCK":"#d35400","UNBLOCK":"#16a085","TESTING":"#6c3483",
                  "ISOLATION":"#1a6b8a","CR_PROT":"#1a5276",
                  "DEVICE ADD":"#117a65","DEVICE REMOVE":"#784212"}

    def __init__(self, parent, job_type, existing=None, registry=None,
                 history=None, ep_history=None, jobs=None, settings=None,
                 maintenance_standards=None, engineering_standards=None,
                 ctrl_desks=None, crows=None):
        super().__init__(parent)
        self.title(f"{'Edit' if existing else 'Add'} — {job_type}")
        self.result = None
        self.job_type  = job_type
        self.registry  = registry   if registry   is not None else {}
        self.history   = history    if history    is not None else {}
        self.ep_history= ep_history if ep_history is not None else []
        self.jobs      = jobs       if jobs       is not None else []
        self.settings  = settings   if settings   is not None else {}
        self.maintenance_standards = maintenance_standards if maintenance_standards is not None else {}
        self.engineering_standards = engineering_standards if engineering_standards is not None else {}
        self.ctrl_desks = ctrl_desks if ctrl_desks is not None else []
        self.crows = crows if crows is not None else []
        self.resizable(True, True)
        self._build(existing)
        self.grab_set()
        self.wait_window()

    def _build(self, existing):
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas, padding=10)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _resize(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(win_id, width=canvas.winfo_width())

        inner.bind("<Configure>", _resize)
        canvas.bind("<Configure>", _resize)

        def _scroll(e):
            try:
                canvas.yview_scroll(-1 * (e.delta // 120), "units")
            except tk.TclError:
                pass

        canvas.bind_all("<MouseWheel>", _scroll)
        self.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>")
                  if e.widget is self else None)

        self._fill_form(inner, existing)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(btn_row, text="Save",   command=self._save).pack(side="right", padx=2)

        if self.job_type in ("BLOCK","UNBLOCK","TESTING","ISOLATION","CR_PROT","DEVICE ADD","DEVICE REMOVE"):
            self.geometry("660x560")
        else:
            self.geometry("960x640")

    def _section_label(self, parent, row, text, color):
        ttk.Separator(parent, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(8,2))
        tk.Label(parent, text=text, foreground=color, font=("",10,"bold"), bg="#f0f0f0").grid(
            row=row+1, column=0, columnspan=2, pady=(0,4))

    def _wire_combo(self, parent, var):
        """Return a wire-label Combobox with history and live search."""
        combo = ttk.Combobox(parent, textvariable=var, width=30)
        combo["postcommand"] = lambda: combo.__setitem__("values", self.history.get("wire", []))
        _bind_search_combobox(combo, lambda: self.history.get("wire", []))
        return combo

    def _fill_form(self, f, existing):
        # Builds the body of the dialog.  Layout varies significantly by job type:
        #   REMOVE / ADD    — one endpoint pair + wire
        #   MOVE            — two endpoint pairs (remove + add) + two wire fields
        #   BLOCK / UNBLOCK — protection frame only (no endpoints)
        #   TESTING         — free-text notes box only
        row = 0
        ex = existing or {}
        color = self.TYPE_COLOR[self.job_type]

        desc_hdr = ttk.Frame(f)
        desc_hdr.grid(row=row, column=0, columnspan=2, sticky="w")
        ttk.Label(desc_hdr, text="Description:", font=("",10,"bold")).pack(side="left")
        ttk.Button(desc_hdr, text="Auto-fill ✦", command=self._auto_desc).pack(side="left", padx=(8,0))
        row += 1
        self.desc_var = tk.StringVar(value=ex.get("description",""))
        ttk.Entry(f, textvariable=self.desc_var, width=60).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(0,6))
        row += 1

        def _ep(title):
            return EndpointFrame(f, title, registry=self.registry,
                                 history=self.history, ep_history=self.ep_history)

        if self.job_type in ("REMOVE","ADD"):
            self._section_label(f, row, f"── {self.job_type} WIRE ──", color); row += 2
            self.ep_start = _ep("Start Point / Device")
            self.ep_start.grid(row=row, column=0, sticky="nsew", padx=(0,4), pady=2)
            self.ep_start.set(ex.get("start",{}))
            self.ep_end = _ep("End Point / Device")
            self.ep_end.grid(row=row, column=1, sticky="nsew", padx=(4,0), pady=2)
            self.ep_end.set(ex.get("end",{}))
            row += 1
            ttk.Label(f, text="Wire Label / ID:").grid(row=row, column=0, sticky="e", padx=(0,6), pady=(6,2))
            self.wire_var = tk.StringVar(value=ex.get("wire",""))
            self._wire_combo(f, self.wire_var).grid(row=row, column=1, sticky="w", pady=(6,2))
            row += 1

        elif self.job_type == "MOVE":
            # MOVE has two endpoint pairs: the wire being removed ("start"/"end") and
            # the new wire being added ("add_start"/"add_end").  This lets engineers
            # describe both sides of a terminal re-route in a single job record.
            self._section_label(f, row, "── REMOVE (Wire Being Moved) ──", "#c0392b"); row += 2
            self.ep_rem_start = _ep("Remove: Start")
            self.ep_rem_start.grid(row=row, column=0, sticky="nsew", padx=(0,4), pady=2)
            self.ep_rem_start.set(ex.get("start",{}))
            self.ep_rem_end = _ep("Remove: End")
            self.ep_rem_end.grid(row=row, column=1, sticky="nsew", padx=(4,0), pady=2)
            self.ep_rem_end.set(ex.get("end",{}))
            row += 1
            ttk.Label(f, text="Wire Label / ID (Remove):").grid(row=row, column=0, sticky="e", padx=(0,6), pady=(6,2))
            self.wire_var = tk.StringVar(value=ex.get("wire",""))
            self._wire_combo(f, self.wire_var).grid(row=row, column=1, sticky="w", pady=(6,2))
            row += 1
            self._section_label(f, row, "── ADD (New Wire Location) ──", "#27ae60"); row += 2
            self.ep_add_start = _ep("Add: Start")
            self.ep_add_start.grid(row=row, column=0, sticky="nsew", padx=(0,4), pady=2)
            self.ep_add_start.set(ex.get("add_start",{}))
            self.ep_add_end = _ep("Add: End")
            self.ep_add_end.grid(row=row, column=1, sticky="nsew", padx=(4,0), pady=2)
            self.ep_add_end.set(ex.get("add_end",{}))
            row += 1
            ttk.Label(f, text="Wire Label / ID (Add):").grid(row=row, column=0, sticky="e", padx=(0,6), pady=(6,2))
            self.add_wire_var = tk.StringVar(value=ex.get("add_wire",""))
            self._wire_combo(f, self.add_wire_var).grid(row=row, column=1, sticky="w", pady=(6,2))
            row += 1

        elif self.job_type in ("BLOCK","UNBLOCK"):
            lbl = "BLOCK PROTECTION" if self.job_type == "BLOCK" else "UNBLOCK PROTECTION"
            self._section_label(f, row, f"── {lbl} ──", color); row += 2

            # UNBLOCK: offer to copy settings from an existing BLOCK step
            if self.job_type == "UNBLOCK":
                block_jobs = [(i, j) for i, j in enumerate(self.jobs) if j["type"] == "BLOCK"]
                if block_jobs:
                    af = ttk.LabelFrame(f, text="Auto-fill from Block step", padding=4)
                    af.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0,6)); row += 1
                    labels = [
                        f"#{i+1}  {j.get('description','') or j.get('protection',{}).get('equipment','(no name)')}"
                        for i, j in block_jobs]
                    src_var = tk.StringVar()
                    src_cb = ttk.Combobox(af, textvariable=src_var, values=labels,
                                          state="readonly", width=52)
                    src_cb.grid(row=0, column=0, sticky="ew", padx=(0,4))
                    src_cb.current(0)
                    def _apply_block(bj=block_jobs, sv=src_cb):
                        sel = sv.current()
                        if sel >= 0:
                            self.ep_prot.set(bj[sel][1].get("protection", {}))
                    ttk.Button(af, text="Copy →", command=_apply_block).grid(row=0, column=1)
                    af.columnconfigure(0, weight=1)

            self.ep_prot = ProtectionFrame(f, "Equipment / Device",
                                           registry=self.registry, job_type=self.job_type,
                                           base_drawing_url=self.settings.get("base_drawing_url",""))
            self.ep_prot.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
            self.ep_prot.set(ex.get("protection",{}))
            row += 1

        elif self.job_type in ("DEVICE ADD", "DEVICE REMOVE"):
            verb = "INSTALL" if self.job_type == "DEVICE ADD" else "REMOVE"
            self._section_label(f, row, f"── {verb} DEVICE ──", color); row += 2
            self.ep_device = _ep("Device / Location")
            self.ep_device.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
            self.ep_device.set(ex.get("endpoint", {}))
            row += 1
            ttk.Label(f, text="Notes:").grid(row=row, column=0, sticky="ne", padx=(0,6), pady=(8,2))
            self._dev_notes_widget = tk.Text(f, width=58, height=5, wrap="word", font=("",9))
            self._dev_notes_widget.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8,2))
            self._dev_notes_widget.insert("1.0", ex.get("notes",""))
            row += 1

        elif self.job_type == "TESTING":
            self._section_label(f, row, "── TESTING / NOTE ──", color); row += 2
            ttk.Label(f, text="Notes:").grid(row=row, column=0, sticky="ne", padx=(0,6), pady=2)
            self.test_notes_var = tk.StringVar(value=ex.get("notes",""))
            notes_txt = tk.Text(f, width=58, height=6, wrap="word", font=("",9))
            notes_txt.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
            notes_txt.insert("1.0", ex.get("notes",""))
            self._test_notes_widget = notes_txt
            row += 1

        elif self.job_type == "ISOLATION":
            self._section_label(f, row, "── ISOLATION ──", color); row += 2
            ttk.Label(f, text="Notes:").grid(row=row, column=0, sticky="ne", padx=(0,6), pady=2)
            iso_txt = tk.Text(f, width=58, height=6, wrap="word", font=("",9))
            iso_txt.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
            iso_txt.insert("1.0", ex.get("notes",""))
            self._test_notes_widget = iso_txt
            row += 1

        elif self.job_type == "CR_PROT":
            self._section_label(f, row, "── CONTROL ROOM PROTECTION ──", color); row += 2

            def _make_checklist(parent_frame, items, checked_ids, label_fn):
                """Return (frame, {id: BooleanVar}) for a bordered checklist."""
                border = tk.Frame(parent_frame, bg="#d5d8dc", padx=1, pady=1)
                border.columnconfigure(0, weight=1)
                inner = tk.Frame(border, bg="white")
                inner.pack(fill="both", expand=True)
                vars_ = {}
                if items:
                    for item in items:
                        iid = item["desk_id"] if "desk_id" in item else item["outage_number"]
                        var = tk.BooleanVar(value=(iid in checked_ids))
                        vars_[iid] = var
                        ttk.Checkbutton(inner, text=label_fn(item), variable=var).pack(
                            anchor="w", padx=6, pady=2)
                return border, vars_

            # ── Desks ────────────────────────────────────────────────
            ttk.Label(f, text="Desks to call:").grid(row=row, column=0, sticky="ne",
                                                      padx=(0, 6), pady=2)
            selected_desk_ids = {d["desk_id"] for d in ex.get("desks", [])}
            self._cr_desk_vars = {}

            if self.ctrl_desks:
                desk_border, self._cr_desk_vars = _make_checklist(
                    f, self.ctrl_desks, selected_desk_ids,
                    lambda d: d["desk_name"] + (f"  ({d['desk_type']})" if d.get("desk_type") else ""))
                desk_border.grid(row=row, column=1, sticky="ew", pady=2)
            else:
                tk.Label(f, text="No desks configured — use File → Control Room Desks.",
                         fg="#7f8c8d", font=("", 8)).grid(row=row, column=1, sticky="w", pady=2)

            # Live description update when desks are toggled
            def _update_desc(*_):
                sel_names = [d["desk_name"] for d in self.ctrl_desks
                             if self._cr_desk_vars.get(d["desk_id"], tk.BooleanVar()).get()]
                if sel_names and not self.desc_var.get().strip():
                    self.desc_var.set("Call: " + ", ".join(sel_names))
                elif sel_names:
                    self.desc_var.set("Call: " + ", ".join(sel_names))

            for var in self._cr_desk_vars.values():
                var.trace_add("write", _update_desc)

            row += 1

            # ── CROWs ────────────────────────────────────────────────
            ttk.Label(f, text="Associated CROWs:").grid(row=row, column=0, sticky="ne",
                                                         padx=(0, 6), pady=2)
            selected_crow_nums = set(ex.get("crows", []))
            self._cr_crow_vars = {}

            if self.crows:
                crow_border, self._cr_crow_vars = _make_checklist(
                    f, self.crows, selected_crow_nums,
                    lambda c: c["outage_number"])
                crow_border.grid(row=row, column=1, sticky="ew", pady=2)
            else:
                tk.Label(f, text="No CROWs registered for this project.",
                         fg="#7f8c8d", font=("", 8)).grid(row=row, column=1, sticky="w", pady=2)

            row += 1

            # ── Notes ────────────────────────────────────────────────
            ttk.Label(f, text="Notes:").grid(row=row, column=0, sticky="ne", padx=(0, 6), pady=2)
            cr_txt = tk.Text(f, width=58, height=4, wrap="word", font=("", 9))
            cr_txt.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
            cr_txt.insert("1.0", ex.get("notes", ""))
            self._test_notes_widget = cr_txt
            row += 1

        self._maint_lb = self._eng_lb = None

        if self.job_type != "CR_PROT":
            # Standards — shown for all job types except CR_PROT
            ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8,4)); row+=1
            self._section_label(f, row, "── STANDARDS ──", "#5d6d7e"); row+=2

            def _std_list_widget(parent, grid_row, label, all_ids, existing_vals):
                """Inline list+combobox widget for multi-select standards. Returns the Listbox."""
                ttk.Label(parent, text=label).grid(row=grid_row, column=0, sticky="ne", padx=(0,6), pady=2)
                holder = ttk.Frame(parent)
                holder.grid(row=grid_row, column=1, sticky="ew", pady=2)
                holder.columnconfigure(0, weight=1)
                lb = tk.Listbox(holder, height=3, selectmode="single", font=("",9),
                                relief="flat", bd=1, highlightthickness=1,
                                highlightbackground="#d5d8dc", highlightcolor="#2980b9",
                                bg="white", exportselection=False)
                lb.grid(row=0, column=0, sticky="ew")
                for v in existing_vals:
                    lb.insert("end", v)
                btn_f = ttk.Frame(holder); btn_f.grid(row=0, column=1, sticky="ns", padx=(4,0))
                pick_var = tk.StringVar()
                cb = ttk.Combobox(holder, textvariable=pick_var, values=all_ids, width=28, state="readonly")
                cb.grid(row=1, column=0, sticky="ew", pady=(2,0))
                def _add():
                    val = pick_var.get().strip()
                    if val and val not in lb.get(0, "end"):
                        lb.insert("end", val)
                def _remove():
                    sel = lb.curselection()
                    if sel: lb.delete(sel[0])
                ttk.Button(btn_f, text="Add",    command=_add,    width=7).pack(pady=(0,2))
                ttk.Button(btn_f, text="Remove", command=_remove, width=7).pack()
                return lb

            def _load_std_list(job, key):
                val = job.get(key, [])
                if isinstance(val, str):
                    return [val] if val else []
                return list(val)

            maint_ids = sorted(self.maintenance_standards.keys())
            self._maint_lb = _std_list_widget(f, row, "Maintenance Standards:", maint_ids,
                                              _load_std_list(ex, "maintenance_standards")); row+=1
            eng_ids = sorted(self.engineering_standards.keys())
            self._eng_lb   = _std_list_widget(f, row, "Engineering Standards:", eng_ids,
                                              _load_std_list(ex, "engineering_standards")); row+=1

        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

    def _auto_desc(self):
        """Generate a description from the current form data and put it in the field."""
        jt = self.job_type

        def dev(ep):
            d = ep.vars.get("device", tk.StringVar()).get().strip()
            p = ep.vars.get("pin",    tk.StringVar()).get().strip()
            return (f"{d} pin {p}" if d and p else d or p or "?")

        def wire(var):
            w = var.get().strip()
            return f" wire {w}" if w else ""

        if jt == "REMOVE":
            desc = f"Remove{wire(self.wire_var)} from {dev(self.ep_start)} to {dev(self.ep_end)}"
        elif jt == "ADD":
            desc = f"Add{wire(self.wire_var)} from {dev(self.ep_start)} to {dev(self.ep_end)}"
        elif jt == "MOVE":
            desc = (f"Move{wire(self.wire_var)} {dev(self.ep_rem_start)}→{dev(self.ep_rem_end)}"
                    f" to{wire(self.add_wire_var)} {dev(self.ep_add_start)}→{dev(self.ep_add_end)}")
        elif jt in ("BLOCK", "UNBLOCK"):
            eq = self.ep_prot.vars.get("equipment", tk.StringVar()).get().strip() or "?"
            action = "Block" if jt == "BLOCK" else "Unblock"
            desc = f"{action} protection on {eq}"
        elif jt in ("DEVICE ADD", "DEVICE REMOVE"):
            d = dev(self.ep_device)
            loc = self.ep_device.vars.get("location", tk.StringVar()).get().strip()
            verb = "Install" if jt == "DEVICE ADD" else "Remove"
            desc = f"{verb} device {d}" + (f" at {loc}" if loc else "")
        elif jt in ("TESTING", "ISOLATION", "CR_PROT"):
            return  # no auto-fill for free-form notes
        else:
            return
        self.desc_var.set(desc)

    def _save(self):
        job = {"type": self.job_type, "description": self.desc_var.get().strip()}
        if self.job_type in ("REMOVE","ADD"):
            job["start"] = self.ep_start.get()
            job["wire"]  = self.wire_var.get().strip()
            job["end"]   = self.ep_end.get()
        elif self.job_type == "MOVE":
            job["start"]     = self.ep_rem_start.get()
            job["wire"]      = self.wire_var.get().strip()
            job["end"]       = self.ep_rem_end.get()
            job["add_wire"]  = self.add_wire_var.get().strip()
            job["add_start"] = self.ep_add_start.get()
            job["add_end"]   = self.ep_add_end.get()
        elif self.job_type in ("BLOCK","UNBLOCK"):
            job["protection"] = self.ep_prot.get()
        elif self.job_type in ("DEVICE ADD","DEVICE REMOVE"):
            job["endpoint"] = self.ep_device.get()
            job["notes"]    = self._dev_notes_widget.get("1.0","end").strip()
        elif self.job_type in ("TESTING", "ISOLATION"):
            job["notes"] = self._test_notes_widget.get("1.0","end").strip()
        elif self.job_type == "CR_PROT":
            selected_ids = {did for did, var in self._cr_desk_vars.items() if var.get()}
            job["desks"] = [d for d in self.ctrl_desks if d["desk_id"] in selected_ids]
            job["crows"] = [num for num, var in self._cr_crow_vars.items() if var.get()]
            job["notes"] = self._test_notes_widget.get("1.0","end").strip()
        if self._maint_lb is not None:
            job["maintenance_standards"] = list(self._maint_lb.get(0, "end"))
        if self._eng_lb is not None:
            job["engineering_standards"] = list(self._eng_lb.get(0, "end"))
        self.result = job
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Drawing registry edit dialog
# ──────────────────────────────────────────────────────────────────

class DrawingEditDialog(tk.Toplevel):
    def __init__(self, parent, existing=None, base_url="", app_config=None, proj_cache=None):
        super().__init__(parent)
        self.title("Edit Drawing" if existing else "Add Drawing")
        self.result = None
        self._old_name = existing.get("name") if existing else None
        self._base_url = base_url
        self._app_config = app_config or {}
        self._proj_cache = proj_cache
        self.resizable(False, False)
        self._build(existing or {})
        self.grab_set()
        self.wait_window()

    def _build(self, ex):
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        fields = [("name","Drawing Name / No.:"),("title","Title / Description:"),
                  ("rev","Revision:"),("url","Drawing URL:"),("notes","Notes:")]
        self.vars = {}
        for row,(key,label) in enumerate(fields):
            ttk.Label(f,text=label).grid(row=row,column=0,sticky="e",padx=(0,6),pady=4)
            default = self._base_url if (key == "url" and not ex.get("url")) else ""
            var = tk.StringVar(value=ex.get(key, default))
            self.vars[key] = var
            ent = ttk.Entry(f,textvariable=var,width=46)
            ent.grid(row=row,column=1,sticky="ew",pady=4)
            if key == "url":
                ttk.Label(f, text="Ctrl+click to open", foreground="grey",
                          font=("",7)).grid(row=row,column=2,sticky="w",padx=(4,0))
                _bind_url_open(ent, var)
        f.columnconfigure(1, weight=1)
        br = ttk.Frame(self); br.pack(fill="x",padx=10,pady=(0,8))
        ttk.Button(br,text="Cancel",command=self.destroy).pack(side="right",padx=2)
        ttk.Button(br,text="Save",  command=self._save).pack(side="right",padx=2)
        if _DRAWING_SEARCH_AVAILABLE:
            ttk.Button(br, text="Search…", command=self._search_drawing).pack(side="left", padx=2)
        self.geometry("460x268")

    def _search_drawing(self):
        dlg = DrawingSearchDialog(self, self._app_config, multi_select=False,
                                  proj_cache=self._proj_cache)
        if dlg.selected:
            r = dlg.selected[0]
            self.vars["name"].set(r.drawing_number)
            self.vars["title"].set(r.title)
            self.vars["rev"].set(r.revision)
            self.vars["url"].set(r.document_url)

    def _save(self):
        name = self.vars["name"].get().strip()
        if not name:
            messagebox.showwarning("Required","Drawing name is required.",parent=self)
            return
        self.result = {"name":name,"old_name":self._old_name,
                       "title":self.vars["title"].get().strip(),
                       "rev":self.vars["rev"].get().strip(),
                       "url":self.vars["url"].get().strip(),
                       "notes":self.vars["notes"].get().strip()}
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Report formatting (text)
# ──────────────────────────────────────────────────────────────────

W = 62

def _bar(char="="):
    return char * W

def _ep_block(ep, label):
    lines = [f"  {label}"]
    for key,disp in [("device","  Device      "),("location","  Location    "),
                     ("pin","  Pin         "),("panel","  Panel       "),
                     ("drawing","  Drawing     "),("drawing_rev","  Drawing Rev "),
                     ("drawing_url","  Drawing URL "),("drawing_cell","  Drawing Cell")]:
        lines.append(f"    {disp}: {ep.get(key,'')}")
    return "\n".join(lines)

def _prot_block(prot, label):
    lines = [f"  {label}"]
    for key,disp in [("equipment","  Equipment   "),("location","  Location    "),
                     ("panel","  Panel       "),("notes","  Notes       ")]:
        lines.append(f"    {disp}: {prot.get(key,'')}")
    drawings = _get_prot_drawings(prot)
    if drawings:
        lines.append("")
        for i, d in enumerate(drawings, 1):
            n = f" #{i}" if len(drawings) > 1 else ""
            lines.append(f"    Drawing{n}")
            lines.append(f"      Name    : {d.get('drawing','')}")
            if d.get("drawing_rev"):  lines.append(f"      Rev     : {d['drawing_rev']}")
            if d.get("drawing_url"):  lines.append(f"      URL     : {d['drawing_url']}")
            if d.get("drawing_cell"): lines.append(f"      Cell    : {d['drawing_cell']}")
    iso_points = prot.get("iso_points", [])
    if iso_points:
        lines.append("")
        lines.append("    Isolation / FT Points")
        for p in iso_points:
            equip = f"  ({p['equipment']})" if p.get("equipment") else ""
            notes = f"  — {p['notes']}" if p.get("notes") else ""
            lines.append(f"      {p.get('iso_type','ISO')} block  {p.get('reference','')}{equip}{notes}")
    if prot.get("mb_enabled"):
        lines.append("")
        remote = f"  Remote: {prot['mb_remote']}" if prot.get("mb_remote") else ""
        notes  = f"  ({prot['mb_notes']})"        if prot.get("mb_notes")  else ""
        lines.append(f"  *** MIRRORED BIT — also required: MB INPUT block/unblock{remote}{notes} ***")
    return "\n".join(lines)

def _std_list(job, key):
    """Return a list of standard IDs for a job key, handling legacy single-string values."""
    val = job.get(key, [])
    if isinstance(val, str):
        return [val] if val.strip() else []
    return [v for v in val if v]

def format_job(index, job):
    jtype = job["type"]
    labels = {"REMOVE":"REMOVE WIRE","ADD":"ADD WIRE","MOVE":"MOVE WIRE",
              "BLOCK":"BLOCK PROTECTION","UNBLOCK":"UNBLOCK PROTECTION","TESTING":"TESTING / NOTE",
              "ISOLATION":"ISOLATION","CR_PROT":"CONTROL ROOM PROTECTION",
              "DEVICE ADD":"INSTALL DEVICE","DEVICE REMOVE":"REMOVE DEVICE"}
    lines = [_bar(), f"  JOB #{index+1}   [{labels.get(jtype,jtype)}]", _bar()]
    if job.get("description"):
        lines += ["","  DESCRIPTION", f"    {job['description']}"]
    if jtype in ("REMOVE","ADD"):
        lines += ["",_ep_block(job.get("start",{}),"START POINT / DEVICE"),
                  "",f"  WIRE: {job.get('wire','')}",
                  "",_ep_block(job.get("end",{}),"END POINT / DEVICE")]
    elif jtype == "MOVE":
        lines += ["","  "+"─"*30+"  REMOVE  "+"─"*(W-42),
                  "",_ep_block(job.get("start",{}),"REMOVE: Start Point / Device"),
                  "",f"  WIRE (Remove): {job.get('wire','')}",
                  "",_ep_block(job.get("end",{}),"REMOVE: End Point / Device"),
                  "","  "+"─"*31+"  ADD  "+"─"*(W-39),
                  "",_ep_block(job.get("add_start",{}),"ADD: Start Point / Device"),
                  "",f"  WIRE (Add):    {job.get('add_wire','')}",
                  "",_ep_block(job.get("add_end",{}),"ADD: End Point / Device")]
    elif jtype in ("BLOCK","UNBLOCK"):
        lbl = "BLOCK PROTECTION" if jtype=="BLOCK" else "UNBLOCK PROTECTION"
        lines += ["",_prot_block(job.get("protection",{}), lbl)]
    elif jtype in ("DEVICE ADD", "DEVICE REMOVE"):
        lines += ["", _ep_block(job.get("endpoint", {}), "DEVICE / LOCATION")]
        if job.get("notes"):
            lines += ["", "  NOTES", *[f"    {ln}" for ln in job["notes"].splitlines()]]
    elif jtype in ("TESTING", "ISOLATION"):
        if job.get("notes"):
            lines += ["","  NOTES", *[f"    {ln}" for ln in job["notes"].splitlines()]]
    elif jtype == "CR_PROT":
        desks = job.get("desks", [])
        if desks:
            lines += ["", "  CONTROL ROOM DESKS"]
            for d in desks:
                phones = "  /  ".join(filter(None, [d.get("phone_int",""),
                                                     d.get("phone_local",""),
                                                     d.get("phone_toll","")]))
                lines.append(f"    {d.get('desk_name','')}  [{d.get('desk_type','')}]")
                if phones:    lines.append(f"      Phones   : {phones}")
                if d.get("stations"):
                    lines.append(f"      Stations : {', '.join(d['stations'])}")
        crows = job.get("crows", [])
        if crows:
            lines += ["", "  CROW OUTAGES", *[f"    {c}" for c in crows]]
        if job.get("notes"):
            lines += ["", "  NOTES", *[f"    {ln}" for ln in job["notes"].splitlines()]]
    ms = _std_list(job, "maintenance_standards")
    es = _std_list(job, "engineering_standards")
    if ms or es:
        lines += ["", "  STANDARDS"]
        if ms: lines.append("    Maintenance: " + ", ".join(ms))
        if es: lines.append("    Engineering: " + ", ".join(es))
    lines.append("")
    return "\n".join(lines)

# ──────────────────────────────────────────────────────────────────
# Job row colour theme + HTML escaping (shared by Export Wizard)
# ──────────────────────────────────────────────────────────────────

_ROW_STYLE = {
    "REMOVE":      ("background:#fde8e6","color:#922b21;font-weight:bold"),
    "ADD":         ("background:#e8f8ee","color:#1e8449;font-weight:bold"),
    "MOVE-REMOVE": ("background:#fef0e6","color:#a04000;font-weight:bold"),
    "MOVE-ADD":    ("background:#fefbe6","color:#7d6608;font-weight:bold"),
    "BLOCK":       ("background:#fef3e6","color:#a04000;font-weight:bold"),
    "UNBLOCK":     ("background:#e6f6f3","color:#0e6655;font-weight:bold"),
    "TESTING":     ("background:#f5eef8","color:#6c3483;font-weight:bold"),
    "ISOLATION":   ("background:#e8f4f8","color:#1a6b8a;font-weight:bold"),
    "CR_PROT":     ("background:#e8f1f8","color:#1a5276;font-weight:bold"),
}

_ROW_BORDER = {
    "REMOVE":      "#c0392b",
    "ADD":         "#27ae60",
    "MOVE-REMOVE": "#e67e22",
    "MOVE-ADD":    "#d4ac0d",
    "BLOCK":       "#ca6f1e",
    "UNBLOCK":     "#148f77",
    "TESTING":     "#7d3c98",
    "ISOLATION":   "#1a6b8a",
    "CR_PROT":     "#1a5276",
}

def _esc(t):
    return (str(t).replace("&","&amp;").replace("<","&lt;")
            .replace(">","&gt;").replace('"',"&quot;"))


# ──────────────────────────────────────────────────────────────────
# Export Wizard — helpers and dialog
# ──────────────────────────────────────────────────────────────────

_EW_PAGE_SIZES = [
    "Letter Portrait", "Letter Landscape",
    "11×17 Landscape", "11×17 Portrait",
]

_EW_PAGE_DIMS = {
    "Letter Portrait":  ("8.5in", "11in"),
    "Letter Landscape": ("11in",  "8.5in"),
    "11×17 Landscape":  ("17in",  "11in"),
    "11×17 Portrait":   ("11in",  "17in"),
}


def _ew_url_cell(url: str, label: str, mode: str,
                 project_folder: str, subfolders: list) -> str:
    """URL cell: truncated text for paper, local-path link for tablet, hyperlink for digital."""
    if not url:
        return ""
    if mode == "paper":
        s = url[:72] + ("…" if len(url) > 72 else "")
        return _esc(s)
    if mode == "tablet" and project_folder:
        try:
            basename = os.path.basename(urllib.parse.urlparse(url).path)
            if basename:
                for sf in subfolders:
                    local = os.path.join(project_folder, sf, basename)
                    if os.path.isfile(local):
                        rel = (sf + "/" + basename).replace("\\", "/")
                        return f'<a href="{_esc(rel)}" class="doc-link">{_esc(label)} ↗</a>'
        except Exception:
            pass
    return f'<a href="{_esc(url)}" class="doc-link">{_esc(label)} ↗</a>'


def _ew_full_html(title: str, body_html: str, page_css: str, mode: str) -> str:
    vp = '<meta name="viewport" content="width=device-width,initial-scale=1">' \
         if mode == "tablet" else ""
    base_fs = "11pt" if mode == "tablet" else "9pt"
    tablet_css = """
    body { font-size: 11pt !important; }
    td, th { padding: 8px 10px !important; font-size: 10pt !important; }
    h2.sec-hdr { font-size: 14pt !important; }
    a.doc-link {
        display: inline-block; background: #2980b9; color: white !important;
        padding: 5px 12px; border-radius: 5px; text-decoration: none;
        font-size: 9pt; margin: 2px;
    }
    .qr-grid { gap: 20px; }
    .qr-card { width: 170px; padding: 12px; }
""" if mode == "tablet" else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
{vp}
<title>{_esc(title)}</title>
<style>
*,*::before,*::after{{box-sizing:border-box}}
body{{font-family:-apple-system,"Helvetica Neue",Arial,sans-serif;
     font-size:{base_fs};color:#1a252f;margin:0;background:#888}}
@media screen{{
  body{{padding:24px}}
  section{{background:white;margin:0 auto 24px;padding:0.7in;
          box-shadow:0 2px 14px rgba(0,0,0,.3);position:relative;overflow:hidden;
          min-width:6in;max-width:16.5in}}
  section.page-cover{{padding:0 0 0.7in}}
}}
@media print{{
  body{{background:white;padding:0}}
  section{{page-break-after:always}}
  section:last-child{{page-break-after:auto}}
  -webkit-print-color-adjust:exact;print-color-adjust:exact
  a{{color:#000!important;text-decoration:none}}
  input[type=checkbox]{{
    -webkit-appearance:none;appearance:none;
    border:1.5px solid #444;width:11px;height:11px;
    display:inline-block;vertical-align:middle}}
  tr.done td{{text-decoration:line-through;opacity:.55}}
}}
{page_css}
/* Cover */
.cover-hdr{{background:#1a252f;color:white;padding:2cm 0.7in 1.5cm;text-align:center;margin-bottom:.4in}}
.cover-title{{font-size:22pt;margin:0 0 6px;font-weight:bold}}
.cover-sub{{font-size:11pt;opacity:.75;margin:0}}
.cover-meta{{border-collapse:collapse;margin-bottom:18px}}
.cover-meta td{{padding:3px 12px 3px 0;font-size:10pt}}
.meta-lbl{{color:#666;font-weight:bold;white-space:nowrap}}
.cover-tbl{{border-collapse:collapse;width:100%;margin-bottom:18px;font-size:9pt}}
.cover-tbl th{{background:#1a252f;color:white;padding:4px 8px;text-align:left}}
.cover-tbl td{{padding:4px 8px;border:1px solid #ccc}}
h3{{font-size:10pt;margin:14px 0 4px;color:#1a252f;text-transform:uppercase;
    letter-spacing:.06em;border-bottom:1px solid #ccc;padding-bottom:3px}}
/* Table of Contents */
.toc-tbl{{border-collapse:collapse;width:100%;margin-bottom:18px}}
.toc-tbl td{{padding:7px 8px;border:none;border-bottom:1px solid #eee;font-size:10pt}}
.toc-num{{color:#1a252f;font-weight:bold;width:32px;text-align:right;
          padding-right:14px !important;font-size:11pt}}
.toc-tbl a{{color:#1a252f;text-decoration:none;font-weight:500}}
.toc-ext{{color:#888;font-style:italic}}
.toc-tbl tr:hover td{{background:#f0f4f8}}
/* General */
h2.sec-hdr{{font-size:13pt;color:#1a252f;border-bottom:2px solid #1a252f;
           padding-bottom:3px;margin:0 0 10px}}
table{{border-collapse:collapse;width:100%;margin-bottom:12px}}
th{{background:#1a252f;color:white;padding:5px 7px;text-align:left;font-size:8pt}}
td{{padding:4px 7px;border:1px solid #ddd;vertical-align:top;line-height:1.4;font-size:8pt}}
tr{{break-inside:avoid;page-break-inside:avoid}}
tr:nth-child(even){{background:#f7f9fc}}
.num{{text-align:center;font-weight:bold;color:#555}}
.wire{{font-family:"Courier New",monospace;font-size:7.5pt}}
.chk{{width:22px;text-align:center;padding:3px}}
.dim{{color:#666;font-style:italic;font-size:7.5pt}}
.std-ref{{color:#1a5276;font-size:7.5pt}}
.mb-warn{{background:#fff3cd;color:#7d4e00;font-weight:bold;padding:1px 5px;border-radius:3px}}
.empty-note{{color:#999;font-style:italic}}
a{{color:#1a5276}}
a.doc-link{{color:#2980b9}}
input[type=checkbox]{{width:13px;height:13px;cursor:pointer;accent-color:#1a252f}}
.qr-grid{{display:flex;flex-wrap:wrap;gap:14px;margin-top:12px}}
.qr-card{{width:155px;border:1px solid #ddd;border-radius:4px;padding:8px;text-align:center}}
.qr-lbl{{font-weight:bold;font-size:7.5pt;margin:4px 0 2px}}
.qr-url{{font-size:6pt;color:#666;word-break:break-all}}
.qr-note{{font-size:7.5pt;color:#888;font-style:italic;margin-bottom:10px}}
{tablet_css}
</style>
</head>
<body>
{body_html}
<script>
document.querySelectorAll('input[type=checkbox]').forEach(function(cb){{
  cb.addEventListener('change',function(){{
    var tr=this.closest('tr');
    if(tr)tr.classList[this.checked?'add':'remove']('done');
  }});
}});
</script>
</body></html>"""


def _ew_with_id(html: str, section_id: str) -> str:
    """Inject id attribute into the first <section tag in html."""
    if not section_id or not html:
        return html
    return html.replace("<section ", f'<section id="{section_id}" ', 1)


def _ew_cover(project, crows, date, toc_items, mode, css_class="page-cover",
              project_folder=""):
    def _crow_url_cell(c):
        url = c.get("url", "")
        if mode == "paper" or not url:
            return _esc(url)
        return f'<a href="{_esc(url)}">{_esc(url)}</a>'

    def _crow_files_cell(c):
        files = c.get("files", [])
        if not files:
            return ""
        if mode == "tablet" and project_folder:
            links = []
            for fname in files:
                local = os.path.join(project_folder, "CROW Outage", fname)
                rel   = os.path.join("CROW Outage", fname).replace("\\", "/")
                if os.path.isfile(local):
                    links.append(f'<a href="{_esc(rel)}">{_esc(fname)}</a>')
                else:
                    links.append(_esc(fname))
            return " ".join(links)
        if mode == "paper":
            return _esc(", ".join(files))
        return _esc(", ".join(files))

    has_files = any(c.get("files") for c in crows) if crows else False
    outage_rows = "".join(
        "<tr><td><b>{}</b></td><td>{}</td>{}</tr>".format(
            _esc(c.get("outage_number", "")),
            _crow_url_cell(c),
            f"<td>{_crow_files_cell(c)}</td>" if has_files else "",
        )
        for c in crows
    ) if crows else ""
    file_th = "<th>Documents</th>" if has_files else ""
    outage_html = (
        "<h3>Outage / CROW Numbers</h3>"
        "<table class='cover-tbl'><thead><tr><th>Outage #</th>"
        f"<th>URL</th>{file_th}</tr></thead>"
        f"<tbody>{outage_rows}</tbody></table>"
    ) if crows else ""
    toc_rows = "".join(
        f"<tr><td class='toc-num'>{i + 1}</td>"
        + (f"<td><a href='#{_esc(anch)}'>{_esc(label)}</a></td></tr>" if anch
           else f"<td><span class='toc-ext'>{_esc(label)}</span></td></tr>")
        for i, (label, anch) in enumerate(toc_items)
    ) if toc_items else ""
    toc_html = (
        "<h3>Table of Contents</h3>"
        f"<table class='toc-tbl'><tbody>{toc_rows}</tbody></table>"
    ) if toc_items else ""
    return (
        f'<section class="{css_class}">'
        f'<div class="cover-hdr">'
        f'<div style="font-size:32pt;margin-bottom:8px">&#9889;</div>'
        f'<div class="cover-title">{_esc(project) or "Red-Line-Routing"}</div>'
        f'<p class="cover-sub">Red-Line Routing Work Package</p>'
        f'</div>'
        f'<div style="padding:0 0.7in">'
        f'<table class="cover-meta"><tbody>'
        f'<tr><td class="meta-lbl">Generated</td><td>{_esc(date)}</td></tr>'
        f'</tbody></table>'
        f'{outage_html}{toc_html}'
        f'</div></section>\n'
    )


def _ew_work_orders(jobs, drawing_registry, mode, css_class="page-content"):
    reg = drawing_registry or {}

    def ep_r(ep):
        parts = []
        if ep.get("device"):   parts.append(f"<b>{_esc(ep['device'])}</b>")
        if ep.get("pin"):      parts.append(f"Pin {_esc(ep['pin'])}")
        if ep.get("location"): parts.append(_esc(ep["location"]))
        if ep.get("panel"):    parts.append(f"Panel {_esc(ep['panel'])}")
        if ep.get("drawing"):
            name = ep["drawing"]
            ri   = reg.get(name, {})
            rev  = f" Rev{_esc(ep['drawing_rev'])}" if ep.get("drawing_rev") else ""
            cell = f" [{_esc(ep['drawing_cell'])}]" if ep.get("drawing_cell") else ""
            url  = ep.get("drawing_url", "") or ri.get("url", "")
            ttl  = ri.get("title", "")
            ttl_h = f' <span class="dim">— {_esc(ttl)}</span>' if ttl else ""
            if url and mode != "paper":
                parts.append(f'<a href="{_esc(url)}">{_esc(name)}</a>{ttl_h}{rev}{cell}')
            else:
                parts.append(f'{_esc(name)}{ttl_h}{rev}{cell}')
        return "<br>".join(parts)

    def prot_r(prot):
        parts = []
        if prot.get("equipment"): parts.append(f"<b>{_esc(prot['equipment'])}</b>")
        if prot.get("location"):  parts.append(_esc(prot["location"]))
        if prot.get("panel"):     parts.append(f"Panel {_esc(prot['panel'])}")
        if prot.get("notes"):     parts.append(f"<i>{_esc(prot['notes'])}</i>")
        for d in _get_prot_drawings(prot):
            name = d.get("drawing", "")
            ri   = reg.get(name, {})
            url  = d.get("drawing_url", "") or ri.get("url", "")
            rev  = f" Rev{_esc(d['drawing_rev'])}" if d.get("drawing_rev") else ""
            if url and mode != "paper":
                parts.append(f'Dwg: <a href="{_esc(url)}">{_esc(name)}</a>{rev}')
            else:
                parts.append(f"Dwg: {_esc(name)}{rev}")
        for p in prot.get("iso_points", []):
            notes = f" — {_esc(p['notes'])}" if p.get("notes") else ""
            parts.append(f'<span class="dim">{_esc(p.get("iso_type","ISO"))} '
                         f'{_esc(p.get("reference",""))}{notes}</span>')
        if prot.get("mb_enabled"):
            remote = f" [{_esc(prot.get('mb_remote',''))}]" if prot.get("mb_remote") else ""
            parts.append(f'<span class="mb-warn">&#9888; MB INPUT — '
                         f'block/unblock required{remote}</span>')
        return "<br>".join(parts)

    tl = {
        "REMOVE":"Remove Wire","ADD":"Add Wire","MOVE":"Move Wire",
        "BLOCK":"Block Protection","UNBLOCK":"Unblock Protection","TESTING":"Testing",
        "ISOLATION":"Isolation","CR_PROT":"CR Protection",
    }
    rows = ""
    seq  = 1
    for job in jobs:
        jt  = job["type"]
        dsc = _esc(job.get("description", ""))
        ms  = _std_list(job, "maintenance_standards")
        es  = _std_list(job, "engineering_standards")
        stds = []
        if ms: stds.append("Maint: " + ", ".join(_esc(s) for s in ms))
        if es: stds.append("Eng: "   + ", ".join(_esc(s) for s in es))
        if stds:
            dsc += ("<br>" if dsc else "") + " &nbsp; ".join(
                f'<span class="std-ref">{s}</span>' for s in stds)

        def _tr(key, label, s_html, wire, e_html, _d=dsc):
            nonlocal seq
            bg = _ROW_STYLE.get(key, ("", ""))[0]
            ts = _ROW_STYLE.get(key, ("", ""))[1]
            bc = _ROW_BORDER.get(key, "#aaa")
            r = (f'<tr style="{bg}">'
                 f'<td class="chk" style="border-left:4px solid {bc}">'
                 f'<input type="checkbox"></td>'
                 f'<td class="num">{seq}</td>'
                 f'<td style="{ts}">{_esc(label)}</td>'
                 f'<td>{_d}</td><td>{s_html}</td>'
                 f'<td class="wire">{_esc(wire)}</td>'
                 f'<td>{e_html}</td></tr>')
            seq += 1
            return r

        if jt in ("REMOVE", "ADD"):
            rows += _tr(jt, tl[jt],
                        ep_r(job.get("start", {})), job.get("wire", ""),
                        ep_r(job.get("end", {})))
        elif jt == "MOVE":
            rows += _tr("MOVE-REMOVE", "Move — Remove",
                        ep_r(job.get("start", {})), job.get("wire", ""),
                        ep_r(job.get("end", {})))
            rows += _tr("MOVE-ADD", "Move — Add",
                        ep_r(job.get("add_start", {})), job.get("add_wire", ""),
                        ep_r(job.get("add_end", {})))
        elif jt in ("BLOCK", "UNBLOCK"):
            rows += _tr(jt, tl[jt], prot_r(job.get("protection", {})), "", "")
        elif jt == "TESTING":
            rows += _tr("TESTING", tl.get("TESTING", "Testing"),
                        _esc(job.get("notes", "")), "", "")
        elif jt == "ISOLATION":
            rows += _tr("ISOLATION", tl.get("ISOLATION", "Isolation"),
                        _esc(job.get("notes", "")), "", "")
        elif jt == "CR_PROT":
            desks = job.get("desks", [])
            desk_parts = []
            for d in desks:
                phones = " / ".join(filter(None, [d.get("phone_int",""),
                                                   d.get("phone_local",""),
                                                   d.get("phone_toll","")]))
                stations = ", ".join(d.get("stations",[]))
                part = f"<b>{_esc(d.get('desk_name',''))}</b>"
                if d.get("desk_type"): part += f" ({_esc(d['desk_type'])})"
                if phones:   part += f"<br><small>&#128222; {_esc(phones)}</small>"
                if stations: part += f"<br><small>Stations: {_esc(stations)}</small>"
                desk_parts.append(part)
            cell = "<br>".join(desk_parts)
            crows = job.get("crows", [])
            if crows:
                cell += ("<br>" if cell else "") + "<small><b>CROWs:</b> " + _esc(", ".join(crows)) + "</small>"
            if job.get("notes"):
                cell += ("<br>" if cell else "") + f"<em>{_esc(job['notes'])}</em>"
            rows += _tr("CR_PROT", tl.get("CR_PROT", "CR Protection"), cell, "", "")

    return (
        f'<section class="{css_class}">'
        '<h2 class="sec-hdr">Work Orders</h2>'
        '<table>'
        '<thead><tr>'
        '<th class="chk">✓</th><th style="width:28px">#</th>'
        '<th style="width:110px">Type</th><th style="width:14%">Description</th>'
        '<th style="width:23%">Start Point / Device</th>'
        '<th style="width:80px">Wire</th>'
        '<th style="width:23%">End Point / Device</th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table></section>\n'
    )


def _ew_drawings_reg(reg, mode, project_folder="", css_class="page-content"):
    if not reg:
        return (f'<section class="{css_class}"><h2 class="sec-hdr">Drawings Register</h2>'
                '<p class="empty-note">No drawings registered.</p></section>\n')
    rows = ""
    for name, info in sorted(reg.items()):
        uc = _ew_url_cell(info.get("url", ""), "Open", mode, project_folder, ["Drawings"])
        rows += (f"<tr><td><b>{_esc(name)}</b></td>"
                 f"<td>{_esc(info.get('title',''))}</td>"
                 f"<td>{_esc(info.get('rev',''))}</td>"
                 f"<td>{uc}</td>"
                 f"<td>{_esc(info.get('notes',''))}</td></tr>")
    return (
        f'<section class="{css_class}"><h2 class="sec-hdr">Drawings Register</h2>'
        '<table><thead><tr>'
        '<th>Drawing #</th><th>Title</th><th>Rev</th><th>URL / Link</th><th>Notes</th>'
        f'</tr></thead><tbody>{rows}</tbody></table></section>\n'
    )


def _ew_relay_reg(reg, mode, project_folder="", css_class="page-content"):
    if not reg:
        return (f'<section class="{css_class}"><h2 class="sec-hdr">Relay Settings</h2>'
                '<p class="empty-note">No relay settings registered.</p></section>\n')
    rows = ""
    for dev_id, info in sorted(reg.items()):
        uc = _ew_url_cell(info.get("url", ""), "Open", mode, project_folder, ["Relay Settings"])
        rows += (f"<tr><td><b>{_esc(dev_id)}</b></td>"
                 f"<td>{_esc(info.get('title',''))}</td>"
                 f"<td>{_esc(info.get('revision',''))}</td>"
                 f"<td>{_esc(info.get('engineer',''))}</td>"
                 f"<td>{uc}</td></tr>")
    return (
        f'<section class="{css_class}"><h2 class="sec-hdr">Relay Settings</h2>'
        '<table><thead><tr>'
        '<th>Device ID</th><th>Title</th><th>Rev</th><th>Engineer</th><th>URL / Link</th>'
        f'</tr></thead><tbody>{rows}</tbody></table></section>\n'
    )


def _ew_standards(maint_reg, eng_reg, mode, project_folder="",
                  maint_css="page-content", eng_css="page-content"):
    parts = []
    if maint_reg:
        rows = ""
        for sid, info in sorted(maint_reg.items()):
            ut = info.get("url_telecom", "")
            ux = info.get("url_transmission", "")
            if mode == "paper":
                links = "; ".join(filter(None, [
                    (ut[:55] + "…" if len(ut) > 55 else ut) if ut else "",
                    (ux[:55] + "…" if len(ux) > 55 else ux) if ux else "",
                ]))
            else:
                lp = []
                sf = ["Maintenance Standards"]
                if ut: lp.append(_ew_url_cell(ut, "Telecom", mode, project_folder, sf))
                if ux: lp.append(_ew_url_cell(ux, "Trans",   mode, project_folder, sf))
                links = " ".join(lp)
            rows += (f"<tr><td><b>{_esc(sid)}</b></td>"
                     f"<td>{_esc(info.get('title',''))}</td>"
                     f"<td>{_esc(info.get('revision',''))}</td>"
                     f"<td>{links}</td>"
                     f"<td>{_esc(info.get('notes',''))}</td></tr>")
        parts.append(
            f'<section class="{maint_css}"><h2 class="sec-hdr">Maintenance Standards</h2>'
            '<table><thead><tr>'
            '<th>Standard ID</th><th>Title</th><th>Rev</th><th>Links</th><th>Notes</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></section>\n'
        )
    if eng_reg:
        rows = ""
        for sid, info in sorted(eng_reg.items()):
            uc = _ew_url_cell(info.get("url", ""), "Open", mode,
                               project_folder, ["Engineering Standards"])
            rows += (f"<tr><td><b>{_esc(sid)}</b></td>"
                     f"<td>{_esc(info.get('title',''))}</td>"
                     f"<td>{_esc(info.get('revision',''))}</td>"
                     f"<td>{_esc(info.get('standard_type',''))}</td>"
                     f"<td>{uc}</td>"
                     f"<td>{_esc(info.get('notes',''))}</td></tr>")
        parts.append(
            f'<section class="{eng_css}"><h2 class="sec-hdr">Engineering Standards</h2>'
            '<table><thead><tr>'
            '<th>Standard ID</th><th>Title</th><th>Rev</th><th>Type</th>'
            '<th>URL / Link</th><th>Notes</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></section>\n'
        )
    return "".join(parts)


def _ew_qr_sheet(items, css_class="page-content"):
    """QR code reference sheet (paper mode). Requires internet to render codes."""
    if not items:
        return ""
    cards = ""
    for label, url in items:
        if not url:
            continue
        qr = ("https://api.qrserver.com/v1/create-qr-code/"
              f"?size=100x100&data={urllib.parse.quote(url, safe='')}")
        cards += (
            f'<div class="qr-card">'
            f'<img src="{_esc(qr)}" width="100" height="100" alt="QR" loading="lazy">'
            f'<div class="qr-lbl">{_esc(label)}</div>'
            f'<div class="qr-url">{_esc(url)}</div>'
            f'</div>'
        )
    return (
        f'<section class="{css_class}"><h2 class="sec-hdr">Document URLs</h2>'
        '<p class="qr-note">QR codes require internet access when opening this file. '
        'Scan or type URLs to access documents.</p>'
        f'<div class="qr-grid">{cards}</div></section>\n'
    )


_PDF_EXTS  = {".pdf"}
_DOC_EXTS  = {".doc", ".docx"}
_EMBED_EXTS = _PDF_EXTS | _DOC_EXTS


def _ew_embedded_files(project_folder, subfolder, mode, css_class="page-content",
                       recurse=False, crow_files=None):
    """Append embedded/linked local documents after a registry section.

    ``crow_files`` overrides directory scanning — pass a list of filenames
    already copied into ``<project_folder>/<subfolder>/``.
    """
    if not project_folder or mode == "digital":
        return ""

    full_dir = os.path.join(project_folder, subfolder)

    if crow_files is not None:
        # Explicit list from CROW attachments
        found = []
        for fname in crow_files:
            fpath = os.path.join(full_dir, fname)
            if os.path.isfile(fpath):
                rel = os.path.join(subfolder, fname).replace("\\", "/")
                found.append((fname, rel))
    elif os.path.isdir(full_dir):
        found = []
        if recurse:
            for root, dirs, files in os.walk(full_dir):
                dirs[:] = sorted(d for d in dirs if d.lower() != "archive")
                for f in sorted(files):
                    if os.path.splitext(f)[1].lower() in _EMBED_EXTS:
                        full = os.path.join(root, f)
                        rel  = os.path.relpath(full, project_folder).replace("\\", "/")
                        found.append((f, rel))
        else:
            for f in sorted(os.listdir(full_dir)):
                fpath = os.path.join(full_dir, f)
                if os.path.isfile(fpath) and os.path.splitext(f)[1].lower() in _EMBED_EXTS:
                    rel = os.path.join(subfolder, f).replace("\\", "/")
                    found.append((f, rel))
    else:
        return ""

    if not found:
        return ""

    items = []
    for fname, rel in found:
        ext = os.path.splitext(fname)[1].lower()
        if ext in _PDF_EXTS:
            items.append(
                f'<div style="page-break-before:always;margin:0;padding:0">'
                f'<p style="font-size:7pt;color:#aaa;margin:0 0 2px;'
                f'font-family:monospace">{_esc(fname)}</p>'
                f'<embed src="{_esc(rel)}" type="application/pdf" '
                f'width="100%" style="height:10.5in;border:none;display:block">'
                f'</div>\n'
            )
        else:
            items.append(
                f'<p style="margin:4px 0"><a href="{_esc(rel)}">'
                f'&#128196; {_esc(fname)}</a>'
                f' <span style="font-size:8pt;color:#888">'
                f'(open in Word to print)</span></p>\n'
            )

    return f'<section class="{css_class}">{"".join(items)}</section>\n'


# ══════════════════════════════════════════════════════════════════
# PDF Package generation
# ══════════════════════════════════════════════════════════════════

# ──────────────────────────────────────────────────────────────────
# PDF Package generation
# Requires pypdf/ folder next to this script (copy from release).
# Falls back gracefully if not present.
# ──────────────────────────────────────────────────────────────────

_PYPDF_AVAILABLE = False
_PYPDF_ERROR = ""
try:
    _pypdf_pkg_dir = os.path.dirname(os.path.abspath(__file__))
    if _pypdf_pkg_dir not in sys.path:
        sys.path.insert(0, _pypdf_pkg_dir)
    from pypdf import PdfWriter as _PdfWriter, PdfReader as _PdfReader
    _PYPDF_AVAILABLE = True
except BaseException as _e:
    _PYPDF_ERROR = str(_e)
    _PdfWriter = _PdfReader = None

# Helvetica AFM character widths (units = 1/1000 em)
_HELV_W = {
    ' ':278,'!':278,'"':355,'#':556,'$':556,'%':889,'&':667,"'":222,
    '(':333,')':333,'*':389,'+':584,',':278,'-':333,'.':278,'/':278,
    '0':556,'1':556,'2':556,'3':556,'4':556,'5':556,'6':556,'7':556,
    '8':556,'9':556,':':278,';':278,'<':584,'=':584,'>':584,'?':556,
    '@':1015,'A':667,'B':667,'C':722,'D':722,'E':667,'F':611,'G':778,
    'H':722,'I':278,'J':500,'K':667,'L':556,'M':833,'N':722,'O':778,
    'P':667,'Q':778,'R':722,'S':667,'T':611,'U':722,'V':667,'W':944,
    'X':667,'Y':667,'Z':611,'[':278,'\\':278,']':278,'^':469,'_':556,
    '`':333,'a':556,'b':556,'c':500,'d':556,'e':556,'f':278,'g':556,
    'h':556,'i':222,'j':222,'k':500,'l':222,'m':833,'n':556,'o':556,
    'p':556,'q':556,'r':333,'s':500,'t':278,'u':556,'v':500,'w':722,
    'x':500,'y':500,'z':500,'{':334,'|':260,'}':334,'~':584,
}


def _ptw(text, size, bold=False):
    """Width of a text string in Helvetica at given point size."""
    f = 1.05 if bold else 1.0
    return sum(_HELV_W.get(c, 556) for c in str(text)) * size * f / 1000.0


def _ptrunc(text, max_pts, size, bold=False):
    """Truncate text to fit within max_pts width; append '...' if cut."""
    text = str(text)
    if _ptw(text, size, bold) <= max_pts:
        return text
    ew = _ptw('...', size, bold)
    lo, hi = 0, len(text)
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if _ptw(text[:mid], size, bold) + ew <= max_pts:
            lo = mid
        else:
            hi = mid
    return (text[:lo] + '...') if lo > 0 else ''


def _pwrap(text, max_pts, size, bold=False, max_lines=10):
    """Word-wrap text to fit max_pts wide; returns a list of lines.

    Overly long single words are hard-broken; output is capped at
    max_lines with an ellipsis on the final line.
    """
    text = " ".join(str(text).split())
    if not text:
        return [""]
    lines, cur = [], ""
    for word in text.split(" "):
        test = (cur + " " + word) if cur else word
        if _ptw(test, size, bold) <= max_pts:
            cur = test
            continue
        if cur:
            lines.append(cur)
        while _ptw(word, size, bold) > max_pts and len(word) > 1:
            lo, hi = 1, len(word)
            while lo < hi - 1:
                mid = (lo + hi) // 2
                if _ptw(word[:mid], size, bold) <= max_pts:
                    lo = mid
                else:
                    hi = mid
            lines.append(word[:lo])
            word = word[lo:]
        cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _ptrunc(lines[-1] + "...", max_pts, size, bold)
    return lines or [""]


def _penc(text):
    """Encode a string for a PDF string literal (WinAnsi, octal for non-ASCII)."""
    out = []
    for ch in str(text).replace('\r', '').replace('\n', ' '):
        if ch == '\\': out.append('\\\\')
        elif ch == '(': out.append('\\(')
        elif ch == ')': out.append('\\)')
        elif 32 <= ord(ch) <= 126: out.append(ch)
        else:
            try: out.append(f'\\{ch.encode("cp1252")[0]:03o}')
            except (UnicodeEncodeError, LookupError): out.append('?')
    return ''.join(out)


class _PDFPage:
    """Mutable PDF page. All coords: x,y from TOP-LEFT, y increases downward."""

    DARK  = (26,  37,  47)    # #1a252f header/column-header bg
    WHITE = (255, 255, 255)
    LIGHT = (247, 249, 252)   # #f7f9fc alternating row bg
    GREY  = (102, 102, 102)   # muted text
    RULE  = (200, 200, 200)   # table gridlines

    ROW_BG = {
        "REMOVE":      (253, 232, 230),
        "ADD":         (232, 248, 238),
        "MOVE-REMOVE": (254, 240, 230),
        "MOVE-ADD":    (254, 251, 230),
        "BLOCK":       (254, 243, 230),
        "UNBLOCK":     (230, 246, 243),
        "TESTING":     (245, 238, 248),
        "ISOLATION":   (232, 244, 248),
        "CR_PROT":     (232, 241, 248),
    }
    ROW_ACC = {
        "REMOVE":      (192,  57,  43),
        "ADD":         ( 39, 174,  96),
        "MOVE-REMOVE": (230, 126,  34),
        "MOVE-ADD":    (212, 172,  13),
        "BLOCK":       (202, 111,  30),
        "UNBLOCK":     ( 20, 143, 119),
        "TESTING":     (125,  60, 152),
        "ISOLATION":   ( 26, 107, 138),
        "CR_PROT":     ( 26,  82, 118),
    }
    TYPE_LBL = {
        "REMOVE":      "Remove Wire",
        "ADD":         "Add Wire",
        "MOVE-REMOVE": "Move — Remove",
        "MOVE-ADD":    "Move — Add",
        "BLOCK":       "Block",
        "UNBLOCK":     "Unblock",
        "TESTING":     "Testing",
        "ISOLATION":   "Isolation",
        "CR_PROT":     "CR Protection",
    }
    # Font indices: F1=Helvetica, F2=Helvetica-Bold, F3=Courier
    FR = 1; FB = 2; FM = 3

    def __init__(self, w, h):
        self.w = w; self.h = h; self._ops = []

    def _rgb(self, c):
        return f"{c[0]/255:.3f} {c[1]/255:.3f} {c[2]/255:.3f}"

    def _by(self, y, rh=0):
        """Top-down y -> PDF bottom-up y. rh = rect height for fill_rect."""
        return self.h - y - rh

    def _bl(self, y, sz):
        """Top of text box (top-down) -> PDF baseline."""
        return self.h - y - sz * 0.78

    def frect(self, x, y, w, h, color):
        self._ops += [f"{self._rgb(color)} rg",
                      f"{x:.2f} {self._by(y,h):.2f} {w:.2f} {h:.2f} re f"]

    def srect(self, x, y, w, h, color=RULE, lw=0.3):
        self._ops += [f"{lw:.2f} w {self._rgb(color)} RG",
                      f"{x:.2f} {self._by(y,h):.2f} {w:.2f} {h:.2f} re S"]

    def hline(self, x, y, length, color=RULE, lw=0.3):
        py = self._by(y)
        self._ops += [f"{lw:.2f} w {self._rgb(color)} RG",
                      f"{x:.2f} {py:.2f} m {x+length:.2f} {py:.2f} l S"]

    def text(self, x, y, s, fi=1, sz=9, color=DARK):
        if not s: return
        self._ops += ["BT", f"{self._rgb(color)} rg",
                      f"/F{fi} {sz:.1f} Tf",
                      f"{x:.2f} {self._bl(y,sz):.2f} Td",
                      f"({_penc(s)}) Tj", "ET"]

    def ctext(self, x, y, w, h, s, fi=1, sz=8, color=DARK,
              align="left", pad=3):
        """Vertically-centered, truncated, aligned text in a cell rect."""
        s = _ptrunc(str(s), w - 2*pad, sz, fi == self.FB)
        ty = y + (h - sz) * 0.5
        if align == "center":
            tx = x + (w - _ptw(s, sz, fi == self.FB)) / 2
        elif align == "right":
            tx = x + w - _ptw(s, sz, fi == self.FB) - pad
        else:
            tx = x + pad
        self.text(tx, ty, s, fi=fi, sz=sz, color=color)

    def stream(self):
        return "\n".join(self._ops)


class _SimplePDFBuilder:
    """Minimal self-contained PDF generator using standard Type1 fonts."""
    SIZES = {
        "Letter Portrait":  (612.0,  792.0),
        "Letter Landscape": (792.0,  612.0),
        "11x17 Landscape":  (1224.0, 792.0),   # ASCII x
        "11x17 Portrait":   (792.0, 1224.0),
        "11×17 Landscape":  (1224.0, 792.0),  # Unicode ×
        "11×17 Portrait":   (792.0, 1224.0),
    }
    _FNMS = ["Helvetica", "Helvetica-Bold", "Courier"]

    def __init__(self):
        self._pages = []

    def new_page(self, size="Letter Portrait") -> _PDFPage:
        w, h = self.SIZES.get(size, (612.0, 792.0))
        p = _PDFPage(w, h)
        self._pages.append(p)
        return p

    def build(self) -> bytes:
        NF = len(self._FNMS); NP = len(self._pages)
        total = 2 + NF + 2 * NP
        buf = bytearray(); offsets = {}

        def emit(d):
            buf.extend(d.encode('ascii') if isinstance(d, str) else d)

        def begin(oid):
            offsets[oid] = len(buf); emit(f"{oid} 0 obj\n")

        def end():
            emit("endobj\n")

        emit(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

        begin(1); emit("<< /Type /Catalog /Pages 2 0 R >>\n"); end()

        kids = " ".join(f"{3+NF+2*i} 0 R" for i in range(NP))
        begin(2); emit(f"<< /Type /Pages /Kids [{kids}] /Count {NP} >>\n"); end()

        for i, fn in enumerate(self._FNMS):
            begin(3+i)
            emit(f"<< /Type /Font /Subtype /Type1 /BaseFont /{fn}"
                 f" /Encoding /WinAnsiEncoding >>\n")
            end()

        fdict = " ".join(f"/F{i+1} {3+i} 0 R" for i in range(NF))
        res = f"<< /Font << {fdict} >> >>"

        for i, page in enumerate(self._pages):
            pid = 3 + NF + 2*i; cid = pid + 1
            stream = page.stream().encode('ascii')
            begin(cid)
            emit(f"<< /Length {len(stream)} >>\nstream\n")
            emit(stream); emit(b"\nendstream\n"); end()
            begin(pid)
            emit(f"<< /Type /Page /Parent 2 0 R"
                 f" /MediaBox [0 0 {page.w:.2f} {page.h:.2f}]"
                 f" /Contents {cid} 0 R /Resources {res} >>\n")
            end()

        xpos = len(buf)
        emit(f"xref\n0 {total+1}\n")
        emit(b"0000000000 65535 f \r\n")
        for oid in range(1, total+1):
            emit(f"{offsets.get(oid,0):010d} 00000 n \r\n")
        emit(f"trailer\n<< /Size {total+1} /Root 1 0 R >>\n"
             f"startxref\n{xpos}\n%%EOF\n")
        return bytes(buf)


def _ep_flat(ep) -> str:
    parts = []
    if ep.get("device"):   parts.append(ep["device"])
    if ep.get("pin"):      parts.append(f"Pin {ep['pin']}")
    if ep.get("location"): parts.append(ep["location"])
    if ep.get("panel"):    parts.append(f"Panel {ep['panel']}")
    if ep.get("drawing"):
        d = ep["drawing"]
        if ep.get("drawing_rev"):  d += f" Rev{ep['drawing_rev']}"
        if ep.get("drawing_cell"): d += f" [{ep['drawing_cell']}]"
        parts.append(d)
    return "  /  ".join(parts)


def _prot_flat(prot) -> str:
    parts = []
    if prot.get("equipment"): parts.append(prot["equipment"])
    if prot.get("location"):  parts.append(prot["location"])
    if prot.get("panel"):     parts.append(f"Panel {prot['panel']}")
    if prot.get("notes"):     parts.append(prot["notes"])
    for d in _get_prot_drawings(prot):
        name = d.get("drawing","")
        if d.get("drawing_rev"): name += f" Rev{d['drawing_rev']}"
        parts.append(f"Dwg:{name}")
    for pt in prot.get("iso_points",[]):
        parts.append(f"{pt.get('iso_type','ISO')} {pt.get('reference','')}".strip())
    if prot.get("mb_enabled"): parts.append("MB INPUT")
    return "  /  ".join(parts)


def _pdf_cover(bld, project, crows, date, toc_items, size="Letter Portrait"):
    margin = 43
    p = bld.new_page(size)
    w, h = p.w, p.h
    cw = w - 2*margin
    HDR = max(85.0, h * 0.11)

    # ── Header band ──────────────────────────────────────────────
    p.frect(0, 0, w, HDR, _PDFPage.DARK)
    # accent stripe
    p.frect(0, HDR - 4, w, 4, (39, 174, 96))
    proj = _ptrunc(project or "Red-Line Routing", cw - 12, 20, True)
    p.text(margin, 16, proj, fi=_PDFPage.FB, sz=20, color=_PDFPage.WHITE)
    p.text(margin, 46, "Red-Line Routing — Work Package", fi=_PDFPage.FR,
           sz=10, color=(160, 175, 190))

    y = HDR + 18
    # Generated date row
    p.text(margin, y, "Generated:", fi=_PDFPage.FB, sz=9, color=_PDFPage.GREY)
    p.text(margin + 72, y, date, fi=_PDFPage.FR, sz=9, color=_PDFPage.DARK)
    y += 22

    # ── CROW / outage numbers ─────────────────────────────────────
    if crows:
        y += 4
        p.text(margin, y, "CROW / OUTAGE NUMBERS", fi=_PDFPage.FB, sz=8,
               color=_PDFPage.GREY)
        y += 13
        col_url = cw - 130
        # header row
        p.frect(margin, y, cw, 18, _PDFPage.DARK)
        p.ctext(margin,       y, 130,     18, "Outage #",
                fi=_PDFPage.FB, sz=8, color=_PDFPage.WHITE)
        p.ctext(margin+130,   y, col_url, 18, "URL",
                fi=_PDFPage.FB, sz=8, color=_PDFPage.WHITE)
        y += 18
        for ci, crow in enumerate(crows):
            bg = _PDFPage.LIGHT if ci % 2 == 0 else _PDFPage.WHITE
            p.frect(margin, y, cw, 16, bg)
            p.ctext(margin,     y, 130,     16, crow.get("outage_number",""),
                    fi=_PDFPage.FB, sz=8)
            p.ctext(margin+130, y, col_url, 16, crow.get("url",""),
                    fi=_PDFPage.FR, sz=7, color=(50,100,170))
            p.hline(margin, y+16, cw)
            y += 16
        p.srect(margin, y - len(crows)*16 - 18, cw, len(crows)*16 + 18)
        y += 16

    # ── Table of contents ─────────────────────────────────────────
    if toc_items:
        y += 4
        p.text(margin, y, "TABLE OF CONTENTS", fi=_PDFPage.FB, sz=8,
               color=_PDFPage.GREY)
        y += 13
        for i, (label, _anch) in enumerate(toc_items):
            bg = _PDFPage.LIGHT if i % 2 == 0 else _PDFPage.WHITE
            p.frect(margin, y, cw, 20, bg)
            p.ctext(margin,    y, 30,    20, str(i+1), fi=_PDFPage.FB, sz=10,
                    color=_PDFPage.DARK, align="center")
            p.ctext(margin+30, y, cw-30, 20, label,   fi=_PDFPage.FR, sz=10,
                    color=_PDFPage.DARK)
            p.hline(margin, y+20, cw)
            y += 20
        p.srect(margin, y - len(toc_items)*20, cw, len(toc_items)*20)


def _pdf_section_table(bld, title, col_specs, rows_iter, size="Letter Portrait"):
    """Render a multi-page table section.

    col_specs : list of (header_str, width_pts, align_str) -- widths must sum to content width.
    rows_iter : iterable of (row_key, [cell_value,...]) -- row_key None = plain alternating rows,
                string key from _PDFPage.ROW_BG = colored job row.
    """
    margin = 43 if "Letter" in size else 36
    HDR_H = 30; COL_H = 20; ROW_H = 17

    pw, ph = _SimplePDFBuilder.SIZES.get(size, (612.0, 792.0))
    cw = pw - 2*margin

    col_names  = [c[0] for c in col_specs]
    col_widths = [c[1] for c in col_specs]
    col_aligns = [c[2] for c in col_specs]

    state = {"p": None, "y": 0, "ri": 0}

    def new_pg(cont=False):
        p = bld.new_page(size)
        p.frect(0, 0, pw, HDR_H, _PDFPage.DARK)
        p.frect(0, HDR_H - 3, pw, 3, (39, 174, 96))
        lbl = title + (" (cont.)" if cont else "")
        p.text(margin, 8, lbl, fi=_PDFPage.FB, sz=13, color=_PDFPage.WHITE)
        y = HDR_H
        p.frect(margin, y, cw, COL_H, (40, 52, 65))
        x = margin
        for name, cwidth, align in zip(col_names, col_widths, col_aligns):
            p.ctext(x, y, cwidth, COL_H, name, fi=_PDFPage.FB, sz=8,
                   color=_PDFPage.WHITE, align=align)
            x += cwidth
        state["p"] = p
        state["y"] = y + COL_H

    new_pg()

    LH = 10.0   # line height for 8pt wrapped text
    PAD = 3.5   # top/bottom cell padding

    for row_key, cells in rows_iter:
        # Wrap every cell, row height fits the tallest one
        wrapped = []
        for ci, (val, cwidth) in enumerate(zip(cells, col_widths)):
            bold = (ci == 0 and not row_key)
            wrapped.append(_pwrap(val, cwidth - 6, 8, bold=bold))
        n_lines = max(len(w) for w in wrapped) if wrapped else 1
        rh = max(ROW_H, n_lines * LH + 2 * PAD)

        if state["y"] + rh > ph - margin:
            new_pg(cont=True)
        p = state["p"]; y = state["y"]; ri = state["ri"]

        if row_key and row_key in _PDFPage.ROW_BG:
            bg  = _PDFPage.ROW_BG[row_key]
            acc = _PDFPage.ROW_ACC.get(row_key)
        else:
            bg  = _PDFPage.LIGHT if ri % 2 == 0 else _PDFPage.WHITE
            acc = None

        p.frect(margin, y, cw, rh, bg)
        if acc:
            p.frect(margin, y, 4, rh, acc)

        x = margin
        for ci, (lines, cwidth, align) in enumerate(zip(wrapped, col_widths, col_aligns)):
            fi = _PDFPage.FB if (ci == 0 and not row_key) else _PDFPage.FR
            for li, line in enumerate(lines):
                if align == "center":
                    tx = x + (cwidth - _ptw(line, 8, fi == _PDFPage.FB)) / 2
                elif align == "right":
                    tx = x + cwidth - _ptw(line, 8, fi == _PDFPage.FB) - 3
                else:
                    tx = x + 3
                p.text(tx, y + PAD + li * LH, line, fi=fi, sz=8)
            x += cwidth

        p.hline(margin, y + rh, cw)
        state["y"] += rh; state["ri"] += 1


def _pdf_work_orders(bld, jobs, drw_reg, size="11x17 Landscape"):
    # Normalise "x" vs "x" variants
    size = size.replace("×", "x")
    margin = 36 if "17" in size else 43
    HDR_H = 30; COL_H = 20; ROW_H = 18

    pw, ph = _SimplePDFBuilder.SIZES.get(size, (1224.0, 792.0))
    cw = pw - 2*margin

    # Column widths that sum to cw
    fixed_w = 20 + 28 + 95 + 75  # chk + seq + type + wire
    rem = cw - fixed_w
    desc_w = int(rem * 0.21)
    ep_w   = (rem - desc_w) // 2
    # Final adjustment to fill cw exactly
    total = 20 + 28 + 95 + desc_w + ep_w + 75 + ep_w
    desc_w += cw - total  # give remainder to description
    col_ws = [20, 28, 95, desc_w, ep_w, 75, ep_w]

    state = {"p": None, "y": 0}

    def new_pg(cont=False):
        p = bld.new_page(size)
        p.frect(0, 0, pw, HDR_H, _PDFPage.DARK)
        p.frect(0, HDR_H-3, pw, 3, (39, 174, 96))
        p.text(margin, 8, "Work Orders" + (" (cont.)" if cont else ""),
               fi=_PDFPage.FB, sz=13, color=_PDFPage.WHITE)
        y = HDR_H
        # Column headers
        p.frect(margin, y, cw, COL_H, (40, 52, 65))
        x = margin
        for name, cwidth, align in zip(
            ["", "#", "Type", "Description",
             "Start Point / Device", "Wire", "End Point / Device"],
            col_ws,
            ["c","c","l","l","l","c","l"]):
            al = {"c":"center","l":"left"}.get(align,"left")
            p.ctext(x, y, cwidth, COL_H, name, fi=_PDFPage.FB, sz=8,
                   color=_PDFPage.WHITE, align=al)
            x += cwidth
        state["p"] = p; state["y"] = y + COL_H

    new_pg()

    reg = drw_reg or {}
    seq = 1

    LH = 9.0    # line height for 7pt wrapped text
    PAD = 4.0   # top/bottom cell padding

    def draw_row(jkey, desc, start_s, wire, end_s):
        nonlocal seq
        # Wrap the three long-text columns; row grows to fit
        desc_lines  = _pwrap(desc,    col_ws[3] - 6, 7)
        start_lines = _pwrap(start_s, col_ws[4] - 6, 7)
        end_lines   = _pwrap(end_s,   col_ws[6] - 6, 7)
        n_lines = max(len(desc_lines), len(start_lines), len(end_lines), 1)
        rh = max(ROW_H, n_lines * LH + 2 * PAD)

        if state["y"] + rh > ph - margin:
            new_pg(cont=True)
        p = state["p"]; y = state["y"]
        bg  = _PDFPage.ROW_BG.get(jkey, _PDFPage.WHITE)
        acc = _PDFPage.ROW_ACC.get(jkey, _PDFPage.RULE)
        p.frect(margin, y, cw, rh, bg)
        p.frect(margin, y, 4, rh, acc)
        # Checkbox square — stays at the top of tall rows
        cbx = margin + 5; cby = y + 4
        p.srect(cbx, cby, 9, 9, color=(120,120,120), lw=0.7)
        x = margin + col_ws[0]
        p.ctext(x, y, col_ws[1], rh, str(seq), fi=_PDFPage.FB,
                sz=8, align="center")
        x += col_ws[1]
        lbl = _PDFPage.TYPE_LBL.get(jkey, jkey)
        col = _PDFPage.ROW_ACC.get(jkey, _PDFPage.DARK)
        p.ctext(x, y, col_ws[2], rh, lbl, fi=_PDFPage.FB, sz=8, color=col)
        x += col_ws[2]
        for li, line in enumerate(desc_lines):
            p.text(x + 3, y + PAD + li * LH, line, fi=_PDFPage.FR, sz=7)
        x += col_ws[3]
        for li, line in enumerate(start_lines):
            p.text(x + 3, y + PAD + li * LH, line, fi=_PDFPage.FR, sz=7)
        x += col_ws[4]
        p.ctext(x, y, col_ws[5], rh, wire, fi=_PDFPage.FM, sz=7, align="center")
        x += col_ws[5]
        for li, line in enumerate(end_lines):
            p.text(x + 3, y + PAD + li * LH, line, fi=_PDFPage.FR, sz=7)
        p.hline(margin, y + rh, cw)
        state["y"] += rh; seq += 1

    for job in jobs:
        jt   = job["type"]
        desc = job.get("description","")
        ms   = _std_list(job,"maintenance_standards")
        es   = _std_list(job,"engineering_standards")
        stds = []
        if ms: stds.append("Maint:" + ",".join(ms))
        if es: stds.append("Eng:" + ",".join(es))
        if stds: desc += (" | " if desc else "") + " | ".join(stds)

        if jt in ("REMOVE","ADD"):
            draw_row(jt, desc, _ep_flat(job.get("start",{})),
                     job.get("wire",""), _ep_flat(job.get("end",{})))
        elif jt == "MOVE":
            draw_row("MOVE-REMOVE", desc,
                     _ep_flat(job.get("start",{})), job.get("wire",""),
                     _ep_flat(job.get("end",{})))
            draw_row("MOVE-ADD", "",
                     _ep_flat(job.get("add_start",{})), job.get("add_wire",""),
                     _ep_flat(job.get("add_end",{})))
        elif jt in ("BLOCK","UNBLOCK"):
            draw_row(jt, desc, _prot_flat(job.get("protection",{})), "", "")
        elif jt in ("TESTING", "ISOLATION"):
            draw_row(jt, desc, job.get("notes",""), "", "")
        elif jt == "CR_PROT":
            desks = job.get("desks", [])
            desk_str = "; ".join(
                d.get("desk_name","") + (f" ({d['desk_type']})" if d.get("desk_type") else "")
                for d in desks
            )
            crows = job.get("crows", [])
            if crows:
                desk_str += (" | " if desk_str else "") + "CROWs: " + ", ".join(crows)
            if job.get("notes"):
                desk_str += (" | " if desk_str else "") + job["notes"]
            draw_row("CR_PROT", desc, desk_str, "", "")


def _pdf_drawings_reg(bld, reg, size="Letter Portrait"):
    if not reg: return
    margin = 43 if "Letter" in size else 36
    pw = _SimplePDFBuilder.SIZES.get(size, (612.0, 792.0))[0]
    cw = pw - 2*margin
    notes_w = min(160, int(cw * 0.20))
    rev_w   = 42
    num_w   = min(160, int(cw * 0.22))
    title_w = cw - num_w - rev_w - notes_w
    specs = [
        ("Drawing #", num_w, "left"),
        ("Title",     title_w, "left"),
        ("Rev",       rev_w,  "center"),
        ("Notes",     notes_w,"left"),
    ]
    def rows():
        for name, info in sorted(reg.items()):
            yield None, [name, info.get("title",""), info.get("rev",""),
                         info.get("notes","")]
    _pdf_section_table(bld, "Drawings Register", specs, rows(), size)


def _pdf_relay_reg(bld, reg, size="Letter Portrait"):
    if not reg: return
    margin = 43 if "Letter" in size else 36
    pw = _SimplePDFBuilder.SIZES.get(size, (612.0, 792.0))[0]
    cw = pw - 2*margin
    rev_w  = 42; eng_w = 130
    dev_w  = min(130, int(cw * 0.18))
    title_w = cw - dev_w - rev_w - eng_w
    specs = [
        ("Device ID", dev_w,   "left"),
        ("Title",     title_w, "left"),
        ("Rev",       rev_w,   "center"),
        ("Engineer",  eng_w,   "left"),
    ]
    def rows():
        for did, info in sorted(reg.items()):
            yield None, [did, info.get("title",""), info.get("revision",""),
                         info.get("engineer","")]
    _pdf_section_table(bld, "Relay Settings", specs, rows(), size)


def _pdf_standards(bld, maint_reg, eng_reg, size="Letter Portrait"):
    margin = 43 if "Letter" in size else 36
    pw = _SimplePDFBuilder.SIZES.get(size, (612.0, 792.0))[0]
    cw = pw - 2*margin
    rev_w = 42; sid_w = min(130, int(cw * 0.20))
    title_w = cw - sid_w - rev_w
    specs = [
        ("Standard ID", sid_w,   "left"),
        ("Title",       title_w, "left"),
        ("Rev",         rev_w,   "center"),
    ]
    if maint_reg:
        def mrows():
            for sid, info in sorted(maint_reg.items()):
                yield None, [sid, info.get("title",""), info.get("revision","")]
        _pdf_section_table(bld, "Maintenance Standards", specs, mrows(), size)
    if eng_reg:
        def erows():
            for sid, info in sorted(eng_reg.items()):
                yield None, [sid, info.get("title",""), info.get("revision","")]
        _pdf_section_table(bld, "Engineering Standards", specs, erows(), size)


def _merge_pdfs_bytes(pdf_bytes_list: list) -> bytes:
    """Merge list of PDF byte strings into one PDF using pypdf."""
    if not _PYPDF_AVAILABLE:
        return pdf_bytes_list[0] if pdf_bytes_list else b""
    import io
    from pypdf.generic import RectangleObject, NameObject
    writer = _PdfWriter()
    for data in pdf_bytes_list:
        if not data:
            continue
        try:
            reader = _PdfReader(io.BytesIO(data))
            for page in reader.pages:
                # Ensure MediaBox is explicitly on each page dict (not just
                # inherited from a parent /Pages node) so it survives clone().
                if "/MediaBox" not in page:
                    try:
                        mb = page.mediabox
                        page[NameObject("/MediaBox")] = RectangleObject(
                            (float(mb.left), float(mb.bottom),
                             float(mb.right), float(mb.top))
                        )
                    except Exception:
                        pass
                writer.add_page(page)
        except Exception:
            pass
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


_CONVERTIBLE_EXTS = {".pdf", ".txt", ".docx", ".doc"}


def _txt_to_pdf_bytes(path):
    """Render a plain-text file as a PDF using _SimplePDFBuilder."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return b""

    pw, ph = 612.0, 792.0
    margin = 50.0
    sz = 8.5
    line_h = sz * 1.5
    max_w = pw - 2 * margin
    HDR_H = 26.0
    content_top = HDR_H + 10
    max_y = ph - margin
    fname = os.path.basename(path)

    bld = _SimplePDFBuilder()
    state = {"p": None, "y": content_top}

    def new_pg():
        pg = bld.new_page("Letter Portrait")
        pg.frect(0, 0, pw, HDR_H, _PDFPage.DARK)
        pg.frect(0, HDR_H - 3, pw, 3, (39, 174, 96))
        pg.text(margin, 6, _ptrunc(fname, max_w, 10, True),
                fi=_PDFPage.FB, sz=10, color=_PDFPage.WHITE)
        state["p"] = pg
        state["y"] = content_top

    new_pg()

    def emit_line(line):
        if state["y"] + line_h > max_y:
            new_pg()
        state["p"].text(margin, state["y"], line, fi=_PDFPage.FM, sz=sz)
        state["y"] += line_h

    for raw_line in text.splitlines():
        if not raw_line.strip():
            state["y"] += line_h * 0.4  # blank line gap
            continue
        # word-wrap
        words = raw_line.split(" ")
        current = ""
        for word in words:
            test = (current + " " + word).lstrip() if current else word
            if _ptw(test, sz) <= max_w:
                current = test
            else:
                if current:
                    emit_line(current)
                current = word if _ptw(word, sz) <= max_w else _ptrunc(word, max_w, sz)
        if current is not None:
            emit_line(current)

    return bld.build()


def _docx_to_pdf_bytes(path):
    """Try to convert a .docx/.doc file to PDF bytes.

    Attempts LibreOffice headless first, then PowerShell + Word on Windows.
    Returns PDF bytes on success, None if no converter is available.
    """
    import tempfile
    abs_path = os.path.abspath(path)

    # ── LibreOffice (Windows / macOS / Linux) ─────────────────────
    for cmd in ("libreoffice", "soffice"):
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                r = subprocess.run(
                    [cmd, "--headless", "--convert-to", "pdf",
                     "--outdir", tmpdir, abs_path],
                    timeout=60, capture_output=True)
                if r.returncode == 0:
                    stem = os.path.splitext(os.path.basename(abs_path))[0]
                    out = os.path.join(tmpdir, stem + ".pdf")
                    if os.path.isfile(out):
                        with open(out, "rb") as fh:
                            return fh.read()
        except (FileNotFoundError, OSError):
            continue
        except subprocess.TimeoutExpired:
            break

    # ── PowerShell + Word COM (Windows only, no extra packages) ───
    if sys.platform == "win32":
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                out = os.path.join(tmpdir, "out.pdf")
                ps = (
                    f'$w = New-Object -ComObject Word.Application; '
                    f'$w.Visible = $false; '
                    f'$d = $w.Documents.Open("{abs_path}"); '
                    f'$d.SaveAs2("{out}", 17); '
                    f'$d.Close(); $w.Quit()'
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps],
                    timeout=60, capture_output=True)
                if os.path.isfile(out):
                    with open(out, "rb") as fh:
                        return fh.read()
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass

    return None


def _convert_file_to_pdf(path):
    """Convert a supported file to PDF bytes.

    Returns (bytes, error_str). bytes is None when conversion fails.
    Supported: .pdf (pass-through), .txt, .docx, .doc
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            with open(path, "rb") as fh:
                return fh.read(), ""
        elif ext == ".txt":
            data = _txt_to_pdf_bytes(path)
            return (data, "") if data else (None, "text conversion failed")
        elif ext in (".docx", ".doc"):
            data = _docx_to_pdf_bytes(path)
            if data:
                return data, ""
            return None, "requires LibreOffice or Microsoft Word"
        else:
            return None, f"unsupported type {ext}"
    except Exception as exc:
        return None, str(exc)


def _collect_pdfs(folder, subfolder, recurse=False):
    """Return list of PDF bytes from a project subfolder.

    Collects .pdf files directly; converts .txt, .docx, .doc to PDF.
    """
    result = []
    full = os.path.join(folder, subfolder)
    if not os.path.isdir(full):
        return result

    def _try_file(fpath):
        ext = os.path.splitext(fpath)[1].lower()
        if ext not in _CONVERTIBLE_EXTS:
            return
        data, _ = _convert_file_to_pdf(fpath)
        if data:
            result.append(data)

    if recurse:
        for root, dirs, files in os.walk(full):
            dirs[:] = sorted(d for d in dirs if d.lower() != "archive")
            for f in sorted(files):
                _try_file(os.path.join(root, f))
    else:
        for f in sorted(os.listdir(full)):
            fpath = os.path.join(full, f)
            if os.path.isfile(fpath):
                _try_file(fpath)
    return result


def _build_print_pdf(app, inc: dict, sizes: dict, crows: list,
                     folder: str) -> tuple:
    """Assemble a complete PDF print package.

    Returns (pdf_bytes, non_pdf_files_list).
    pdf_bytes is the merged PDF; non_pdf_files_list contains filenames that
    could not be included (e.g. .doc attachments).
    """
    bld   = _SimplePDFBuilder()
    parts = []     # list of PDF bytes to merge
    nopdf = []     # filenames of files that couldn't be merged

    # ── 1. Cover page ─────────────────────────────────────────────
    toc_items = [("Work Orders", "")]
    for k, lbl in [
        ("drawings",    "Drawings Register"),
        ("relay",       "Relay Settings"),
        ("maintenance", "Maintenance Standards"),
        ("engineering", "Engineering Standards"),
    ]:
        if inc.get(k) == "print":
            toc_items.append((lbl, ""))
        elif inc.get(k) == "toc":
            toc_items.append((lbl + "  — printed separately", ""))
    _pdf_cover(
        bld,
        app.project_var.get().strip(),
        crows,
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        toc_items=toc_items,
        size=sizes.get("cover", "Letter Portrait"),
    )

    # ── 2. Work Orders ────────────────────────────────────────────
    wo_size = sizes.get("work_orders", "11x17 Landscape").replace("×","x")
    _pdf_work_orders(bld, app.jobs, app.drawing_registry, wo_size)

    # ── 3. Drawings ───────────────────────────────────────────────
    if inc.get("drawings") == "print":
        _pdf_drawings_reg(bld, app.drawing_registry,
                          sizes.get("drawings","Letter Portrait"))
        if folder:
            parts.extend(_collect_pdfs(folder, "Drawings", recurse=True))

    # ── 4. Relay Settings ─────────────────────────────────────────
    if inc.get("relay") == "print":
        _pdf_relay_reg(bld, app.relay_registry,
                       sizes.get("relay","Letter Portrait"))
        if folder:
            parts.extend(_collect_pdfs(folder, "Relay Settings"))

    # ── 5. Maintenance Standards ──────────────────────────────────
    if inc.get("maintenance") == "print":
        _pdf_standards(bld, app.maintenance_standards_registry, {},
                       sizes.get("maintenance","Letter Portrait"))
        if folder:
            parts.extend(_collect_pdfs(folder, "Maintenance Standards"))

    # ── 6. Engineering Standards ──────────────────────────────────
    if inc.get("engineering") == "print":
        _pdf_standards(bld, {}, app.engineering_standards_registry,
                       sizes.get("engineering","Letter Portrait"))
        if folder:
            parts.extend(_collect_pdfs(folder, "Engineering Standards"))

    # ── 7. CROW attached files ────────────────────────────────────
    if folder:
        crow_dir = os.path.join(folder, "CROW Outage")
        for crow in crows:
            for fname in crow.get("files", []):
                fpath = os.path.join(crow_dir, fname)
                if os.path.isfile(fpath):
                    data, err = _convert_file_to_pdf(fpath)
                    if data:
                        parts.append(data)
                    else:
                        nopdf.append(f"{fname} ({err})")

    # ── Build + merge ─────────────────────────────────────────────
    doc_pdf = bld.build()
    if parts and _PYPDF_AVAILABLE:
        final = _merge_pdfs_bytes([doc_pdf] + parts)
    else:
        final = doc_pdf

    return final, nopdf


class ExportWizard(tk.Toplevel):
    """Three-step wizard generating Paper, HTML/PDF, and Tablet export packages."""

    _SECTIONS = [
        ("drawings",    "Drawings Register"),
        ("relay",       "Relay Settings"),
        ("maintenance", "Maintenance Standards"),
        ("engineering", "Engineering Standards"),
    ]
    # default paper sizes per section key
    _DEFAULTS = {
        "cover":       "Letter Portrait",
        "work_orders": "11×17 Landscape",
        "drawings":    "11×17 Landscape",
        "relay":       "Letter Portrait",
        "maintenance": "Letter Portrait",
        "engineering": "Letter Portrait",
    }

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("Export Wizard")
        self.resizable(False, False)

        self._v_paper   = tk.BooleanVar(value=True)
        self._v_digital = tk.BooleanVar(value=True)
        self._v_tablet  = tk.BooleanVar(value=False)
        # Per-section mode: "Skip" | "Print" | "TOC only"
        self._v_sec     = {k: tk.StringVar(value="Skip") for k, _ in self._SECTIONS}
        self._v_qr      = tk.BooleanVar(value=True)
        self._v_sizes   = {k: tk.StringVar(value=v) for k, v in self._DEFAULTS.items()}

        self._step = 1
        self._build()
        self.geometry("600x530")
        _center_window(self)
        self.grab_set()
        self.wait_window()

    # ── build ─────────────────────────────────────────────────────

    def _build(self):
        self._hdr = tk.Frame(self, bg="#1a252f"); self._hdr.pack(fill="x")
        self._hdr_lbl = tk.Label(self._hdr, bg="#1a252f", fg="white",
                                  font=("", 11, "bold"), padx=14, pady=10)
        self._hdr_lbl.pack(side="left")

        container = tk.Frame(self, bg=self.cget("bg"))
        container.pack(fill="both", expand=True)

        self._frames = [
            self._step1(container),
            self._step2(container),
            self._step3(container),
        ]

        nav = ttk.Frame(self, padding=(14, 6, 14, 10)); nav.pack(fill="x")
        self._back_btn = ttk.Button(nav, text="◄ Back",  command=self._back,    state="disabled")
        self._back_btn.pack(side="left")
        self._cancel_btn = ttk.Button(nav, text="Cancel", command=self.destroy)
        self._cancel_btn.pack(side="right")
        self._next_btn = ttk.Button(nav, text="Next ►",  command=self._next)
        self._next_btn.pack(side="right", padx=(0, 6))

        self._show_step(1)

    def _step1(self, parent):
        f = ttk.Frame(parent, padding=20)
        ttk.Label(f, text="Choose which formats to generate:",
                  font=("", 9, "bold")).pack(anchor="w", pady=(0, 12))
        cards = [
            (self._v_paper,   "📄  PDF Package",
             "Merged PDF: cover, work orders, registry tables + all downloaded\n"
             "PDFs (drawings, relay settings, standards) in one printable file."),
            (self._v_digital, "💻  HTML / PDF",
             "Screen-optimised with live hyperlinks.\n"
             "Open in browser → Ctrl+P → Save as PDF."),
            (self._v_tablet,  "📱  Tablet (iPad)",
             "Large-text HTML for Safari. Downloaded files linked locally;\n"
             "remaining documents linked by URL."),
        ]
        for var, title_text, desc in cards:
            card = tk.Frame(f, bd=1, relief="solid", padx=14, pady=10,
                            bg="white", cursor="hand2")
            card.pack(fill="x", pady=4)
            top = tk.Frame(card, bg="white"); top.pack(fill="x")
            cb = tk.Checkbutton(top, variable=var, bg="white",
                                activebackground="white")
            cb.pack(side="left")
            tk.Label(top, text=title_text, font=("", 10, "bold"), bg="white",
                     cursor="hand2").pack(side="left", padx=(4, 0))
            tk.Label(card, text=desc, fg="grey", font=("", 8),
                     justify="left", bg="white", wraplength=500,
                     cursor="hand2").pack(anchor="w", padx=22)
            for w in [card] + card.winfo_children() + top.winfo_children():
                w.bind("<Button-1>", lambda e, v=var: v.set(not v.get()))
        return f

    def _step2(self, parent):
        f = ttk.Frame(parent, padding=(20, 14, 20, 14))
        req = ttk.LabelFrame(f, text="Always included", padding=(12, 6))
        req.pack(fill="x", pady=(0, 10))
        for txt in ("Cover Page  — project name, outage numbers, date, table of contents",
                    "Work Orders  — full work order table with colour coding"):
            r = ttk.Frame(req); r.pack(fill="x", pady=2)
            ttk.Label(r, text="✓", foreground="#27ae60",
                      font=("", 9, "bold")).pack(side="left")
            ttk.Label(r, text=txt).pack(side="left", padx=(6, 0))

        opt = ttk.LabelFrame(f, text="Optional sections", padding=(12, 6))
        opt.pack(fill="x", pady=(0, 10))
        for key, label in self._SECTIONS:
            r = ttk.Frame(opt); r.pack(fill="x", pady=2)
            ttk.Combobox(r, textvariable=self._v_sec[key], width=10,
                         values=("Skip", "Print", "TOC only"),
                         state="readonly").pack(side="left")
            ttk.Label(r, text=label).pack(side="left", padx=(8, 0))
        ttk.Label(opt, text="TOC only — listed on the cover page as “printed separately”,"
                            " no pages added to the package.",
                  foreground="grey", font=("", 8)).pack(anchor="w", pady=(6, 0))

        ext = ttk.LabelFrame(f, text="Extras", padding=(12, 6))
        ext.pack(fill="x")
        r2 = ttk.Frame(ext); r2.pack(fill="x", pady=2)
        ttk.Checkbutton(r2, variable=self._v_qr).pack(side="left")
        ttk.Label(r2, text="QR Code Sheet  (paper only — all document URLs as scannable codes)"
                  ).pack(side="left", padx=(4, 0))
        return f

    def _step3(self, parent):
        f = ttk.Frame(parent, padding=(20, 14, 20, 14))
        ttk.Label(f, text="Paper output — select page size per section:",
                  font=("", 9, "bold")).pack(anchor="w", pady=(0, 10))
        grid = ttk.Frame(f); grid.pack(fill="x")
        rows = [
            ("cover",       "Cover Page"),
            ("work_orders", "Work Orders"),
            ("drawings",    "Drawings Register"),
            ("relay",       "Relay Settings"),
            ("maintenance", "Maintenance Standards"),
            ("engineering", "Engineering Standards"),
        ]
        for i, (key, lbl) in enumerate(rows):
            ttk.Label(grid, text=lbl).grid(row=i, column=0, sticky="w",
                                           pady=4, padx=(0, 18))
            ttk.Combobox(grid, textvariable=self._v_sizes[key],
                         values=_EW_PAGE_SIZES, state="readonly",
                         width=22).grid(row=i, column=1, sticky="w", pady=4)
        ttk.Label(f,
                  text="Tip: 11×17 Landscape gives wider columns for work orders and drawing registers.",
                  foreground="grey", font=("", 8), wraplength=520
                  ).pack(anchor="w", pady=(14, 0))
        return f

    # ── navigation ─────────────────────────────────────────────────

    def _show_step(self, n):
        self._step = n
        for i, fr in enumerate(self._frames, 1):
            if i == n: fr.pack(fill="both", expand=True)
            else:       fr.pack_forget()
        labels = ["Choose Outputs", "Choose Sections", "Paper Sizes"]
        self._hdr_lbl.configure(
            text=f"Export Wizard — Step {n} of 3: {labels[n-1]}")
        self._back_btn.configure(state="normal" if n > 1 else "disabled")
        self._next_btn.configure(
            text="Generate ⚡" if n == 3 else "Next ►")

    def _back(self):
        if self._step > 1: self._show_step(self._step - 1)

    def _next(self):
        if self._step == 1 and not any(
                [self._v_paper.get(), self._v_digital.get(), self._v_tablet.get()]):
            messagebox.showwarning("Nothing Selected",
                                   "Choose at least one output format.", parent=self)
            return
        if self._step == 2 and not self._v_paper.get():
            # Skip paper-size step if paper not selected
            self._generate(); return
        if self._step < 3:
            self._show_step(self._step + 1)
        else:
            self._generate()

    # ── generation ─────────────────────────────────────────────────

    def _generate(self):
        app = self.app
        project = app.project_var.get().strip() or "Red-Line-Routing"
        folder  = app.project_folder or ""
        if not folder:
            messagebox.showwarning("Save First",
                                   "Save the project before exporting.", parent=self)
            return

        date   = datetime.now().strftime("%Y-%m-%d %H:%M")
        date_s = datetime.now().strftime("%Y-%m-%d")
        crows  = app.title_page.get("crows", [])
        _sec_mode = {"Skip": "skip", "Print": "print", "TOC only": "toc"}
        inc    = {k: _sec_mode.get(self._v_sec[k].get(), "skip")
                  for k, _ in self._SECTIONS}
        sizes  = {k: v.get() for k, v in self._v_sizes.items()}
        qr_flag = self._v_qr.get()

        _sec_anchor = {
            "drawings":    "sec-drawings",
            "relay":       "sec-relay",
            "maintenance": "sec-maintenance",
            "engineering": "sec-engineering",
        }
        toc = [("Work Orders", "sec-work-orders")]
        for k, lbl in self._SECTIONS:
            if inc.get(k) == "print":
                toc.append((lbl, _sec_anchor[k]))
            elif inc.get(k) == "toc":
                toc.append((lbl + "  — printed separately", ""))

        # Collect all document URLs for the QR sheet
        qr_items = []
        for n, info in sorted(app.drawing_registry.items()):
            if info.get("url"): qr_items.append((n, info["url"]))
        for d, info in sorted(app.relay_registry.items()):
            if info.get("url"): qr_items.append((d, info["url"]))

        modes = (["paper"]   if self._v_paper.get()   else []) + \
                (["digital"] if self._v_digital.get() else []) + \
                (["tablet"]  if self._v_tablet.get()  else [])

        generated = []
        errors    = []
        for mode in modes:
            try:
                if mode == "paper":
                    if _PYPDF_AVAILABLE:
                        pdf_bytes, nopdf = _build_print_pdf(
                            app, inc, sizes, crows, folder)
                        fpath = os.path.join(folder, f"Package_{date_s}.pdf")
                        with open(fpath, "wb") as fh:
                            fh.write(pdf_bytes)
                        generated.append(fpath)
                        if nopdf:
                            messagebox.showinfo(
                                "Non-PDF Attachments",
                                "The following files could not be included in the PDF "
                                "(open them separately):\n\n" + "\n".join(nopdf),
                                parent=self)
                    else:
                        # pypdf not available — fall back to print-ready HTML
                        messagebox.showwarning(
                            "PDF Library Not Available",
                            "The pypdf library could not be loaded so the export will\n"
                            "be a print-ready HTML file instead of a PDF.\n\n"
                            "Make sure the pypdf/ folder is in the same directory as\n"
                            f"wire_planner.py.\n\nDetail: {_PYPDF_ERROR or 'unknown'}",
                            parent=self)
                        html = self._assemble(
                            mode, project, date, crows, toc, inc, qr_flag,
                            sizes, qr_items, folder,
                            app.jobs, app.drawing_registry,
                            app.relay_registry,
                            app.maintenance_standards_registry,
                            app.engineering_standards_registry,
                        )
                        fpath = os.path.join(folder, f"Paper_{date_s}.html")
                        with open(fpath, "w", encoding="utf-8") as fh:
                            fh.write(html)
                        generated.append(fpath)
                else:
                    html = self._assemble(
                        mode, project, date, crows, toc, inc, qr_flag,
                        sizes, qr_items, folder,
                        app.jobs, app.drawing_registry,
                        app.relay_registry,
                        app.maintenance_standards_registry,
                        app.engineering_standards_registry,
                    )
                    suffix = {"digital": "Digital", "tablet": "Tablet"}[mode]
                    fpath  = os.path.join(folder, f"{suffix}_{date_s}.html")
                    with open(fpath, "w", encoding="utf-8") as fh:
                        fh.write(html)
                    generated.append(fpath)
            except Exception as exc:
                errors.append(f"{mode}: {exc}")

        if errors:
            messagebox.showerror("Export Errors",
                                  "Some exports failed:\n" + "\n".join(errors),
                                  parent=self)
        if generated:
            self.destroy()
            for path in generated:
                if path.lower().endswith(".pdf"):
                    _reveal_file(path)
                else:
                    _open_file(path)

    def _assemble(self, mode, project, date, crows, toc, inc,
                  qr_flag, sizes, qr_items, folder,
                  jobs, drw_reg, relay_reg, maint_reg, eng_reg):
        """Build the complete HTML for one output mode."""

        def pcls(key):
            """Return CSS class name for a section (adds @page binding in paper mode)."""
            return f"ew-{key}" if mode == "paper" else "page-content"

        # Build @page CSS for paper mode
        page_css = ""
        if mode == "paper":
            mapping = {
                "ew-cover":       sizes.get("cover",       "Letter Portrait"),
                "ew-work-orders": sizes.get("work_orders", "11×17 Landscape"),
                "ew-drawings":    sizes.get("drawings",    "11×17 Landscape"),
                "ew-relay":       sizes.get("relay",       "Letter Portrait"),
                "ew-maintenance": sizes.get("maintenance", "Letter Portrait"),
                "ew-engineering": sizes.get("engineering", "Letter Portrait"),
                "ew-qr":          "Letter Portrait",
            }
            lines = []
            for cls, size_name in mapping.items():
                w, h = _EW_PAGE_DIMS.get(size_name, ("8.5in", "11in"))
                pn   = cls.replace("-", "_")
                lines.append(f"@page {pn}{{size:{w} {h};margin:0.6in}}")
                lines.append(f"section.{cls}{{page:{pn}}}")
            page_css = "\n".join(lines)

        pf = folder  # always pass project folder for local-file resolution

        body = ""

        # Cover (+ CROW attached documents for paper/tablet)
        body += _ew_cover(project, crows, date, toc, mode,
                           css_class=pcls("cover") if mode == "paper" else "page-cover",
                           project_folder=pf)
        all_crow_files = [f for c in crows for f in c.get("files", [])]
        if all_crow_files:
            body += _ew_embedded_files(
                pf, "CROW Outage", mode,
                css_class="ew-cover" if mode == "paper" else "page-content",
                crow_files=all_crow_files,
            )

        # Work Orders
        body += _ew_with_id(
            _ew_work_orders(jobs, drw_reg, mode,
                            css_class="ew-work-orders" if mode == "paper" else "page-content"),
            "sec-work-orders",
        )

        # Optional sections — table + embedded local files
        _emb_cls = "page-content"  # embedded files always use generic class
        _sec_id = {
            "drawings":    "sec-drawings",
            "relay":       "sec-relay",
            "maintenance": "sec-maintenance",
            "engineering": "sec-engineering",
        }
        sec_funcs = {
            "drawings": lambda: (
                _ew_with_id(
                    _ew_drawings_reg(
                        drw_reg, mode, pf,
                        css_class="ew-drawings" if mode == "paper" else "page-content"),
                    "sec-drawings") +
                _ew_embedded_files(pf, "Drawings", mode, css_class=_emb_cls, recurse=True)
            ),
            "relay": lambda: (
                _ew_with_id(
                    _ew_relay_reg(
                        relay_reg, mode, pf,
                        css_class="ew-relay" if mode == "paper" else "page-content"),
                    "sec-relay") +
                _ew_embedded_files(pf, "Relay Settings", mode, css_class=_emb_cls)
            ),
            "maintenance": lambda: (
                _ew_with_id(
                    _ew_standards(
                        maint_reg, {}, mode, pf,
                        maint_css="ew-maintenance" if mode == "paper" else "page-content",
                        eng_css="page-content"),
                    "sec-maintenance") +
                _ew_embedded_files(pf, "Maintenance Standards", mode, css_class=_emb_cls)
            ),
            "engineering": lambda: (
                _ew_with_id(
                    _ew_standards(
                        {}, eng_reg, mode, pf,
                        maint_css="page-content",
                        eng_css="ew-engineering" if mode == "paper" else "page-content"),
                    "sec-engineering") +
                _ew_embedded_files(pf, "Engineering Standards", mode, css_class=_emb_cls)
            ),
        }
        for key, _ in self._SECTIONS:
            if inc.get(key) != "print":
                continue
            body += sec_funcs[key]()

        # QR sheet (paper only)
        if qr_flag and mode == "paper" and qr_items:
            body += _ew_qr_sheet(qr_items, css_class="ew-qr")

        suffix = {"paper":"Paper Export","digital":"HTML/PDF","tablet":"Tablet"}[mode]
        return _ew_full_html(f"{suffix} — {project}", body, page_css, mode)


# ──────────────────────────────────────────────────────────────────
# CROW dialog (outage record: outage_number + URL)
# ──────────────────────────────────────────────────────────────────

class CrowDialog(tk.Toplevel):
    """Add/edit a CROW outage record."""
    def __init__(self, parent, existing=None, base_url="", project_folder=""):
        super().__init__(parent)
        self.title("Edit CROW" if existing else "Add CROW")
        self.result = None
        self._base_url = base_url
        self._project_folder = project_folder
        self._files = list((existing or {}).get("files", []))
        self.resizable(True, False)
        self._build(existing or {})
        self.grab_set()
        self.wait_window()

    def _build(self, ex):
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Outage Number:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=4)
        self.num_var = tk.StringVar(value=ex.get("outage_number", ""))
        ttk.Entry(f, textvariable=self.num_var, width=20).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(f, text="format: 8-XXXXXXXX", foreground="grey",
                  font=("", 8)).grid(row=0, column=2, sticky="w", padx=(4, 0))
        ttk.Label(f, text="URL:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=4)
        self.url_var = tk.StringVar(value=ex.get("url", "") or self._base_url)
        crow_url_e = ttk.Entry(f, textvariable=self.url_var, width=36)
        crow_url_e.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(f, text="Ctrl+click to open", foreground="grey",
                  font=("", 7)).grid(row=1, column=2, sticky="w", padx=(4, 0))
        _bind_url_open(crow_url_e, self.url_var)

        # Attached documents
        ttk.Label(f, text="Attached Files:").grid(row=2, column=0, sticky="ne", padx=(0, 6), pady=4)
        file_fr = ttk.Frame(f)
        file_fr.grid(row=2, column=1, columnspan=2, sticky="nsew", pady=4)
        self._file_lb = tk.Listbox(file_fr, height=4, selectmode="single",
                                   relief="flat", bd=1, highlightthickness=1)
        self._file_lb.pack(side="left", fill="both", expand=True)
        lbsb = ttk.Scrollbar(file_fr, orient="vertical", command=self._file_lb.yview)
        self._file_lb.configure(yscrollcommand=lbsb.set)
        lbsb.pack(side="left", fill="y")
        btns = ttk.Frame(file_fr)
        btns.pack(side="left", padx=(4, 0), anchor="n")
        ttk.Button(btns, text="Add…",   width=8, command=self._add_file).pack(pady=(0, 2))
        ttk.Button(btns, text="Remove", width=8, command=self._remove_file).pack()
        for fname in self._files:
            self._file_lb.insert("end", fname)
        if not self._project_folder:
            ttk.Label(f, text="Save project first to attach files.",
                      foreground="grey", font=("", 8)).grid(
                row=3, column=1, sticky="w", pady=(0, 4))

        f.columnconfigure(1, weight=1)
        f.rowconfigure(2, weight=1)
        br = ttk.Frame(self)
        br.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(br, text="Cancel", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(br, text="Save",   command=self._save).pack(side="right", padx=2)
        self.geometry("520x280")

    def _add_file(self):
        if not self._project_folder:
            messagebox.showinfo("Save First",
                                "Save the project first, then you can attach files.",
                                parent=self)
            return
        path = filedialog.askopenfilename(parent=self, title="Attach file to CROW")
        if not path:
            return
        dest_dir = os.path.join(self._project_folder, "CROW Outage")
        os.makedirs(dest_dir, exist_ok=True)
        fname = os.path.basename(path)
        dest = os.path.join(dest_dir, fname)
        try:
            shutil.copy2(path, dest)
            self._files.append(fname)
            self._file_lb.insert("end", fname)
        except Exception as exc:
            messagebox.showerror("Copy Failed", str(exc), parent=self)

    def _remove_file(self):
        sel = self._file_lb.curselection()
        if not sel:
            return
        idx = sel[0]
        self._files.pop(idx)
        self._file_lb.delete(idx)

    def _save(self):
        num = self.num_var.get().strip()
        if not num:
            messagebox.showwarning("Required", "Outage number is required.", parent=self)
            return
        self.result = {
            "outage_number": num,
            "url": self.url_var.get().strip(),
            "files": list(self._files),
        }
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Relay Setting dialog
# ──────────────────────────────────────────────────────────────────

class RelaySettingDialog(tk.Toplevel):
    """Add/edit a relay settings record."""
    def __init__(self, parent, existing=None, wo_devices=None, base_url=""):
        super().__init__(parent)
        self.title("Edit Relay Setting" if existing else "Add Relay Setting")
        self.result = None
        self.wo_devices = wo_devices or []
        self.base_url = base_url
        self._import_src = tk.StringVar()   # source path chosen by Browse
        self.resizable(False, False)
        self._build(existing or {})
        self.grab_set()
        self.wait_window()

    def _build(self, ex):
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        self.vars = {
            "device_id": tk.StringVar(value=ex.get("device_id", "")),
            "title":     tk.StringVar(value=ex.get("title", "")),
            "revision":  tk.StringVar(value=ex.get("revision", "")),
            "engineer":  tk.StringVar(value=ex.get("engineer", "")),
            "contact":   tk.StringVar(value=ex.get("contact", "")),
            "url":       tk.StringVar(value=ex.get("url", "") or self.base_url),
            "wo_device": tk.StringVar(value=ex.get("wo_device", "")),
        }

        fields = [
            ("device_id", "Device ID:"),
            ("title",     "Title / Description:"),
            ("revision",  "Revision:"),
            ("engineer",  "Engineer:"),
            ("contact",   "Contact (email / phone):"),
            ("url",       "URL:"),
        ]
        for i, (key, label) in enumerate(fields):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="e", padx=(0, 6), pady=4)
            ttk.Entry(f, textvariable=self.vars[key], width=46).grid(
                row=i, column=1, sticky="ew", pady=4)

        row_wo = len(fields)
        ttk.Label(f, text="Link to WO Device:").grid(row=row_wo, column=0, sticky="e", padx=(0, 6), pady=4)
        ttk.Combobox(f, textvariable=self.vars["wo_device"],
                     values=self.wo_devices, width=43).grid(
            row=row_wo, column=1, sticky="ew", pady=4)
        ttk.Label(f, text="Optional — links this relay record to a device in the Work Order",
                  foreground="grey", font=("", 8)).grid(
            row=row_wo + 1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        # Setting file import — embedded in the dialog
        sep = ttk.Separator(f, orient="horizontal")
        sep.grid(row=row_wo + 2, column=0, columnspan=2, sticky="ew", pady=(2, 6))
        ttk.Label(f, text="Setting File:").grid(
            row=row_wo + 3, column=0, sticky="e", padx=(0, 6), pady=4)
        file_f = ttk.Frame(f)
        file_f.grid(row=row_wo + 3, column=1, sticky="ew", pady=4)
        file_f.columnconfigure(0, weight=1)
        self._file_lbl = ttk.Label(file_f, textvariable=self._import_src,
                                    foreground="grey", font=("", 8), anchor="w")
        self._file_lbl.grid(row=0, column=0, sticky="ew")
        ttk.Button(file_f, text="Browse…", command=self._browse_file).grid(
            row=0, column=1, padx=(6, 0))
        ttk.Label(f, text="Optional — choose a .txt setting file to import into Relay Settings/",
                  foreground="grey", font=("", 8)).grid(
            row=row_wo + 4, column=0, columnspan=2, sticky="w", pady=(0, 6))

        bf = ttk.Frame(f); bf.grid(row=row_wo + 5, column=0, columnspan=2, sticky="e")
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bf, text="Save",   command=self._save).pack(side="right")

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Select relay setting file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            parent=self)
        if path:
            self._import_src.set(path)
            self._file_lbl.configure(foreground="#2980b9")

    def _save(self):
        if not self.vars["device_id"].get().strip():
            messagebox.showwarning("Required", "Device ID is required.", parent=self); return
        self.result = {k: v.get().strip() for k, v in self.vars.items()}
        self.result["import_file"] = self._import_src.get().strip() or None
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Maintenance Standards dialog
# ──────────────────────────────────────────────────────────────────

class MaintenanceStandardDialog(tk.Toplevel):
    """Add/edit a maintenance standard record."""
    def __init__(self, parent, existing=None, base_url_telecom="", base_url_transmission=""):
        super().__init__(parent)
        self.title("Edit Maintenance Standard" if existing else "Add Maintenance Standard")
        self.result = None
        self.base_url_telecom = base_url_telecom
        self.base_url_transmission = base_url_transmission
        self.resizable(False, False)
        self._build(existing or {})
        self.grab_set()
        self.wait_window()

    def _build(self, ex):
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        self.vars = {
            "standard_id":       tk.StringVar(value=ex.get("standard_id", "")),
            "title":             tk.StringVar(value=ex.get("title", "")),
            "revision":          tk.StringVar(value=ex.get("revision", "")),
            "url_telecom":       tk.StringVar(value=ex.get("url_telecom", "") or self.base_url_telecom),
            "url_transmission":  tk.StringVar(value=ex.get("url_transmission", "") or self.base_url_transmission),
            "notes":             tk.StringVar(value=ex.get("notes", "")),
        }

        fields = [
            ("standard_id",      "Standard ID:"),
            ("title",            "Title / Description:"),
            ("revision",         "Revision:"),
            ("url_telecom",      "URL (Telecom):"),
            ("url_transmission", "URL (Transmission):"),
        ]
        for i, (key, label) in enumerate(fields):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="e", padx=(0, 6), pady=4)
            ttk.Entry(f, textvariable=self.vars[key], width=52).grid(
                row=i, column=1, sticky="ew", pady=4)

        row_n = len(fields)
        ttk.Label(f, text="Notes:").grid(row=row_n, column=0, sticky="ne", padx=(0, 6), pady=4)
        self._notes_widget = tk.Text(f, width=52, height=4, wrap="word", font=("", 9))
        self._notes_widget.grid(row=row_n, column=1, sticky="ew", pady=4)
        self._notes_widget.insert("1.0", ex.get("notes", ""))

        bf = ttk.Frame(f); bf.grid(row=row_n + 1, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bf, text="Save",   command=self._save).pack(side="right")

    def _save(self):
        if not self.vars["standard_id"].get().strip():
            messagebox.showwarning("Required", "Standard ID is required.", parent=self); return
        self.result = {k: v.get().strip() for k, v in self.vars.items()}
        self.result["notes"] = self._notes_widget.get("1.0", "end").strip()
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Engineering Standards dialog
# ──────────────────────────────────────────────────────────────────

class EngineeringStandardDialog(tk.Toplevel):
    """Add/edit an engineering standard record."""
    def __init__(self, parent, existing=None, base_url_telecom="", base_url_transmission=""):
        super().__init__(parent)
        self.title("Edit Engineering Standard" if existing else "Add Engineering Standard")
        self.result = None
        self.base_url_telecom = base_url_telecom
        self.base_url_transmission = base_url_transmission
        self.resizable(False, False)
        self._build(existing or {})
        self.grab_set()
        self.wait_window()

    def _build(self, ex):
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        stype = ex.get("standard_type", "Telecom")
        stored_url = ex.get("url", "")
        base = self.base_url_telecom if stype == "Telecom" else self.base_url_transmission

        # Extract document code from stored URL:
        # 1. strip from known base URL prefix, or
        # 2. parse documentId= query param directly
        doc_code_default = ""
        if stored_url:
            if base and stored_url.startswith(base):
                doc_code_default = stored_url[len(base):]
            else:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(stored_url).query)
                if "documentId" in qs:
                    doc_code_default = qs["documentId"][0]
                    # also derive base as everything up to and including "documentId="
                    idx = stored_url.lower().find("documentid=")
                    if idx != -1 and not base:
                        base = stored_url[:idx + len("documentId=")]
        url_default = stored_url if stored_url else base

        self.vars = {
            "standard_id":    tk.StringVar(value=ex.get("standard_id", "")),
            "title":          tk.StringVar(value=ex.get("title", "")),
            "revision":       tk.StringVar(value=ex.get("revision", "")),
            "standard_type":  tk.StringVar(value=stype),
            "document_code":  tk.StringVar(value=doc_code_default),
            "url":            tk.StringVar(value=url_default),
            "notes":          tk.StringVar(value=ex.get("notes", "")),
        }

        fields = [
            ("standard_id", "Standard ID:"),
            ("title",       "Title / Description:"),
            ("revision",    "Revision:"),
        ]
        for i, (key, label) in enumerate(fields):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="e", padx=(0, 6), pady=4)
            ttk.Entry(f, textvariable=self.vars[key], width=52).grid(
                row=i, column=1, sticky="ew", pady=4)

        # Type combobox
        row_type = len(fields)
        ttk.Label(f, text="Type:").grid(row=row_type, column=0, sticky="e", padx=(0, 6), pady=4)
        type_cb = ttk.Combobox(f, textvariable=self.vars["standard_type"],
                               values=["Telecom", "Transmission"], state="readonly", width=20)
        type_cb.grid(row=row_type, column=1, sticky="w", pady=4)

        # Document Code field — auto-builds URL from base URL + code
        row_code = row_type + 1
        ttk.Label(f, text="Document Code:").grid(row=row_code, column=0, sticky="e", padx=(0, 6), pady=4)
        code_frame = ttk.Frame(f)
        code_frame.grid(row=row_code, column=1, sticky="ew", pady=4)
        code_frame.columnconfigure(0, weight=1)
        ttk.Entry(code_frame, textvariable=self.vars["document_code"], width=40).grid(
            row=0, column=0, sticky="ew")
        ttk.Label(code_frame, text="e.g. {503E2F9C-0000-CA1C-9031-F0C466ADD444}",
                  foreground="grey", font=("", 8)).grid(row=1, column=0, sticky="w")

        # URL field — auto-populated from Document Code, or entered manually
        row_url = row_code + 1
        ttk.Label(f, text="Full URL:").grid(row=row_url, column=0, sticky="e", padx=(0, 6), pady=4)
        url_entry = ttk.Entry(f, textvariable=self.vars["url"], width=52)
        url_entry.grid(row=row_url, column=1, sticky="ew", pady=4)

        def _get_base():
            t = self.vars["standard_type"].get()
            return self.base_url_telecom if t == "Telecom" else self.base_url_transmission

        def _rebuild_url(*_):
            code = self.vars["document_code"].get().strip()
            b = _get_base()
            if code and b:
                self.vars["url"].set(b + code)
            elif code:
                # no base URL configured — just show the code so the user knows it's partial
                self.vars["url"].set(code)

        def _on_type_change(*_):
            b = _get_base()
            code = self.vars["document_code"].get().strip()
            if code and b:
                self.vars["url"].set(b + code)
            elif not self.vars["url"].get().strip():
                self.vars["url"].set(b)

        self.vars["document_code"].trace_add("write", _rebuild_url)
        type_cb.bind("<<ComboboxSelected>>", _on_type_change)

        row_n = row_url + 1
        ttk.Label(f, text="Notes:").grid(row=row_n, column=0, sticky="ne", padx=(0, 6), pady=4)
        self._notes_widget = tk.Text(f, width=52, height=4, wrap="word", font=("", 9))
        self._notes_widget.grid(row=row_n, column=1, sticky="ew", pady=4)
        self._notes_widget.insert("1.0", ex.get("notes", ""))

        bf = ttk.Frame(f); bf.grid(row=row_n + 1, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bf, text="Save",   command=self._save).pack(side="right")

    def _save(self):
        if not self.vars["standard_id"].get().strip():
            messagebox.showwarning("Required", "Standard ID is required.", parent=self); return
        self.result = {k: v.get().strip() for k, v in self.vars.items() if k != "document_code"}
        self.result["notes"] = self._notes_widget.get("1.0", "end").strip()
        self.destroy()


class StandardsLibraryDialog(tk.Toplevel):
    """Pick standards from the cross-project library to add to this project.

    kind         : "maintenance" | "engineering" — library bucket name
    library      : {sid: info} — the global library bucket for that kind
    existing_ids : iterable of IDs already in the project (shown greyed, not addable)
    result       : {sid: info} of the chosen entries, or None on cancel
    """

    def __init__(self, parent, kind, kind_label, library, existing_ids):
        super().__init__(parent)
        self.title(f"{kind_label} Library")
        self.resizable(True, True)
        self.grab_set()
        self.result = None
        self._kind = kind
        self._library = library

        ttk.Label(self, text=f"Standards remembered from previous projects."
                             f"  Select the ones to add:",
                  padding=(10, 8, 10, 0)).pack(anchor="w")

        frame = ttk.Frame(self, padding=10); frame.pack(fill="both", expand=True)
        cols = ("Standard ID", "Title", "Rev")
        self._tree = ttk.Treeview(frame, columns=cols, show="headings",
                                  selectmode="extended", height=14)
        for c, w in zip(cols, (140, 320, 60)):
            self._tree.heading(c, text=c)
            self._tree.column(c, width=w, stretch=(c == "Title"))
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._tree.tag_configure("inproj", foreground="#aaaaaa")

        existing = set(existing_ids)
        for sid, info in sorted(library.items()):
            in_proj = sid in existing
            self._tree.insert(
                "", "end", iid=sid,
                values=(sid + ("   (already in project)" if in_proj else ""),
                        info.get("title", ""), info.get("revision", "")),
                tags=("inproj",) if in_proj else ())
        self._existing = existing

        bf = ttk.Frame(self, padding=(10, 0, 10, 10)); bf.pack(fill="x")
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bf, text="Add Selected", command=self._add).pack(side="right")
        ttk.Button(bf, text="Select All New",
                   command=self._select_all_new).pack(side="left")
        ttk.Button(bf, text="Remove from Library",
                   command=self._remove_from_library).pack(side="left", padx=6)

        self.geometry("640x420")
        self.wait_window()

    def _select_all_new(self):
        new = [iid for iid in self._tree.get_children()
               if iid not in self._existing]
        self._tree.selection_set(new)

    def _remove_from_library(self):
        sel = [iid for iid in self._tree.selection()]
        if not sel:
            messagebox.showinfo("Select", "Select entries to remove from the library.",
                                parent=self)
            return
        if not messagebox.askyesno(
                "Remove", f"Forget {len(sel)} entr{'y' if len(sel)==1 else 'ies'} "
                          "from the library?\n(Projects that already contain them "
                          "are not affected.)", parent=self):
            return
        app = self.master
        for iid in sel:
            self._library.pop(iid, None)
            self._tree.delete(iid)
            if hasattr(app, "_forget_standard"):
                app._forget_standard(self._kind, iid)

    def _add(self):
        chosen = {}
        for iid in self._tree.selection():
            if iid in self._existing:
                continue
            if iid in self._library:
                chosen[iid] = dict(self._library[iid])
        if not chosen:
            messagebox.showinfo("Nothing Selected",
                                "Select at least one standard that is not already "
                                "in the project.", parent=self)
            return
        self.result = chosen
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Startup / wizard dialogs  —  shared UI helpers
# ──────────────────────────────────────────────────────────────────

def _center_window(win, w=None, h=None):
    """Center win on screen.  If w/h are omitted the window auto-sizes to content.

    Auto-size logic: call update_idletasks() first so Tk has computed the widget
    geometry, then read winfo_reqwidth/Height which reflects the packed content size.
    A small padding (40px wide, 20px tall) is added to avoid tight edges.
    The result is clamped to 90% of the screen so oversized dialogs are not clipped.
    The y offset is nudged up by 40px to allow for taskbars at the bottom of screen.
    Falls back to a plain geometry string without positioning if any Tk call fails
    (e.g. on headless/CI environments).
    """
    try:
        win.update_idletasks()
        if w is None: w = win.winfo_reqwidth()  + 40
        if h is None: h = win.winfo_reqheight() + 20
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        # Don't let the window exceed 90 % of screen
        w = min(w, int(sw * 0.90))
        h = min(h, int(sh * 0.90))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 40)
        win.geometry(f"{w}x{h}+{x}+{y}")
    except Exception:
        if w and h:
            win.geometry(f"{w}x{h}")

def _hover_btn(frame, bg_normal, bg_hover):
    """Add Enter/Leave colour-swap to a tk.Frame used as a clickable button."""
    def _all_widgets():
        result = [frame]
        def _walk(w):
            for c in w.winfo_children():
                result.append(c); _walk(c)
        _walk(frame)
        return result
    def _enter(_):
        for w in _all_widgets():
            try: w.configure(bg=bg_hover)
            except tk.TclError: pass
    def _leave(_):
        for w in _all_widgets():
            try: w.configure(bg=bg_normal)
            except tk.TclError: pass
    for w in _all_widgets():
        w.bind("<Enter>", _enter, add=True)
        w.bind("<Leave>", _leave, add=True)

def _styled_header(parent, title, subtitle=None, bg="#1c2833"):
    hdr = tk.Frame(parent, bg=bg); hdr.pack(fill="x")
    tk.Label(hdr, text=title, bg=bg, fg="white",
             font=("", 15, "bold"), pady=18, padx=24).pack(anchor="w")
    if subtitle:
        tk.Label(hdr, text=subtitle, bg=bg, fg="#85929e",
                 font=("", 9), pady=0, padx=24).pack(anchor="w")
    tk.Frame(hdr, bg=bg, height=14).pack()   # bottom padding
    return hdr

class DrawingSearchDialog(tk.Toplevel):
    """Reusable drawing search UI backed by DrawingSearchClient."""

    def __init__(self, parent, app_config: dict, multi_select=True, proj_cache=None):
        super().__init__(parent)
        self.title("Search Drawings")
        self.resizable(True, True)
        self.app_config = app_config
        self.multi_select = multi_select
        self.selected: list = []  # list[DrawingResult]
        self._page = 0
        self._last_paged = None   # most recent PagedResults
        self._from_cache = False
        self._client = None
        self._proj_cache: "_GlobalDrawingCache | None" = proj_cache
        self._build()
        self.geometry("900x580")
        _center_window(self)
        self.grab_set()
        self.wait_window()

    def _build(self):
        base_url = self.app_config.get("drawing_search_url", "").strip()

        # Header
        _styled_header(self, "Drawing Search",
                       "Search the corporate drawing register")

        # Warning if no URL configured
        self._warn_lbl = None
        if not base_url:
            self._warn_lbl = tk.Label(self, text="Configure Drawing Search URL in File → Software Settings",
                                      bg="#fdebd0", fg="#784212", font=("", 9, "bold"), pady=6)
            self._warn_lbl.pack(fill="x", padx=12, pady=(4, 0))

        # ── Search form ──────────────────────────────────────────────
        form_outer = ttk.LabelFrame(self, text="Search Criteria", padding=8)
        form_outer.pack(fill="x", padx=10, pady=6)

        row0 = ttk.Frame(form_outer); row0.pack(fill="x", pady=2)
        row1 = ttk.Frame(form_outer); row1.pack(fill="x", pady=2)
        row2 = ttk.Frame(form_outer); row2.pack(fill="x", pady=(4, 0))

        def _lbl_ent(parent, label, width=18):
            ttk.Label(parent, text=label).pack(side="left")
            var = tk.StringVar()
            ttk.Entry(parent, textvariable=var, width=width).pack(side="left", padx=(2, 10))
            return var

        self._v_drawing_num  = _lbl_ent(row0, "Drawing #:", 20)
        self._v_title        = _lbl_ent(row0, "Title contains:", 26)
        self._v_serial_from  = _lbl_ent(row0, "Serial From:", 10)
        self._v_serial_to    = _lbl_ent(row0, "Serial To:", 10)

        # Load options — prefer live cached data over static fallbacks
        if _DRAWING_SEARCH_AVAILABLE:
            _live = load_cached_options() or {}
            _fac  = _live.get("facilities",       FACILITIES)
            _typ  = _live.get("drawing_types",    DRAWING_TYPES)
            _subj = _live.get("drawing_subjects",  DRAWING_SUBJECTS)
        else:
            _fac = _typ = _subj = {}

        ttk.Label(row1, text="Facility:").pack(side="left")
        self._v_facility = tk.StringVar()
        fac_choices = [""] + [f"{k} — {v}" for k, v in _fac.items()]
        self._cb_facility = ttk.Combobox(row1, textvariable=self._v_facility,
                                         values=fac_choices, width=18)
        self._cb_facility.pack(side="left", padx=(2, 10))
        _bind_filter_combobox(self._cb_facility, fac_choices)

        ttk.Label(row1, text="Type:").pack(side="left")
        self._v_type = tk.StringVar()
        type_choices = [""] + [f"{k} — {v}" for k, v in _typ.items()] if _DRAWING_SEARCH_AVAILABLE else [""]
        self._cb_type = ttk.Combobox(row1, textvariable=self._v_type, values=type_choices, width=22)
        self._cb_type.pack(side="left", padx=(2, 10))
        _bind_filter_combobox(self._cb_type, type_choices)

        ttk.Label(row1, text="Subject:").pack(side="left")
        self._v_subject = tk.StringVar()
        subj_choices = [""] + [f"{k} — {v}" for k, v in _subj.items()] if _DRAWING_SEARCH_AVAILABLE else [""]
        self._cb_subject = ttk.Combobox(row1, textvariable=self._v_subject, values=subj_choices, width=22)
        self._cb_subject.pack(side="left", padx=(2, 10))
        _bind_filter_combobox(self._cb_subject, subj_choices)

        ttk.Label(row1, text="State:").pack(side="left")
        self._v_state = tk.StringVar(value="Released")
        ttk.Combobox(row1, textvariable=self._v_state,
                     values=["Released", "Preliminary", "Superseded", ""],
                     width=14, state="readonly").pack(side="left", padx=(2, 10))

        self._search_btn = ttk.Button(row2, text="\U0001f50d Search", command=self._do_search)
        self._search_btn.pack(side="left", padx=2)
        ttk.Button(row2, text="Clear", command=self._clear_form).pack(side="left", padx=2)

        if not base_url:
            self._search_btn.configure(state="disabled")

        # ── Results treeview ─────────────────────────────────────────
        tree_frame = ttk.Frame(self); tree_frame.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        cols = ("Drawing #", "Title", "Facility", "Type", "Subj", "Rev", "State")
        sm = "extended" if self.multi_select else "browse"
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode=sm)
        for col, w in [("Drawing #", 140), ("Title", 220), ("Facility", 70),
                       ("Type", 55), ("Subj", 55), ("Rev", 50), ("State", 90)]:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, stretch=(col == "Title"))
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # ── Status / pagination bar ───────────────────────────────────
        status_bar = ttk.Frame(self); status_bar.pack(fill="x", padx=10, pady=(0, 2))
        self._status_var = tk.StringVar(value="Enter search criteria and click Search.")
        self._status_lbl = tk.Label(status_bar, textvariable=self._status_var,
                                    fg="grey", bg=self.cget("bg"), font=("", 9))
        self._status_lbl.pack(side="left")

        pag_frame = ttk.Frame(status_bar); pag_frame.pack(side="right")
        self._prev_btn = ttk.Button(pag_frame, text="◄ Prev",
                                    command=lambda: self._do_search(page=self._page - 1))
        self._prev_btn.pack(side="left", padx=2)
        self._page_lbl = ttk.Label(pag_frame, text="Page 1")
        self._page_lbl.pack(side="left", padx=4)
        self._next_btn = ttk.Button(pag_frame, text="Next ►",
                                    command=lambda: self._do_search(page=self._page + 1))
        self._next_btn.pack(side="left", padx=2)
        self._prev_btn.configure(state="disabled")
        self._next_btn.configure(state="disabled")

        # ── Bottom buttons ────────────────────────────────────────────
        bf = ttk.Frame(self); bf.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        self._import_btn = ttk.Button(bf, text="Import Selected", command=self._import)
        self._import_btn.pack(side="right", padx=4)
        self._show_response_btn = ttk.Button(bf, text="Show Response",
                                             command=self._show_last_response, state="disabled")
        self._show_response_btn.pack(side="left", padx=4)

    def _clear_form(self):
        for v in (self._v_drawing_num, self._v_title, self._v_serial_from,
                  self._v_serial_to, self._v_facility, self._v_type,
                  self._v_subject):
            v.set("")
        self._v_state.set("Released")

    def _build_client(self):
        base_url = self.app_config.get("drawing_search_url", "").strip()
        if not base_url:
            return None
        raw_hdrs = self.app_config.get("request_headers", "")
        cookies  = _parse_cookies_from_headers(raw_hdrs)
        extra    = _parse_request_headers_raw(raw_hdrs)
        extra.pop("Cookie", None)
        return DrawingSearchClient(base_url=base_url, cookies=cookies,
                                   extra_headers=extra or None)

    def _get_params(self, page=0):
        # Parse code from "CODE — Label" or raw code
        def _code(val):
            return val.split("—")[0].strip() if "—" in val else val.strip()

        return SearchParams(
            drawing_num=self._v_drawing_num.get().strip(),
            title=self._v_title.get().strip(),
            serial_from=self._v_serial_from.get().strip(),
            serial_to=self._v_serial_to.get().strip(),
            facility=_code(self._v_facility.get()),
            drawing_type=_code(self._v_type.get()),
            drawing_subject=_code(self._v_subject.get()),
            state=self._v_state.get().strip(),
            page=page,
        )

    def _do_search(self, page=0):
        if not _DRAWING_SEARCH_AVAILABLE:
            messagebox.showerror("Unavailable", "drawing_search package not found.", parent=self)
            return
        client = self._build_client()
        if client is None:
            messagebox.showwarning("No URL", "Configure Drawing Search URL in Software Settings.", parent=self)
            return
        self._client = client
        self._page = page
        params = self._get_params(page=page)
        self._search_btn.configure(state="disabled")
        self._status_var.set("Searching…")
        self._status_lbl.configure(fg="grey")
        self.update_idletasks()

        # ── Cache-first path ──────────────────────────────────────────────
        if self._proj_cache is not None and page == 0:
            cached = self._proj_cache.get(params)
            if cached is not None:
                paged = PagedResults(results=cached, page=0, page_size=params.page_size,
                                     total_count=len(cached), has_next=False)
                self._on_results(paged, from_cache=True)
                # Background refresh — silently update cache then redisplay
                def _refresh():
                    try:
                        fresh = client.search_all_pages(params)
                        self._proj_cache.put(params, fresh)
                        fp = PagedResults(results=fresh, page=0,
                                          page_size=params.page_size,
                                          total_count=len(fresh), has_next=False)
                        self.after(0, lambda p=fp: self._on_results(p, from_cache=False))
                    except Exception:
                        pass  # keep showing cached results
                threading.Thread(target=_refresh, daemon=True).start()
                return

        # ── Live search path ──────────────────────────────────────────────
        if (self._proj_cache is not None
                and _GlobalDrawingCache.is_cacheable(params)
                and page == 0):
            # Fetch all pages so the cache is useful for future searches
            def _run_all():
                try:
                    results = client.search_all_pages(params)
                    self._proj_cache.put(params, results)
                    fp = PagedResults(results=results, page=0,
                                      page_size=params.page_size,
                                      total_count=len(results), has_next=False)
                    self.after(0, lambda p=fp: self._on_results(p, from_cache=False))
                except Exception as exc:
                    self.after(0, lambda e=exc: self._on_error(e))
            threading.Thread(target=_run_all, daemon=True).start()
        else:
            def on_done(paged):
                self.after(0, lambda: self._on_results(paged))
            def on_error(exc):
                self.after(0, lambda: self._on_error(exc))
            client.search_async(params, on_done=on_done, on_error=on_error)

    def _on_results(self, paged, from_cache=False):
        self._last_paged = paged
        self._from_cache = from_cache
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for r in paged.results:
            self.tree.insert("", "end", values=(
                r.drawing_number, r.title, r.facility,
                r.drawing_type, r.drawing_subject, r.revision, r.state,
            ), tags=(r.document_url,))
        count = paged.total_count if paged.total_count else len(paged.results)
        self._page_lbl.configure(text=f"Page {paged.page + 1}")
        self._prev_btn.configure(state="normal" if paged.page > 0 else "disabled")
        self._next_btn.configure(state="normal" if paged.has_next else "disabled")
        self._search_btn.configure(state="normal")
        self._show_response_btn.configure(state="normal")
        if count == 0:
            self._status_var.set("0 results — possible auth issue. Click 'Show Response' to inspect the server reply.")
            self._status_lbl.configure(fg="#c0392b")
        elif from_cache:
            self._status_var.set(f"{count} result(s) (cached — refreshing in background…)")
            self._status_lbl.configure(fg="#5dade2")
        else:
            self._status_var.set(f"{count} result(s)")
            self._status_lbl.configure(fg="grey")

    def _on_error(self, exc):
        self._status_var.set(f"Error: {exc}")
        self._status_lbl.configure(fg="#c0392b")
        self._search_btn.configure(state="normal")
        self._show_response_btn.configure(state="normal")
        url = ""
        if self._client:
            url = self._client.base_url + self._client.search_path
        _show_search_error_dialog(self, exc, url=url, context="Drawing search request failed.")

    def _show_last_response(self):
        """Show the raw server response from the last search — useful for diagnosing auth issues."""
        html = getattr(self._client, "_last_response_html", "") if self._client else ""
        win = tk.Toplevel(self)
        win.title("Raw Server Response")
        win.resizable(True, True)
        hdr = tk.Frame(win, bg="#1c3a5a"); hdr.pack(fill="x")
        tk.Label(hdr, text="Raw Server Response", bg="#1c3a5a", fg="white",
                 font=("", 10, "bold"), padx=12, pady=7).pack(side="left")
        f = ttk.Frame(win, padding=10); f.pack(fill="both", expand=True)
        ttk.Label(f, text="This is what the server returned. If it looks like a login page your "
                           "Cookie has expired or is for the wrong server.",
                  foreground="grey", font=("", 8), wraplength=560).pack(anchor="w", pady=(0, 6))
        txt = scrolledtext.ScrolledText(f, height=22, font=("Courier", 8), wrap="word")
        txt.pack(fill="both", expand=True)
        if html:
            plain = re.sub(r"<[^>]+>", " ", html)
            plain = re.sub(r"\s+", " ", plain).strip()
            txt.insert("1.0", plain[:8000])
        else:
            txt.insert("1.0", "No response stored yet — run a search first, then click this button.")
        txt.configure(state="disabled")
        bf = ttk.Frame(win, padding=(10, 0, 10, 8)); bf.pack(fill="x")
        ttk.Button(bf, text="Close", command=win.destroy).pack(side="right")
        win.geometry("620x460")
        _center_window(win)

    def _import(self):
        if self._last_paged is None:
            self.destroy()
            return
        sel_iids = self.tree.selection()
        results_map = {}
        if self._last_paged:
            for r in self._last_paged.results:
                results_map[r.drawing_number] = r
        if sel_iids:
            for iid in sel_iids:
                vals = self.tree.item(iid, "values")
                dn = vals[0] if vals else ""
                if dn in results_map:
                    self.selected.append(results_map[dn])
        elif self.multi_select and self._last_paged:
            self.selected = list(self._last_paged.results)
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Drawing-options fetch helpers  (used by both settings dialogs)
# ──────────────────────────────────────────────────────────────────

class _StickyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-attach auth headers when urllib follows a redirect.

    Python's urllib strips Cookie/Authorization on cross-domain redirects
    (security default).  Corporate SSO systems (e.g. SharePoint) redirect
    to a different host to authenticate, so we need to carry the headers.
    Host, Content-Length and Content-Type are intentionally excluded.
    """
    _SKIP = {"host", "content-length", "content-type"}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        all_hdrs = {**req.headers, **req.unredirected_hdrs}
        for k, v in all_hdrs.items():
            if k.lower() not in self._SKIP:
                new_req.add_unredirected_header(k, v)
        return new_req


class _ComboFilterHelper:
    """Floating autocomplete popup for a ttk.Combobox that keeps focus in the entry.

    A frameless Toplevel listbox appears below the combobox as the user types,
    filtered to entries containing the typed text anywhere (case-insensitive).
    Focus stays in the entry widget so typing is uninterrupted.
    ↓ moves focus into the popup; click or Enter selects; Escape closes.

    all_choices may be a plain list or a zero-argument callable that returns
    a list (used when the candidate values change dynamically).
    """

    _NAV = frozenset({
        "Shift_L", "Shift_R", "Control_L", "Control_R",
        "Alt_L", "Alt_R", "Win_L", "Win_R",
    })

    def __init__(self, combo: ttk.Combobox, all_choices):
        self.combo        = combo
        self._get_choices = all_choices if callable(all_choices) else (lambda: all_choices)
        self._popup: tk.Toplevel | None = None
        self._lb:    tk.Listbox  | None = None

        combo.configure(state="normal")
        combo.bind("<KeyRelease>",  self._on_key)
        combo.bind("<FocusOut>",    self._on_focus_out)
        combo.bind("<Down>",        self._on_down)
        combo.bind("<Escape>",      lambda _e: self._hide())
        combo.bind("<Destroy>",     lambda _e: self._destroy())

    # ── popup lifecycle ───────────────────────────────────────────

    def _build(self):
        p = tk.Toplevel(self.combo)
        p.wm_overrideredirect(True)
        p.wm_attributes("-topmost", True)
        outer = tk.Frame(p, bd=1, relief="solid", bg="#888888")
        outer.pack(fill="both", expand=True)
        vsb = tk.Scrollbar(outer, orient="vertical")
        lb  = tk.Listbox(outer, yscrollcommand=vsb.set, height=8,
                         font=("", 9), activestyle="dotbox",
                         selectmode="single", bd=0, highlightthickness=0)
        vsb.configure(command=lb.yview)
        lb.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        lb.bind("<ButtonRelease-1>", lambda _e: self._select())
        lb.bind("<Return>",          lambda _e: self._select())
        lb.bind("<Escape>",          lambda _e: (self._hide(),
                                                  self.combo.focus_set()))
        lb.bind("<FocusOut>",        self._on_lb_focus_out)
        p.withdraw()
        self._popup, self._lb = p, lb

    def _show(self, choices):
        if self._popup is None:
            self._build()
        self._lb.delete(0, "end")
        for c in choices[:100]:
            self._lb.insert("end", c)
        n = min(len(choices), 8)
        self._lb.configure(height=n)
        x = self.combo.winfo_rootx()
        y = self.combo.winfo_rooty() + self.combo.winfo_height()
        w = max(self.combo.winfo_width(), 180)
        self._popup.geometry(f"{w}x{n * 20 + 6}+{x}+{y}")
        self._popup.deiconify()
        self._popup.lift()

    def _hide(self):
        if self._popup:
            self._popup.withdraw()

    def _destroy(self):
        if self._popup:
            self._popup.destroy()
            self._popup = self._lb = None

    # ── entry key handlers ────────────────────────────────────────

    def _on_key(self, event):
        if event.keysym not in self._NAV:
            self.combo.after_idle(self._do_filter)

    def _do_filter(self):
        typed = self.combo.get().lower().strip()
        if not typed:
            self._hide()
            return
        filtered = [c for c in self._get_choices() if typed in c.lower()]
        if filtered:
            self._show(filtered)
        else:
            self._hide()

    def _on_down(self, event):
        if self._popup and self._popup.winfo_viewable() and self._lb:
            self._lb.focus_set()
            if not self._lb.curselection():
                self._lb.selection_set(0)
            self._lb.activate(0)
            return "break"

    def _on_focus_out(self, event):
        self.combo.after(200, self._maybe_hide)

    def _on_lb_focus_out(self, event):
        self.combo.after(200, self._maybe_hide)

    def _maybe_hide(self):
        try:
            if self.combo.focus_get() is not self._lb:
                self._hide()
        except Exception:
            self._hide()

    # ── selection ─────────────────────────────────────────────────

    def _select(self):
        if self._lb and self._lb.curselection():
            self.combo.set(self._lb.get(self._lb.curselection()[0]))
        self._hide()
        self.combo.focus_set()


def _bind_filter_combobox(combo: ttk.Combobox, all_choices: list) -> None:
    """Attach a floating autocomplete popup to a ttk.Combobox (no focus stealing)."""
    _ComboFilterHelper(combo, all_choices)


class _AppDB:
    """Global application database (~/.redlinerouting.db).

    Holds the app settings, the cross-project standards library, and the
    drawing-search cache shared by all projects. A new connection is
    opened per call so the same instance is safe from any thread
    (background cache refreshes write here too).

    On first run, settings and the standards library are migrated from
    the legacy ~/.redlinerouting.json (the file is left in place).
    """

    PATH = os.path.expanduser("~/.redlinerouting.db")
    _LEGACY_JSON = os.path.expanduser("~/.redlinerouting.json")

    def __init__(self, path=None):
        self.path = path or self.PATH
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS config(
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS standards_library(
                    kind        TEXT NOT NULL,
                    standard_id TEXT NOT NULL,
                    info        TEXT NOT NULL,
                    updated_at  REAL NOT NULL,
                    PRIMARY KEY (kind, standard_id));
                CREATE TABLE IF NOT EXISTS drawing_cache(
                    cache_key TEXT PRIMARY KEY,
                    results   TEXT NOT NULL,
                    cached_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS control_room_desks(
                    desk_id     TEXT PRIMARY KEY,
                    desk_name   TEXT NOT NULL DEFAULT '',
                    desk_type   TEXT NOT NULL DEFAULT '',
                    phone_int   TEXT NOT NULL DEFAULT '',
                    phone_local TEXT NOT NULL DEFAULT '',
                    phone_toll  TEXT NOT NULL DEFAULT '',
                    stations    TEXT NOT NULL DEFAULT '[]');
            """)
        self._migrate_legacy_json()

    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.isolation_level = None   # autocommit
        return conn

    def _migrate_legacy_json(self):
        try:
            with self._conn() as c:
                if c.execute("SELECT COUNT(*) FROM config").fetchone()[0]:
                    return   # already migrated / in use
            with open(self._LEGACY_JSON, encoding="utf-8") as fh:
                legacy = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, sqlite3.Error):
            return
        lib = legacy.pop("standards_library", {})
        self.save_config(legacy)
        for kind, bucket in lib.items():
            for sid, info in bucket.items():
                self.put_standard(kind, sid, info)

    # ── config ────────────────────────────────────────────────────

    def load_config(self) -> dict:
        try:
            with self._conn() as c:
                rows = c.execute("SELECT key, value FROM config").fetchall()
            return {k: json.loads(v) for k, v in rows}
        except (sqlite3.Error, json.JSONDecodeError):
            return {}

    def save_config(self, cfg: dict):
        with self._conn() as c:
            c.execute("BEGIN")
            c.execute("DELETE FROM config")
            c.executemany(
                "INSERT INTO config(key, value) VALUES (?, ?)",
                [(k, json.dumps(v)) for k, v in cfg.items()])
            c.execute("COMMIT")

    # ── standards library ─────────────────────────────────────────

    def get_standards(self, kind) -> dict:
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT standard_id, info FROM standards_library "
                    "WHERE kind = ?", (kind,)).fetchall()
            return {sid: json.loads(info) for sid, info in rows}
        except (sqlite3.Error, json.JSONDecodeError):
            return {}

    def put_standard(self, kind, sid, info):
        with self._conn() as c:
            c.execute(
                "INSERT INTO standards_library(kind, standard_id, info, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(kind, standard_id) DO UPDATE SET "
                "info = excluded.info, updated_at = excluded.updated_at",
                (kind, sid, json.dumps(info), datetime.now().timestamp()))

    def delete_standard(self, kind, sid):
        with self._conn() as c:
            c.execute("DELETE FROM standards_library "
                      "WHERE kind = ? AND standard_id = ?", (kind, sid))

    # ── drawing search cache ──────────────────────────────────────

    def cache_get(self, key):
        """Return the cached list of result dicts for key, or None."""
        try:
            with self._conn() as c:
                row = c.execute(
                    "SELECT results FROM drawing_cache WHERE cache_key = ?",
                    (key,)).fetchone()
            return json.loads(row[0]) if row else None
        except (sqlite3.Error, json.JSONDecodeError):
            return None

    def cache_put(self, key, results, cached_at=None):
        with self._conn() as c:
            c.execute(
                "INSERT INTO drawing_cache(cache_key, results, cached_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(cache_key) DO UPDATE SET "
                "results = excluded.results, cached_at = excluded.cached_at",
                (key, json.dumps(results),
                 cached_at if cached_at is not None
                 else datetime.now().timestamp()))

    def cache_keys(self):
        try:
            with self._conn() as c:
                return [r[0] for r in
                        c.execute("SELECT cache_key FROM drawing_cache")]
        except sqlite3.Error:
            return []

    def cache_stale_keys(self, max_age_seconds):
        """Return cache keys whose entry is older than max_age_seconds."""
        cutoff = datetime.now().timestamp() - max_age_seconds
        try:
            with self._conn() as c:
                return [r[0] for r in c.execute(
                    "SELECT cache_key FROM drawing_cache WHERE cached_at < ?",
                    (cutoff,))]
        except sqlite3.Error:
            return []

    def cache_merge_legacy(self, store: dict):
        """Import a per-project drawing_search_cache dict from an old .redline.

        Entries newer than what the DB already holds win.
        """
        for key, entry in store.items():
            try:
                ts = float(entry.get("cached_at", 0))
                results = entry.get("results", [])
            except (AttributeError, TypeError, ValueError):
                continue
            with self._conn() as c:
                row = c.execute(
                    "SELECT cached_at FROM drawing_cache WHERE cache_key = ?",
                    (key,)).fetchone()
            if row is None or row[0] < ts:
                self.cache_put(key, results, cached_at=ts)

    # ── control room desks ────────────────────────────────────────

    def get_ctrl_desks(self) -> list:
        """Return all control room desks ordered by name."""
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT desk_id,desk_name,desk_type,phone_int,phone_local,phone_toll,stations "
                    "FROM control_room_desks ORDER BY desk_name"
                ).fetchall()
            return [{"desk_id": r[0], "desk_name": r[1], "desk_type": r[2],
                     "phone_int": r[3], "phone_local": r[4], "phone_toll": r[5],
                     "stations": json.loads(r[6])} for r in rows]
        except (sqlite3.Error, json.JSONDecodeError):
            return []

    def put_ctrl_desk(self, desk: dict):
        with self._conn() as c:
            c.execute(
                "INSERT INTO control_room_desks"
                "(desk_id,desk_name,desk_type,phone_int,phone_local,phone_toll,stations) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(desk_id) DO UPDATE SET "
                "desk_name=excluded.desk_name, desk_type=excluded.desk_type, "
                "phone_int=excluded.phone_int, phone_local=excluded.phone_local, "
                "phone_toll=excluded.phone_toll, stations=excluded.stations",
                (desk["desk_id"], desk.get("desk_name",""), desk.get("desk_type",""),
                 desk.get("phone_int",""), desk.get("phone_local",""),
                 desk.get("phone_toll",""), json.dumps(desk.get("stations",[]))))

    def delete_ctrl_desk(self, desk_id: str):
        with self._conn() as c:
            c.execute("DELETE FROM control_room_desks WHERE desk_id=?", (desk_id,))


class _GlobalDrawingCache:
    """Drawing search cache shared across all projects (SQLite-backed).

    Only categorical searches (facility / drawing_type / drawing_subject / state,
    no free-text filters) are cached and auto-refreshed.
    """

    def __init__(self, db: "_AppDB"):
        self._db = db

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def is_cacheable(params) -> bool:
        return not any([
            params.drawing_num, params.title, params.title2,
            params.serial_from, params.serial_to,
            params.manufacturer_name, params.manufacturer_doc_num,
            params.remarks_contain, params.legacy_document_num,
        ])

    @staticmethod
    def _key(params) -> str:
        return f"{params.facility}|{params.drawing_type}|{params.drawing_subject}|{params.state}"

    # ── public API ─────────────────────────────────────────────────

    def get(self, params) -> "list | None":
        if not self.is_cacheable(params):
            return None
        raw = self._db.cache_get(self._key(params))
        if raw is None:
            return None
        try:
            return [DrawingResult(**d) for d in raw]
        except Exception:
            return None

    def put(self, params, results: list) -> None:
        if not self.is_cacheable(params):
            return
        self._db.cache_put(self._key(params), [vars(r) for r in results])

    def iter_keys(self):
        """Yield (facility, drawing_type, drawing_subject, state) for every cached entry."""
        for k in self._db.cache_keys():
            parts = k.split("|")
            if len(parts) == 4:
                yield tuple(parts)


def _show_search_error_dialog(parent, exc: Exception, url: str = "", context: str = "") -> None:
    """Show a scrollable error-detail dialog for HTTP/search failures."""
    win = tk.Toplevel(parent)
    win.title("Request Error")
    win.resizable(True, True)

    hdr = tk.Frame(win, bg="#7b241c"); hdr.pack(fill="x")
    tk.Label(hdr, text="Request Error", bg="#7b241c", fg="white",
             font=("", 11, "bold"), padx=14, pady=8).pack(side="left")

    body_f = ttk.Frame(win, padding=12); body_f.pack(fill="both", expand=True)

    if context:
        ttk.Label(body_f, text=context, font=("", 9, "bold")).pack(anchor="w")

    if url:
        r = ttk.Frame(body_f); r.pack(fill="x", pady=(4, 2))
        ttk.Label(r, text="URL:", font=("", 9, "bold")).pack(side="left")
        ttk.Label(r, text=url, font=("Courier", 8), foreground="#2980b9",
                  wraplength=520, justify="left").pack(side="left", padx=(4, 0))

    lines = [f"Error: {type(exc).__name__}: {exc}"]
    if hasattr(exc, "code"):
        lines.append(f"HTTP status: {exc.code} {getattr(exc, 'reason', '')}")

    body_text = getattr(exc, "_response_body", None)
    if body_text is None:
        try:
            raw = exc.read() if hasattr(exc, "read") else b""
            body_text = raw.decode("utf-8", errors="replace") if raw else ""
        except Exception:
            body_text = ""
    if body_text:
        plain = re.sub(r"<[^>]+>", " ", body_text)
        plain = re.sub(r"\s+", " ", plain).strip()[:2000]
        lines.append(f"\nServer response:\n{plain}")

    txt = scrolledtext.ScrolledText(body_f, height=12, font=("Courier", 9), wrap="word")
    txt.pack(fill="both", expand=True, pady=(6, 0))
    txt.insert("1.0", "\n".join(lines))
    txt.configure(state="disabled")

    ttk.Label(body_f,
              text="Check: correct URL in Software Settings · current Cookie value · "
                   "Authorization header if required",
              foreground="grey", font=("", 8), wraplength=520).pack(anchor="w", pady=(6, 0))

    bf = ttk.Frame(win, padding=(12, 0, 12, 10)); bf.pack(fill="x")
    ttk.Button(bf, text="Close", command=win.destroy).pack(side="right")

    win.geometry("600x400")
    _center_window(win)
    win.grab_set()


def _parse_request_headers_raw(raw_headers: str) -> dict:
    """Parse a raw 'Header-Name: value' block into a dict (module-level helper)."""
    headers = {}
    for line in raw_headers.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        headers[k.strip()] = v.strip()
    return headers


def _parse_cookies_from_headers(raw_headers: str) -> dict:
    """Extract Cookie key=value pairs from a raw headers text block.

    Looks for a ``Cookie:`` line; if absent, treats the whole string as a
    raw cookie string (``name=val; name2=val2``).
    """
    raw = ""
    for line in raw_headers.splitlines():
        if line.lower().startswith("cookie:"):
            raw = line.split(":", 1)[1].strip()
            break
    if not raw:
        raw = raw_headers.strip()
    cookies = {}
    for part in re.split(r";\s*", raw):
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies


def _show_fetch_options_dialog(parent, url: str, headers: dict, search_path: str = None) -> None:
    """Open a pop-out dialog that fetches and displays drawing form options."""
    if not _DRAWING_SEARCH_AVAILABLE:
        messagebox.showerror("Unavailable",
            "The drawing_search package is not installed or could not be imported.",
            parent=parent)
        return
    if not url:
        messagebox.showwarning("No URL",
            "Set the Drawing Search URL in Software Settings first.", parent=parent)
        return

    dlg = tk.Toplevel(parent)
    dlg.title("Fetch Drawing Options")
    dlg.resizable(True, True)
    dlg.grab_set()

    # Header
    hdr = tk.Frame(dlg, bg="#1c3a5a"); hdr.pack(fill="x")
    tk.Label(hdr, text="Fetch Drawing Options", bg="#1c3a5a", fg="white",
             font=("", 11, "bold"), padx=14, pady=10).pack(side="left")

    body = ttk.Frame(dlg, padding=14); body.pack(fill="both", expand=True)

    ttk.Label(body, text=f"URL:  {url}", foreground="grey",
              font=("", 8), wraplength=460).pack(anchor="w", pady=(0, 6))

    log = scrolledtext.ScrolledText(body, height=14, font=("Courier", 9),
                                    state="disabled", wrap="word",
                                    bg="#1c2833", fg="#ecf0f1")
    log.tag_configure("ok",   foreground="#58d68d")
    log.tag_configure("err",  foreground="#ec7063")
    log.tag_configure("head", foreground="#85c1e9")
    log.tag_configure("info", foreground="#85929e")
    log.pack(fill="both", expand=True)

    bf = ttk.Frame(dlg, padding=(14, 4, 14, 10)); bf.pack(fill="x")
    close_btn = ttk.Button(bf, text="Close", command=dlg.destroy, state="disabled")
    close_btn.pack(side="right")

    def _log(text, tag=None):
        log.configure(state="normal")
        log.insert("end", text, tag or "")
        log.see("end")
        log.configure(state="disabled")

    def _run():
        _pb = urllib.parse.urlparse(url.rstrip("/"))
        if search_path:
            _actual = url.rstrip("/") + search_path
        elif _pb.path and _pb.path not in ("", "/"):
            _actual = url
        else:
            _actual = url.rstrip("/") + "/search/searchGT.html"
        dlg.after(0, lambda a=_actual: _log(f"Connecting to {a} …\n", "head"))
        try:
            opts = fetch_form_options(url, extra_headers=headers, form_path=search_path)

            fac   = opts.get("facilities",       {})
            typs  = opts.get("drawing_types",    {})
            subjs = opts.get("drawing_subjects", {})

            dlg.after(0, lambda: _log(
                f"\n── Facilities ({len(fac)}) ──\n", "head"))
            for code, label in sorted(fac.items()):
                dlg.after(0, lambda c=code, l=label: _log(f"  {c:12}  {l}\n", "ok"))

            dlg.after(0, lambda: _log(
                f"\n── Drawing Types ({len(typs)}) ──\n", "head"))
            for code, label in sorted(typs.items()):
                dlg.after(0, lambda c=code, l=label: _log(f"  {c:6}  {l}\n", "ok"))

            dlg.after(0, lambda: _log(
                f"\n── Drawing Subjects ({len(subjs)}) ──\n", "head"))
            for code, label in sorted(subjs.items()):
                dlg.after(0, lambda c=code, l=label: _log(f"  {c:6}  {l}\n", "ok"))

            # Persist and update in-memory tables
            save_cached_options(opts)
            from drawing_search.lookup_tables import DRAWING_TYPES, DRAWING_SUBJECTS, FACILITIES
            if fac:   FACILITIES.clear();     FACILITIES.update(fac)
            if typs:  DRAWING_TYPES.clear();  DRAWING_TYPES.update(typs)
            if subjs: DRAWING_SUBJECTS.clear(); DRAWING_SUBJECTS.update(subjs)

            dlg.after(0, lambda: _log(
                f"\n✓  Saved — {len(fac)} facilities, {len(typs)} types, "
                f"{len(subjs)} subjects.\n", "ok"))
        except Exception as exc:
            body_info = ""
            try:
                _b = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
                if _b:
                    _b = re.sub(r"<[^>]+>", " ", _b)
                    _b = re.sub(r"\s+", " ", _b).strip()[:500]
                    body_info = f"\n  Server: {_b}"
            except Exception:
                pass
            dlg.after(0, lambda e=exc, bi=body_info: _log(f"\n✗  Error: {e}{bi}\n", "err"))
        finally:
            dlg.after(0, lambda: close_btn.configure(state="normal"))

    threading.Thread(target=_run, daemon=True).start()
    _center_window(dlg, 520, 480)


# ──────────────────────────────────────────────────────────────────
# Control Room Desks dialogs
# ──────────────────────────────────────────────────────────────────

class CtrlRoomDeskEditDialog(tk.Toplevel):
    """Create or edit a single control room desk entry."""

    def __init__(self, parent, existing=None):
        super().__init__(parent)
        self.title("Edit Desk" if existing else "Add Control Room Desk")
        self.resizable(True, False)
        self.result = None
        self._desk_id = (existing or {}).get("desk_id")
        self._build(existing or {})
        _center_window(self)
        self.grab_set()
        self.wait_window()

    def _build(self, d):
        f = tk.Frame(self, bg="white", padx=16, pady=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        fields = [
            ("desk_name",  "Desk Name:"),
            ("desk_type",  "Desk Type:"),
            ("phone_int",  "Internal Phone:"),
            ("phone_local","Local Phone:"),
            ("phone_toll", "Toll Free:"),
        ]
        self._vars = {}
        for i, (key, label) in enumerate(fields):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="e", padx=(0, 6), pady=3)
            var = tk.StringVar(value=d.get(key, ""))
            self._vars[key] = var
            ttk.Entry(f, textvariable=var, width=40).grid(row=i, column=1, sticky="ew", pady=3)

        r = len(fields)
        ttk.Label(f, text="Stations:").grid(row=r, column=0, sticky="ne", padx=(0, 6), pady=(8, 3))
        st_f = ttk.Frame(f)
        st_f.grid(row=r, column=1, sticky="ew", pady=(8, 3))
        st_f.columnconfigure(0, weight=1)

        self._stations_lb = tk.Listbox(st_f, height=5, selectmode="single", font=("", 9),
                                       bg="white", relief="flat", bd=1,
                                       highlightthickness=1, highlightbackground="#d5d8dc",
                                       exportselection=False)
        self._stations_lb.grid(row=0, column=0, sticky="ew")
        for s in d.get("stations", []):
            self._stations_lb.insert("end", s)

        btn_col = ttk.Frame(st_f)
        btn_col.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        ttk.Button(btn_col, text="Remove",
                   command=lambda: self._stations_lb.delete(
                       self._stations_lb.curselection()[0])
                   if self._stations_lb.curselection() else None,
                   width=7).pack()

        add_row = ttk.Frame(st_f)
        add_row.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        add_row.columnconfigure(0, weight=1)
        st_var = tk.StringVar()
        ttk.Entry(add_row, textvariable=st_var, width=28).grid(row=0, column=0, sticky="ew")

        def _add_station(e=None):
            s = st_var.get().strip()
            if s and s not in self._stations_lb.get(0, "end"):
                self._stations_lb.insert("end", s)
                st_var.set("")

        ttk.Button(add_row, text="Add", command=_add_station,
                   width=6).grid(row=0, column=1, padx=(4, 0))
        ttk.Entry(add_row, textvariable=st_var).bind("<Return>", _add_station)

        sep = tk.Frame(self, bg="#d5d8dc", height=1); sep.pack(fill="x", side="bottom")
        bf = tk.Frame(self, bg="#eaecee"); bf.pack(fill="x", side="bottom")
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 12), pady=8)
        ttk.Button(bf, text="Save", command=self._save).pack(side="right", pady=8)

    def _save(self):
        name = self._vars["desk_name"].get().strip()
        if not name:
            messagebox.showwarning("Name Required", "Please enter a desk name.", parent=self)
            return
        import uuid as _uuid
        self.result = {
            "desk_id":    self._desk_id or f"desk_{_uuid.uuid4().hex[:8]}",
            "desk_name":  name,
            "desk_type":  self._vars["desk_type"].get().strip(),
            "phone_int":  self._vars["phone_int"].get().strip(),
            "phone_local": self._vars["phone_local"].get().strip(),
            "phone_toll": self._vars["phone_toll"].get().strip(),
            "stations":   list(self._stations_lb.get(0, "end")),
        }
        self.destroy()


class CtrlRoomDesksManagerDialog(tk.Toplevel):
    """List, add, edit and delete control room desks stored in the global DB."""

    def __init__(self, parent, app_db):
        super().__init__(parent)
        self.title("Control Room Desks")
        self.resizable(True, True)
        self._db = app_db
        self._build()
        _center_window(self, 860, 400)
        self.grab_set()
        self.wait_window()

    def _build(self):
        _styled_header(self, "Control Room Desks",
                       "Configure desks for protection requests and returns")

        body = tk.Frame(self, bg="white"); body.pack(fill="both", expand=True, padx=12, pady=8)

        tb = ttk.Frame(body); tb.pack(fill="x", pady=(0, 4))
        ttk.Button(tb, text="+ Add Desk",  command=self._add).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",        command=self._edit).pack(side="left", padx=2)
        ttk.Button(tb, text="Delete",      command=self._delete).pack(side="left", padx=2)

        cols = ("name", "type", "phone_int", "phone_local", "phone_toll", "stations")
        self._tree = ttk.Treeview(body, columns=cols, show="headings", height=14)
        for col, hdr, w in [
            ("name",       "Desk Name",       160),
            ("type",       "Type",            110),
            ("phone_int",  "Internal Phone",  120),
            ("phone_local","Local Phone",     120),
            ("phone_toll", "Toll Free",       120),
            ("stations",   "Associated Stations", 220),
        ]:
            self._tree.heading(col, text=hdr)
            self._tree.column(col, width=w, minwidth=60)

        vsb = ttk.Scrollbar(body, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._tree.bind("<Double-1>", lambda _: self._edit())

        sep = tk.Frame(self, bg="#d5d8dc", height=1); sep.pack(fill="x", side="bottom")
        bf = tk.Frame(self, bg="#eaecee"); bf.pack(fill="x", side="bottom")
        ttk.Button(bf, text="Close", command=self.destroy).pack(side="right", padx=12, pady=8)

        self._load()

    def _load(self):
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        for d in self._db.get_ctrl_desks():
            self._tree.insert("", "end", iid=d["desk_id"], values=(
                d["desk_name"], d["desk_type"],
                d["phone_int"], d["phone_local"], d["phone_toll"],
                ", ".join(d["stations"]),
            ))

    def _add(self):
        dlg = CtrlRoomDeskEditDialog(self)
        if dlg.result:
            self._db.put_ctrl_desk(dlg.result)
            self._load()

    def _edit(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("Select a Desk", "Please select a desk to edit.", parent=self)
            return
        desks = {d["desk_id"]: d for d in self._db.get_ctrl_desks()}
        desk = desks.get(sel[0])
        if desk:
            dlg = CtrlRoomDeskEditDialog(self, existing=desk)
            if dlg.result:
                self._db.put_ctrl_desk(dlg.result)
                self._load()

    def _delete(self):
        sel = self._tree.selection()
        if not sel:
            return
        name = self._tree.item(sel[0], "values")[0]
        if messagebox.askyesno("Delete Desk", f'Delete desk "{name}"?', parent=self):
            self._db.delete_ctrl_desk(sel[0])
            self._load()


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
            ("Drawings",       [("base_drawing_url",    "Base Drawing URL"),
                                 ("drawing_search_url",  "Drawing Search URL"),
                                ]),
            ("Aspen",          [("aspen_url",           "Aspen URL (future)")]),
            ("CROWs",          [("base_crow_url",       "Base CROW URL")]),
            ("Relay Settings", [("base_relay_url",      "Base Relay URL")]),
            ("Maintenance Standards", [
                ("base_maintenance_telecom_url",      "Base URL (Telecom)"),
                ("base_maintenance_transmission_url", "Base URL (Transmission)"),
            ]),
            ("Engineering Standards", [
                ("base_engineering_telecom_url",      "Base URL (Telecom)"),
                ("base_engineering_transmission_url", "Base URL (Transmission)"),
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
        dlg = DrawingEditDialog(self, base_url=self.app_config.get("base_drawing_url",""),
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
                                base_url=self.app_config.get("base_drawing_url",""),
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
        dlg = EngineeringStandardDialog(
            self,
            base_url_telecom=self.app_config.get("base_engineering_telecom_url", ""),
            base_url_transmission=self.app_config.get("base_engineering_transmission_url", ""),
        )
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
        dlg = EngineeringStandardDialog(
            self,
            existing={"standard_id": sid, **info},
            base_url_telecom=self.app_config.get("base_engineering_telecom_url", ""),
            base_url_transmission=self.app_config.get("base_engineering_transmission_url", ""),
        )
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
        self._app_db = _AppDB()           # ~/.redlinerouting.db — settings, standards library, drawing cache
        self._drawing_cache = _GlobalDrawingCache(self._app_db)
        self.app_config = self._load_app_config()
        self._build_menu()
        self._build_ui()
        self.after_idle(self._startup_flow)
        self._schedule_drawing_cache_refresh()

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

        proj = result["project_name"]
        safe = "".join(c if c not in r'<>:"/\|?*' else "_" for c in proj) if proj else "RedLine_Plan"
        folder = os.path.join(result["save_location"], safe)
        try:
            os.makedirs(folder, exist_ok=True)
            for sub in ("Drawings", "Relay Settings", "Maintenance Standards", "Engineering Standards",
                        "CROW Outage", "Other", os.path.join("Tailboards", "Completed")):
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
        ttk.Label(self, textvariable=self.status_var, relief="sunken",
                  anchor="w", padding=(4, 1)).pack(fill="x", side="bottom")

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
        ct = ttk.Frame(nb); nb.add(ct, text="  CROW  ");             self._build_title_tab(ct)

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
                ("+ Block", "BLOCK", "#d35400"), ("+ Unblock", "UNBLOCK", "#16a085"),
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
        ttk.Label(tb, text="Drawing names entered in any job are added here automatically.  Ctrl+click a row to open its URL.",
                  foreground="grey").pack(side="left", padx=8)
        frame = ttk.Frame(parent); frame.pack(fill="both", expand=True, padx=4, pady=(0,4))
        cols = ("Drawing","Title","Revision","URL","Notes")
        self.drawings_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.drawings_tree.heading("Drawing",text="Drawing"); self.drawings_tree.heading("Title",text="Title")
        self.drawings_tree.heading("Revision",text="Revision")
        self.drawings_tree.heading("URL",text="Drawing URL"); self.drawings_tree.heading("Notes",text="Notes")
        self.drawings_tree.column("Drawing",width=140,stretch=False); self.drawings_tree.column("Title",width=160,stretch=False)
        self.drawings_tree.column("Revision",width=68,stretch=False)
        self.drawings_tree.column("URL",width=300); self.drawings_tree.column("Notes",width=160)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.drawings_tree.yview)
        self.drawings_tree.configure(yscrollcommand=vsb.set)
        self.drawings_tree.pack(side="left", fill="both", expand=True); vsb.pack(side="right", fill="y")
        self.drawings_tree.bind("<Double-1>", lambda _: self._edit_drawing())
        self.drawings_tree.bind("<Control-Button-1>", self._on_drawings_ctrl_click)

    # ── Drawing registry CRUD ─────────────────────────────────────

    def _search_drawings(self):
        dlg = DrawingSearchDialog(self, self.app_config, multi_select=True,
                                  proj_cache=self._drawing_cache)
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
            self.drawings_tree.insert("","end",iid=name,
                values=(name,info.get("title",""),info.get("rev",""),info.get("url",""),info.get("notes","")))

    def _add_drawing(self):
        dlg = DrawingEditDialog(self, base_url=self.app_config.get("base_drawing_url",""),
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
                                base_url=self.app_config.get("base_drawing_url",""),
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

    def _on_drawings_ctrl_click(self, event):
        row = self.drawings_tree.identify_row(event.y)
        if not row:
            return
        url = self.drawing_registry.get(row, {}).get("url", "").strip()
        if url:
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

    def _download_with_progress(self, title, targets, dest_dir, organize=False, extra_headers=None):
        """Shared download engine with thread-safe progress dialog.

        Uses a queue.Queue so the worker thread never touches tkinter directly —
        all widget updates happen on the main thread via after() polling.
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
        close_btn = ttk.Button(bf, text="Close", state="disabled", command=dlg.destroy)
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
                    archived = _archive_existing(sub_dir, name) if organize else 0
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

        sections = [
            ("Drawings", [
                ("base_drawing_url",    "Base Drawing URL:",    "Used to pre-fill URLs when adding drawings"),
                ("drawing_search_url",  "Drawing Search URL:",  "Base URL for the corporate drawing search server"),
            ]),
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
            ("Engineering Standards", [
                ("base_engineering_telecom_url",      "Base URL (Telecom):",      "Pre-fills Telecom URL when adding engineering standards"),
                ("base_engineering_transmission_url", "Base URL (Transmission):", "Pre-fills Transmission URL when adding engineering standards"),
            ]),
            ("Tailboard", [
                ("tailboard_url",  "Tailboard URL:",  "Reference URL only — place tailboard-template.pdf in the project root folder"),
                ("crew_email",     "Crew Email(s):",  "Default recipients when emailing a completed tailboard (comma-separated)"),
            ]),
        ]

        cfg_vars = {}
        for section_name, fields in sections:
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

        ttk.Label(f, text="These settings apply to all projects and are stored globally.",
                  foreground="grey", font=("",8)).pack(anchor="w", pady=(4,0))

        auth_lf = ttk.LabelFrame(f, text="Authentication / Request Headers", padding=8)
        auth_lf.pack(fill="x", pady=(0, 8))
        ttk.Label(auth_lf,
                  text="Headers sent with every download request. One per line as  Header-Name: value\n"
                       "To authenticate, open your browser's DevTools (F12) → Network tab, make a request\n"
                       "to the target site, then copy the full  Cookie:  and  Referer:  header values here.",
                  foreground="grey", font=("", 8), wraplength=480, justify="left").pack(anchor="w", pady=(0, 4))
        headers_txt = scrolledtext.ScrolledText(auth_lf, height=4, font=("Courier", 9), wrap="none")
        headers_txt.pack(fill="x")
        headers_txt.insert("1.0", self.app_config.get("request_headers", ""))

        eng_hdrs_lf = ttk.LabelFrame(f, text="Engineering Standards Headers (optional override)", padding=8)
        eng_hdrs_lf.pack(fill="x", pady=(0, 8))
        ttk.Label(eng_hdrs_lf,
                  text="Leave blank to use the master headers above. Fill in only if engineering\n"
                       "standards are served from a different server with different auth credentials.",
                  foreground="grey", font=("", 8), justify="left").pack(anchor="w", pady=(0, 4))
        eng_headers_txt = scrolledtext.ScrolledText(eng_hdrs_lf, height=3, font=("Courier", 9), wrap="none")
        eng_headers_txt.pack(fill="x")
        eng_headers_txt.insert("1.0", self.app_config.get("engineering_request_headers", ""))

        drw_search_lf = ttk.LabelFrame(f, text="Drawing Search", padding=8)
        drw_search_lf.pack(fill="x", pady=(0, 8))
        ttk.Label(drw_search_lf,
                  text="Fetches the live facility / drawing-type / drawing-subject lists from the "
                       "search server.\nAuthentication uses the master Cookie header set above.",
                  foreground="grey", font=("", 8), justify="left").pack(anchor="w", pady=(0, 4))

        def _do_fetch_options():
            url  = cfg_vars.get("drawing_search_url", tk.StringVar()).get().strip()
            headers = _parse_request_headers_raw(headers_txt.get("1.0", "end"))
            _show_fetch_options_dialog(dlg, url, headers)

        ttk.Button(drw_search_lf, text="🔄 Fetch Drawing Options",
                   command=_do_fetch_options).pack(anchor="w")

        bf = ttk.Frame(f); bf.pack(fill="x", pady=(10, 0))
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)

        def _save():
            self.app_config.update({k: v.get().strip() for k, v in cfg_vars.items()})
            self.app_config["request_headers"] = headers_txt.get("1.0", "end").strip()
            self.app_config["engineering_request_headers"] = eng_headers_txt.get("1.0", "end").strip()
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
        for iid in self.eng_tree.get_children(): self.eng_tree.delete(iid)
        for sid, info in sorted(self.engineering_standards_registry.items()):
            self.eng_tree.insert("", "end", iid=sid, values=(
                sid,
                info.get("title",         ""),
                info.get("revision",      ""),
                info.get("standard_type", ""),
                info.get("url",           ""),
                info.get("notes",         ""),
            ))

    def _add_engineering(self):
        dlg = EngineeringStandardDialog(
            self,
            base_url_telecom=self.app_config.get("base_engineering_telecom_url", ""),
            base_url_transmission=self.app_config.get("base_engineering_transmission_url", ""),
        )
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

    def _edit_engineering(self):
        sel = self.eng_tree.selection()
        if not sel: messagebox.showinfo("Select", "Please select a standard to edit."); return
        sid = sel[0]; info = self.engineering_standards_registry.get(sid, {})
        dlg = EngineeringStandardDialog(
            self,
            existing={"standard_id": sid, **info},
            base_url_telecom=self.app_config.get("base_engineering_telecom_url", ""),
            base_url_transmission=self.app_config.get("base_engineering_transmission_url", ""),
        )
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
                and sel and sel[0] not in ("__prep__", "__tailboard__")):
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
        disp = {"REMOVE":"REMOVE","ADD":"ADD","MOVE":"MOVE",
                "BLOCK":"BLOCK PROT.","UNBLOCK":"UNBLOCK PROT.","TESTING":"TESTING",
                "ISOLATION":"ISOLATION","CR_PROT":"CR PROTECTION",
                "DEVICE ADD":"INSTALL DEVICE","DEVICE REMOVE":"REMOVE DEVICE"}
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
        # All other rows: hide tailboard panel, show text preview
        self.impl_tb_frame.pack_forget()
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
            if sel and sel[0] not in ("__prep__", "__tailboard__"):
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
        """Return path to tailboard-template.pdf beside the script, or None."""
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
                eng  = f"  Eng: {info['engineer']}"    if info.get("engineer") else ""
                rev  = f"  Rev {info['revision']}"     if info.get("revision") else ""
                url  = f"\n    {info['url']}"          if info.get("url") else ""
                lines.append(f"  {dev_id}{rev}{eng}{url}")
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
        idx = int(row)
        if 0 <= idx < len(self.jobs):
            self.jobs[idx]["completed"] = not self.jobs[idx].get("completed", False)
            self._refresh_list()
            self.impl_tree.selection_set(str(idx))
            self._on_impl_select()

    # ── Job list ─────────────────────────────────────────────────

    def _refresh_list(self):
        for iid in self.tree.get_children(): self.tree.delete(iid)
        disp = {"REMOVE":"REMOVE","ADD":"ADD","MOVE":"MOVE","BLOCK":"BLOCK PROT.",
                "UNBLOCK":"UNBLOCK PROT.","TESTING":"TESTING","ISOLATION":"ISOLATION",
                "CR_PROT":"CR PROTECTION",
                "DEVICE ADD":"INSTALL DEVICE","DEVICE REMOVE":"REMOVE DEVICE"}
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
                        settings=self._get_settings(),
                        maintenance_standards=self.maintenance_standards_registry,
                        engineering_standards=self.engineering_standards_registry,
                        ctrl_desks=self._app_db.get_ctrl_desks(),
                        crows=self.title_page.get("crows", []))
        if dlg.result:
            self._collect_history(dlg.result)
            self.jobs.append(dlg.result); self._refresh_list(); self._refresh_drawings_list()
            idx = len(self.jobs)-1; self.tree.selection_set(str(idx)); self._on_select()

    def _edit_job(self):
        idx = self._selected_idx()
        if idx is None: messagebox.showinfo("Select a Job","Please select a job from the list."); return
        dlg = JobDialog(self, self.jobs[idx]["type"], existing=deepcopy(self.jobs[idx]),
                        registry=self.drawing_registry, history=self.history,
                        ep_history=self.ep_history, jobs=self.jobs, settings=self._get_settings(),
                        maintenance_standards=self.maintenance_standards_registry,
                        engineering_standards=self.engineering_standards_registry,
                        ctrl_desks=self._app_db.get_ctrl_desks(),
                        crows=self.title_page.get("crows", []))
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
        path = filedialog.askopenfilename(filetypes=[("Red-Line Plan","*.redline"),("Legacy wirePlan","*.wirePlan"),("JSON","*.json"),("All","*.*")])
        if not path: return
        try:
            with open(path,encoding="utf-8") as fh: data = json.load(fh)
            self.jobs = data.get("jobs",[]); self.project_var.set(data.get("project",""))
            self.drawing_registry = data.get("drawing_registry",{})
            self.relay_registry   = data.get("relay_settings",{})  # key kept as "relay_settings" for file compatibility
            self.maintenance_standards_registry = data.get("maintenance_standards", {})
            self.engineering_standards_registry = data.get("engineering_standards", {})
            self.history = data.get("history", {"device":[],"location":[],"pin":[],"panel":[],"wire":[]})
            # Older files carried a per-project drawing cache — fold it
            # into the global DB so nothing is lost, then ignore it.
            legacy_cache = data.get("drawing_search_cache", {})
            if legacy_cache:
                try: self._app_db.cache_merge_legacy(legacy_cache)
                except sqlite3.Error: pass
            self.title_page = data.get("title_page", {"notes": "", "crows": []})
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
            if self.mode_var.get() == "impl": self._refresh_file_tabs()
            proj = data.get("project","") or os.path.splitext(os.path.basename(path))[0]
            self.title(f"Red-Line-Routing — {proj}")
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
                        "CROW Outage", "Other", os.path.join("Tailboards", "Completed")):
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
                           "history":self.history,
                           "jobs":self.jobs},fh,indent=2)
            proj = self.project_var.get().strip() or os.path.splitext(os.path.basename(path))[0]
            self.title(f"Red-Line-Routing — {proj}")
            self._update_status()
            self._remember_all_standards()
        except Exception as exc: messagebox.showerror("Save Error",str(exc))

    # How often to look for stale cache entries, and how old an entry must
    # be before it is re-fetched. The age is configurable via the
    # drawing_cache_refresh_hours app setting.
    _CACHE_CHECK_INTERVAL_MS = 15 * 60 * 1000   # check every 15 minutes
    _CACHE_DEFAULT_MAX_AGE_H = 4.0              # refresh entries older than 4 h

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

        def _run():
            done = 0
            try:
                for fac, typ, subj, state in keys:
                    try:
                        params = SearchParams(facility=fac, drawing_type=typ,
                                              drawing_subject=subj, state=state)
                        results = client.search_all_pages(params)
                        cache.put(params, results)
                        done += 1
                    except Exception:
                        pass
            finally:
                self._cache_refresh_running = False
                if done:
                    self.after(0, lambda: self.status_var.set(
                        f"Drawing cache refreshed — {done} search(es) updated"))

        threading.Thread(target=_run, daemon=True).start()


# ──────────────────────────────────────────────────────────────────

def _open_file(path):
    try:
        if sys.platform=="win32": os.startfile(path)
        elif sys.platform=="darwin": subprocess.call(["open",path])
        else: subprocess.call(["xdg-open",path])
    except Exception: pass


def _reveal_file(path):
    """Open the containing folder with the file selected/highlighted."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception:
        _open_file(path)


if __name__ == "__main__":
    app = RedLineApp()
    app.mainloop()
