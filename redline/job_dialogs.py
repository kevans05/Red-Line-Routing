"""Job entry dialogs: endpoint frames, protection dialogs, JobDialog, DrawingEditDialog."""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import re

from .models import (empty_endpoint, empty_protection, _get_prot_drawings,
                     JOB_TYPE_SHORT)
from .utils import (is_h_type_drawing, _center_window,
                    _bind_search_combobox, _bind_url_open)
from .formatting import format_job, _ROW_STYLE, _ROW_BORDER

try:
    from pts_parser import (parse_pts as _parse_pts,
                            row_key as _pts_row_key,
                            write_completions as _pts_write_completions)
    _PTS_PARSER_AVAILABLE = True
except ImportError:
    _PTS_PARSER_AVAILABLE = False
    def _parse_pts(*a, **kw): return []
    def _pts_row_key(entry): return ""
    def _pts_write_completions(*a, **kw): return False, "pts_parser not available"

try:
    from drawing_search import DrawingSearchClient
    _DRAWING_SEARCH_AVAILABLE = True
except ImportError:
    _DRAWING_SEARCH_AVAILABLE = False

# Imported at call-time to avoid circular dependency at module load
def _get_drawing_search_dialog():
    from .search_dialogs import DrawingSearchDialog
    return DrawingSearchDialog

def _get_pts_completion_dialog():
    from .pts_dialogs import _PTSCompletionDialog
    return _PTSCompletionDialog

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
        action = "BLOCK MB INPUT" if self.job_type == "BLOCK" else "RESTORE MB INPUT"
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
                 ctrl_desks=None, crows=None,
                 pts_files=None, on_complete_pts=None):
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
        self.pts_files = pts_files if pts_files is not None else {}
        self.on_complete_pts = on_complete_pts
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

    def _get_pts_entries_for_steps(self, fname):
        """Return parsed entries for a PTS file, or [] if unavailable."""
        if not _PTS_PARSER_AVAILABLE:
            return []
        pts_dir = self.settings.get("_pts_dir_hint", "")
        fpath = os.path.join(pts_dir, fname) if pts_dir else fname
        if not os.path.isfile(fpath):
            return []
        try:
            result = _parse_pts(fpath)
            return result.get("entries", []) if not result.get("error") else []
        except Exception:
            return []

    def _complete_pts_step(self, fname, key, entry, refresh_cb):
        """Open _PTSCompletionDialog for one step; call on_complete_pts on save."""
        existing = (self.pts_files.get(fname, {})
                    .get("completions", {}).get(key))
        dlg = _get_pts_completion_dialog()(self, entry, existing=existing)
        if dlg.result is None:
            return
        if self.on_complete_pts:
            self.on_complete_pts(fname, key, dlg.result or None)
        refresh_cb()

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
            ttk.Label(f, text="Notes:").grid(row=row, column=0, sticky="ne", padx=(0,6), pady=2)
            self._rem_add_notes = tk.Text(f, width=58, height=3, wrap="word", font=("",9))
            self._rem_add_notes.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
            self._rem_add_notes.insert("1.0", ex.get("notes",""))
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
            lbl = "BLOCK PROTECTION" if self.job_type == "BLOCK" else "RESTORE PROTECTION"
            self._section_label(f, row, f"── {lbl} ──", color); row += 2

            # RESTORE: offer to copy settings from an existing BLOCK step
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
                                           base_drawing_url=self.settings.get("drawing_download_url",""))
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
            notes_txt = tk.Text(f, width=58, height=4, wrap="word", font=("",9))
            notes_txt.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
            notes_txt.insert("1.0", ex.get("notes",""))
            self._test_notes_widget = notes_txt
            row += 1

            # ── Linked PTS File ───────────────────────────────────────
            self._section_label(f, row, "── PTS FILE ──", color); row += 2
            ttk.Label(f, text="PTS File:").grid(row=row, column=0, sticky="e",
                                                 padx=(0, 6), pady=2)
            pts_names = [""] + sorted(self.pts_files.keys())
            self._testing_pts_var = tk.StringVar(value=ex.get("pts_file", ""))
            pts_cb = ttk.Combobox(f, textvariable=self._testing_pts_var,
                                  values=pts_names, state="readonly", width=46)
            pts_cb.grid(row=row, column=1, sticky="w", pady=2)
            row += 1

            # Steps list (rebuilt whenever the dropdown changes)
            steps_lf = ttk.LabelFrame(f, text="PTS Steps", padding=4)
            steps_lf.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(4, 2))
            f.columnconfigure(1, weight=1)
            row += 1
            self._testing_steps_lf = steps_lf

            def _rebuild_steps(*_):
                for w in steps_lf.winfo_children():
                    w.destroy()
                fname = self._testing_pts_var.get()
                if not fname or fname not in self.pts_files:
                    ttk.Label(steps_lf, text="Select a PTS file above to view steps.",
                              foreground="grey").pack(anchor="w")
                    return
                completions = self.pts_files[fname].get("completions", {})
                # Try to get live entries via parser; fall back to stored IDs
                entries = self._get_pts_entries_for_steps(fname)
                if not entries:
                    ttk.Label(steps_lf,
                              text="Parse file from the PTS tab to see individual steps.",
                              foreground="grey").pack(anchor="w")
                    return
                canvas = tk.Canvas(steps_lf, height=120, highlightthickness=0)
                vsb = ttk.Scrollbar(steps_lf, orient="vertical", command=canvas.yview)
                canvas.configure(yscrollcommand=vsb.set)
                vsb.pack(side="right", fill="y")
                canvas.pack(side="left", fill="both", expand=True)
                inner = ttk.Frame(canvas)
                canvas.create_window((0, 0), window=inner, anchor="nw")
                inner.bind("<Configure>",
                           lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
                for entry in entries:
                    key = _pts_row_key(entry)
                    comp = completions.get(key)
                    sect = entry.get("section", "")
                    subs = entry.get("subsection", "")
                    sys_ = entry.get("system", "")
                    ctx  = f"{sect}  ›  {subs}" if sect and subs else (sect or subs)
                    stds = ",  ".join(entry.get("standard_ids", []))
                    status = "✓" if comp else "○"
                    fg = "#1a7a3a" if comp else "#555"
                    row_f = ttk.Frame(inner)
                    row_f.pack(fill="x", pady=1)
                    tk.Label(row_f, text=status, fg=fg, font=("", 10, "bold"),
                             width=2).pack(side="left")
                    lbl_txt = f"{ctx}  |  {sys_}  |  {stds}"
                    if comp:
                        lbl_txt += f"  [{comp.get('tested_by','')}  {comp.get('date','')}]"
                    tk.Label(row_f, text=lbl_txt, anchor="w",
                             font=("", 8)).pack(side="left", fill="x", expand=True)
                    _entry = entry  # capture for lambda
                    _key   = key
                    ttk.Button(row_f, text="Complete" if not comp else "Edit",
                               command=lambda e=_entry, k=_key, fn=fname:
                                   self._complete_pts_step(fn, k, e, _rebuild_steps)
                               ).pack(side="right", padx=(4, 0))

            pts_cb.bind("<<ComboboxSelected>>", _rebuild_steps)
            _rebuild_steps()

        elif self.job_type == "ISOLATION":
            self._section_label(f, row, "── ISOLATION ──", color); row += 2
            ttk.Label(f, text="Notes:").grid(row=row, column=0, sticky="ne", padx=(0,6), pady=2)
            iso_txt = tk.Text(f, width=58, height=4, wrap="word", font=("",9))
            iso_txt.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
            iso_txt.insert("1.0", ex.get("notes",""))
            self._test_notes_widget = iso_txt
            row += 1
            self._iso_drawings_frame = MultiDrawingFrame(
                f, registry=self.registry,
                base_drawing_url=self.settings.get("drawing_download_url", ""))
            self._iso_drawings_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(4,2))
            self._iso_drawings_frame.set(ex.get("drawings", []))
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
            action = "Block" if jt == "BLOCK" else "Restore"
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
            job["notes"] = self._rem_add_notes.get("1.0","end").strip()
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
        elif self.job_type == "TESTING":
            job["notes"]     = self._test_notes_widget.get("1.0", "end").strip()
            job["pts_file"]  = self._testing_pts_var.get()
        elif self.job_type == "ISOLATION":
            job["drawings"] = self._iso_drawings_frame.get()
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
        dlg = _get_drawing_search_dialog()(self, self._app_config, multi_select=False,
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



