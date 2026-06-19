"""PTS (Protection Test Sheet) import and completion dialogs."""
import tkinter as tk
from tkinter import ttk, messagebox
import os
from datetime import datetime

from .utils import _center_window

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

class _PTSImportDialog(tk.Toplevel):
    """Review and selectively import engineering standards extracted from a PTS.

    lookup_results : list of dicts, one per unique standard ID:
        {
            'id':          str,       # extracted ES ID, e.g. "ES 62-K0100"
            'std':         obj|None,  # EngineeringStandard from API, or None if not found
            'in_registry': bool,      # already in engineering_standards_registry
        }
    result : list of selected dicts (the ones the user wants to import), or None on cancel.
    """

    _TAG_NEW      = 'new'
    _TAG_EXISTS   = 'exists'
    _TAG_NOTFOUND = 'notfound'

    def __init__(self, parent, filename, lookup_results):
        super().__init__(parent)
        self.title("Import Standards from PTS")
        self.resizable(True, True)
        self.grab_set()
        self.result = None
        self._rows = lookup_results

        # Header bar
        hdr = tk.Frame(self, bg="#1c2833"); hdr.pack(fill="x")
        tk.Label(hdr, text="Import Engineering Standards from PTS",
                 bg="#1c2833", fg="white", font=("", 11, "bold"),
                 padx=14, pady=10).pack(side="left")
        tk.Label(hdr, text=filename, bg="#1c2833", fg="#85929e",
                 font=("", 9), padx=8, pady=10).pack(side="right")

        # Summary line
        n_new     = sum(1 for r in lookup_results if not r['in_registry'])
        n_exists  = sum(1 for r in lookup_results if r['in_registry'])
        n_found   = sum(1 for r in lookup_results if r['std'] is not None)
        n_total   = len(lookup_results)
        info_f = ttk.Frame(self, padding=(10, 6, 10, 0)); info_f.pack(fill="x")
        ttk.Label(info_f,
                  text=(f"{n_total} standard(s) found  ·  "
                        f"{n_new} new to this project  ·  "
                        f"{n_exists} already in registry  ·  "
                        f"{n_found} matched in API"),
                  foreground="#2980b9").pack(anchor="w")
        ttk.Label(info_f,
                  text=("Standards already in registry will receive a PTS-source tag; "
                        "their existing title/URL/revision are not overwritten."),
                  foreground="grey", font=("", 8)).pack(anchor="w", pady=(2, 0))

        # Results treeview
        frame = ttk.Frame(self, padding=(10, 6, 10, 0)); frame.pack(fill="both", expand=True)
        cols = ("Standard ID", "Title", "Rev", "Status")
        self._tree = ttk.Treeview(frame, columns=cols, show="headings",
                                  selectmode="extended", height=14)
        self._tree.heading("Standard ID", text="Standard ID")
        self._tree.heading("Title",       text="Title")
        self._tree.heading("Rev",         text="Rev")
        self._tree.heading("Status",      text="Status")
        self._tree.column("Standard ID", width=140, stretch=False)
        self._tree.column("Title",       width=320)
        self._tree.column("Rev",         width=50,  stretch=False)
        self._tree.column("Status",      width=160, stretch=False)
        self._tree.tag_configure(self._TAG_NEW,      background="white")
        self._tree.tag_configure(self._TAG_EXISTS,   background="#f0f0f0", foreground="#666666")
        self._tree.tag_configure(self._TAG_NOTFOUND, background="#fff3cd")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for i, r in enumerate(lookup_results):
            std    = r['std']
            in_reg = r['in_registry']
            if in_reg:
                tag    = self._TAG_EXISTS
                status = "Already in registry"
            elif std is None:
                tag    = self._TAG_NOTFOUND
                status = "Not found in API"
            else:
                tag    = self._TAG_NEW
                status = "New"
            title = std.description        if std else ""
            rev   = (str(std.major_version)
                     if (std and std.major_version) else "")
            self._tree.insert("", "end", iid=str(i), tags=(tag,),
                              values=(r['id'], title, rev, status))

        # Button bar
        bf = ttk.Frame(self, padding=(10, 6, 10, 10)); bf.pack(fill="x")
        ttk.Button(bf, text="Cancel",
                   command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bf, text="Import Selected",
                   command=self._do_import).pack(side="right")
        ttk.Button(bf, text="Select All New",
                   command=self._select_all_new).pack(side="left")
        ttk.Button(bf, text="Deselect All",
                   command=lambda: self._tree.selection_set([])).pack(side="left", padx=6)

        self._select_all_new()
        self.geometry("740x520")
        _center_window(self)
        self.wait_window()

    def _select_all_new(self):
        new_iids = [
            iid for iid in self._tree.get_children()
            if self._TAG_NOTFOUND in self._tree.item(iid, "tags")
            or self._TAG_NEW in self._tree.item(iid, "tags")
        ]
        self._tree.selection_set(new_iids)

    def _do_import(self):
        selected = self._tree.selection()
        if not selected:
            messagebox.showinfo("Nothing Selected",
                                "Select at least one standard to import.",
                                parent=self)
            return
        self.result = [self._rows[int(iid)] for iid in selected]
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# PTS completion dialog
# ──────────────────────────────────────────────────────────────────

class _PTSCompletionDialog(tk.Toplevel):
    """Capture initials, date, and comment for one PTS test entry.

    result : {tested_by, date, comment} when saved; {} when cleared; None if cancelled.
    """

    def __init__(self, parent, entry, existing=None):
        super().__init__(parent)
        self.title("Complete PTS Test")
        self.resizable(False, False)
        self.result = None
        self.grab_set()

        hdr = tk.Frame(self, bg="#6c3483"); hdr.pack(fill="x")
        tk.Label(hdr, text="PTS Test Completion",
                 bg="#6c3483", fg="white", font=("", 11, "bold"),
                 padx=10, pady=8).pack(side="left")

        ctx = ttk.Frame(self, padding=(12, 8, 12, 4)); ctx.pack(fill="x")
        ctx.columnconfigure(1, weight=1)
        r = 0
        sect = entry.get('section', '')
        subs = entry.get('subsection', '')
        ctx_str = f"{sect}  ›  {subs}".strip(" ›") if sect or subs else ""
        for lbl, val in [
            ("Section:",   ctx_str),
            ("System:",    entry.get('system', '')),
            ("Test:",      (entry.get('tests', '')[:120]
                            + ('…' if len(entry.get('tests', '')) > 120 else ''))),
            ("Standards:", ",  ".join(entry.get('standard_ids', []))),
        ]:
            if val:
                ttk.Label(ctx, text=lbl, font=("", 9, "bold")).grid(
                    row=r, column=0, sticky="ne", padx=(0, 6), pady=1)
                ttk.Label(ctx, text=val, wraplength=440, justify="left",
                          font=("", 9)).grid(row=r, column=1, sticky="nw", pady=1)
                r += 1

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=4)

        fields = ttk.Frame(self, padding=(12, 4, 12, 8)); fields.pack(fill="x")
        fields.columnconfigure(1, weight=1)
        ex = existing or {}

        ttk.Label(fields, text="Tested By:").grid(
            row=0, column=0, sticky="e", padx=(0, 6), pady=4)
        self._tested_by = tk.StringVar(value=ex.get('tested_by', ''))
        ttk.Entry(fields, textvariable=self._tested_by, width=20).grid(
            row=0, column=1, sticky="w", pady=4)

        ttk.Label(fields, text="Date:").grid(
            row=1, column=0, sticky="e", padx=(0, 6), pady=4)
        date_f = ttk.Frame(fields); date_f.grid(row=1, column=1, sticky="w", pady=4)
        self._date = tk.StringVar(value=ex.get('date', ''))
        ttk.Entry(date_f, textvariable=self._date, width=16).pack(side="left")
        ttk.Button(date_f, text="Today",
                   command=lambda: self._date.set(
                       datetime.now().strftime("%Y-%m-%d"))
                   ).pack(side="left", padx=(4, 0))

        ttk.Label(fields, text="Comment:").grid(
            row=2, column=0, sticky="ne", padx=(0, 6), pady=4)
        self._comment = tk.Text(fields, width=40, height=3, wrap="word", font=("", 9))
        self._comment.grid(row=2, column=1, sticky="ew", pady=4)
        if ex.get('comment'):
            self._comment.insert("1.0", ex['comment'])

        btn_f = ttk.Frame(self, padding=(12, 4, 12, 10)); btn_f.pack(fill="x")
        ttk.Button(btn_f, text="Save",   command=self._save).pack(side="right", padx=2)
        ttk.Button(btn_f, text="Cancel", command=self.destroy).pack(side="right", padx=2)
        if existing:
            ttk.Button(btn_f, text="Clear Completion",
                       command=self._clear).pack(side="left")

        _center_window(self)
        self.wait_window()

    def _save(self):
        tested_by = self._tested_by.get().strip()
        if not tested_by:
            messagebox.showwarning("Required", "Tested By is required.", parent=self)
            return
        self.result = {
            'tested_by': tested_by,
            'date':      self._date.get().strip(),
            'comment':   self._comment.get("1.0", "end").strip(),
        }
        self.destroy()

    def _clear(self):
        self.result = {}   # empty dict = "clear" signal
        self.destroy()


