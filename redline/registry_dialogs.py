"""Registry edit dialogs: relay settings, standards, and standards library."""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import urllib.parse

from .db import _AppDB

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
    def __init__(self, parent, existing=None):
        super().__init__(parent)
        self.title("Edit Engineering Standard" if existing else "Add Engineering Standard")
        self.result = None
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

        # Extract document code from stored URL (documentId= query param)
        doc_code_default = ""
        if stored_url:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(stored_url).query)
            if "documentId" in qs:
                doc_code_default = qs["documentId"][0]
        url_default = stored_url

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

        def _rebuild_url(*_):
            code = self.vars["document_code"].get().strip()
            if code and not self.vars["url"].get().strip():
                self.vars["url"].set(code)

        self.vars["document_code"].trace_add("write", _rebuild_url)

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


