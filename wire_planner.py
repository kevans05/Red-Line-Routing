#!/usr/bin/env python3
"""
Red-Line-Routing
----------------
All-in-one electrical job planner: work orders, drawings, relay settings, CROWs.
Save/load plans as project folders with .wirePlan JSON and organised subfolders.
Export detailed report, table, CSV, or colour-coded HTML/PDF.
"""

# stdlib
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import json
import os
import subprocess
import sys
import webbrowser
from copy import deepcopy
from datetime import datetime
import threading
import urllib.request


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
    if job_type == "TESTING":
        return {"type": "TESTING", "description": "", "notes": ""}
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
    Filters values containing the typed text and opens the dropdown."""
    def _do_filter():
        typed = combo.get()
        all_vals = get_values_fn()
        if typed.strip():
            lo = typed.lower()
            filtered = [v for v in all_vals if lo in v.lower()]
        else:
            filtered = all_vals
        combo["values"] = filtered
        if filtered and typed.strip():
            try:
                combo.tk.call("ttk::combobox::Post", str(combo))
            except tk.TclError:
                pass
    def _on_key(event):
        if event.keysym in ("Return","Tab","Escape","Up","Down","Left","Right","Home","End"):
            return
        combo.after(1, _do_filter)
    combo.bind("<KeyRelease>", _on_key)


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
        ttk.Entry(mb_outer, textvariable=self.mb_notes_var, width=32,
                  ).grid(row=2, column=1, sticky="ew", pady=2)
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
                  "BLOCK":"#d35400","UNBLOCK":"#16a085","TESTING":"#6c3483"}

    def __init__(self, parent, job_type, existing=None, registry=None,
                 history=None, ep_history=None, jobs=None, settings=None):
        super().__init__(parent)
        self.title(f"{'Edit' if existing else 'Add'} — {job_type}")
        self.result = None
        self.job_type  = job_type
        self.registry  = registry   if registry   is not None else {}
        self.history   = history    if history    is not None else {}
        self.ep_history= ep_history if ep_history is not None else []
        self.jobs      = jobs       if jobs       is not None else []
        self.settings  = settings   if settings   is not None else {}
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

        self.geometry("660x600" if self.job_type in ("BLOCK","UNBLOCK","TESTING") else "960x640")

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

        elif self.job_type == "MOVE":
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

        elif self.job_type == "TESTING":
            self._section_label(f, row, "── TESTING / NOTE ──", color); row += 2
            ttk.Label(f, text="Notes:").grid(row=row, column=0, sticky="ne", padx=(0,6), pady=2)
            self.test_notes_var = tk.StringVar(value=ex.get("notes",""))
            notes_txt = tk.Text(f, width=58, height=6, wrap="word", font=("",9))
            notes_txt.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
            notes_txt.insert("1.0", ex.get("notes",""))
            self._test_notes_widget = notes_txt

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
        elif jt == "TESTING":
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
        elif self.job_type == "TESTING":
            job["notes"] = self._test_notes_widget.get("1.0","end").strip()
        self.result = job
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Drawing registry edit dialog
# ──────────────────────────────────────────────────────────────────

class DrawingEditDialog(tk.Toplevel):
    def __init__(self, parent, existing=None, base_url=""):
        super().__init__(parent)
        self.title("Edit Drawing" if existing else "Add Drawing")
        self.result = None
        self._old_name = existing.get("name") if existing else None
        self._base_url = base_url
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
        self.geometry("460x268")

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

def format_job(index, job):
    jtype = job["type"]
    labels = {"REMOVE":"REMOVE WIRE","ADD":"ADD WIRE","MOVE":"MOVE WIRE",
              "BLOCK":"BLOCK PROTECTION","UNBLOCK":"UNBLOCK PROTECTION","TESTING":"TESTING / NOTE"}
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
    elif jtype == "TESTING":
        if job.get("notes"):
            lines += ["","  NOTES", *[f"    {ln}" for ln in job["notes"].splitlines()]]
    lines.append("")
    return "\n".join(lines)

def generate_report(jobs, project="", drawing_registry=None, title_page=None):
    now = datetime.now().strftime("%Y-%m-%d  %H:%M")
    title = "WIRE WORK PLAN" + (f"  —  {project}" if project else "")
    counts = {}
    for j in jobs: counts[j["type"]] = counts.get(j["type"],0)+1
    summary = "  ".join(f"{v} {k}" for k,v in counts.items())
    parts = [_bar("*"),title.center(W),f"Generated: {now}".center(W),_bar("*"),"",
             f"  Total Jobs : {len(jobs)}",f"  Breakdown  : {summary}",""]
    if title_page:
        notes = title_page.get("notes", "").strip()
        crows = title_page.get("crows", [])
        if notes or crows:
            parts += [_bar("-"), "  TITLE PAGE".center(W), _bar("-"), ""]
            if notes:
                parts += ["  Notes:"] + [f"    {ln}" for ln in notes.splitlines()] + [""]
            if crows:
                parts += ["  CROWs:"]
                for c in crows:
                    parts.append(f"    {c.get('outage_number', '')}")
                    if c.get("url"): parts.append(f"      {c['url']}")
                parts.append("")
    if drawing_registry:
        parts += [_bar("-"),"  PROJECT DRAWINGS".center(W),_bar("-"),""]
        for name in sorted(drawing_registry.keys()):
            info = drawing_registry[name]
            parts.append(f"  {name}" + (f"  Rev: {info['rev']}" if info.get("rev") else ""))
            if info.get("url"):   parts.append(f"    URL:   {info['url']}")
            if info.get("notes"): parts.append(f"    Notes: {info['notes']}")
        parts.append("")
    return "\n".join(parts) + "\n".join(format_job(i,j) for i,j in enumerate(jobs))


# ──────────────────────────────────────────────────────────────────
# Table / CSV export helpers
# ──────────────────────────────────────────────────────────────────

def _ep_summary(ep):
    parts = []
    if ep.get("device"):   parts.append(ep["device"])
    if ep.get("pin"):      parts.append(f"Pin {ep['pin']}")
    if ep.get("location"): parts.append(ep["location"])
    if ep.get("panel"):    parts.append(f"Panel {ep['panel']}")
    if ep.get("drawing"):
        rev  = f" Rev{ep['drawing_rev']}"  if ep.get("drawing_rev")  else ""
        cell = f" [{ep['drawing_cell']}]"  if ep.get("drawing_cell") else ""
        parts.append(f"Dwg {ep['drawing']}{rev}{cell}")
    return "  |  ".join(parts)

def _prot_summary(prot):
    parts = []
    if prot.get("equipment"): parts.append(prot["equipment"])
    if prot.get("location"):  parts.append(prot["location"])
    if prot.get("panel"):     parts.append(f"Panel {prot['panel']}")
    if prot.get("notes"):     parts.append(prot["notes"])
    for d in _get_prot_drawings(prot):
        rev  = f" Rev{d['drawing_rev']}"  if d.get("drawing_rev")  else ""
        cell = f" [{d['drawing_cell']}]"  if d.get("drawing_cell") else ""
        parts.append(f"Dwg {d.get('drawing','')}{rev}{cell}")
    for p in prot.get("iso_points", []):
        equip = f" ({p['equipment']})" if p.get("equipment") else ""
        parts.append(f"{p.get('iso_type','ISO')} {p.get('reference','')}{equip}")
    if prot.get("mb_enabled"):
        remote = f" [{prot['mb_remote']}]"  if prot.get("mb_remote") else ""
        notes  = f" {prot['mb_notes']}"     if prot.get("mb_notes")  else ""
        parts.append(f"⚠ MB INPUT req.{remote}{notes}")
    return "  |  ".join(parts)

def generate_table(jobs, project="", drawing_registry=None):
    now = datetime.now().strftime("%Y-%m-%d  %H:%M")
    title = "WIRE WORK PLAN  —  TABLE FORMAT" + (f"  —  {project}" if project else "")
    CW = {"seq":4,"type":14,"desc":28,"start":36,"wire":18,"end":36}
    SEP = "  "
    def pad(t,w): return str(t)[:w].ljust(w)
    def row(*c): return SEP.join(pad(c[i],list(CW.values())[i]) for i in range(len(c)))
    div = "-"*(sum(CW.values())+len(SEP)*(len(CW)-1))
    hdr = row("#","TYPE","DESCRIPTION","START POINT / DEVICE","WIRE","END POINT / DEVICE")
    lines = ["="*len(div),title.center(len(div)),f"Generated: {now}".center(len(div)),"="*len(div),""]
    if drawing_registry:
        lines += ["PROJECT DRAWINGS","-"*40]
        for name in sorted(drawing_registry.keys()):
            info = drawing_registry[name]
            lines.append(f"  {name}" + (f"  Rev: {info['rev']}" if info.get("rev") else ""))
            if info.get("url"): lines.append(f"    URL: {info['url']}")
        lines += ["",""]
    lines += [hdr, div]
    type_labels = {"REMOVE":"REMOVE WIRE","ADD":"ADD WIRE","MOVE":"MOVE WIRE",
                   "BLOCK":"BLOCK PROT.","UNBLOCK":"UNBLOCK PROT.","TESTING":"TESTING"}
    seq = 1
    for job in jobs:
        jtype = job["type"]; desc = job.get("description",""); tl = type_labels.get(jtype,jtype)
        if jtype in ("REMOVE","ADD"):
            lines.append(row(seq,tl,desc,_ep_summary(job.get("start",{})),job.get("wire",""),_ep_summary(job.get("end",{})))); seq+=1
        elif jtype == "MOVE":
            lines.append(row(seq,"MOVE — REMOVE",desc,_ep_summary(job.get("start",{})),job.get("wire",""),_ep_summary(job.get("end",{})))); seq+=1
            lines.append(row(seq,"MOVE — ADD",desc,_ep_summary(job.get("add_start",{})),job.get("add_wire",""),_ep_summary(job.get("add_end",{})))); seq+=1
        elif jtype in ("BLOCK","UNBLOCK"):
            lines.append(row(seq,tl,desc,_prot_summary(job.get("protection",{})),"","")); seq+=1
        elif jtype == "TESTING":
            lines.append(row(seq,tl,desc,job.get("notes",""),"","")); seq+=1
        lines.append(div)
    return "\n".join(lines)

def generate_csv(jobs, project="", drawing_registry=None):
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Wire Work Plan", project,"","","",""])
    w.writerow([])
    w.writerow(["#","Type","Description","Start Point / Device","Wire","End Point / Device"])
    type_labels = {"REMOVE":"REMOVE WIRE","ADD":"ADD WIRE","MOVE":"MOVE WIRE",
                   "BLOCK":"BLOCK PROTECTION","UNBLOCK":"UNBLOCK PROTECTION","TESTING":"TESTING"}
    seq = 1
    for job in jobs:
        jtype = job["type"]; desc = job.get("description",""); tl = type_labels.get(jtype,jtype)
        if jtype in ("REMOVE","ADD"):
            w.writerow([seq,tl,desc,_ep_summary(job.get("start",{})),job.get("wire",""),_ep_summary(job.get("end",{}))]); seq+=1
        elif jtype == "MOVE":
            w.writerow([seq,"MOVE — REMOVE",desc,_ep_summary(job.get("start",{})),job.get("wire",""),_ep_summary(job.get("end",{}))]); seq+=1
            w.writerow([seq,"MOVE — ADD",desc,_ep_summary(job.get("add_start",{})),job.get("add_wire",""),_ep_summary(job.get("add_end",{}))]); seq+=1
        elif jtype in ("BLOCK","UNBLOCK"):
            w.writerow([seq,tl,desc,_prot_summary(job.get("protection",{})),"",""]); seq+=1
        elif jtype == "TESTING":
            w.writerow([seq,tl,desc,job.get("notes",""),"",""]); seq+=1
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────
# HTML (colour-coded, print to PDF in browser)
# ──────────────────────────────────────────────────────────────────

_ROW_STYLE = {
    "REMOVE":      ("background:#fde8e6","color:#922b21;font-weight:bold"),
    "ADD":         ("background:#e8f8ee","color:#1e8449;font-weight:bold"),
    "MOVE-REMOVE": ("background:#fef0e6","color:#a04000;font-weight:bold"),
    "MOVE-ADD":    ("background:#fefbe6","color:#7d6608;font-weight:bold"),
    "BLOCK":       ("background:#fef3e6","color:#a04000;font-weight:bold"),
    "UNBLOCK":     ("background:#e6f6f3","color:#0e6655;font-weight:bold"),
    "TESTING":     ("background:#f5eef8","color:#6c3483;font-weight:bold"),
}

_ROW_BORDER = {
    "REMOVE":      "#c0392b",
    "ADD":         "#27ae60",
    "MOVE-REMOVE": "#e67e22",
    "MOVE-ADD":    "#d4ac0d",
    "BLOCK":       "#ca6f1e",
    "UNBLOCK":     "#148f77",
    "TESTING":     "#7d3c98",
}

def _esc(t):
    return (str(t).replace("&","&amp;").replace("<","&lt;")
            .replace(">","&gt;").replace('"',"&quot;"))

def _ep_html(ep):
    parts = []
    if ep.get("device"):   parts.append(f"<b>{_esc(ep['device'])}</b>")
    if ep.get("pin"):      parts.append(f"Pin {_esc(ep['pin'])}")
    if ep.get("location"): parts.append(_esc(ep["location"]))
    if ep.get("panel"):    parts.append(f"Panel {_esc(ep['panel'])}")
    if ep.get("drawing"):
        rev  = f" Rev{_esc(ep['drawing_rev'])}" if ep.get("drawing_rev") else ""
        cell = f" [{_esc(ep['drawing_cell'])}]" if ep.get("drawing_cell") else ""
        url  = ep.get("drawing_url","")
        tag  = f'<a href="{_esc(url)}">' if url else ""
        etag = "</a>" if url else ""
        parts.append(f"{tag}{_esc(ep['drawing'])}{rev}{etag}{cell}")
    return "<br>".join(parts)

def _prot_html(prot):
    parts = []
    if prot.get("equipment"): parts.append(f"<b>{_esc(prot['equipment'])}</b>")
    if prot.get("location"):  parts.append(_esc(prot["location"]))
    if prot.get("panel"):     parts.append(f"Panel {_esc(prot['panel'])}")
    if prot.get("notes"):     parts.append(f"<i>{_esc(prot['notes'])}</i>")
    for d in _get_prot_drawings(prot):
        rev  = f" Rev{_esc(d['drawing_rev'])}" if d.get("drawing_rev") else ""
        cell = f" [{_esc(d['drawing_cell'])}]" if d.get("drawing_cell") else ""
        url  = d.get("drawing_url","")
        tag  = f'<a href="{_esc(url)}">' if url else ""
        etag = "</a>" if url else ""
        parts.append(f"Dwg: {tag}{_esc(d.get('drawing',''))}{rev}{etag}{cell}")
    for p in prot.get("iso_points", []):
        equip = f" <i>({_esc(p['equipment'])})</i>" if p.get("equipment") else ""
        notes = f" — {_esc(p['notes'])}" if p.get("notes") else ""
        parts.append(f"<span style='color:#555'>{_esc(p.get('iso_type','ISO'))} block "
                     f"{_esc(p.get('reference',''))}{equip}{notes}</span>")
    if prot.get("mb_enabled"):
        remote = f" &nbsp;<i>[{_esc(prot['mb_remote'])}]</i>" if prot.get("mb_remote") else ""
        notes  = f" &nbsp;{_esc(prot['mb_notes'])}"            if prot.get("mb_notes")  else ""
        parts.append(
            f'<span style="background:#fff3cd;color:#7d4e00;font-weight:bold;'
            f'padding:1px 5px;border-radius:3px">'
            f'&#9888; MB INPUT — also required: block/unblock{remote}{notes}</span>')
    return "<br>".join(parts)

def generate_html_table(jobs, project="", drawing_registry=None, title_page=None):
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = "Red-Line-Routing" + (f" — {project}" if project else "")

    tp_html = ""
    if title_page:
        tp_parts = []
        notes = title_page.get("notes", "").strip()
        crows = title_page.get("crows", [])
        if notes:
            tp_parts.append(
                f"<h2>Notes</h2>"
                f"<p style='white-space:pre-wrap;margin:4px 0 8px'>{_esc(notes)}</p>")
        if crows:
            crow_rows = "".join(
                "<tr><td>{}</td><td>{}</td></tr>".format(
                    _esc(c.get("outage_number", "")),
                    ('<a href="{u}">{u}</a>'.format(u=_esc(c["url"])) if c.get("url") else ""))
                for c in crows)
            tp_parts.append(
                "<h2>CROWs</h2>"
                "<table><thead><tr><th>Outage Number</th><th>URL</th></tr></thead>"
                f"<tbody>{crow_rows}</tbody></table>")
        if tp_parts:
            tp_html = "".join(tp_parts) + "<br>"

    drw_html = ""
    if drawing_registry:
        def _url_cell(info):
            u = info.get("url","")
            return f'<a href="{_esc(u)}">{_esc(u)}</a>' if u else ""
        rows = "".join(
            f"<tr><td>{_esc(n)}</td><td>{_esc(i.get('title',''))}</td>"
            f"<td>{_esc(i.get('rev',''))}</td>"
            f"<td>{_url_cell(i)}</td>"
            f"<td>{_esc(i.get('notes',''))}</td></tr>"
            for n,i in sorted(drawing_registry.items()))
        drw_html = (f"<h2>Project Drawings</h2><table>"
                    f"<thead><tr><th>Drawing</th><th>Title</th><th>Rev</th><th>URL</th><th>Notes</th></tr></thead>"
                    f"<tbody>{rows}</tbody></table><br>")

    reg = drawing_registry or {}

    def ep_html_r(ep):
        """Like _ep_html but enriches the drawing line with title and URL from the registry."""
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
            url  = ep.get("drawing_url","") or ri.get("url","")
            tag  = f'<a href="{_esc(url)}">' if url else ""
            etag = "</a>" if url else ""
            ttl  = ri.get("title","")
            ttl_html = (f' <span style="color:#666;font-style:italic">'
                        f'— {_esc(ttl)}</span>') if ttl else ""
            parts.append(f"{tag}{_esc(name)}{etag}{ttl_html}{rev}{cell}")
        return "<br>".join(parts)

    def prot_html_r(prot):
        """Like _prot_html but enriches drawing lines with title from the registry."""
        parts = []
        if prot.get("equipment"): parts.append(f"<b>{_esc(prot['equipment'])}</b>")
        if prot.get("location"):  parts.append(_esc(prot["location"]))
        if prot.get("panel"):     parts.append(f"Panel {_esc(prot['panel'])}")
        if prot.get("notes"):     parts.append(f"<i>{_esc(prot['notes'])}</i>")
        for d in _get_prot_drawings(prot):
            name = d.get("drawing","")
            ri   = reg.get(name, {})
            rev  = f" Rev{_esc(d['drawing_rev'])}" if d.get("drawing_rev") else ""
            cell = f" [{_esc(d['drawing_cell'])}]" if d.get("drawing_cell") else ""
            url  = d.get("drawing_url","") or ri.get("url","")
            tag  = f'<a href="{_esc(url)}">' if url else ""
            etag = "</a>" if url else ""
            ttl  = ri.get("title","")
            ttl_html = (f' <span style="color:#666;font-style:italic">'
                        f'— {_esc(ttl)}</span>') if ttl else ""
            parts.append(f"Dwg: {tag}{_esc(name)}{etag}{ttl_html}{rev}{cell}")
        for p in prot.get("iso_points", []):
            equip = f" <i>({_esc(p['equipment'])})</i>" if p.get("equipment") else ""
            notes = f" — {_esc(p['notes'])}" if p.get("notes") else ""
            parts.append(f"<span style='color:#555'>{_esc(p.get('iso_type','ISO'))} block "
                         f"{_esc(p.get('reference',''))}{equip}{notes}</span>")
        if prot.get("mb_enabled"):
            remote = f" &nbsp;<i>[{_esc(prot['mb_remote'])}]</i>" if prot.get("mb_remote") else ""
            notes  = f" &nbsp;{_esc(prot['mb_notes'])}"            if prot.get("mb_notes")  else ""
            parts.append(
                f'<span style="background:#fff3cd;color:#7d4e00;font-weight:bold;'
                f'padding:1px 5px;border-radius:3px">'
                f'&#9888; MB INPUT — also required: block/unblock{remote}{notes}</span>')
        return "<br>".join(parts)

    job_rows = ""
    seq = 1
    type_labels = {"REMOVE":"Remove Wire","ADD":"Add Wire","MOVE":"Move Wire",
                   "BLOCK":"Block Protection","UNBLOCK":"Unblock Protection"}
    for job in jobs:
        jtype = job["type"]; desc = _esc(job.get("description",""))

        def tr(key, label, s_html, wire, e_html):
            row_bg, type_style = _ROW_STYLE.get(key, ("",""))
            bc = _ROW_BORDER.get(key, "#aaa")
            nonlocal seq
            r = (f'<tr style="{row_bg}">'
                 f'<td class="chk" style="border-left:4px solid {bc}">'
                 f'<input type="checkbox"></td>'
                 f'<td style="text-align:center">{seq}</td>'
                 f'<td style="{type_style}">{_esc(label)}</td>'
                 f'<td>{desc}</td>'
                 f'<td>{s_html}</td>'
                 f'<td>{_esc(wire)}</td>'
                 f'<td>{e_html}</td></tr>')
            seq += 1
            return r

        if jtype in ("REMOVE","ADD"):
            job_rows += tr(jtype, type_labels[jtype],
                           ep_html_r(job.get("start",{})), job.get("wire",""),
                           ep_html_r(job.get("end",{})))
        elif jtype == "MOVE":
            job_rows += tr("MOVE-REMOVE","Move — Remove",
                           ep_html_r(job.get("start",{})), job.get("wire",""),
                           ep_html_r(job.get("end",{})))
            job_rows += tr("MOVE-ADD","Move — Add",
                           ep_html_r(job.get("add_start",{})), job.get("add_wire",""),
                           ep_html_r(job.get("add_end",{})))
        elif jtype in ("BLOCK","UNBLOCK"):
            job_rows += tr(jtype, type_labels[jtype],
                           prot_html_r(job.get("protection",{})), "", "")
        elif jtype == "TESTING":
            job_rows += tr("TESTING", type_labels.get("TESTING","Testing"),
                           _esc(job.get("notes","")), "", "")

    def _leg_span(k, lbl):
        bg = _ROW_STYLE[k][0]
        fg = _ROW_STYLE[k][1].split(";")[0].replace("color:", "")
        return f'<span class="leg" style="{bg};color:{fg}">{lbl}</span>'
    legend = "".join(
        _leg_span(k, lbl)
        for k,lbl in [("REMOVE","Remove Wire"),("ADD","Add Wire"),
                      ("MOVE-REMOVE","Move — Remove"),("MOVE-ADD","Move — Add"),
                      ("BLOCK","Block Protection"),("UNBLOCK","Unblock Protection"),
                      ("TESTING","Testing")])

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{_esc(title)}</title>
<style>
body{{font-family:Arial,sans-serif;font-size:9pt;margin:12mm 8mm;color:#222}}
h1{{font-size:13pt;margin-bottom:3px}}h2{{font-size:10pt;margin:14px 0 3px}}
p.meta{{color:#666;margin-top:0;font-size:8pt}}
table{{border-collapse:collapse;width:100%;margin-bottom:10px}}
th{{background:#2c3e50;color:#fff;padding:5px 7px;text-align:left;font-size:8pt}}
td{{padding:4px 7px;border:1px solid #ccc;vertical-align:top;font-size:8pt;line-height:1.4}}
tr{{break-inside:avoid;page-break-inside:avoid}}
a{{color:#1a5276}}
.chk{{text-align:center;padding:3px 4px;width:22px}}
input[type=checkbox]{{width:14px;height:14px;cursor:pointer;accent-color:#2c3e50}}
.legend{{display:flex;gap:8px;margin:6px 0 12px;flex-wrap:wrap;font-size:7.5pt}}
.leg{{padding:2px 7px;border-radius:3px;border:1px solid #ccc}}
.print-header{{display:none;font-size:7.5pt;color:#555;border-bottom:1px solid #ccc;padding:3px 0 3px;margin-bottom:6px}}
.print-footer{{display:none}}
@media print{{
  body{{margin-top:18mm;margin-bottom:14mm;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
  .print-header{{display:flex;position:fixed;top:0;left:0;right:0;background:#fff;
    padding:3px 8mm;justify-content:space-between;z-index:99}}
  .print-footer{{display:block;position:fixed;bottom:0;left:0;right:0;background:#fff;
    font-size:7pt;color:#999;padding:2px 8mm;border-top:1px solid #eee;text-align:right}}
  .print-footer::after{{content:"Page " counter(page)}}
  a{{color:#000;text-decoration:none}}
  input[type=checkbox]{{-webkit-appearance:none;appearance:none;border:1.5px solid #444;
    width:11px;height:11px;display:inline-block;vertical-align:middle}}
  tr.done td{{text-decoration:line-through;opacity:0.55}}
  @page{{size:A3 landscape;margin:14mm 8mm 12mm 8mm}}
}}
tr.done td{{text-decoration:line-through;opacity:0.6}}
</style></head><body>
<div class="print-header">
  <span><b>{_esc(title)}</b></span>
  <span>Generated: {now} &nbsp;|&nbsp; {seq-1} step(s)</span>
</div>
<div class="print-footer"></div>
<h1>{_esc(title)}</h1>
<p class="meta">Generated: {now} &nbsp;|&nbsp; {seq-1} step(s)</p>
<div class="legend">{legend}</div>
{tp_html}{drw_html}
<table><thead><tr>
<th class="chk" style="width:22px">✓</th><th style="width:28px">#</th><th style="width:110px">Type</th>
<th style="width:15%">Description</th><th style="width:24%">Start Point / Device</th>
<th style="width:95px">Wire</th><th style="width:24%">End Point / Device</th>
</tr></thead><tbody>{job_rows}</tbody></table>
<p style="font-size:7pt;color:#aaa">Ctrl+P → Save as PDF</p>
<script>
document.querySelectorAll('input[type=checkbox]').forEach(function(cb){{
  cb.addEventListener('change', function(){{
    var tr = this.closest('tr');
    if(this.checked){{
      tr.classList.add('done');
    }} else {{
      tr.classList.remove('done');
    }}
  }});
}});
</script>
</body></html>"""


# ──────────────────────────────────────────────────────────────────
# CROW dialog (outage record: outage_number + URL)
# ──────────────────────────────────────────────────────────────────

class CrowDialog(tk.Toplevel):
    """Add/edit a CROW outage record."""
    def __init__(self, parent, existing=None, base_url=""):
        super().__init__(parent)
        self.title("Edit CROW" if existing else "Add CROW")
        self.result = None
        self._base_url = base_url
        self.resizable(False, False)
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
        f.columnconfigure(1, weight=1)
        br = ttk.Frame(self)
        br.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(br, text="Cancel", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(br, text="Save",   command=self._save).pack(side="right", padx=2)
        self.geometry("480x140")

    def _save(self):
        num = self.num_var.get().strip()
        if not num:
            messagebox.showwarning("Required", "Outage number is required.", parent=self)
            return
        self.result = {"outage_number": num, "url": self.url_var.get().strip()}
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

        bf = ttk.Frame(f); bf.grid(row=row_wo + 2, column=0, columnspan=2, sticky="e")
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bf, text="Save",   command=self._save).pack(side="right")

    def _save(self):
        if not self.vars["device_id"].get().strip():
            messagebox.showwarning("Required", "Device ID is required.", parent=self); return
        self.result = {k: v.get().strip() for k, v in self.vars.items()}
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Startup / wizard dialogs  —  shared UI helpers
# ──────────────────────────────────────────────────────────────────

def _center_window(win, w=None, h=None):
    """Center win on screen.  If w/h are omitted the window auto-sizes to content."""
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

class SoftwareSetupDialog(tk.Toplevel):
    """First-time global setup: collect base URLs."""
    def __init__(self, parent, app_config):
        super().__init__(parent)
        self.title("Red-Line-Routing — First Time Setup")
        self.resizable(False, False)
        self.result = None
        self._cfg_vars = {}
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
            ("Drawings",       [("base_drawing_url",   "Base Drawing URL"),
                                 ("drawing_search_url", "Drawing Search URL (future)")]),
            ("Aspen",          [("aspen_url",           "Aspen URL (future)")]),
            ("CROWs",          [("base_crow_url",       "Base CROW URL")]),
            ("Relay Settings", [("base_relay_url",      "Base Relay URL")]),
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

        sep = tk.Frame(self, bg="#d5d8dc", height=1); sep.pack(fill="x", side="bottom")
        bf = tk.Frame(self, bg="#eaecee"); bf.pack(fill="x", side="bottom")
        tk.Label(bf, text="You can skip this and fill in URLs later.",
                 bg="#eaecee", fg="#aab7b8", font=("", 8)).pack(side="left", padx=12, pady=8)
        ttk.Button(bf, text="Skip for Now",    command=self._skip).pack(side="right", padx=(6, 12), pady=8)
        ttk.Button(bf, text="Save & Continue", command=self._save).pack(side="right", pady=8)

    def _skip(self):
        self.result = {}; self.destroy()

    def _save(self):
        self.result = {k: v.get().strip() for k, v in self._cfg_vars.items()}
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
                     "Browse for a .wirePlan file",
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

    _STEPS = ["Project Info", "Drawings", "Relays & Devices", "CROWs", "Summary"]

    _SUGGESTIONS = [
        ("Project Team",     "Add team members (engineers, technicians, supervisors) with roles and contacts."),
        ("Outage Window",    "Record the planned outage date, start time, and expected duration."),
        ("Safety Checklist", "Pre-work safety items: PPE requirements, isolation verification, grounding."),
        ("Job Templates",    "Save common job sequences as reusable templates for standard terminal work."),
        ("Protection Review","List protection devices that need before/after verification at re-energisation."),
    ]

    # Step accent colours: (sidebar-active, sidebar-done, content-strip)
    _STEP_COLORS = [
        ("#154360", "#1a5276", "#2980b9"),   # Project Info  — blue
        ("#0b3d2e", "#1b6b46", "#27ae60"),   # Drawings      — green
        ("#4a1f6a", "#6c3483", "#8e44ad"),   # Relays        — purple
        ("#6e2706", "#943126", "#e74c3c"),   # CROWs         — red
        ("#17202a", "#273746", "#566573"),   # Summary       — slate
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
        self.wiz_drawings = {}
        self.wiz_relays   = {}
        self.wiz_crows    = []
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

        # Footer nav — pack BEFORE content so pack(expand=True) doesn't swallow it
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

        self._build_step_info(    _inner(self._frames[0]))
        self._build_step_drawings(_inner(self._frames[1]))
        self._build_step_relays(  _inner(self._frames[2]))
        self._build_step_crows(   _inner(self._frames[3]))
        self._build_step_summary( _inner(self._frames[4]))

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
        tk.Button(tb, text="🔍 Search Drawings  (coming soon)", relief="flat",
                  fg="#95a5a6", bg="#ecf0f1", state="disabled").pack(side="left", padx=(10,2))

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
        dlg = DrawingEditDialog(self, base_url=self.app_config.get("base_drawing_url",""))
        if dlg.result:
            n = dlg.result["name"]
            self.wiz_drawings[n] = {k: dlg.result[k] for k in ("title","rev","url","notes")}
            self._wiz_refresh_drawings()

    def _wiz_edit_drawing(self):
        sel = self.wiz_drw_tree.selection()
        if not sel: return
        n = sel[0]; info = self.wiz_drawings.get(n, {})
        dlg = DrawingEditDialog(self, existing={"name":n,**info},
                                base_url=self.app_config.get("base_drawing_url",""))
        if dlg.result:
            old = dlg.result.get("old_name"); new = dlg.result["name"]
            if old and old != new: self.wiz_drawings.pop(old, None)
            self.wiz_drawings[new] = {k: dlg.result[k] for k in ("title","rev","url","notes")}
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
            f"Project:        {proj}",
            f"Site:           {site}  (ID: {sid})",
            f"Save folder:    {loc}/{proj}",
            f"",
            f"Drawings:       {len(self.wiz_drawings)} added",
            f"Relay records:  {len(self.wiz_relays)} added",
            f"CROWs:          {len(self.wiz_crows)} added",
            f"",
            f"Click 'Create Project' to build the folder structure and save.",
        ]
        self._summary_text.configure(state="normal")
        self._summary_text.delete("1.0","end")
        self._summary_text.insert("1.0","\n".join(lines))
        self._summary_text.configure(state="disabled")

    def _finish(self):
        if not self._validate() and self._step != 4:
            self._show_step(0); return
        proj = self.wiz_vars.get("project_name", tk.StringVar()).get().strip()
        loc  = self.wiz_vars.get("save_location",tk.StringVar()).get().strip()
        if not proj or not loc:
            messagebox.showwarning("Required",
                "Project Name and Save Location are required.", parent=self)
            self._show_step(0); return
        self.result = {
            "project_name":  proj,
            "site_name":     self.wiz_vars.get("site_name",    tk.StringVar()).get().strip(),
            "site_id":       self.wiz_vars.get("site_id",      tk.StringVar()).get().strip(),
            "save_location": loc,
            "notes":         self.wiz_notes_widget.get("1.0","end").strip() if self.wiz_notes_widget else "",
            "drawings":      dict(self.wiz_drawings),
            "relays":        dict(self.wiz_relays),
            "crows":         list(self.wiz_crows),
        }
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Main application
# ──────────────────────────────────────────────────────────────────

class WirePlannerApp(tk.Tk):
    TYPE_FG = {"REMOVE":"#c0392b","ADD":"#1a7a3c","MOVE":"#1a5a99",
               "BLOCK":"#d35400","UNBLOCK":"#16a085","TESTING":"#6c3483"}

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
        self.app_config = self._load_app_config()   # global prefs (~/.redlinerouting.json)
        self._build_menu()
        self._build_ui()
        self.after_idle(self._startup_flow)

    # ── Startup flow ─────────────────────────────────────────────

    def _startup_flow(self):
        """Run once after the UI is ready: first-time setup → landing dialog."""
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
        self.drawing_registry = result.get("drawings", {})
        self.relay_registry   = result.get("relays",   {})
        self.title_page = {
            "notes": result.get("notes", ""),
            "crows": result.get("crows", []),
        }

        proj = result["project_name"]
        safe = "".join(c if c not in r'<>:"/\|?*' else "_" for c in proj) if proj else "RedLine_Plan"
        folder = os.path.join(result["save_location"], safe)
        try:
            os.makedirs(folder, exist_ok=True)
            for sub in ("Drawings", "Relay Settings", "CROW Outage", "Other"):
                os.makedirs(os.path.join(folder, sub), exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Error", f"Could not create project folder:\n{exc}"); return

        self.project_folder = folder
        path = os.path.join(folder, safe + ".wirePlan")
        self.current_file = path

        self.title_notes.delete("1.0", "end")
        self.title_notes.insert("1.0", result.get("notes", ""))
        self._refresh_list()
        self._refresh_drawings_list()
        self._refresh_relay_list()
        self._refresh_crows()
        self._write(path)
        messagebox.showinfo("Project Created",
            f"'{proj}' created at:\n{folder}\n\nYou're ready to start adding jobs.")

    # ── History helpers ───────────────────────────────────────────

    def _add_to_history(self, key, value):
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

    # ── Menu ──────────────────────────────────────────────────────

    def _build_menu(self):
        mb = tk.Menu(self)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label="New",                    command=self._new_plan,     accelerator="Ctrl+N")
        fm.add_command(label="Open…",                  command=self._open,         accelerator="Ctrl+O")
        fm.add_command(label="Save",                   command=self._save,         accelerator="Ctrl+S")
        fm.add_command(label="Save As…",               command=self._save_as)
        fm.add_separator()
        fm.add_command(label="Export Report (detail)…",command=self._export_report,accelerator="Ctrl+E")
        fm.add_command(label="Export Table (text)…",   command=self._export_table)
        fm.add_command(label="Export CSV (Excel)…",    command=self._export_csv)
        fm.add_command(label="Export HTML (colour PDF)…",command=self._export_html)
        fm.add_separator()
        fm.add_command(label="Software Settings…",     command=self._open_software_settings)
        fm.add_separator()
        fm.add_command(label="Quit",command=self.quit, accelerator="Ctrl+Q")
        mb.add_cascade(label="File",menu=fm)
        self.config(menu=mb)
        self.bind("<Control-n>", lambda _: self._new_plan())
        self.bind("<Control-o>", lambda _: self._open())
        self.bind("<Control-s>", lambda _: self._save())
        self.bind("<Control-e>", lambda _: self._export_report())
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

        # Right: export buttons
        rf = tk.Frame(hdr, bg="#1c2833"); rf.pack(side="right", padx=6)
        for lbl, cmd in [("HTML / PDF", self._export_html), ("CSV", self._export_csv),
                          ("Table", self._export_table), ("Report", self._export_report),
                          ("Preview", self._preview_report)]:
            tk.Button(rf, text=lbl, bg="#2e4053", fg="#bdc3c7", relief="flat",
                      padx=7, bd=0, cursor="hand2", font=("", 8),
                      activebackground="#3d5166", activeforeground="white",
                      command=cmd).pack(side="left", padx=2, ipady=4, pady=6)

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
        ct = ttk.Frame(nb); nb.add(ct, text="  CROW  ");             self._build_title_tab(ct)

        # ── Implementation mode frame (hidden initially) ────────────
        self.impl_frame = ttk.Frame(self)
        self._build_impl_view(self.impl_frame)

    def _build_work_tab(self, parent):
        # Job-type buttons
        tb1 = ttk.Frame(parent, padding=(4, 4, 4, 2)); tb1.pack(fill="x")
        for label, jtype, color in [
                ("+ Remove", "REMOVE", "#c0392b"), ("+ Add", "ADD", "#27ae60"),
                ("+ Block", "BLOCK", "#d35400"), ("+ Unblock", "UNBLOCK", "#16a085"),
                ("+ Testing", "TESTING", "#6c3483")]:
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
        lf = ttk.LabelFrame(pw, text="Work Order", padding=4); pw.add(lf, weight=1)
        cols = ("Done","Seq","Type","Description")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("Done",text="✓"); self.tree.heading("Seq",text="#")
        self.tree.heading("Type",text="Type"); self.tree.heading("Description",text="Description")
        self.tree.column("Done",width=30,stretch=False,anchor="center")
        self.tree.column("Seq",width=35,stretch=False); self.tree.column("Type",width=110,stretch=False)
        self.tree.column("Description",width=230)
        for t,fg in self.TYPE_FG.items(): self.tree.tag_configure(t, foreground=fg)
        self.tree.tag_configure("COMPLETED", foreground="#aaaaaa")
        vsb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True); vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _: self._edit_job())
        self.tree.bind("<Button-1>", self._on_tree_click)
        pf = ttk.LabelFrame(pw, text="Job Preview", padding=4); pw.add(pf, weight=2)
        self.preview = scrolledtext.ScrolledText(pf, font=("Courier",9), state="disabled", wrap="none")
        self.preview.pack(fill="both", expand=True)

    def _build_drawings_tab(self, parent):
        tb = ttk.Frame(parent, padding=(4,4)); tb.pack(fill="x")
        ttk.Button(tb, text="+ Add Drawing", command=self._add_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",          command=self._edit_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Delete",        command=self._delete_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Scan Jobs →",   command=self._scan_and_refresh).pack(side="left", padx=(10,2))
        ttk.Button(tb, text="Drawing Index",   command=self._show_drawing_index).pack(side="left", padx=2)
        ttk.Button(tb, text="⬇ Download All",  command=self._download_drawings).pack(side="left", padx=(10,2))
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

    def _refresh_drawings_list(self):
        for iid in self.drawings_tree.get_children(): self.drawings_tree.delete(iid)
        for name in sorted(self.drawing_registry.keys()):
            info = self.drawing_registry[name]
            self.drawings_tree.insert("","end",iid=name,
                values=(name,info.get("title",""),info.get("rev",""),info.get("url",""),info.get("notes","")))

    def _add_drawing(self):
        dlg = DrawingEditDialog(self, base_url=self.app_config.get("base_drawing_url",""))
        if dlg.result:
            name = dlg.result["name"]
            self.drawing_registry[name] = {"title":dlg.result["title"],"rev":dlg.result["rev"],"url":dlg.result["url"],"notes":dlg.result["notes"]}
            self._refresh_drawings_list()

    def _edit_drawing(self):
        sel = self.drawings_tree.selection()
        if not sel: messagebox.showinfo("Select","Please select a drawing to edit."); return
        name = sel[0]; info = self.drawing_registry.get(name,{})
        dlg = DrawingEditDialog(self, existing={"name":name,**info},
                                base_url=self.app_config.get("base_drawing_url",""))
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
        drawings_dir = os.path.join(self.project_folder, "Drawings")
        os.makedirs(drawings_dir, exist_ok=True)

        targets = [(name, info["url"]) for name, info in self.drawing_registry.items()
                   if info.get("url","").strip()]
        if not targets:
            messagebox.showinfo("No URLs", "No drawing URLs are set in the registry."); return

        # Progress dialog
        dlg = tk.Toplevel(self)
        dlg.title("Downloading Drawings")
        dlg.geometry("420x200"); dlg.resizable(False,False)
        dlg.grab_set()
        ttk.Label(dlg, text="Downloading drawings…", font=("",10,"bold")).pack(pady=(14,4))
        prog_var = tk.StringVar(value="Starting…")
        ttk.Label(dlg, textvariable=prog_var, wraplength=380).pack(pady=4)
        import tkinter.ttk as _ttk
        bar = _ttk.Progressbar(dlg, length=360, maximum=len(targets))
        bar.pack(pady=8)
        results_var = tk.StringVar(value="")
        ttk.Label(dlg, textvariable=results_var, foreground="grey", font=("",8)).pack()

        def _run():
            ok, fail = 0, []
            for i, (name, url) in enumerate(targets):
                prog_var.set(f"Downloading: {name}")
                bar["value"] = i
                # Determine file extension from URL, default to .pdf
                ext = os.path.splitext(url.split("?")[0])[-1].lower()
                if ext not in (".pdf",".png",".jpg",".jpeg",".tif",".tiff",".svg",".dwg",".dxf"):
                    ext = ".pdf"
                dest = os.path.join(drawings_dir, name + ext)
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "RedLineRouting/1.0"})
                    with urllib.request.urlopen(req, timeout=30) as resp, \
                         open(dest, "wb") as out:
                        out.write(resp.read())
                    ok += 1
                except Exception as e:
                    fail.append(f"{name}: {e}")
            bar["value"] = len(targets)
            prog_var.set("Done.")
            msg = f"{ok} downloaded"
            if fail:
                msg += f", {len(fail)} failed:\n" + "\n".join(fail[:5])
                if len(fail) > 5: msg += f"\n…and {len(fail)-5} more"
            results_var.set(msg)
            ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=6)

        threading.Thread(target=_run, daemon=True).start()

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
            self.relay_registry[dev_id] = {k: v for k, v in dlg.result.items() if k != "device_id"}
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
            self.relay_registry[new_id] = {k: v for k, v in dlg.result.items() if k != "device_id"}
            self._refresh_relay_list()

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
        dest_dir = os.path.join(self.project_folder, "Relay Settings")
        os.makedirs(dest_dir, exist_ok=True)
        targets = [(dev_id, info["url"]) for dev_id, info in self.relay_registry.items()
                   if info.get("url", "").strip()]
        if not targets:
            messagebox.showinfo("No URLs", "No relay setting URLs are set."); return

        dlg = tk.Toplevel(self)
        dlg.title("Downloading Relay Settings"); dlg.geometry("420x200"); dlg.resizable(False, False)
        dlg.grab_set()
        ttk.Label(dlg, text="Downloading relay settings…", font=("", 10, "bold")).pack(pady=(14, 4))
        prog_var = tk.StringVar(value="Starting…")
        ttk.Label(dlg, textvariable=prog_var, wraplength=380).pack(pady=4)
        bar = ttk.Progressbar(dlg, length=360, maximum=len(targets))
        bar.pack(pady=8)
        res_var = tk.StringVar()
        ttk.Label(dlg, textvariable=res_var, foreground="grey", font=("", 8)).pack()

        def _run():
            ok, fail = 0, []
            for i, (dev_id, url) in enumerate(targets):
                prog_var.set(f"Downloading: {dev_id}")
                bar["value"] = i
                ext = os.path.splitext(url.split("?")[0])[-1].lower()
                if ext not in (".pdf",".png",".jpg",".jpeg",".tif",".tiff",".svg",".dwg"):
                    ext = ".pdf"
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "RedLineRouting/1.0"})
                    with urllib.request.urlopen(req, timeout=30) as resp, \
                         open(os.path.join(dest_dir, dev_id + ext), "wb") as out:
                        out.write(resp.read())
                    ok += 1
                except Exception as e:
                    fail.append(f"{dev_id}: {e}")
            bar["value"] = len(targets)
            prog_var.set("Done.")
            msg = f"{ok} downloaded"
            if fail: msg += f", {len(fail)} failed:\n" + "\n".join(fail[:5])
            res_var.set(msg)
            ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=6)

        threading.Thread(target=_run, daemon=True).start()

    # ── Software / Global Settings ────────────────────────────────

    _APP_CONFIG_PATH = os.path.expanduser("~/.redlinerouting.json")

    def _load_app_config(self):
        try:
            with open(self._APP_CONFIG_PATH, encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_app_config(self):
        try:
            with open(self._APP_CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(self.app_config, fh, indent=2)
        except Exception as exc:
            messagebox.showerror("Settings Error", f"Could not save software settings:\n{exc}")

    def _open_software_settings(self):
        dlg = tk.Toplevel(self)
        dlg.title("Software Settings")
        dlg.resizable(False, False)
        dlg.grab_set()

        f = ttk.Frame(dlg, padding=14)
        f.pack(fill="both", expand=True)

        sections = [
            ("Drawings", [
                ("base_drawing_url",  "Base Drawing URL:",  "Used to pre-fill URLs when adding drawings"),
                ("drawing_search_url","Drawing Search URL:","Base URL for drawing search (future use)"),
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

        bf = ttk.Frame(f); bf.pack(fill="x", pady=(10,0))
        ttk.Button(bf, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)

        def _save():
            self.app_config = {k: v.get().strip() for k, v in cfg_vars.items()}
            self._save_app_config()
            dlg.destroy()

        ttk.Button(bf, text="Save", command=_save).pack(side="right")

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
        ccols = ("Outage Number", "URL")
        self.crow_tree = ttk.Treeview(crow_fr, columns=ccols, show="headings", height=6)
        self.crow_tree.heading("Outage Number", text="Outage Number")
        self.crow_tree.heading("URL",           text="URL")
        self.crow_tree.column("Outage Number", width=160, stretch=False)
        self.crow_tree.column("URL",           width=450)
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
            self.crow_tree.insert("", "end", values=(
                crow.get("outage_number", ""), crow.get("url", "")))

    def _add_crow(self):
        dlg = CrowDialog(self, base_url=self.app_config.get("base_crow_url",""))
        if dlg.result:
            self.title_page.setdefault("crows", []).append(dlg.result)
            self._refresh_crows()

    def _edit_crow(self):
        sel = self.crow_tree.selection()
        if not sel:
            return
        idx = self.crow_tree.index(sel[0])
        dlg = CrowDialog(self, existing=self.title_page.get("crows", [])[idx])
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

    # ── Mode switching ────────────────────────────────────────────

    def _update_mode_buttons(self, active):
        for val, (btn, act_bg) in self._mode_btns.items():
            if val == active:
                btn.configure(bg=act_bg, fg="white")
            else:
                btn.configure(bg="#1c2833", fg="#7f8c8d")

    def _set_mode(self, mode):
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

    def _build_impl_view(self, parent):
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
        self.impl_tree.tag_configure("COMPLETED", foreground="#aaaaaa")
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
        self.impl_preview = scrolledtext.ScrolledText(
            details_f, font=("Courier", 9), state="disabled", wrap="none")
        self.impl_preview.pack(fill="both", expand=True)

        viewer_f = ttk.LabelFrame(pw_right, text="Reference Files", padding=4)
        pw_right.add(viewer_f, weight=3)

        vf_top = ttk.Frame(viewer_f); vf_top.pack(fill="x", pady=(0, 4))
        ttk.Button(vf_top, text="⟳ Refresh", command=self._refresh_file_tabs).pack(side="left")
        ttk.Label(vf_top, text="Downloaded files from project folder  —  double-click to open",
                  foreground="grey", font=("", 8)).pack(side="left", padx=8)

        self.file_nb = ttk.Notebook(viewer_f)
        self.file_nb.pack(fill="both", expand=True)

        drw_tab = ttk.Frame(self.file_nb); self.file_nb.add(drw_tab,   text="  Drawings  ")
        rly_tab = ttk.Frame(self.file_nb); self.file_nb.add(rly_tab,   text="  Relay Settings  ")
        self._build_file_listbox(drw_tab, "impl_drw_lb", "Drawings")
        self._build_file_listbox(rly_tab, "impl_rly_lb", "Relay Settings")

    def _build_file_listbox(self, parent, attr, subfolder):
        lb = tk.Listbox(parent, selectmode="browse", font=("Courier", 9),
                        activestyle="none", relief="flat", borderwidth=0)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=vsb.set)
        lb.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        lb.bind("<Double-1>", lambda e, l=lb, s=subfolder: self._open_impl_file(l, s))
        setattr(self, attr, lb)

    def _refresh_file_tabs(self):
        for attr, subfolder in [("impl_drw_lb", "Drawings"), ("impl_rly_lb", "Relay Settings")]:
            lb = getattr(self, attr)
            lb.delete(0, "end")
            if not self.project_folder:
                lb.insert("end", "(save project first to see downloaded files)"); continue
            folder = os.path.join(self.project_folder, subfolder)
            if os.path.isdir(folder):
                files = sorted(f for f in os.listdir(folder) if not f.startswith("."))
                if files:
                    for fn in files: lb.insert("end", fn)
                else:
                    lb.insert("end", f"(no files in {subfolder}/ yet — use ⬇ Download All)")
            else:
                lb.insert("end", f"({subfolder}/ folder not found)")

    def _open_impl_file(self, lb, subfolder):
        sel = lb.curselection()
        if not sel: return
        fname = lb.get(sel[0])
        if fname.startswith("("): return
        path = os.path.join(self.project_folder, subfolder, fname)
        if os.path.exists(path): _open_file(path)

    def _refresh_impl_list(self):
        for iid in self.impl_tree.get_children(): self.impl_tree.delete(iid)
        disp = {"REMOVE":"REMOVE","ADD":"ADD","MOVE":"MOVE",
                "BLOCK":"BLOCK PROT.","UNBLOCK":"UNBLOCK PROT.","TESTING":"TESTING"}
        for i, job in enumerate(self.jobs):
            done = job.get("completed", False)
            tags = (job["type"], "COMPLETED") if done else (job["type"],)
            self.impl_tree.insert("", "end", iid=str(i),
                values=("☑" if done else "☐", i + 1,
                        disp.get(job["type"], job["type"]),
                        job.get("description", "")),
                tags=tags)

    def _on_impl_select(self, _=None):
        sel = self.impl_tree.selection()
        if not sel: return
        idx = int(sel[0])
        if 0 <= idx < len(self.jobs):
            text = format_job(idx, self.jobs[idx])
            self.impl_preview.configure(state="normal")
            self.impl_preview.delete("1.0", "end")
            self.impl_preview.insert("1.0", text)
            self.impl_preview.configure(state="disabled")

    def _on_impl_tree_click(self, event):
        if self.impl_tree.identify_region(event.x, event.y) != "cell": return
        if self.impl_tree.identify_column(event.x) != "#1": return
        row = self.impl_tree.identify_row(event.y)
        if not row: return
        idx = int(row)
        if 0 <= idx < len(self.jobs):
            self.jobs[idx]["completed"] = not self.jobs[idx].get("completed", False)
            self._refresh_list()
            self.impl_tree.selection_set(str(idx))
            self._on_impl_select()

    # ── Job list ─────────────────────────────────────────────────

    def _refresh_list(self):
        for iid in self.tree.get_children(): self.tree.delete(iid)
        disp = {"REMOVE":"REMOVE","ADD":"ADD","MOVE":"MOVE","BLOCK":"BLOCK PROT.","UNBLOCK":"UNBLOCK PROT.","TESTING":"TESTING"}
        for i,job in enumerate(self.jobs):
            done = job.get("completed", False)
            tags = (job["type"], "COMPLETED") if done else (job["type"],)
            self.tree.insert("","end",iid=str(i),
                values=("☑" if done else "☐", i+1,
                        disp.get(job["type"],job["type"]), job.get("description","")),
                tags=tags)
        self._update_status()
        self._refresh_impl_list()

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
                        settings=self._get_settings())
        if dlg.result:
            self._collect_history(dlg.result)
            self.jobs.append(dlg.result); self._refresh_list(); self._refresh_drawings_list()
            idx = len(self.jobs)-1; self.tree.selection_set(str(idx)); self._on_select()

    def _edit_job(self):
        idx = self._selected_idx()
        if idx is None: messagebox.showinfo("Select a Job","Please select a job from the list."); return
        dlg = JobDialog(self, self.jobs[idx]["type"], existing=deepcopy(self.jobs[idx]),
                        registry=self.drawing_registry, history=self.history,
                        ep_history=self.ep_history, jobs=self.jobs, settings=self._get_settings())
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
            self._refresh_list()
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

    # ── Reports ───────────────────────────────────────────────────

    def _preview_report(self):
        if not self.jobs: messagebox.showinfo("No Jobs","Add at least one job before previewing."); return
        tp = dict(self.title_page); tp["notes"] = self.title_notes.get("1.0","end").strip()
        report = generate_report(self.jobs,self.project_var.get().strip(),self.drawing_registry,title_page=tp)
        win = tk.Toplevel(self); win.title("Report Preview"); win.geometry("740x720")
        txt = scrolledtext.ScrolledText(win,font=("Courier",9),wrap="none")
        txt.pack(fill="both",expand=True,padx=6,pady=6); txt.insert("1.0",report); txt.configure(state="disabled")
        bf = ttk.Frame(win); bf.pack(pady=(0,8))
        ttk.Button(bf,text="Export Report…",command=self._export_report).pack(side="left",padx=4)
        ttk.Button(bf,text="Close",command=win.destroy).pack(side="left",padx=4)

    def _export_report(self):
        tp = dict(self.title_page); tp["notes"] = self.title_notes.get("1.0","end").strip()
        self._export_text(generate_report(self.jobs,self.project_var.get().strip(),self.drawing_registry,title_page=tp),
                          f"wire_report_{datetime.now().strftime('%Y%m%d')}.txt")

    def _export_table(self):
        self._export_text(generate_table(self.jobs,self.project_var.get().strip(),self.drawing_registry),
                          f"wire_table_{datetime.now().strftime('%Y%m%d')}.txt")

    def _export_text(self, content, default_name):
        if not self.jobs: messagebox.showinfo("No Jobs","Add at least one job first."); return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                   filetypes=[("Text files","*.txt"),("All files","*.*")],initialfile=default_name)
        if not path: return
        with open(path,"w",encoding="utf-8") as fh: fh.write(content)
        self.status_var.set(f"Exported → {path}")
        if messagebox.askyesno("Exported",f"Saved to:\n{path}\n\nOpen the file now?"): _open_file(path)

    def _export_csv(self):
        if not self.jobs: messagebox.showinfo("No Jobs","Add at least one job first."); return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                   filetypes=[("CSV files","*.csv"),("All files","*.*")],
                   initialfile=f"wire_table_{datetime.now().strftime('%Y%m%d')}.csv")
        if not path: return
        with open(path,"w",encoding="utf-8",newline="") as fh:
            fh.write(generate_csv(self.jobs,self.project_var.get().strip(),self.drawing_registry))
        self.status_var.set(f"CSV exported → {path}")
        if messagebox.askyesno("Exported",f"Saved to:\n{path}\n\nOpen now?"): _open_file(path)

    def _export_html(self):
        if not self.jobs: messagebox.showinfo("No Jobs","Add at least one job first."); return
        path = filedialog.asksaveasfilename(defaultextension=".html",
                   filetypes=[("HTML files","*.html"),("All files","*.*")],
                   initialfile=f"wire_table_{datetime.now().strftime('%Y%m%d')}.html")
        if not path: return
        tp = dict(self.title_page); tp["notes"] = self.title_notes.get("1.0","end").strip()
        html = generate_html_table(self.jobs,self.project_var.get().strip(),self.drawing_registry,title_page=tp)
        with open(path,"w",encoding="utf-8") as fh: fh.write(html)
        self.status_var.set(f"HTML exported → {path}")
        webbrowser.open(path)
        messagebox.showinfo("HTML Opened",
            "The file has been opened in your browser.\n\nTo save as PDF:\nCtrl+P  →  Destination: Save as PDF")

    # ── File I/O ─────────────────────────────────────────────────

    def _new_plan(self):
        if self.jobs and not messagebox.askyesno("New Plan","Discard current plan and start fresh?"): return
        self.jobs=[]; self.drawing_registry={}; self.relay_registry={}
        self.current_file=None; self.project_folder=None
        self.project_var.set("")
        self.history = {"device": [], "location": [], "pin": [], "panel": [], "wire": []}
        self.ep_history = []
        self.title_page = {"notes": "", "crows": []}
        self.title_notes.delete("1.0", "end")
        self.title("Red-Line-Routing")
        self._refresh_list(); self._refresh_drawings_list()
        self._refresh_relay_list(); self._refresh_crows()
        self.preview.configure(state="normal"); self.preview.delete("1.0","end")
        self.preview.configure(state="disabled")
        self.impl_preview.configure(state="normal"); self.impl_preview.delete("1.0","end")
        self.impl_preview.configure(state="disabled")

    def _open(self):
        path = filedialog.askopenfilename(filetypes=[("Wire Plan","*.wirePlan"),("JSON","*.json"),("All","*.*")])
        if not path: return
        try:
            with open(path,encoding="utf-8") as fh: data = json.load(fh)
            self.jobs = data.get("jobs",[]); self.project_var.set(data.get("project",""))
            self.drawing_registry = data.get("drawing_registry",{})
            self.relay_registry   = data.get("relay_settings",{})
            self.history = data.get("history", {"device":[],"location":[],"pin":[],"panel":[],"wire":[]})
            self.title_page = data.get("title_page", {"notes": "", "crows": []})
            self.current_file = path
            self.project_folder = os.path.dirname(path)
            self._scan_jobs_for_drawings()
            self._rebuild_history()
            self.title_notes.delete("1.0", "end")
            self.title_notes.insert("1.0", self.title_page.get("notes", ""))
            self._refresh_list(); self._refresh_drawings_list()
            self._refresh_relay_list(); self._refresh_crows()
            if self.mode_var.get() == "impl": self._refresh_file_tabs()
            proj = data.get("project","") or os.path.splitext(os.path.basename(path))[0]
            self.title(f"Red-Line-Routing — {proj}")
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
            for sub in ("Drawings", "Relay Settings", "CROW Outage", "Other"):
                os.makedirs(os.path.join(folder, sub), exist_ok=True)
        except Exception as exc:
            messagebox.showerror("Save Error", f"Could not create project folder:\n{exc}"); return
        self.project_folder = folder
        path = os.path.join(folder, safe + ".wirePlan")
        self.current_file = path
        self._write(path)

    def _write(self, path):
        try:
            tp = dict(self.title_page)
            tp["notes"] = self.title_notes.get("1.0", "end").strip()
            with open(path,"w",encoding="utf-8") as fh:
                json.dump({"project":self.project_var.get().strip(),
                           "title_page":tp,
                           "drawing_registry":self.drawing_registry,
                           "relay_settings":self.relay_registry,
                           "history":self.history,
                           "jobs":self.jobs},fh,indent=2)
            proj = self.project_var.get().strip() or os.path.splitext(os.path.basename(path))[0]
            self.title(f"Red-Line-Routing — {proj}")
            self._update_status()
        except Exception as exc: messagebox.showerror("Save Error",str(exc))


# ──────────────────────────────────────────────────────────────────

def _open_file(path):
    try:
        if sys.platform=="win32": os.startfile(path)
        elif sys.platform=="darwin": subprocess.call(["open",path])
        else: subprocess.call(["xdg-open",path])
    except Exception: pass


if __name__ == "__main__":
    app = WirePlannerApp()
    app.mainloop()
