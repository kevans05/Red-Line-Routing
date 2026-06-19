"""Three-step Export Wizard dialog and CrowDialog."""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import shutil
from datetime import datetime

from .utils import _center_window, _open_file, _reveal_file, _bind_url_open
from .html_export import (
    _EW_PAGE_SIZES, _EW_PAGE_DIMS,
    _ew_full_html, _ew_with_id, _ew_cover,
    _ew_work_orders, _ew_drawings_reg, _ew_relay_reg,
    _ew_standards, _ew_qr_sheet, _ew_embedded_files,
    _build_tablet_zip,
)
from .pdf_export import _PYPDF_AVAILABLE, _PYPDF_ERROR, _build_print_pdf

try:
    from drawing_search import DrawingSearchClient
    _DRAWING_SEARCH_AVAILABLE = True
except ImportError:
    _DRAWING_SEARCH_AVAILABLE = False

class ExportWizard(tk.Toplevel):
    """Three-step wizard generating Paper, HTML/PDF, and Tablet export packages."""

    _SECTIONS = [
        ("drawings",    "Drawings Register"),
        ("relay",       "Relay Settings"),
        ("maintenance", "Maintenance Standards"),
        ("engineering", "Engineering Standards"),
        ("tailboards",  "Tailboards"),
        ("safety",      "Safety Documents"),
        ("other",       "Other Documents"),
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
        self.geometry("600x580")
        _center_window(self)
        self.grab_set()
        self.wait_window()

    # ── build ─────────────────────────────────────────────────────

    def _build(self):
        self._hdr = tk.Frame(self, bg="#1a252f"); self._hdr.pack(fill="x")
        self._hdr_lbl = tk.Label(self._hdr, bg="#1a252f", fg="white",
                                  font=("", 11, "bold"), padx=14, pady=10)
        self._hdr_lbl.pack(side="left")

        # Nav bar MUST be packed with side="bottom" before the expanding
        # container, otherwise the container claims all remaining height and
        # the buttons are pushed below the visible window area.
        nav = ttk.Frame(self, padding=(14, 6, 14, 10))
        nav.pack(side="bottom", fill="x")
        self._back_btn = ttk.Button(nav, text="◄ Back",  command=self._back,    state="disabled")
        self._back_btn.pack(side="left")
        self._cancel_btn = ttk.Button(nav, text="Cancel", command=self.destroy)
        self._cancel_btn.pack(side="right")
        self._next_btn = ttk.Button(nav, text="Next ►",  command=self._next)
        self._next_btn.pack(side="right", padx=(0, 6))

        container = tk.Frame(self, bg=self.cget("bg"))
        container.pack(fill="both", expand=True)

        self._frames = [
            self._step1(container),
            self._step2(container),
            self._step3(container),
        ]

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
             "Zip package for transfer to a tablet: large-text HTML, all\n"
             "downloaded documents, project data and a manifest in one file."),
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

        # Pack "Extras" with side="bottom" first so it always stays visible when
        # the optional-sections area expands to fill remaining space.
        ext = ttk.LabelFrame(f, text="Extras", padding=(12, 6))
        ext.pack(side="bottom", fill="x")
        r2 = ttk.Frame(ext); r2.pack(fill="x", pady=2)
        ttk.Checkbutton(r2, variable=self._v_qr).pack(side="left")
        ttk.Label(r2, text="QR Code Sheet  (paper only — all document URLs as scannable codes)"
                  ).pack(side="left", padx=(4, 0))

        opt = ttk.LabelFrame(f, text="Optional sections", padding=(12, 6))
        opt.pack(fill="both", expand=True, pady=(0, 8))

        # Scrollable canvas so the dialog size stays fixed regardless of section count
        _cv  = tk.Canvas(opt, highlightthickness=0, bd=0)
        _vsb = ttk.Scrollbar(opt, orient="vertical", command=_cv.yview)
        _cv.configure(yscrollcommand=_vsb.set)
        _inner = ttk.Frame(_cv)
        for key, label in self._SECTIONS:
            r = ttk.Frame(_inner); r.pack(fill="x", pady=2)
            ttk.Combobox(r, textvariable=self._v_sec[key], width=10,
                         values=("Skip", "Print", "TOC only"),
                         state="readonly").pack(side="left")
            ttk.Label(r, text=label).pack(side="left", padx=(8, 0))
        ttk.Label(_inner,
                  text="TOC only — listed on the cover page as “printed separately”,"
                       " no pages added to the package.",
                  foreground="grey", font=("", 8)).pack(anchor="w", pady=(6, 0))
        _cwin = _cv.create_window((0, 0), window=_inner, anchor="nw")
        _inner.bind("<Configure>",
                    lambda e: _cv.configure(scrollregion=_cv.bbox("all")))
        _cv.bind("<Configure>",
                 lambda e: _cv.itemconfigure(_cwin, width=e.width))
        def _wheel(event):
            if event.delta:
                _cv.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif event.num == 4:
                _cv.yview_scroll(-1, "units")
            elif event.num == 5:
                _cv.yview_scroll(1, "units")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            _cv.bind(seq, _wheel)
            _inner.bind(seq, _wheel)
        _vsb.pack(side="right", fill="y")
        _cv.pack(side="left", fill="both", expand=True)
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
            "tailboards":  "sec-tailboards",
            "safety":      "sec-safety",
            "other":       "sec-other",
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
                    if mode == "tablet":
                        # Tablet ships as a zip package (HTML + documents +
                        # manifest + project JSON) for transfer to a tablet app
                        fpath = _build_tablet_zip(
                            app, html, folder, inc, self._SECTIONS,
                            project, date_s)
                    else:
                        fpath = os.path.join(folder, f"Digital_{date_s}.html")
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
                if path.lower().endswith((".pdf", ".zip")):
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
            "tailboards":  "sec-tailboards",
            "safety":      "sec-safety",
            "other":       "sec-other",
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
            "tailboards": lambda: _ew_with_id(
                _ew_embedded_files(
                    pf, os.path.join("Tailboards", "Completed"), mode, css_class=_emb_cls),
                "sec-tailboards"),
            "safety": lambda: _ew_with_id(
                _ew_embedded_files(
                    pf, os.path.join("Safety Documents", "Completed"), mode, css_class=_emb_cls),
                "sec-safety"),
            "other": lambda: _ew_with_id(
                _ew_embedded_files(pf, "Other Documents", mode, css_class=_emb_cls),
                "sec-other"),
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

