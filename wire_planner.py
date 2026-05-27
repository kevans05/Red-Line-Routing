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
        "device": "",
        "location": "",
        "pin": "",
        "panel": "",
        "drawing": "",
        "drawing_url": "",
        "drawing_cell": "",
    }


def empty_job(job_type="REMOVE"):
    job = {
        "type": job_type,
        "description": "",
        "wire": "",          # wire label / ID linking the two endpoints
        "start": empty_endpoint(),
        "end": empty_endpoint(),
    }
    if job_type == "MOVE":
        job["add_wire"] = ""
        job["add_start"] = empty_endpoint()
        job["add_end"] = empty_endpoint()
    return job


# ──────────────────────────────────────────────────────────────────
# Reusable endpoint entry widget
# ──────────────────────────────────────────────────────────────────

class EndpointFrame(ttk.LabelFrame):
    """Compact form for a single device / endpoint."""

    FIELDS = [
        ("device",       "Device"),
        ("location",     "Location"),
        ("pin",          "Pin"),
        ("panel",        "Panel"),
        ("drawing",      "Drawing"),
        ("drawing_url",  "Drawing URL"),
        ("drawing_cell", "Drawing Cell"),
    ]

    def __init__(self, parent, title, **kwargs):
        super().__init__(parent, text=title, padding=6, **kwargs)
        self.vars = {}
        self._build()

    def _build(self):
        for row, (key, label) in enumerate(self.FIELDS):
            ttk.Label(self, text=label + ":").grid(
                row=row, column=0, sticky="e", padx=(0, 4), pady=1
            )
            var = tk.StringVar()
            self.vars[key] = var
            ttk.Entry(self, textvariable=var, width=28).grid(
                row=row, column=1, sticky="ew", pady=1
            )
        self.columnconfigure(1, weight=1)

    def get(self):
        return {k: v.get().strip() for k, v in self.vars.items()}

    def set(self, data):
        for k, v in self.vars.items():
            v.set(data.get(k, ""))


# ──────────────────────────────────────────────────────────────────
# Add / Edit dialog
# ──────────────────────────────────────────────────────────────────

class JobDialog(tk.Toplevel):
    """Modal dialog for creating or editing a wire job."""

    TYPE_COLOR = {"REMOVE": "#c0392b", "ADD": "#27ae60", "MOVE": "#2980b9"}

    def __init__(self, parent, job_type, existing=None):
        super().__init__(parent)
        self.title(f"{'Edit' if existing else 'Add'} — {job_type} WIRE")
        self.result = None
        self.job_type = job_type
        self.resizable(True, True)
        self._build(existing)
        self.grab_set()
        self.wait_window()

    # ── layout ──────────────────────────────────────────────────

    def _build(self, existing):
        # Scrollable inner frame
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
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"),
        )

        self._fill_form(inner, existing)

        # Button row
        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(btn_row, text="Save", command=self._save).pack(side="right", padx=2)

        self.geometry("920x580")

    def _section_label(self, parent, row, text, color):
        ttk.Separator(parent, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(8, 2)
        )
        lbl = tk.Label(
            parent, text=text, foreground=color, font=("", 10, "bold"), bg="#f0f0f0"
        )
        lbl.grid(row=row + 1, column=0, columnspan=2, pady=(0, 4))

    def _wire_row(self, parent, row, label, var):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=(0, 6), pady=2)
        ttk.Entry(parent, textvariable=var, width=40).grid(
            row=row, column=1, sticky="ew", pady=2
        )

    def _fill_form(self, f, existing):
        row = 0
        ex = existing or {}

        # Description
        ttk.Label(f, text="Description:", font=("", 10, "bold")).grid(
            row=row, column=0, sticky="w"
        )
        row += 1
        self.desc_var = tk.StringVar(value=ex.get("description", ""))
        ttk.Entry(f, textvariable=self.desc_var, width=60).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(0, 6)
        )
        row += 1

        color = self.TYPE_COLOR[self.job_type]

        if self.job_type in ("REMOVE", "ADD"):
            self._section_label(f, row, f"── {self.job_type} WIRE ──", color)
            row += 2

            self.ep_start = EndpointFrame(f, "Start Point / Device")
            self.ep_start.grid(row=row, column=0, sticky="nsew", padx=(0, 4), pady=2)
            self.ep_start.set(ex.get("start", {}))

            self.ep_end = EndpointFrame(f, "End Point / Device")
            self.ep_end.grid(row=row, column=1, sticky="nsew", padx=(4, 0), pady=2)
            self.ep_end.set(ex.get("end", {}))
            row += 1

            ttk.Label(f, text="Wire Label / ID:").grid(
                row=row, column=0, sticky="e", padx=(0, 6), pady=(6, 2)
            )
            self.wire_var = tk.StringVar(value=ex.get("wire", ""))
            ttk.Entry(f, textvariable=self.wire_var, width=30).grid(
                row=row, column=1, sticky="w", pady=(6, 2)
            )

        elif self.job_type == "MOVE":
            # ── Remove side ──────────────────────────────────────
            self._section_label(f, row, "── REMOVE (Wire Being Moved) ──", "#c0392b")
            row += 2

            self.ep_rem_start = EndpointFrame(f, "Remove: Start Point / Device")
            self.ep_rem_start.grid(row=row, column=0, sticky="nsew", padx=(0, 4), pady=2)
            self.ep_rem_start.set(ex.get("start", {}))

            self.ep_rem_end = EndpointFrame(f, "Remove: End Point / Device")
            self.ep_rem_end.grid(row=row, column=1, sticky="nsew", padx=(4, 0), pady=2)
            self.ep_rem_end.set(ex.get("end", {}))
            row += 1

            ttk.Label(f, text="Wire Label / ID (Remove):").grid(
                row=row, column=0, sticky="e", padx=(0, 6), pady=(6, 2)
            )
            self.wire_var = tk.StringVar(value=ex.get("wire", ""))
            ttk.Entry(f, textvariable=self.wire_var, width=30).grid(
                row=row, column=1, sticky="w", pady=(6, 2)
            )
            row += 1

            # ── Add side ─────────────────────────────────────────
            self._section_label(f, row, "── ADD (New Wire Location) ──", "#27ae60")
            row += 2

            self.ep_add_start = EndpointFrame(f, "Add: Start Point / Device")
            self.ep_add_start.grid(row=row, column=0, sticky="nsew", padx=(0, 4), pady=2)
            self.ep_add_start.set(ex.get("add_start", {}))

            self.ep_add_end = EndpointFrame(f, "Add: End Point / Device")
            self.ep_add_end.grid(row=row, column=1, sticky="nsew", padx=(4, 0), pady=2)
            self.ep_add_end.set(ex.get("add_end", {}))
            row += 1

            ttk.Label(f, text="Wire Label / ID (Add):").grid(
                row=row, column=0, sticky="e", padx=(0, 6), pady=(6, 2)
            )
            self.add_wire_var = tk.StringVar(value=ex.get("add_wire", ""))
            ttk.Entry(f, textvariable=self.add_wire_var, width=30).grid(
                row=row, column=1, sticky="w", pady=(6, 2)
            )

        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

    # ── save ────────────────────────────────────────────────────

    def _save(self):
        job = {
            "type": self.job_type,
            "description": self.desc_var.get().strip(),
        }
        if self.job_type in ("REMOVE", "ADD"):
            job["start"] = self.ep_start.get()
            job["wire"] = self.wire_var.get().strip()
            job["end"] = self.ep_end.get()
        else:  # MOVE
            job["start"] = self.ep_rem_start.get()
            job["wire"] = self.wire_var.get().strip()
            job["end"] = self.ep_rem_end.get()
            job["add_wire"] = self.add_wire_var.get().strip()
            job["add_start"] = self.ep_add_start.get()
            job["add_end"] = self.ep_add_end.get()
        self.result = job
        self.destroy()


# ──────────────────────────────────────────────────────────────────
# Report formatting
# ──────────────────────────────────────────────────────────────────

W = 62  # line width


def _bar(char="="):
    return char * W


def _ep_block(ep, label):
    lines = [f"  {label}"]
    for key, display in [
        ("device",       "  Device      "),
        ("location",     "  Location    "),
        ("pin",          "  Pin         "),
        ("panel",        "  Panel       "),
        ("drawing",      "  Drawing     "),
        ("drawing_url",  "  Drawing URL "),
        ("drawing_cell", "  Drawing Cell"),
    ]:
        val = ep.get(key, "")
        lines.append(f"    {display}: {val}")
    return "\n".join(lines)


def format_job(index, job):
    lines = [_bar(), f"  JOB #{index + 1}   [{job['type']} WIRE]", _bar()]
    desc = job.get("description", "")
    if desc:
        lines += ["", f"  DESCRIPTION", f"    {desc}"]

    jtype = job["type"]

    if jtype in ("REMOVE", "ADD"):
        lines += ["", _ep_block(job.get("start", {}), "START POINT / DEVICE")]
        wire = job.get("wire", "")
        lines += ["", f"  WIRE: {wire}"]
        lines += ["", _ep_block(job.get("end", {}), "END POINT / DEVICE")]

    elif jtype == "MOVE":
        lines += ["", "  " + "─" * 30 + "  REMOVE  " + "─" * (W - 42)]
        lines += ["", _ep_block(job.get("start", {}), "REMOVE: Start Point / Device")]
        wire = job.get("wire", "")
        lines += ["", f"  WIRE (Remove): {wire}"]
        lines += ["", _ep_block(job.get("end", {}), "REMOVE: End Point / Device")]
        lines += ["", "  " + "─" * 31 + "  ADD  " + "─" * (W - 39)]
        lines += ["", _ep_block(job.get("add_start", {}), "ADD: Start Point / Device")]
        add_wire = job.get("add_wire", "")
        lines += ["", f"  WIRE (Add):    {add_wire}"]
        lines += ["", _ep_block(job.get("add_end", {}), "ADD: End Point / Device")]

    lines.append("")
    return "\n".join(lines)


def generate_report(jobs, project=""):
    now = datetime.now().strftime("%Y-%m-%d  %H:%M")
    title = "WIRE WORK PLAN"
    if project:
        title += f"  —  {project}"

    counts = {}
    for j in jobs:
        counts[j["type"]] = counts.get(j["type"], 0) + 1

    summary = "  ".join(f"{v} {k}" for k, v in counts.items())

    header = "\n".join([
        _bar("*"),
        title.center(W),
        f"Generated: {now}".center(W),
        _bar("*"),
        "",
        f"  Total Jobs : {len(jobs)}",
        f"  Breakdown  : {summary}",
        "",
    ])

    body = "\n".join(format_job(i, j) for i, j in enumerate(jobs))
    return header + body


# ──────────────────────────────────────────────────────────────────
# Main application window
# ──────────────────────────────────────────────────────────────────

class WirePlannerApp(tk.Tk):

    TYPE_FG = {"REMOVE": "#c0392b", "ADD": "#1a7a3c", "MOVE": "#1a5a99"}

    def __init__(self):
        super().__init__()
        self.title("Wire Work Planner")
        self.geometry("1000x620")
        self.jobs = []
        self.current_file = None
        self._build_menu()
        self._build_ui()

    # ── menu ────────────────────────────────────────────────────

    def _build_menu(self):
        mb = tk.Menu(self)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label="New",           command=self._new_plan,      accelerator="Ctrl+N")
        fm.add_command(label="Open…",         command=self._open,          accelerator="Ctrl+O")
        fm.add_command(label="Save",          command=self._save,          accelerator="Ctrl+S")
        fm.add_command(label="Save As…",      command=self._save_as)
        fm.add_separator()
        fm.add_command(label="Export Report…",command=self._export_report, accelerator="Ctrl+E")
        fm.add_separator()
        fm.add_command(label="Quit",          command=self.quit,           accelerator="Ctrl+Q")
        mb.add_cascade(label="File", menu=fm)
        self.config(menu=mb)

        self.bind("<Control-n>", lambda _: self._new_plan())
        self.bind("<Control-o>", lambda _: self._open())
        self.bind("<Control-s>", lambda _: self._save())
        self.bind("<Control-e>", lambda _: self._export_report())
        self.bind("<Control-q>", lambda _: self.quit())

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Toolbar ─────────────────────────────────────────────
        tb = ttk.Frame(self, padding=(6, 4))
        tb.pack(fill="x")

        ttk.Label(tb, text="Project:").pack(side="left")
        self.project_var = tk.StringVar()
        ttk.Entry(tb, textvariable=self.project_var, width=28).pack(side="left", padx=(4, 16))

        for label, jtype, color in [
            ("+ Remove Wire", "REMOVE", "#c0392b"),
            ("+ Add Wire",    "ADD",    "#27ae60"),
            ("+ Move Wire",   "MOVE",   "#2980b9"),
        ]:
            btn = tk.Button(
                tb, text=label, fg="white", bg=color, relief="flat",
                padx=8, pady=3, cursor="hand2",
                command=lambda t=jtype: self._add_job(t),
            )
            btn.pack(side="left", padx=2)

        # Right-side action buttons
        rf = ttk.Frame(tb)
        rf.pack(side="right")
        for label, cmd in [
            ("↑ Up",          self._move_up),
            ("↓ Down",        self._move_down),
            ("Edit",          self._edit_job),
            ("Duplicate",     self._duplicate_job),
            ("Delete",        self._delete_job),
            ("Preview",       self._preview_report),
            ("Export Report", self._export_report),
        ]:
            ttk.Button(rf, text=label, command=cmd).pack(side="left", padx=2)

        # ── Paned area ──────────────────────────────────────────
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        # Left: job list
        lf = ttk.LabelFrame(pw, text="Work Order", padding=4)
        pw.add(lf, weight=1)

        cols = ("Seq", "Type", "Description")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("Seq",         text="#")
        self.tree.heading("Type",        text="Type")
        self.tree.heading("Description", text="Description")
        self.tree.column("Seq",  width=35,  stretch=False)
        self.tree.column("Type", width=80,  stretch=False)
        self.tree.column("Description", width=260)

        for t, fg in self.TYPE_FG.items():
            self.tree.tag_configure(t, foreground=fg)

        vsb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _: self._edit_job())

        # Right: preview
        pf = ttk.LabelFrame(pw, text="Job Preview", padding=4)
        pw.add(pf, weight=2)

        self.preview = scrolledtext.ScrolledText(
            pf, font=("Courier", 9), state="disabled", wrap="none"
        )
        self.preview.pack(fill="both", expand=True)

        # ── Status bar ──────────────────────────────────────────
        self.status_var = tk.StringVar(value="Ready  —  no jobs loaded")
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=(4, 1)
                  ).pack(fill="x", side="bottom")

    # ── List management ─────────────────────────────────────────

    def _refresh_list(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for i, job in enumerate(self.jobs):
            self.tree.insert(
                "", "end", iid=str(i),
                values=(i + 1, job["type"], job.get("description", "")),
                tags=(job["type"],),
            )
        self._update_status()

    def _update_status(self):
        n = len(self.jobs)
        if n == 0:
            self.status_var.set("No jobs")
            return
        counts = {}
        for j in self.jobs:
            counts[j["type"]] = counts.get(j["type"], 0) + 1
        parts = "  |  ".join(f"{v} {k}" for k, v in counts.items())
        fname = os.path.basename(self.current_file) if self.current_file else "unsaved"
        self.status_var.set(f"{fname}    {n} job(s):  {parts}")

    def _selected_idx(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _on_select(self, _=None):
        idx = self._selected_idx()
        if idx is None:
            return
        text = format_job(idx, self.jobs[idx])
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    # ── CRUD ────────────────────────────────────────────────────

    def _add_job(self, job_type):
        dlg = JobDialog(self, job_type)
        if dlg.result:
            self.jobs.append(dlg.result)
            self._refresh_list()
            idx = len(self.jobs) - 1
            self.tree.selection_set(str(idx))
            self._on_select()

    def _edit_job(self):
        idx = self._selected_idx()
        if idx is None:
            messagebox.showinfo("Select a Job", "Please select a job from the list.")
            return
        dlg = JobDialog(self, self.jobs[idx]["type"], existing=deepcopy(self.jobs[idx]))
        if dlg.result:
            self.jobs[idx] = dlg.result
            self._refresh_list()
            self.tree.selection_set(str(idx))
            self._on_select()

    def _duplicate_job(self):
        idx = self._selected_idx()
        if idx is None:
            messagebox.showinfo("Select a Job", "Please select a job to duplicate.")
            return
        copy = deepcopy(self.jobs[idx])
        desc = copy.get("description", "")
        copy["description"] = f"{desc} (copy)" if desc else "(copy)"
        self.jobs.insert(idx + 1, copy)
        self._refresh_list()
        self.tree.selection_set(str(idx + 1))
        self._on_select()

    def _delete_job(self):
        idx = self._selected_idx()
        if idx is None:
            messagebox.showinfo("Select a Job", "Please select a job to delete.")
            return
        if messagebox.askyesno("Delete Job", f"Delete Job #{idx + 1}?"):
            self.jobs.pop(idx)
            self._refresh_list()
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.configure(state="disabled")

    def _move_up(self):
        idx = self._selected_idx()
        if idx is None or idx == 0:
            return
        self.jobs[idx - 1], self.jobs[idx] = self.jobs[idx], self.jobs[idx - 1]
        self._refresh_list()
        self.tree.selection_set(str(idx - 1))
        self._on_select()

    def _move_down(self):
        idx = self._selected_idx()
        if idx is None or idx >= len(self.jobs) - 1:
            return
        self.jobs[idx], self.jobs[idx + 1] = self.jobs[idx + 1], self.jobs[idx]
        self._refresh_list()
        self.tree.selection_set(str(idx + 1))
        self._on_select()

    # ── Report preview & export ─────────────────────────────────

    def _preview_report(self):
        if not self.jobs:
            messagebox.showinfo("No Jobs", "Add at least one job before previewing.")
            return
        report = generate_report(self.jobs, self.project_var.get().strip())

        win = tk.Toplevel(self)
        win.title("Report Preview")
        win.geometry("720x680")
        txt = scrolledtext.ScrolledText(win, font=("Courier", 9), wrap="none")
        txt.pack(fill="both", expand=True, padx=6, pady=6)
        txt.insert("1.0", report)
        txt.configure(state="disabled")

        bf = ttk.Frame(win)
        bf.pack(pady=(0, 8))
        ttk.Button(bf, text="Export to File…", command=self._export_report).pack(side="left", padx=4)
        ttk.Button(bf, text="Close", command=win.destroy).pack(side="left", padx=4)

    def _export_report(self):
        if not self.jobs:
            messagebox.showinfo("No Jobs", "Add at least one job before exporting.")
            return
        default_name = f"wire_work_plan_{datetime.now().strftime('%Y%m%d')}.txt"
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=default_name,
        )
        if not path:
            return
        report = generate_report(self.jobs, self.project_var.get().strip())
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report)
        self.status_var.set(f"Exported → {path}")
        if messagebox.askyesno("Exported", f"Saved to:\n{path}\n\nOpen the file now?"):
            _open_file(path)

    # ── File I/O ────────────────────────────────────────────────

    def _new_plan(self):
        if self.jobs and not messagebox.askyesno("New Plan", "Discard current plan and start fresh?"):
            return
        self.jobs = []
        self.current_file = None
        self.project_var.set("")
        self._refresh_list()
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.configure(state="disabled")

    def _open(self):
        path = filedialog.askopenfilename(
            filetypes=[("Wire Plan", "*.wirePlan"), ("JSON", "*.json"), ("All", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.jobs = data.get("jobs", [])
            self.project_var.set(data.get("project", ""))
            self.current_file = path
            self._refresh_list()
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
            filetypes=[("Wire Plan", "*.wirePlan"), ("JSON", "*.json"), ("All", "*.*")],
        )
        if path:
            self.current_file = path
            self._write(path)

    def _write(self, path):
        try:
            payload = {"project": self.project_var.get().strip(), "jobs": self.jobs}
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            self._update_status()
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc))


# ──────────────────────────────────────────────────────────────────
# Portable file-open helper
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


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = WirePlannerApp()
    app.mainloop()
