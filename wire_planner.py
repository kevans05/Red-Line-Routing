#!/usr/bin/env python3
"""
Wire Work Planner
-----------------
Plan and print electrical wire removal, addition, and move jobs.
Save/load work plans as .wirePlan files (JSON).
Export detailed report (text), table (text/CSV), or colour-coded HTML (print→PDF).
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import json
import os
import subprocess
import sys
import webbrowser
from copy import deepcopy
from datetime import datetime


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


# ──────────────────────────────────────────────────────────────────
# Drawing-aware endpoint frame (with conditional Drawing Cell)
# ──────────────────────────────────────────────────────────────────

class DrawingAwareFrame(ttk.LabelFrame):
    """LabelFrame with autofill from registry, context-aware suggestions, and live search."""

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
        self.ep_history = ep_history if ep_history is not None else []
        self._drawing_combo = None
        self._cell_entry = None
        self._build()

    def _context_suggestions(self, key):
        """Return an ordered suggestion list for *key*, boosting values that co-occur
        with whatever is already typed in context fields."""
        ctx_keys = self._CONTEXT_MAP.get(key, [])
        ctx_vals = {cf: self.vars[cf].get().strip()
                    for cf in ctx_keys if cf in self.vars}

        seen, prioritized, rest = set(), [], []
        for ep in self.ep_history:
            val = ep.get(key, "").strip()
            if not val:
                continue
            score = sum(1 for cf, cv in ctx_vals.items()
                        if cv and ep.get(cf, "").strip().lower() == cv.lower())
            lo = val.lower()
            if score > 0 and lo not in seen:
                seen.add(lo)
                prioritized.append(val)

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
                combo = ttk.Combobox(self, textvariable=var, width=26)
                combo["postcommand"] = lambda c=combo: c.__setitem__(
                    "values", self._drawing_suggestions())
                combo.bind("<<ComboboxSelected>>", self._on_drawing_selected)
                combo.bind("<FocusOut>", self._on_drawing_focusout)
                _bind_search_combobox(combo, self._drawing_suggestions)
                combo.grid(row=row_idx, column=1, sticky="ew", pady=1)
                self._drawing_combo = combo
                var.trace_add("write", lambda *_: self._update_cell_state())
            elif key == "drawing_cell":
                entry = ttk.Entry(self, textvariable=var, width=28)
                entry.grid(row=row_idx, column=1, sticky="ew", pady=1)
                self._cell_entry = entry
            elif key in self.HISTORY_KEYS:
                combo = ttk.Combobox(self, textvariable=var, width=28)
                combo["postcommand"] = lambda k=key, c=combo: c.__setitem__(
                    "values", self._context_suggestions(k))
                _bind_search_combobox(combo, lambda k=key: self._context_suggestions(k))
                combo.grid(row=row_idx, column=1, sticky="ew", pady=1)
            else:
                entry = ttk.Entry(self, textvariable=var, width=28)
                entry.grid(row=row_idx, column=1, sticky="ew", pady=1)
                if key in ("drawing_rev", "drawing_url"):
                    entry.bind("<FocusOut>", self._on_detail_changed)

        self.columnconfigure(1, weight=1)

    def _update_cell_state(self):
        if self._cell_entry is None:
            return
        name = self.vars.get("drawing", tk.StringVar()).get().strip()
        if not name or is_h_type_drawing(name):
            self._cell_entry.configure(state="normal")
        else:
            self._cell_entry.configure(state="disabled")
            self.vars["drawing_cell"].set("")

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
        if name in self.registry:
            rec = self.registry[name]
            if not self.vars["drawing_rev"].get():
                self.vars["drawing_rev"].set(rec.get("rev", ""))
            if not self.vars["drawing_url"].get():
                self.vars["drawing_url"].set(rec.get("url", ""))

    def _push(self, name):
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
    def __init__(self, parent, existing=None, registry=None):
        super().__init__(parent)
        self.title("Edit Drawing" if existing else "Add Drawing")
        self.result = None
        self.registry = registry if registry is not None else {}
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
        self.url_var = tk.StringVar(value=ex.get("drawing_url", ""))
        url_e = ttk.Entry(f, textvariable=self.url_var, width=32)
        url_e.bind("<FocusOut>", self._push)
        url_e.grid(row=2, column=1, sticky="ew", pady=3)

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
    def __init__(self, parent, registry=None, **kwargs):
        super().__init__(parent, text="Drawings", padding=4, **kwargs)
        self.registry = registry if registry is not None else {}
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
        dlg = DrawingEntryDialog(self, registry=self.registry)
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
    def __init__(self, parent, title, registry=None, job_type="BLOCK", **kwargs):
        super().__init__(parent, text=title, padding=6, **kwargs)
        self.registry = registry if registry is not None else {}
        self.job_type = job_type
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
        self.multi_draw = MultiDrawingFrame(self, registry=self.registry)
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
                 history=None, ep_history=None, jobs=None):
        super().__init__(parent)
        self.title(f"{'Edit' if existing else 'Add'} — {job_type}")
        self.result = None
        self.job_type  = job_type
        self.registry  = registry   if registry   is not None else {}
        self.history   = history    if history    is not None else {}
        self.ep_history= ep_history if ep_history is not None else []
        self.jobs      = jobs       if jobs       is not None else []
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
                                           registry=self.registry, job_type=self.job_type)
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
    def __init__(self, parent, existing=None):
        super().__init__(parent)
        self.title("Edit Drawing" if existing else "Add Drawing")
        self.result = None
        self._old_name = existing.get("name") if existing else None
        self.resizable(False, False)
        self._build(existing or {})
        self.grab_set()
        self.wait_window()

    def _build(self, ex):
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        fields = [("name","Drawing Name / No.:"),("rev","Revision:"),
                  ("url","Drawing URL:"),("notes","Notes:")]
        self.vars = {}
        for row,(key,label) in enumerate(fields):
            ttk.Label(f,text=label).grid(row=row,column=0,sticky="e",padx=(0,6),pady=4)
            var = tk.StringVar(value=ex.get(key,""))
            self.vars[key] = var
            ttk.Entry(f,textvariable=var,width=46).grid(row=row,column=1,sticky="ew",pady=4)
        f.columnconfigure(1, weight=1)
        br = ttk.Frame(self); br.pack(fill="x",padx=10,pady=(0,8))
        ttk.Button(br,text="Cancel",command=self.destroy).pack(side="right",padx=2)
        ttk.Button(br,text="Save",  command=self._save).pack(side="right",padx=2)
        self.geometry("460x230")

    def _save(self):
        name = self.vars["name"].get().strip()
        if not name:
            messagebox.showwarning("Required","Drawing name is required.",parent=self)
            return
        self.result = {"name":name,"old_name":self._old_name,
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

def generate_report(jobs, project="", drawing_registry=None):
    now = datetime.now().strftime("%Y-%m-%d  %H:%M")
    title = "WIRE WORK PLAN" + (f"  —  {project}" if project else "")
    counts = {}
    for j in jobs: counts[j["type"]] = counts.get(j["type"],0)+1
    summary = "  ".join(f"{v} {k}" for k,v in counts.items())
    parts = [_bar("*"),title.center(W),f"Generated: {now}".center(W),_bar("*"),"",
             f"  Total Jobs : {len(jobs)}",f"  Breakdown  : {summary}",""]
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

def generate_html_table(jobs, project="", drawing_registry=None):
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = "Wire Work Plan" + (f" — {project}" if project else "")

    drw_html = ""
    if drawing_registry:
        def _url_cell(info):
            u = info.get("url","")
            return f'<a href="{_esc(u)}">{_esc(u)}</a>' if u else ""
        rows = "".join(
            f"<tr><td>{_esc(n)}</td><td>{_esc(i.get('rev',''))}</td>"
            f"<td>{_url_cell(i)}</td>"
            f"<td>{_esc(i.get('notes',''))}</td></tr>"
            for n,i in sorted(drawing_registry.items()))
        drw_html = (f"<h2>Project Drawings</h2><table>"
                    f"<thead><tr><th>Drawing</th><th>Rev</th><th>URL</th><th>Notes</th></tr></thead>"
                    f"<tbody>{rows}</tbody></table><br>")

    job_rows = ""
    seq = 1
    type_labels = {"REMOVE":"Remove Wire","ADD":"Add Wire","MOVE":"Move Wire",
                   "BLOCK":"Block Protection","UNBLOCK":"Unblock Protection"}
    for job in jobs:
        jtype = job["type"]; desc = _esc(job.get("description",""))

        def tr(key, label, s_html, wire, e_html):
            row_bg, type_style = _ROW_STYLE.get(key, ("",""))
            nonlocal seq
            r = (f'<tr style="{row_bg}">'
                 f'<td>{seq}</td>'
                 f'<td style="{type_style}">{_esc(label)}</td>'
                 f'<td>{desc}</td>'
                 f'<td>{s_html}</td>'
                 f'<td>{_esc(wire)}</td>'
                 f'<td>{e_html}</td></tr>')
            seq += 1
            return r

        if jtype in ("REMOVE","ADD"):
            job_rows += tr(jtype, type_labels[jtype],
                           _ep_html(job.get("start",{})), job.get("wire",""),
                           _ep_html(job.get("end",{})))
        elif jtype == "MOVE":
            job_rows += tr("MOVE-REMOVE","Move — Remove",
                           _ep_html(job.get("start",{})), job.get("wire",""),
                           _ep_html(job.get("end",{})))
            job_rows += tr("MOVE-ADD","Move — Add",
                           _ep_html(job.get("add_start",{})), job.get("add_wire",""),
                           _ep_html(job.get("add_end",{})))
        elif jtype in ("BLOCK","UNBLOCK"):
            job_rows += tr(jtype, type_labels[jtype],
                           _prot_html(job.get("protection",{})), "", "")
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
a{{color:#1a5276}}
.legend{{display:flex;gap:8px;margin:6px 0 12px;flex-wrap:wrap;font-size:7.5pt}}
.leg{{padding:2px 7px;border-radius:3px;border:1px solid #ccc}}
.print-header{{display:none;font-size:7.5pt;color:#555;border-bottom:1px solid #ccc;padding:3px 0 3px;margin-bottom:6px}}
.print-footer{{display:none}}
@media print{{
  body{{margin-top:18mm;margin-bottom:14mm}}
  .print-header{{display:flex;position:fixed;top:0;left:0;right:0;background:#fff;
    padding:3px 8mm;justify-content:space-between;z-index:99}}
  .print-footer{{display:block;position:fixed;bottom:0;left:0;right:0;background:#fff;
    font-size:7pt;color:#999;padding:2px 8mm;border-top:1px solid #eee;text-align:right}}
  .print-footer::after{{content:"Page " counter(page)}}
  a{{color:#000;text-decoration:none}}
  @page{{size:A3 landscape;margin:14mm 8mm 12mm 8mm}}
}}
</style></head><body>
<div class="print-header">
  <span><b>{_esc(title)}</b></span>
  <span>Generated: {now} &nbsp;|&nbsp; {seq-1} step(s)</span>
</div>
<div class="print-footer"></div>
<h1>{_esc(title)}</h1>
<p class="meta">Generated: {now} &nbsp;|&nbsp; {seq-1} step(s)</p>
<div class="legend">{legend}</div>
{drw_html}
<table><thead><tr>
<th style="width:28px">#</th><th style="width:110px">Type</th>
<th style="width:15%">Description</th><th style="width:24%">Start Point / Device</th>
<th style="width:95px">Wire</th><th style="width:24%">End Point / Device</th>
</tr></thead><tbody>{job_rows}</tbody></table>
<p style="font-size:7pt;color:#aaa">Ctrl+P → Save as PDF</p>
</body></html>"""


# ──────────────────────────────────────────────────────────────────
# Main application
# ──────────────────────────────────────────────────────────────────

class WirePlannerApp(tk.Tk):
    TYPE_FG = {"REMOVE":"#c0392b","ADD":"#1a7a3c","MOVE":"#1a5a99",
               "BLOCK":"#d35400","UNBLOCK":"#16a085","TESTING":"#6c3483"}

    def __init__(self):
        super().__init__()
        self.title("Wire Work Planner")
        self.geometry("1080x720")
        self.jobs = []
        self.drawing_registry = {}
        self.current_file = None
        # Flat value history for autocomplete dropdowns
        self.history = {"device": [], "location": [], "pin": [], "panel": [], "wire": []}
        # Full endpoint dicts for context-aware suggestions
        self.ep_history = []
        self._build_menu()
        self._build_ui()

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
        fm.add_command(label="Quit",command=self.quit, accelerator="Ctrl+Q")
        mb.add_cascade(label="File",menu=fm)
        self.config(menu=mb)
        self.bind("<Control-n>", lambda _: self._new_plan())
        self.bind("<Control-o>", lambda _: self._open())
        self.bind("<Control-s>", lambda _: self._save())
        self.bind("<Control-e>", lambda _: self._export_report())
        self.bind("<Control-q>", lambda _: self.quit())

    def _build_ui(self):
        tb1 = ttk.Frame(self, padding=(6,4))
        tb1.pack(fill="x")
        ttk.Label(tb1,text="Project:").pack(side="left")
        self.project_var = tk.StringVar()
        ttk.Entry(tb1,textvariable=self.project_var,width=22).pack(side="left",padx=(4,10))
        for label,jtype,color in [
            ("+ Remove","REMOVE","#c0392b"),("+ Add","ADD","#27ae60"),
            ("+ Block","BLOCK","#d35400"),("+ Unblock","UNBLOCK","#16a085"),
            ("+ Testing","TESTING","#6c3483")]:
            tk.Button(tb1,text=label,fg="white",bg=color,relief="flat",padx=7,pady=3,
                      cursor="hand2",command=lambda t=jtype:self._add_job(t)).pack(side="left",padx=2)
        rf = ttk.Frame(tb1); rf.pack(side="right")
        for label,cmd in [("↑ Up",self._move_up),("↓ Down",self._move_down),
                           ("Edit",self._edit_job),("Duplicate",self._duplicate_job),
                           ("Delete",self._delete_job)]:
            ttk.Button(rf,text=label,command=cmd).pack(side="left",padx=2)

        tb2 = ttk.Frame(self,padding=(6,0,6,4)); tb2.pack(fill="x")
        ttk.Button(tb2,text="Swap ↔ Start/End",command=self._swap_endpoints).pack(side="left",padx=2)
        ttk.Button(tb2,text="Auto-Group by Device",command=self._auto_group,
                   state="disabled").pack(side="left",padx=2)
        ttk.Button(tb2,text="HTML / PDF",  command=self._export_html).pack(side="right",padx=2)
        ttk.Button(tb2,text="Export CSV",  command=self._export_csv).pack(side="right",padx=2)
        ttk.Button(tb2,text="Export Table",command=self._export_table).pack(side="right",padx=2)
        ttk.Button(tb2,text="Export Report",command=self._export_report).pack(side="right",padx=2)
        ttk.Button(tb2,text="Preview",     command=self._preview_report).pack(side="right",padx=2)

        nb = ttk.Notebook(self); nb.pack(fill="both",expand=True,padx=6,pady=(0,4))
        wt = ttk.Frame(nb); nb.add(wt,text="  Work Order  ");  self._build_work_tab(wt)
        dt = ttk.Frame(nb); nb.add(dt,text="  Project Drawings  "); self._build_drawings_tab(dt)

        self.status_var = tk.StringVar(value="Ready  —  no jobs loaded")
        ttk.Label(self,textvariable=self.status_var,relief="sunken",
                  anchor="w",padding=(4,1)).pack(fill="x",side="bottom")

    def _build_work_tab(self, parent):
        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=4, pady=4)
        lf = ttk.LabelFrame(pw, text="Work Order", padding=4); pw.add(lf, weight=1)
        cols = ("Seq","Type","Description")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("Seq",text="#"); self.tree.heading("Type",text="Type"); self.tree.heading("Description",text="Description")
        self.tree.column("Seq",width=35,stretch=False); self.tree.column("Type",width=110,stretch=False); self.tree.column("Description",width=230)
        for t,fg in self.TYPE_FG.items(): self.tree.tag_configure(t, foreground=fg)
        vsb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True); vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _: self._edit_job())
        pf = ttk.LabelFrame(pw, text="Job Preview", padding=4); pw.add(pf, weight=2)
        self.preview = scrolledtext.ScrolledText(pf, font=("Courier",9), state="disabled", wrap="none")
        self.preview.pack(fill="both", expand=True)

    def _build_drawings_tab(self, parent):
        tb = ttk.Frame(parent, padding=(4,4)); tb.pack(fill="x")
        ttk.Button(tb, text="+ Add Drawing", command=self._add_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",          command=self._edit_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Delete",        command=self._delete_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Scan Jobs →",   command=self._scan_and_refresh).pack(side="left", padx=(10,2))
        ttk.Label(tb, text="Drawing names entered in any job are added here automatically.",
                  foreground="grey").pack(side="left", padx=8)
        frame = ttk.Frame(parent); frame.pack(fill="both", expand=True, padx=4, pady=(0,4))
        cols = ("Drawing","Revision","URL","Notes")
        self.drawings_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.drawings_tree.heading("Drawing",text="Drawing"); self.drawings_tree.heading("Revision",text="Revision")
        self.drawings_tree.heading("URL",text="Drawing URL"); self.drawings_tree.heading("Notes",text="Notes")
        self.drawings_tree.column("Drawing",width=160,stretch=False); self.drawings_tree.column("Revision",width=80,stretch=False)
        self.drawings_tree.column("URL",width=380); self.drawings_tree.column("Notes",width=200)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.drawings_tree.yview)
        self.drawings_tree.configure(yscrollcommand=vsb.set)
        self.drawings_tree.pack(side="left", fill="both", expand=True); vsb.pack(side="right", fill="y")
        self.drawings_tree.bind("<Double-1>", lambda _: self._edit_drawing())

    # ── Drawing registry CRUD ─────────────────────────────────────

    def _refresh_drawings_list(self):
        for iid in self.drawings_tree.get_children(): self.drawings_tree.delete(iid)
        for name in sorted(self.drawing_registry.keys()):
            info = self.drawing_registry[name]
            self.drawings_tree.insert("","end",iid=name,
                values=(name,info.get("rev",""),info.get("url",""),info.get("notes","")))

    def _add_drawing(self):
        dlg = DrawingEditDialog(self)
        if dlg.result:
            name = dlg.result["name"]
            self.drawing_registry[name] = {"rev":dlg.result["rev"],"url":dlg.result["url"],"notes":dlg.result["notes"]}
            self._refresh_drawings_list()

    def _edit_drawing(self):
        sel = self.drawings_tree.selection()
        if not sel: messagebox.showinfo("Select","Please select a drawing to edit."); return
        name = sel[0]; info = self.drawing_registry.get(name,{})
        dlg = DrawingEditDialog(self, existing={"name":name,**info})
        if dlg.result:
            old = dlg.result.get("old_name"); new_name = dlg.result["name"]
            if old and old != new_name and old in self.drawing_registry: del self.drawing_registry[old]
            self.drawing_registry[new_name] = {"rev":dlg.result["rev"],"url":dlg.result["url"],"notes":dlg.result["notes"]}
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
                self.drawing_registry[name] = {"rev":"","url":"","notes":""}
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

    # ── Job list ─────────────────────────────────────────────────

    def _refresh_list(self):
        for iid in self.tree.get_children(): self.tree.delete(iid)
        disp = {"REMOVE":"REMOVE","ADD":"ADD","MOVE":"MOVE","BLOCK":"BLOCK PROT.","UNBLOCK":"UNBLOCK PROT.","TESTING":"TESTING"}
        for i,job in enumerate(self.jobs):
            self.tree.insert("","end",iid=str(i),
                values=(i+1,disp.get(job["type"],job["type"]),job.get("description","")),
                tags=(job["type"],))
        self._update_status()

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
                        history=self.history, ep_history=self.ep_history, jobs=self.jobs)
        if dlg.result:
            self._collect_history(dlg.result)
            self.jobs.append(dlg.result); self._refresh_list(); self._refresh_drawings_list()
            idx = len(self.jobs)-1; self.tree.selection_set(str(idx)); self._on_select()

    def _edit_job(self):
        idx = self._selected_idx()
        if idx is None: messagebox.showinfo("Select a Job","Please select a job from the list."); return
        dlg = JobDialog(self, self.jobs[idx]["type"], existing=deepcopy(self.jobs[idx]),
                        registry=self.drawing_registry, history=self.history,
                        ep_history=self.ep_history, jobs=self.jobs)
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
        report = generate_report(self.jobs,self.project_var.get().strip(),self.drawing_registry)
        win = tk.Toplevel(self); win.title("Report Preview"); win.geometry("740x720")
        txt = scrolledtext.ScrolledText(win,font=("Courier",9),wrap="none")
        txt.pack(fill="both",expand=True,padx=6,pady=6); txt.insert("1.0",report); txt.configure(state="disabled")
        bf = ttk.Frame(win); bf.pack(pady=(0,8))
        ttk.Button(bf,text="Export Report…",command=self._export_report).pack(side="left",padx=4)
        ttk.Button(bf,text="Close",command=win.destroy).pack(side="left",padx=4)

    def _export_report(self):
        self._export_text(generate_report(self.jobs,self.project_var.get().strip(),self.drawing_registry),
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
        html = generate_html_table(self.jobs,self.project_var.get().strip(),self.drawing_registry)
        with open(path,"w",encoding="utf-8") as fh: fh.write(html)
        self.status_var.set(f"HTML exported → {path}")
        webbrowser.open(path)
        messagebox.showinfo("HTML Opened",
            "The file has been opened in your browser.\n\nTo save as PDF:\nCtrl+P  →  Destination: Save as PDF")

    # ── File I/O ─────────────────────────────────────────────────

    def _new_plan(self):
        if self.jobs and not messagebox.askyesno("New Plan","Discard current plan and start fresh?"): return
        self.jobs=[]; self.drawing_registry={}; self.current_file=None; self.project_var.set("")
        self.history = {"device": [], "location": [], "pin": [], "panel": [], "wire": []}
        self.ep_history = []
        self._refresh_list(); self._refresh_drawings_list()
        self.preview.configure(state="normal"); self.preview.delete("1.0","end"); self.preview.configure(state="disabled")

    def _open(self):
        path = filedialog.askopenfilename(filetypes=[("Wire Plan","*.wirePlan"),("JSON","*.json"),("All","*.*")])
        if not path: return
        try:
            with open(path,encoding="utf-8") as fh: data = json.load(fh)
            self.jobs = data.get("jobs",[]); self.project_var.set(data.get("project",""))
            self.drawing_registry = data.get("drawing_registry",{})
            self.history = data.get("history", {"device":[],"location":[],"pin":[],"panel":[],"wire":[]})
            self.current_file = path
            self._scan_jobs_for_drawings()
            self._rebuild_history()
            self._refresh_list(); self._refresh_drawings_list()
        except Exception as exc: messagebox.showerror("Open Error",str(exc))

    def _save(self):
        if not self.current_file: self._save_as()
        else: self._write(self.current_file)

    def _save_as(self):
        proj = self.project_var.get().strip()
        safe = "".join(c if c not in r'<>:"/\|?*' else "_" for c in proj) if proj else "wire_plan"
        path = filedialog.asksaveasfilename(defaultextension=".wirePlan",
                   initialfile=safe,
                   filetypes=[("Wire Plan","*.wirePlan"),("JSON","*.json"),("All","*.*")])
        if path: self.current_file=path; self._write(path)

    def _write(self, path):
        try:
            with open(path,"w",encoding="utf-8") as fh:
                json.dump({"project":self.project_var.get().strip(),
                           "drawing_registry":self.drawing_registry,
                           "history":self.history,
                           "jobs":self.jobs},fh,indent=2)
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
