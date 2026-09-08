"""Drawing search, browser-cookie, ctrl-room, and engineering-browse dialogs."""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import os
import re
import threading
import urllib.parse

from .utils import (_center_window, _styled_header, _bind_filter_combobox,
                    _bind_search_combobox, _bind_url_open)
from .net import (_parse_request_headers_raw, _parse_cookies_from_headers,
                  _update_cookie_in_headers, _fmt_phone,
                  _grab_browser_cookies, _ps_grab_windows_cookies)
from .db import _GlobalDrawingCache

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

class DrawingSearchDialog(tk.Toplevel):
    """Reusable drawing search UI backed by DrawingSearchClient."""

    def __init__(self, parent, app_config: dict, multi_select=True, proj_cache=None, persist_fn=None):
        super().__init__(parent)
        self.title("Search Drawings")
        self.resizable(True, True)
        self.app_config = app_config
        self.multi_select = multi_select
        self._persist_fn = persist_fn
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
        base_url     = self.app_config.get("drawing_search_url",   "").strip()
        download_url = self.app_config.get("drawing_download_url", "").strip() or None
        if not base_url:
            return None
        path     = self.app_config.get("drawing_search_path", "").strip() or None
        raw_hdrs = self.app_config.get("request_headers", "")
        cookies  = _parse_cookies_from_headers(raw_hdrs)
        extra    = _parse_request_headers_raw(raw_hdrs)
        extra.pop("Cookie", None)
        cache = DrawingSearchCache() if _DRAWING_SEARCH_AVAILABLE else None

        def _on_cookie_update(updated: dict):
            raw = self.app_config.get("request_headers", "")
            self.app_config["request_headers"] = _update_cookie_in_headers(raw, updated)
            if self._persist_fn:
                self._persist_fn()

        kw = {"on_cookie_update": _on_cookie_update} if _DSC_HAS_COOKIE_CB else {}
        if _DSC_HAS_DOWNLOAD_URL and download_url:
            kw["download_url"] = download_url
        return DrawingSearchClient(base_url=base_url, cookies=cookies, cache=cache,
                                   search_path=path, extra_headers=extra or None,
                                   **kw)

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
            ent = ttk.Entry(f, textvariable=var, width=40)
            ent.grid(row=i, column=1, sticky="ew", pady=3)
            if key.startswith("phone"):
                ent.bind("<FocusOut>", lambda _e, v=var: v.set(_fmt_phone(v.get())))

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


class _BrowserCookieDialog(tk.Toplevel):
    """Extract cookies from Edge/Chrome for a given domain and inject them into a headers widget."""

    def __init__(self, parent, domain: str, headers_widget, cookies_fn=None):
        super().__init__(parent)
        self.title("Grab Cookies from Browser")
        self.resizable(False, False)
        self._headers_widget = headers_widget
        self._vars: dict = {}
        self._cookies: dict = {}
        self._cookies_fn = cookies_fn if cookies_fn is not None else _grab_browser_cookies

        self._build(domain)
        _center_window(self)
        self.grab_set()
        threading.Thread(target=self._fetch, args=(domain,), daemon=True).start()
        self.wait_window()

    def _build(self, domain):
        body = tk.Frame(self, bg="white")
        body.pack(fill="both", expand=True, padx=20, pady=14)

        tk.Label(body, text="Grab Cookies from Edge / Chrome",
                 bg="white", font=("", 11, "bold"), fg="#1c2833").pack(anchor="w")
        tk.Label(body, text=f"Domain: {domain}",
                 bg="white", fg="#5d6d7e", font=("", 9)).pack(anchor="w", pady=(2, 10))

        self._status_lbl = tk.Label(body, text="Searching...",
                                    bg="white", fg="#2980b9", font=("", 9))
        self._status_lbl.pack(anchor="w")

        self._cookie_frame = tk.Frame(body, bg="white")
        self._cookie_frame.pack(fill="x", pady=(6, 0))

        sep = tk.Frame(self, bg="#d5d8dc", height=1)
        sep.pack(fill="x", side="bottom")
        bf = tk.Frame(self, bg="#eaecee")
        bf.pack(fill="x", side="bottom")
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 12), pady=8)
        self._apply_btn = ttk.Button(bf, text="Apply Selected",
                                     state="disabled", command=self._apply)
        self._apply_btn.pack(side="right", pady=8)

    def _fetch(self, domain):
        try:
            cookies = self._cookies_fn(domain)
            self.after(0, self._show_results, cookies)
        except Exception as exc:
            self.after(0, self._show_error, str(exc))

    def _show_results(self, cookies: dict):
        self._cookies = cookies
        if not cookies:
            self._status_lbl.config(text="No cookies found for this domain.", fg="#e74c3c")
            return
        self._status_lbl.config(
            text=f"Found {len(cookies)} cookie(s) — select which to apply:", fg="#1a7a30")
        for name, val in cookies.items():
            row = tk.Frame(self._cookie_frame, bg="white")
            row.pack(fill="x", pady=1)
            var = tk.BooleanVar(value=True)
            self._vars[name] = var
            tk.Checkbutton(row, variable=var, bg="white").pack(side="left")
            preview = val[:48] + "..." if len(val) > 48 else val
            tk.Label(row, text=f"{name}  =  {preview}",
                     bg="white", font=("Courier", 8), anchor="w").pack(side="left")
        self._apply_btn.config(state="normal")
        _center_window(self)

    def _show_error(self, msg: str):
        self._status_lbl.config(text="Error — see details below (you can select and copy):", fg="#e74c3c")
        self._cookie_frame.pack_configure(fill="both", expand=True)
        err_txt = tk.Text(self._cookie_frame, height=12, wrap="word",
                          font=("Courier", 8), bg="#fdfefe", fg="#922b21",
                          relief="solid", bd=1)
        err_txt.pack(fill="both", expand=True, pady=(4, 0))
        err_txt.insert("1.0", msg)
        self.resizable(True, True)
        self.geometry("560x420")
        _center_window(self)

    def _apply(self):
        selected = {n: self._cookies[n] for n, v in self._vars.items() if v.get()}
        if not selected:
            messagebox.showwarning("Nothing selected",
                                   "Select at least one cookie.", parent=self)
            return
        raw     = self._headers_widget.get("1.0", "end")
        updated = _update_cookie_in_headers(raw, selected)
        self._headers_widget.delete("1.0", "end")
        self._headers_widget.insert("1.0", updated)
        self.destroy()


class _EngineeringBrowseDialog(tk.Toplevel):
    """Browse and select engineering standards from the live tree API.

    result: list[EngineeringStandard] of selected items, or None on cancel.
    """

    def __init__(self, parent, client, on_loaded=None):
        super().__init__(parent)
        self.title("Browse Engineering Standards")
        self.resizable(True, True)
        self.result = None
        self._client = client
        self._on_loaded_cb = on_loaded   # called (no args) after data is loaded and persisted
        self._all_standards: list = []
        self._filtered: list = []
        self._sort_col = "Standard ID"
        self._sort_rev = False
        self._build()
        _center_window(self)
        self.geometry("1000x600")
        self.grab_set()
        self._start_fetch()
        self.wait_window()

    def _build(self):
        _styled_header(self, "Browse Engineering Standards",
                       "Select standards to add to this project")

        # ── filter form ──────────────────────────────────────────
        form = ttk.LabelFrame(self, text="Filter", padding=6)
        form.pack(fill="x", padx=10, pady=(4, 0))

        row0 = ttk.Frame(form); row0.pack(fill="x", pady=2)
        row1 = ttk.Frame(form); row1.pack(fill="x", pady=2)

        # Row 0: text searches
        def _lbl_ent(parent, label, width=20):
            ttk.Label(parent, text=label).pack(side="left")
            var = tk.StringVar()
            var.trace_add("write", lambda *_: self._apply_filter())
            ttk.Entry(parent, textvariable=var, width=width).pack(side="left", padx=(2, 10))
            return var

        self._v_id   = _lbl_ent(row0, "Standard ID:", 16)
        self._v_desc = _lbl_ent(row0, "Description contains:", 30)

        ttk.Button(row0, text="Clear", command=self._clear_filters).pack(side="left", padx=4)

        # Row 1: dropdowns
        ttk.Label(row1, text="Series:").pack(side="left")
        self._v_series = tk.StringVar(value="All")
        self._cb_series = ttk.Combobox(row1, textvariable=self._v_series,
                                       values=["All"], state="readonly", width=30)
        self._cb_series.pack(side="left", padx=(2, 10))
        self._cb_series.bind("<<ComboboxSelected>>", lambda _: self._apply_filter())

        ttk.Label(row1, text="State:").pack(side="left")
        self._v_state = tk.StringVar(value="Active")
        state_cb = ttk.Combobox(row1, textvariable=self._v_state,
                                values=["All", "Active", "Superseded"], state="readonly", width=12)
        state_cb.pack(side="left", padx=(2, 10))
        state_cb.bind("<<ComboboxSelected>>", lambda _: self._apply_filter())

        self._status_lbl = ttk.Label(row1, text="Loading…", foreground="#2980b9")
        self._status_lbl.pack(side="left", padx=16)

        # ── view toggle ───────────────────────────────────────────
        row2 = ttk.Frame(form); row2.pack(fill="x", pady=(2, 0))
        self._grouped_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="Group by series", variable=self._grouped_var,
                        command=self._apply_filter).pack(side="left")
        ttk.Button(row2, text="Expand All",
                   command=lambda: [self._tree.item(i, open=True)
                                    for i in self._tree.get_children()]).pack(side="left", padx=6)
        ttk.Button(row2, text="Collapse All",
                   command=lambda: [self._tree.item(i, open=False)
                                    for i in self._tree.get_children()]).pack(side="left")

        # ── results tree ─────────────────────────────────────────
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=10, pady=4)
        cols = ("Standard ID", "Description", "State", "URL")
        self._tree = ttk.Treeview(frame, columns=cols, show="tree headings",
                                  selectmode="extended")
        self._tree.heading("#0", text="Series", command=lambda: self._sort_by("Series"))
        self._tree.column("#0", width=220, minwidth=120, stretch=False)
        for col, w, stretch in zip(cols, (130, 350, 80, 240), (False, True, False, False)):
            self._tree.heading(col, text=col, command=lambda c=col: self._sort_by(c))
            self._tree.column(col, width=w, minwidth=60, stretch=stretch)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        # Tooltip for truncated cells
        self._tip_win = None
        self._tree.bind("<Motion>", self._on_tree_motion)
        self._tree.bind("<Leave>", lambda _: self._hide_tip())

        # ── footer buttons ────────────────────────────────────────
        sep = tk.Frame(self, bg="#d5d8dc", height=1); sep.pack(fill="x", side="bottom")
        bf = tk.Frame(self, bg="#eaecee"); bf.pack(fill="x", side="bottom")
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 12), pady=8)
        self._add_btn = ttk.Button(bf, text="Add Selected",
                                   state="disabled", command=self._add_selected)
        self._add_btn.pack(side="right", pady=8)
        self._count_lbl = tk.Label(bf, text="", bg="#eaecee", fg="#566573")
        self._count_lbl.pack(side="left", padx=12)

    def _clear_filters(self):
        self._v_id.set("")
        self._v_desc.set("")
        self._v_series.set("All")
        self._v_state.set("Active")
        self._apply_filter()

    def _start_fetch(self):
        self._client.fetch_all_async(
            on_done=lambda results: self.after(0, self._on_loaded, results),
            on_error=lambda exc: self.after(0, self._on_error, str(exc)),
            on_progress=lambda done, total, title: self.after(
                0, self._on_progress, done, total, title),
        )

    def _on_progress(self, done: int, total: int, title: str):
        self._status_lbl.config(
            text=f"Loading {done}/{total}: {title[:40]}…", foreground="#2980b9")

    def _on_loaded(self, standards: list):
        self._all_standards = standards
        # Populate series dropdown from actual data
        series_seen = []
        seen_set = set()
        for s in standards:
            if s.series_value not in seen_set:
                seen_set.add(s.series_value)
                series_seen.append(s.series_value)
        self._cb_series.config(values=["All"] + sorted(series_seen))
        self._apply_filter()
        n = len(standards)
        cached_note = " (from cache)" if all(
            self._client.cache is not None and not self._client.cache.is_stale(s.series_value)
            for s in standards[:1]
        ) else ""
        self._status_lbl.config(
            text=f"{n} standard(s) loaded{cached_note}.", foreground="#1a7a30")
        self._add_btn.config(state="normal")
        if self._on_loaded_cb:
            try:
                self._on_loaded_cb()
            except Exception:
                pass

    def _on_error(self, msg: str):
        self._status_lbl.config(text=f"Error: {msg[:140]}", foreground="#e74c3c")

    def _apply_filter(self):
        id_q    = self._v_id.get().strip().lower()
        desc_q  = self._v_desc.get().strip().lower()
        series  = self._v_series.get()
        state   = self._v_state.get()
        filtered = []
        for std in self._all_standards:
            if state != "All" and std.document_state != state:
                continue
            if series != "All" and std.series_value != series:
                continue
            if id_q and id_q not in std.standard_id.lower():
                continue
            if desc_q and desc_q not in std.description.lower():
                continue
            filtered.append(std)
        self._filtered = filtered
        self._populate(filtered)

    def _populate(self, standards: list):
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        if self._grouped_var.get():
            # Group by series — one open parent node per series
            from collections import OrderedDict
            groups: dict = OrderedDict()
            for std in standards:
                groups.setdefault(std.series_value, []).append(std)
            for series_title, items in groups.items():
                parent = self._tree.insert(
                    "", "end", text=f"{series_title}  ({len(items)})",
                    open=True, tags=("series_header",))
                for std in items:
                    self._tree.insert(parent, "end", values=(
                        std.standard_id, std.description,
                        std.document_state, std.url,
                    ), tags=("std_row",))
            self._tree.tag_configure("series_header", font=("", 9, "bold"),
                                     foreground="#1a5276")
        else:
            for std in standards:
                self._tree.insert("", "end", text=std.series_value, values=(
                    std.standard_id, std.description,
                    std.document_state, std.url,
                ), tags=("std_row",))
        self._count_lbl.config(text=f"{len(standards)} shown")

    def _sort_by(self, col: str):
        col_map = {"Standard ID": "standard_id", "Description": "description",
                   "Series": "series_value", "State": "document_state", "URL": "url"}
        attr = col_map.get(col, "standard_id")
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False
        self._filtered.sort(key=lambda s: (getattr(s, attr, '') or '').lower(),
                            reverse=self._sort_rev)
        self._populate(self._filtered)
        arrow = " ▲" if not self._sort_rev else " ▼"
        for c in ("Standard ID", "Description", "State", "URL"):
            self._tree.heading(c, text=c + (arrow if c == col else ""))
        self._tree.heading("#0", text="Series" + (arrow if col == "Series" else ""))

    # ── tooltip for truncated text ────────────────────────────────

    def _on_tree_motion(self, event):
        iid = self._tree.identify_row(event.y)
        col = self._tree.identify_column(event.x)
        if not iid:
            self._hide_tip(); return
        item = self._tree.item(iid)
        if "series_header" in item.get("tags", ()):
            tip_text = item.get("text", "")
        else:
            vals = item.get("values", [])
            col_idx = {"#1": 0, "#2": 1, "#3": 2, "#4": 3}.get(col)
            if col_idx is None:
                tip_text = item.get("text", "")  # #0 column = series
            else:
                tip_text = vals[col_idx] if col_idx < len(vals) else ""
        if not tip_text:
            self._hide_tip(); return
        x = event.x_root + 12
        y = event.y_root + 16
        if self._tip_win:
            self._tip_win.wm_geometry(f"+{x}+{y}")
            lbl = self._tip_win.winfo_children()
            if lbl:
                lbl[0].config(text=str(tip_text))
            return
        self._tip_win = tw = tk.Toplevel(self)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=str(tip_text), justify="left",
                 background="#ffffe0", relief="solid", borderwidth=1,
                 font=("", 9), wraplength=500).pack()

    def _hide_tip(self):
        if self._tip_win:
            self._tip_win.destroy()
            self._tip_win = None

    def _add_selected(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showwarning("Nothing selected",
                                   "Select at least one standard.", parent=self)
            return
        selected_ids = set()
        for iid in sel:
            item = self._tree.item(iid)
            # Skip series header rows (no values)
            if "series_header" in item.get("tags", ()):
                # Also select all children of this group
                for child in self._tree.get_children(iid):
                    vals = self._tree.item(child, "values")
                    if vals:
                        selected_ids.add(vals[0])
            else:
                vals = item.get("values", [])
                if vals:
                    selected_ids.add(vals[0])
        if not selected_ids:
            messagebox.showwarning("Nothing selected",
                                   "Select at least one standard.", parent=self)
            return
        self.result = [s for s in self._filtered if s.standard_id in selected_ids]
        self.destroy()


