#!/usr/bin/env python3
"""
Wire Work Planner
-----------------
Plan and print electrical wire removal, addition, and move jobs.
Save/load work plans as .wirePlan files (JSON).
Export formatted printout as a text file.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import datetime


# ──────────────────────────────────────────────────────────────────
# Data helpers
# ──────────────────────────────────────────────────────────────────

def empty_endpoint():
    return {
        "device": "", "location": "", "pin": "", "panel": "",
        "drawing": "", "drawing_rev": "", "drawing_url": "", "drawing_cell": "",
    }


def empty_protection():
    return {
        "equipment": "", "location": "", "panel": "",
        "drawing": "", "drawing_rev": "", "drawing_url": "", "drawing_cell": "",
        "notes": "",
    }


def empty_job(job_type="REMOVE"):
    if job_type in ("BLOCK", "UNBLOCK"):
        return {"type": job_type, "description": "", "protection": empty_protection()}
    job = {"type": job_type, "description": "", "wire": "",
           "start": empty_endpoint(), "end": empty_endpoint()}
    if job_type == "MOVE":
        job["add_wire"] = ""
        job["add_start"] = empty_endpoint()
        job["add_end"] = empty_endpoint()
    return job


# ──────────────────────────────────────────────────────────────────
# Drawing-aware frame base class  (shared autofill logic)
# ──────────────────────────────────────────────────────────────────

class DrawingAwareFrame(ttk.LabelFrame):
    """LabelFrame whose 'drawing' field is a Combobox that autofills
    Rev and URL from a shared registry dict."""

    FIELDS = []  # subclasses define

    def __init__(self, parent, title, registry=None, **kwargs):
        super().__init__(parent, text=title, padding=6, **kwargs)
        self.vars = {}
        self.registry = registry if registry is not None else {}
        self._drawing_combo = None
        self._build()

    def _build(self):
        for row, (key, label) in enumerate(self.FIELDS):
            ttk.Label(self, text=label + ":").grid(
                row=row, column=0, sticky="e", padx=(0, 4), pady=1
            )
            var = tk.StringVar()
            self.vars[key] = var

            if key == "drawing":
                combo = ttk.Combobox(self, textvariable=var, width=26)
                combo["postcommand"] = self._update_drawing_list
                combo.bind("<<ComboboxSelected>>", self._on_drawing_selected)
                combo.bind("<FocusOut>", self._on_drawing_focusout)
                combo.grid(row=row, column=1, sticky="ew", pady=1)
                self._drawing_combo = combo
            else:
                entry = ttk.Entry(self, textvariable=var, width=28)
                entry.grid(row=row, column=1, sticky="ew", pady=1)
                if key in ("drawing_rev", "drawing_url"):
                    entry.bind("<FocusOut>", self._on_detail_changed)

        self.columnconfigure(1, weight=1)

    def _update_drawing_list(self):
        if self._drawing_combo is not None:
            self._drawing_combo["values"] = sorted(self.registry.keys())

    def _on_drawing_selected(self, _=None):
        self._autofill(self.vars["drawing"].get().strip())

    def _on_drawing_focusout(self, _=None):
        name = self.vars["drawing"].get().strip()
        if name:
            if name not in self.registry:
                self.registry[name] = {"rev": "", "url": "", "notes": ""}
            self._autofill(name)
            self._push_to_registry(name)

    def _on_detail_changed(self, _=None):
        name = self.vars["drawing"].get().strip()
        if name:
            self._push_to_registry(name)

    def _autofill(self, name):
        if name in self.registry:
            rec = self.registry[name]
            if not self.vars["drawing_rev"].get():
                self.vars["drawing_rev"].set(rec.get("rev", ""))
            if not self.vars["drawing_url"].get():
                self.vars["drawing_url"].set(rec.get("url", ""))

    def _push_to_registry(self, name):
        rev = self.vars["drawing_rev"].get().strip()
        url = self.vars["drawing_url"].get().strip()
        if name not in self.registry:
            self.registry[name] = {"rev": "", "url": "", "notes": ""}
        if rev:
            self.registry[name]["rev"] = rev
        if url:
            self.registry[name]["url"] = url

    def get(self):
        return {k: v.get().strip() for k, v in self.vars.items()}

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


class ProtectionFrame(DrawingAwareFrame):
    FIELDS = [
        ("equipment",    "Equipment"),
        ("location",     "Location"),
        ("panel",        "Panel"),
        ("drawing",      "Drawing"),
        ("drawing_rev",  "Drawing Rev"),
        ("drawing_url",  "Drawing URL"),
        ("drawing_cell", "Drawing Cell"),
        ("notes",        "Notes"),
    ]


# ──────────────────────────────────────────────────────────────────
# Job dialog
# ──────────────────────────────────────────────────────────────────

class JobDialog(tk.Toplevel):
    TYPE_COLOR = {
        "REMOVE": "#c0392b", "ADD": "#27ae60", "MOVE": "#2980b9",
        "BLOCK":  "#d35400", "UNBLOCK": "#16a085",
    }

    def __init__(self, parent, job_type, existing=None, registry=None):
        super().__init__(parent)
        self.title(f"{'Edit' if existing else 'Add'} — {job_type}")
        self.result = None
        self.job_type = job_type
        self.registry = registry if registry is not None else {}
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
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        self._fill_form(inner, existing)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(btn_row, text="Save",   command=self._save).pack(side="right", padx=2)

        self.geometry("600x520" if self.job_type in ("BLOCK", "UNBLOCK") else "960x640")

    def _section_label(self, parent, row, text, color):
        ttk.Separator(parent, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        tk.Label(parent, text=text, foreground=color,
                 font=("", 10, "bold"), bg="#f0f0f0").grid(
            row=row + 1, column=0, columnspan=2, pady=(0, 4))

    def _fill_form(self, f, existing):
        row = 0
        ex = existing or {}
        color = self.TYPE_COLOR[self.job_type]

        ttk.Label(f, text="Description:", font=("", 10, "bold")).grid(row=row, column=0, sticky="w")
        row += 1
        self.desc_var = tk.StringVar(value=ex.get("description", ""))
        ttk.Entry(f, textvariable=self.desc_var, width=60).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        row += 1

        if self.job_type in ("REMOVE", "ADD"):
            self._section_label(f, row, f"── {self.job_type} WIRE ──", color)
            row += 2
            self.ep_start = EndpointFrame(f, "Start Point / Device", registry=self.registry)
            self.ep_start.grid(row=row, column=0, sticky="nsew", padx=(0, 4), pady=2)
            self.ep_start.set(ex.get("start", {}))
            self.ep_end = EndpointFrame(f, "End Point / Device", registry=self.registry)
            self.ep_end.grid(row=row, column=1, sticky="nsew", padx=(4, 0), pady=2)
            self.ep_end.set(ex.get("end", {}))
            row += 1
            ttk.Label(f, text="Wire Label / ID:").grid(
                row=row, column=0, sticky="e", padx=(0, 6), pady=(6, 2))
            self.wire_var = tk.StringVar(value=ex.get("wire", ""))
            ttk.Entry(f, textvariable=self.wire_var, width=30).grid(
                row=row, column=1, sticky="w", pady=(6, 2))

        elif self.job_type == "MOVE":
            self._section_label(f, row, "── REMOVE (Wire Being Moved) ──", "#c0392b")
            row += 2
            self.ep_rem_start = EndpointFrame(f, "Remove: Start Point / Device", registry=self.registry)
            self.ep_rem_start.grid(row=row, column=0, sticky="nsew", padx=(0, 4), pady=2)
            self.ep_rem_start.set(ex.get("start", {}))
            self.ep_rem_end = EndpointFrame(f, "Remove: End Point / Device", registry=self.registry)
            self.ep_rem_end.grid(row=row, column=1, sticky="nsew", padx=(4, 0), pady=2)
            self.ep_rem_end.set(ex.get("end", {}))
            row += 1
            ttk.Label(f, text="Wire Label / ID (Remove):").grid(
                row=row, column=0, sticky="e", padx=(0, 6), pady=(6, 2))
            self.wire_var = tk.StringVar(value=ex.get("wire", ""))
            ttk.Entry(f, textvariable=self.wire_var, width=30).grid(
                row=row, column=1, sticky="w", pady=(6, 2))
            row += 1
            self._section_label(f, row, "── ADD (New Wire Location) ──", "#27ae60")
            row += 2
            self.ep_add_start = EndpointFrame(f, "Add: Start Point / Device", registry=self.registry)
            self.ep_add_start.grid(row=row, column=0, sticky="nsew", padx=(0, 4), pady=2)
            self.ep_add_start.set(ex.get("add_start", {}))
            self.ep_add_end = EndpointFrame(f, "Add: End Point / Device", registry=self.registry)
            self.ep_add_end.grid(row=row, column=1, sticky="nsew", padx=(4, 0), pady=2)
            self.ep_add_end.set(ex.get("add_end", {}))
            row += 1
            ttk.Label(f, text="Wire Label / ID (Add):").grid(
                row=row, column=0, sticky="e", padx=(0, 6), pady=(6, 2))
            self.add_wire_var = tk.StringVar(value=ex.get("add_wire", ""))
            ttk.Entry(f, textvariable=self.add_wire_var, width=30).grid(
                row=row, column=1, sticky="w", pady=(6, 2))

        elif self.job_type in ("BLOCK", "UNBLOCK"):
            label = "BLOCK PROTECTION" if self.job_type == "BLOCK" else "UNBLOCK PROTECTION"
            self._section_label(f, row, f"── {label} ──", color)
            row += 2
            self.ep_prot = ProtectionFrame(f, "Equipment / Device", registry=self.registry)
            self.ep_prot.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
            self.ep_prot.set(ex.get("protection", {}))

        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

    def _save(self):
        job = {"type": self.job_type, "description": self.desc_var.get().strip()}
        if self.job_type in ("REMOVE", "ADD"):
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
        elif self.job_type in ("BLOCK", "UNBLOCK"):
            job["protection"] = self.ep_prot.get()
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
        fields = [("name", "Drawing Name / No.:"), ("rev", "Revision:"),
                  ("url", "Drawing URL:"), ("notes", "Notes:")]
        self.vars = {}
        for row, (key, label) in enumerate(fields):
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="e", padx=(0, 6), pady=4)
            var = tk.StringVar(value=ex.get(key, ""))
            self.vars[key] = var
            ttk.Entry(f, textvariable=var, width=46).grid(row=row, column=1, sticky="ew", pady=4)
        f.columnconfigure(1, weight=1)
        br = ttk.Frame(self)
        br.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(br, text="Cancel", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(br, text="Save",   command=self._save).pack(side="right", padx=2)
        self.geometry("460x230")

    def _save(self):
        name = self.vars["name"].get().strip()
        if not name:
            messagebox.showwarning("Required", "Drawing name is required.", parent=self)
            return
        self.result = {
            "name": name, "old_name": self._old_name,
            "rev":  self.vars["rev"].get().strip(),
            "url":  self.vars["url"].get().strip(),
            "notes": self.vars["notes"].get().strip(),
        }
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Report formatting
# ──────────────────────────────────────────────────────────────────

W = 62

def _bar(char="="):
    return char * W

def _ep_block(ep, label):
    lines = [f"  {label}"]
    for key, disp in [
        ("device",       "  Device      "), ("location",    "  Location    "),
        ("pin",          "  Pin         "), ("panel",       "  Panel       "),
        ("drawing",      "  Drawing     "), ("drawing_rev", "  Drawing Rev "),
        ("drawing_url",  "  Drawing URL "), ("drawing_cell","  Drawing Cell"),
    ]:
        lines.append(f"    {disp}: {ep.get(key,'')}")
    return "\n".join(lines)

def _prot_block(prot, label):
    lines = [f"  {label}"]
    for key, disp in [
        ("equipment",    "  Equipment   "), ("location",    "  Location    "),
        ("panel",        "  Panel       "), ("drawing",     "  Drawing     "),
        ("drawing_rev",  "  Drawing Rev "), ("drawing_url", "  Drawing URL "),
        ("drawing_cell", "  Drawing Cell"), ("notes",       "  Notes       "),
    ]:
        lines.append(f"    {disp}: {prot.get(key,'')}")
    return "\n".join(lines)

def format_job(index, job):
    jtype = job["type"]
    labels = {"REMOVE":"REMOVE WIRE","ADD":"ADD WIRE","MOVE":"MOVE WIRE",
              "BLOCK":"BLOCK PROTECTION","UNBLOCK":"UNBLOCK PROTECTION"}
    lines = [_bar(), f"  JOB #{index+1}   [{labels.get(jtype,jtype)}]", _bar()]
    desc = job.get("description","")
    if desc:
        lines += ["", "  DESCRIPTION", f"    {desc}"]

    if jtype in ("REMOVE","ADD"):
        lines += ["", _ep_block(job.get("start",{}),"START POINT / DEVICE"),
                  "", f"  WIRE: {job.get('wire','')}",
                  "", _ep_block(job.get("end",{}),"END POINT / DEVICE")]
    elif jtype == "MOVE":
        lines += ["", "  "+"─"*30+"  REMOVE  "+"─"*(W-42),
                  "", _ep_block(job.get("start",{}),"REMOVE: Start Point / Device"),
                  "", f"  WIRE (Remove): {job.get('wire','')}",
                  "", _ep_block(job.get("end",{}),"REMOVE: End Point / Device"),
                  "", "  "+"─"*31+"  ADD  "+"─"*(W-39),
                  "", _ep_block(job.get("add_start",{}),"ADD: Start Point / Device"),
                  "", f"  WIRE (Add):    {job.get('add_wire','')}",
                  "", _ep_block(job.get("add_end",{}),"ADD: End Point / Device")]
    elif jtype in ("BLOCK","UNBLOCK"):
        lbl = "BLOCK PROTECTION" if jtype=="BLOCK" else "UNBLOCK PROTECTION"
        lines += ["", _prot_block(job.get("protection",{}), lbl)]

    lines.append("")
    return "\n".join(lines)

def generate_report(jobs, project="", drawing_registry=None):
    now = datetime.now().strftime("%Y-%m-%d  %H:%M")
    title = "WIRE WORK PLAN" + (f"  —  {project}" if project else "")
    counts = {}
    for j in jobs:
        counts[j["type"]] = counts.get(j["type"],0) + 1
    summary = "  ".join(f"{v} {k}" for k,v in counts.items())

    parts = [_bar("*"), title.center(W), f"Generated: {now}".center(W), _bar("*"), "",
             f"  Total Jobs : {len(jobs)}", f"  Breakdown  : {summary}", ""]

    if drawing_registry:
        parts += [_bar("-"), "  PROJECT DRAWINGS".center(W), _bar("-"), ""]
        for name in sorted(drawing_registry.keys()):
            info = drawing_registry[name]
            rev_str = f"  Rev: {info['rev']}" if info.get("rev") else ""
            parts.append(f"  {name}{rev_str}")
            if info.get("url"):
                parts.append(f"    URL:   {info['url']}")
            if info.get("notes"):
                parts.append(f"    Notes: {info['notes']}")
        parts.append("")

    return "\n".join(parts) + "\n".join(format_job(i,j) for i,j in enumerate(jobs))


# ──────────────────────────────────────────────────────────────────
# Table (CSV) export
# ──────────────────────────────────────────────────────────────────

def _ep_summary(ep):
    """One-line summary of an endpoint for the table."""
    parts = []
    if ep.get("device"):
        parts.append(ep["device"])
    if ep.get("pin"):
        parts.append(f"Pin {ep['pin']}")
    if ep.get("location"):
        parts.append(ep["location"])
    if ep.get("panel"):
        parts.append(f"Panel {ep['panel']}")
    if ep.get("drawing"):
        rev = f" Rev{ep['drawing_rev']}" if ep.get("drawing_rev") else ""
        cell = f" [{ep['drawing_cell']}]" if ep.get("drawing_cell") else ""
        parts.append(f"Dwg {ep['drawing']}{rev}{cell}")
    return "  |  ".join(parts)


def _prot_summary(prot):
    parts = []
    if prot.get("equipment"):
        parts.append(prot["equipment"])
    if prot.get("location"):
        parts.append(prot["location"])
    if prot.get("panel"):
        parts.append(f"Panel {prot['panel']}")
    if prot.get("drawing"):
        rev = f" Rev{prot['drawing_rev']}" if prot.get("drawing_rev") else ""
        parts.append(f"Dwg {prot['drawing']}{rev}")
    if prot.get("notes"):
        parts.append(prot["notes"])
    return "  |  ".join(parts)


def generate_table(jobs, project="", drawing_registry=None):
    """Generate a fixed-width table: Seq | Type | Description | Start | Wire | End"""
    now = datetime.now().strftime("%Y-%m-%d  %H:%M")
    title = "WIRE WORK PLAN  —  TABLE FORMAT"
    if project:
        title += f"  —  {project}"

    # Column widths
    CW = {"seq": 4, "type": 14, "desc": 28, "start": 36, "wire": 18, "end": 36}
    SEP = "  "

    def pad(text, width):
        text = str(text)
        return text[:width].ljust(width)

    def row(*cells):
        keys = list(CW.keys())
        return SEP.join(pad(c, CW[keys[i]]) for i, c in enumerate(cells))

    divider = "-" * (sum(CW.values()) + len(SEP) * (len(CW) - 1))
    header_row = row("#", "TYPE", "DESCRIPTION", "START POINT / DEVICE", "WIRE", "END POINT / DEVICE")

    lines = [
        "=" * len(divider),
        title.center(len(divider)),
        f"Generated: {now}".center(len(divider)),
        "=" * len(divider),
        "",
    ]

    if drawing_registry:
        lines += ["PROJECT DRAWINGS", "-" * 40]
        for name in sorted(drawing_registry.keys()):
            info = drawing_registry[name]
            rev_str = f"  Rev: {info['rev']}" if info.get("rev") else ""
            lines.append(f"  {name}{rev_str}")
            if info.get("url"):
                lines.append(f"    URL: {info['url']}")
        lines += ["", ""]

    lines += [header_row, divider]

    type_labels = {"REMOVE":"REMOVE WIRE","ADD":"ADD WIRE","MOVE":"MOVE WIRE",
                   "BLOCK":"BLOCK PROT.","UNBLOCK":"UNBLOCK PROT."}

    seq = 1
    for job in jobs:
        jtype = job["type"]
        tlabel = type_labels.get(jtype, jtype)
        desc = job.get("description","")

        if jtype in ("REMOVE","ADD"):
            lines.append(row(seq, tlabel, desc,
                             _ep_summary(job.get("start",{})),
                             job.get("wire",""),
                             _ep_summary(job.get("end",{}))))
            seq += 1

        elif jtype == "MOVE":
            lines.append(row(seq, "MOVE — REMOVE", desc,
                             _ep_summary(job.get("start",{})),
                             job.get("wire",""),
                             _ep_summary(job.get("end",{}))))
            seq += 1
            lines.append(row(seq, "MOVE — ADD", desc,
                             _ep_summary(job.get("add_start",{})),
                             job.get("add_wire",""),
                             _ep_summary(job.get("add_end",{}))))
            seq += 1

        elif jtype in ("BLOCK","UNBLOCK"):
            prot_txt = _prot_summary(job.get("protection",{}))
            lines.append(row(seq, tlabel, desc, prot_txt, "", ""))
            seq += 1

        lines.append(divider)

    return "\n".join(lines)


def generate_csv(jobs, project="", drawing_registry=None):
    """Generate a proper CSV for Excel/Sheets."""
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)

    w.writerow(["Wire Work Plan", project, "", "", "", ""])
    w.writerow([])
    w.writerow(["#","Type","Description","Start Point / Device","Wire","End Point / Device"])

    seq = 1
    for job in jobs:
        jtype = job["type"]
        desc = job.get("description","")
        type_labels = {"REMOVE":"REMOVE WIRE","ADD":"ADD WIRE","MOVE":"MOVE WIRE",
                       "BLOCK":"BLOCK PROTECTION","UNBLOCK":"UNBLOCK PROTECTION"}
        tlabel = type_labels.get(jtype, jtype)

        if jtype in ("REMOVE","ADD"):
            w.writerow([seq, tlabel, desc,
                        _ep_summary(job.get("start",{})),
                        job.get("wire",""),
                        _ep_summary(job.get("end",{}))])
            seq += 1
        elif jtype == "MOVE":
            w.writerow([seq, "MOVE — REMOVE", desc,
                        _ep_summary(job.get("start",{})),
                        job.get("wire",""),
                        _ep_summary(job.get("end",{}))])
            seq += 1
            w.writerow([seq, "MOVE — ADD", desc,
                        _ep_summary(job.get("add_start",{})),
                        job.get("add_wire",""),
                        _ep_summary(job.get("add_end",{}))])
            seq += 1
        elif jtype in ("BLOCK","UNBLOCK"):
            w.writerow([seq, tlabel, desc, _prot_summary(job.get("protection",{})), "", ""])
            seq += 1

    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────
# Main application
# ──────────────────────────────────────────────────────────────────

class WirePlannerApp(tk.Tk):
    TYPE_FG = {"REMOVE":"#c0392b","ADD":"#1a7a3c","MOVE":"#1a5a99",
               "BLOCK":"#d35400","UNBLOCK":"#16a085"}

    def __init__(self):
        super().__init__()
        self.title("Wire Work Planner")
        self.geometry("1080x720")
        self.jobs = []
        self.drawing_registry = {}
        self.current_file = None
        self._build_menu()
        self._build_ui()

    def _build_menu(self):
        mb = tk.Menu(self)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label="New",            command=self._new_plan,      accelerator="Ctrl+N")
        fm.add_command(label="Open…",          command=self._open,          accelerator="Ctrl+O")
        fm.add_command(label="Save",           command=self._save,          accelerator="Ctrl+S")
        fm.add_command(label="Save As…",       command=self._save_as)
        fm.add_separator()
        fm.add_command(label="Export Report (detailed)…", command=self._export_report, accelerator="Ctrl+E")
        fm.add_command(label="Export Table (text)…",     command=self._export_table)
        fm.add_command(label="Export CSV (Excel)…",      command=self._export_csv)
        fm.add_separator()
        fm.add_command(label="Quit",           command=self.quit,           accelerator="Ctrl+Q")
        mb.add_cascade(label="File", menu=fm)
        self.config(menu=mb)
        self.bind("<Control-n>", lambda _: self._new_plan())
        self.bind("<Control-o>", lambda _: self._open())
        self.bind("<Control-s>", lambda _: self._save())
        self.bind("<Control-e>", lambda _: self._export_report())
        self.bind("<Control-q>", lambda _: self.quit())

    def _build_ui(self):
        # Row 1 toolbar
        tb1 = ttk.Frame(self, padding=(6,4))
        tb1.pack(fill="x")
        ttk.Label(tb1, text="Project:").pack(side="left")
        self.project_var = tk.StringVar()
        ttk.Entry(tb1, textvariable=self.project_var, width=22).pack(side="left", padx=(4,10))
        for label, jtype, color in [
            ("+ Remove",  "REMOVE",  "#c0392b"),
            ("+ Add",     "ADD",     "#27ae60"),
            ("+ Move",    "MOVE",    "#2980b9"),
            ("+ Block",   "BLOCK",   "#d35400"),
            ("+ Unblock", "UNBLOCK", "#16a085"),
        ]:
            tk.Button(tb1, text=label, fg="white", bg=color, relief="flat",
                      padx=7, pady=3, cursor="hand2",
                      command=lambda t=jtype: self._add_job(t)).pack(side="left", padx=2)
        rf = ttk.Frame(tb1)
        rf.pack(side="right")
        for label, cmd in [("↑ Up",self._move_up),("↓ Down",self._move_down),
                            ("Edit",self._edit_job),("Duplicate",self._duplicate_job),
                            ("Delete",self._delete_job)]:
            ttk.Button(rf, text=label, command=cmd).pack(side="left", padx=2)

        # Row 2 toolbar
        tb2 = ttk.Frame(self, padding=(6,0,6,4))
        tb2.pack(fill="x")
        tk.Button(tb2, text="Combine 2 → Move", fg="white", bg="#7d3c98", relief="flat",
                  padx=7, pady=3, cursor="hand2", command=self._combine_jobs).pack(side="left", padx=2)
        ttk.Label(tb2, text="(Ctrl+click 2 jobs)", foreground="grey").pack(side="left", padx=(0,12))
        tk.Button(tb2, text="Split Move → 2 Jobs", fg="white", bg="#7f8c8d", relief="flat",
                  padx=7, pady=3, cursor="hand2", command=self._split_job).pack(side="left", padx=2)
        ttk.Button(tb2, text="Export CSV",     command=self._export_csv).pack(side="right", padx=2)
        ttk.Button(tb2, text="Export Table",   command=self._export_table).pack(side="right", padx=2)
        ttk.Button(tb2, text="Export Report",  command=self._export_report).pack(side="right", padx=2)
        ttk.Button(tb2, text="Preview",        command=self._preview_report).pack(side="right", padx=2)

        # Notebook
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=(0,4))
        work_tab = ttk.Frame(nb)
        nb.add(work_tab, text="  Work Order  ")
        self._build_work_tab(work_tab)
        draw_tab = ttk.Frame(nb)
        nb.add(draw_tab, text="  Project Drawings  ")
        self._build_drawings_tab(draw_tab)

        # Status bar
        self.status_var = tk.StringVar(value="Ready  —  no jobs loaded")
        ttk.Label(self, textvariable=self.status_var, relief="sunken",
                  anchor="w", padding=(4,1)).pack(fill="x", side="bottom")

    def _build_work_tab(self, parent):
        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=4, pady=4)

        lf = ttk.LabelFrame(pw, text="Work Order", padding=4)
        pw.add(lf, weight=1)
        cols = ("Seq","Type","Description")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("Seq",         text="#")
        self.tree.heading("Type",        text="Type")
        self.tree.heading("Description", text="Description")
        self.tree.column("Seq",  width=35,  stretch=False)
        self.tree.column("Type", width=110, stretch=False)
        self.tree.column("Description", width=230)
        for t, fg in self.TYPE_FG.items():
            self.tree.tag_configure(t, foreground=fg)
        vsb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _: self._edit_job())

        pf = ttk.LabelFrame(pw, text="Job Preview", padding=4)
        pw.add(pf, weight=2)
        self.preview = scrolledtext.ScrolledText(pf, font=("Courier",9), state="disabled", wrap="none")
        self.preview.pack(fill="both", expand=True)

    def _build_drawings_tab(self, parent):
        tb = ttk.Frame(parent, padding=(4,4))
        tb.pack(fill="x")
        ttk.Button(tb, text="+ Add Drawing",  command=self._add_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Edit",           command=self._edit_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Delete",         command=self._delete_drawing).pack(side="left", padx=2)
        ttk.Button(tb, text="Scan Jobs →",    command=self._scan_and_refresh).pack(side="left", padx=(12,2))
        ttk.Label(tb, text="Drawing names entered in jobs are added automatically for autofill.",
                  foreground="grey").pack(side="left", padx=8)

        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, padx=4, pady=(0,4))
        cols = ("Drawing","Revision","URL","Notes")
        self.drawings_tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.drawings_tree.heading("Drawing",  text="Drawing")
        self.drawings_tree.heading("Revision", text="Revision")
        self.drawings_tree.heading("URL",      text="Drawing URL")
        self.drawings_tree.heading("Notes",    text="Notes")
        self.drawings_tree.column("Drawing",  width=160, stretch=False)
        self.drawings_tree.column("Revision", width=80,  stretch=False)
        self.drawings_tree.column("URL",      width=380)
        self.drawings_tree.column("Notes",    width=200)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.drawings_tree.yview)
        self.drawings_tree.configure(yscrollcommand=vsb.set)
        self.drawings_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.drawings_tree.bind("<Double-1>", lambda _: self._edit_drawing())

    # ── Drawing registry CRUD ─────────────────────────────────────

    def _refresh_drawings_list(self):
        for iid in self.drawings_tree.get_children():
            self.drawings_tree.delete(iid)
        for name in sorted(self.drawing_registry.keys()):
            info = self.drawing_registry[name]
            self.drawings_tree.insert("", "end", iid=name,
                values=(name, info.get("rev",""), info.get("url",""), info.get("notes","")))

    def _add_drawing(self):
        dlg = DrawingEditDialog(self)
        if dlg.result:
            name = dlg.result["name"]
            self.drawing_registry[name] = {
                "rev": dlg.result["rev"], "url": dlg.result["url"], "notes": dlg.result["notes"]}
            self._refresh_drawings_list()

    def _edit_drawing(self):
        sel = self.drawings_tree.selection()
        if not sel:
            messagebox.showinfo("Select", "Please select a drawing to edit.")
            return
        name = sel[0]
        info = self.drawing_registry.get(name, {})
        dlg = DrawingEditDialog(self, existing={"name": name, **info})
        if dlg.result:
            old = dlg.result.get("old_name")
            new_name = dlg.result["name"]
            if old and old != new_name and old in self.drawing_registry:
                del self.drawing_registry[old]
            self.drawing_registry[new_name] = {
                "rev": dlg.result["rev"], "url": dlg.result["url"], "notes": dlg.result["notes"]}
            self._refresh_drawings_list()

    def _delete_drawing(self):
        sel = self.drawings_tree.selection()
        if not sel:
            messagebox.showinfo("Select", "Please select a drawing to delete.")
            return
        name = sel[0]
        if messagebox.askyesno("Delete Drawing", f"Remove '{name}' from the drawing registry?"):
            self.drawing_registry.pop(name, None)
            self._refresh_drawings_list()

    def _scan_jobs_for_drawings(self):
        for job in self.jobs:
            for ep_key in ("start","end","add_start","add_end","protection"):
                ep = job.get(ep_key)
                if not ep:
                    continue
                name = ep.get("drawing","").strip()
                if not name:
                    continue
                if name not in self.drawing_registry:
                    self.drawing_registry[name] = {"rev":"","url":"","notes":""}
                rec = self.drawing_registry[name]
                rev = ep.get("drawing_rev","").strip()
                url = ep.get("drawing_url","").strip()
                if rev and not rec.get("rev"):
                    rec["rev"] = rev
                if url and not rec.get("url"):
                    rec["url"] = url

    def _scan_and_refresh(self):
        self._scan_jobs_for_drawings()
        self._refresh_drawings_list()
        self.status_var.set(f"Drawing registry updated — {len(self.drawing_registry)} drawing(s).")

    # ── Job list ─────────────────────────────────────────────────

    def _refresh_list(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        disp = {"REMOVE":"REMOVE","ADD":"ADD","MOVE":"MOVE",
                "BLOCK":"BLOCK PROT.","UNBLOCK":"UNBLOCK PROT."}
        for i, job in enumerate(self.jobs):
            self.tree.insert("","end", iid=str(i),
                values=(i+1, disp.get(job["type"],job["type"]), job.get("description","")),
                tags=(job["type"],))
        self._update_status()

    def _update_status(self):
        n = len(self.jobs)
        if n == 0:
            self.status_var.set("No jobs")
            return
        counts = {}
        for j in self.jobs:
            counts[j["type"]] = counts.get(j["type"],0) + 1
        parts = "  |  ".join(f"{v} {k}" for k,v in counts.items())
        fname = os.path.basename(self.current_file) if self.current_file else "unsaved"
        self.status_var.set(f"{fname}    {n} job(s):  {parts}")

    def _selected_indices(self):
        return sorted(int(iid) for iid in self.tree.selection())

    def _selected_idx(self):
        idxs = self._selected_indices()
        return idxs[0] if idxs else None

    def _on_select(self, _=None):
        idxs = self._selected_indices()
        if not idxs:
            return
        text = format_job(idxs[0], self.jobs[idxs[0]])
        self.preview.configure(state="normal")
        self.preview.delete("1.0","end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    # ── CRUD ─────────────────────────────────────────────────────

    def _add_job(self, job_type):
        dlg = JobDialog(self, job_type, registry=self.drawing_registry)
        if dlg.result:
            self.jobs.append(dlg.result)
            self._refresh_list()
            self._refresh_drawings_list()
            idx = len(self.jobs) - 1
            self.tree.selection_set(str(idx))
            self._on_select()

    def _edit_job(self):
        idx = self._selected_idx()
        if idx is None:
            messagebox.showinfo("Select a Job","Please select a job from the list.")
            return
        dlg = JobDialog(self, self.jobs[idx]["type"],
                        existing=deepcopy(self.jobs[idx]), registry=self.drawing_registry)
        if dlg.result:
            self.jobs[idx] = dlg.result
            self._refresh_list()
            self._refresh_drawings_list()
            self.tree.selection_set(str(idx))
            self._on_select()

    def _duplicate_job(self):
        idx = self._selected_idx()
        if idx is None:
            messagebox.showinfo("Select a Job","Please select a job to duplicate.")
            return
        copy = deepcopy(self.jobs[idx])
        desc = copy.get("description","")
        copy["description"] = f"{desc} (copy)" if desc else "(copy)"
        self.jobs.insert(idx+1, copy)
        self._refresh_list()
        self.tree.selection_set(str(idx+1))
        self._on_select()

    def _delete_job(self):
        idxs = self._selected_indices()
        if not idxs:
            messagebox.showinfo("Select a Job","Please select a job to delete.")
            return
        msg = f"Delete {len(idxs)} selected jobs?" if len(idxs)>1 else f"Delete Job #{idxs[0]+1}?"
        if messagebox.askyesno("Delete", msg):
            for idx in reversed(idxs):
                self.jobs.pop(idx)
            self._refresh_list()
            self.preview.configure(state="normal")
            self.preview.delete("1.0","end")
            self.preview.configure(state="disabled")

    def _move_up(self):
        idx = self._selected_idx()
        if idx is None or idx == 0:
            return
        self.jobs[idx-1], self.jobs[idx] = self.jobs[idx], self.jobs[idx-1]
        self._refresh_list()
        self.tree.selection_set(str(idx-1))
        self._on_select()

    def _move_down(self):
        idx = self._selected_idx()
        if idx is None or idx >= len(self.jobs)-1:
            return
        self.jobs[idx], self.jobs[idx+1] = self.jobs[idx+1], self.jobs[idx]
        self._refresh_list()
        self.tree.selection_set(str(idx+1))
        self._on_select()

    # ── Combine / Split ──────────────────────────────────────────

    def _combine_jobs(self):
        idxs = self._selected_indices()
        if len(idxs) != 2:
            messagebox.showinfo("Combine → Move",
                "Hold Ctrl and click exactly 2 jobs, then press Combine.")
            return
        ja, jb = self.jobs[idxs[0]], self.jobs[idxs[1]]
        if ja["type"] in ("MOVE","BLOCK","UNBLOCK") or jb["type"] in ("MOVE","BLOCK","UNBLOCK"):
            messagebox.showwarning("Combine → Move",
                "Only REMOVE and ADD jobs can be combined.")
            return
        if ja["type"] == "ADD" and jb["type"] == "REMOVE":
            ja, jb = jb, ja
        combined_desc = " / ".join(filter(None,[ja.get("description",""),jb.get("description","")]))
        move_job = {
            "type":"MOVE","description":combined_desc,
            "start":deepcopy(ja.get("start",empty_endpoint())),
            "wire":ja.get("wire",""),
            "end":deepcopy(ja.get("end",empty_endpoint())),
            "add_start":deepcopy(jb.get("start",empty_endpoint())),
            "add_wire":jb.get("wire",""),
            "add_end":deepcopy(jb.get("end",empty_endpoint())),
        }
        insert_at = idxs[0]
        for idx in reversed(idxs):
            self.jobs.pop(idx)
        self.jobs.insert(insert_at, move_job)
        self._refresh_list()
        self.tree.selection_set(str(insert_at))
        self._on_select()
        self.status_var.set(f"Jobs #{idxs[0]+1} and #{idxs[1]+1} combined into MOVE #{insert_at+1}.")

    def _split_job(self):
        idx = self._selected_idx()
        if idx is None:
            messagebox.showinfo("Split","Select a MOVE job to split.")
            return
        job = self.jobs[idx]
        if job["type"] != "MOVE":
            messagebox.showinfo("Split","Only MOVE jobs can be split.")
            return
        desc = job.get("description","")
        remove_job = {"type":"REMOVE","description":desc,
                      "start":deepcopy(job.get("start",empty_endpoint())),
                      "wire":job.get("wire",""),
                      "end":deepcopy(job.get("end",empty_endpoint()))}
        add_job = {"type":"ADD","description":desc,
                   "start":deepcopy(job.get("add_start",empty_endpoint())),
                   "wire":job.get("add_wire",""),
                   "end":deepcopy(job.get("add_end",empty_endpoint()))}
        self.jobs.pop(idx)
        self.jobs.insert(idx, add_job)
        self.jobs.insert(idx, remove_job)
        self._refresh_list()
        self.tree.selection_set(str(idx))
        self._on_select()
        self.status_var.set(f"MOVE split into REMOVE #{idx+1} and ADD #{idx+2}.")

    # ── Report ────────────────────────────────────────────────────

    def _preview_report(self):
        if not self.jobs:
            messagebox.showinfo("No Jobs","Add at least one job before previewing.")
            return
        report = generate_report(self.jobs, self.project_var.get().strip(), self.drawing_registry)
        win = tk.Toplevel(self)
        win.title("Report Preview")
        win.geometry("740x720")
        txt = scrolledtext.ScrolledText(win, font=("Courier",9), wrap="none")
        txt.pack(fill="both", expand=True, padx=6, pady=6)
        txt.insert("1.0", report)
        txt.configure(state="disabled")
        bf = ttk.Frame(win)
        bf.pack(pady=(0,8))
        ttk.Button(bf, text="Export to File…", command=self._export_report).pack(side="left", padx=4)
        ttk.Button(bf, text="Close", command=win.destroy).pack(side="left", padx=4)

    def _export_report(self):
        if not self.jobs:
            messagebox.showinfo("No Jobs","Add at least one job before exporting.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files","*.txt"),("All files","*.*")],
            initialfile=f"wire_work_plan_{datetime.now().strftime('%Y%m%d')}.txt")
        if not path:
            return
        report = generate_report(self.jobs, self.project_var.get().strip(), self.drawing_registry)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report)
        self.status_var.set(f"Exported → {path}")
        if messagebox.askyesno("Exported", f"Saved to:\n{path}\n\nOpen the file now?"):
            _open_file(path)

    def _export_table(self):
        if not self.jobs:
            messagebox.showinfo("No Jobs","Add at least one job before exporting.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files","*.txt"),("All files","*.*")],
            initialfile=f"wire_table_{datetime.now().strftime('%Y%m%d')}.txt")
        if not path:
            return
        content = generate_table(self.jobs, self.project_var.get().strip(), self.drawing_registry)
        with open(path,"w",encoding="utf-8") as fh:
            fh.write(content)
        self.status_var.set(f"Table exported → {path}")
        if messagebox.askyesno("Exported", f"Saved to:\n{path}\n\nOpen the file now?"):
            _open_file(path)

    def _export_csv(self):
        if not self.jobs:
            messagebox.showinfo("No Jobs","Add at least one job before exporting.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files","*.csv"),("All files","*.*")],
            initialfile=f"wire_table_{datetime.now().strftime('%Y%m%d')}.csv")
        if not path:
            return
        content = generate_csv(self.jobs, self.project_var.get().strip(), self.drawing_registry)
        with open(path,"w",encoding="utf-8",newline="") as fh:
            fh.write(content)
        self.status_var.set(f"CSV exported → {path}")
        if messagebox.askyesno("Exported", f"Saved to:\n{path}\n\nOpen the file now?"):
            _open_file(path)

    # ── File I/O ─────────────────────────────────────────────────

    def _new_plan(self):
        if self.jobs and not messagebox.askyesno("New Plan","Discard current plan and start fresh?"):
            return
        self.jobs = []
        self.drawing_registry = {}
        self.current_file = None
        self.project_var.set("")
        self._refresh_list()
        self._refresh_drawings_list()
        self.preview.configure(state="normal")
        self.preview.delete("1.0","end")
        self.preview.configure(state="disabled")

    def _open(self):
        path = filedialog.askopenfilename(
            filetypes=[("Wire Plan","*.wirePlan"),("JSON","*.json"),("All","*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.jobs = data.get("jobs",[])
            self.project_var.set(data.get("project",""))
            self.drawing_registry = data.get("drawing_registry",{})
            self.current_file = path
            self._scan_jobs_for_drawings()
            self._refresh_list()
            self._refresh_drawings_list()
        except Exception as exc:
            messagebox.showerror("Open Error", str(exc))

    def _save(self):
        if not self.current_file:
            self._save_as()
        else:
            self._write(self.current_file)

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".wirePlan",
            filetypes=[("Wire Plan","*.wirePlan"),("JSON","*.json"),("All","*.*")])
        if path:
            self.current_file = path
            self._write(path)

    def _write(self, path):
        try:
            payload = {"project": self.project_var.get().strip(),
                       "drawing_registry": self.drawing_registry,
                       "jobs": self.jobs}
            with open(path,"w",encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            self._update_status()
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc))


# ──────────────────────────────────────────────────────────────────

def _open_file(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.call(["open", path])
        else:
            subprocess.call(["xdg-open", path])
    except Exception:
        pass


if __name__ == "__main__":
    app = WirePlannerApp()
    app.mainloop()
