"""Shared UI utilities, file-system helpers, and widget classes."""
import os
import sys
import subprocess
import shutil
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime

import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont


# ── Drawing helpers ────────────────────────────────────────────────


def is_h_type_drawing(name):
    """Return True if drawing type character Y == 'H'.
    Format: XXXX-YZZ-IIII-N  or  YZZ-IIII-N"""
    if not name:
        return False
    parts = name.strip().split("-")
    type_seg = parts[1] if len(parts) >= 4 else parts[0]
    return bool(type_seg) and type_seg[0].upper() == "H"


def _treeview_strike_font():
    """Return an overstrike font matching the ttk Treeview row font exactly."""
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

    Folder layout: base_dir / XXXX / YZZ / NNNNN /
    Falls back to base_dir for names that don't match the 4-part convention.
    """
    parts = drawing_name.strip().split("-")
    if len(parts) >= 3:
        facility  = parts[0]
        type_subj = parts[1]
        serial    = parts[2]
        return os.path.join(base_dir, facility, type_subj, serial)
    return base_dir


# ── Archive / file helpers ─────────────────────────────────────────


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


def _archive_revision(folder, fname):
    """Move *folder*/*fname* into *folder*/Archive/ with a timestamp suffix.

    Unlike _archive_existing the archived copy is renamed with the date and
    time it was superseded, so several revisions can be retired on the same
    day without overwriting each other. Returns the archived path, or None if
    the file didn't exist.
    """
    src = os.path.join(folder, fname)
    if not os.path.isfile(src):
        return None
    archive_dir = os.path.join(folder, "Archive")
    stem, ext = os.path.splitext(fname)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    try:
        os.makedirs(archive_dir, exist_ok=True)
        dest = os.path.join(archive_dir, f"{stem}_{stamp}{ext}")
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(archive_dir, f"{stem}_{stamp}_{n}{ext}")
            n += 1
        shutil.move(src, dest)
        return dest
    except OSError:
        return None


def _snapshot_file(folder, fname):
    """Copy *folder*/*fname* into *folder*/Archive/ with a timestamp suffix.

    Unlike _archive_revision the original file is NOT removed — this records
    the pre-edit state while leaving the file in place for the user to edit.
    Returns the snapshot path, or None if the source file doesn't exist.
    """
    src = os.path.join(folder, fname)
    if not os.path.isfile(src):
        return None
    archive_dir = os.path.join(folder, "Archive")
    stem, ext = os.path.splitext(fname)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    try:
        os.makedirs(archive_dir, exist_ok=True)
        dest = os.path.join(archive_dir, f"{stem}_{stamp}{ext}")
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(archive_dir, f"{stem}_{stamp}_{n}{ext}")
            n += 1
        shutil.copy2(src, dest)
        return dest
    except OSError:
        return None


def _file_url_to_path(url: str) -> str:
    """Convert a file:// URL to a local filesystem path.

    Handles UNC network shares (file://server/share/path → \\\\server\\share\\path
    on Windows) and URL-encoded characters (%20 → space, etc.).
    """
    parsed = urllib.parse.urlparse(url)
    path = urllib.parse.unquote(parsed.path)
    if parsed.netloc:
        if os.name == "nt":
            return "\\\\" + parsed.netloc + path.replace("/", "\\")
        return "//" + parsed.netloc + path
    if os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path


# ── Project file scanner ───────────────────────────────────────────


def _iter_project_files(project_folder, subfolder, recurse=False, exts=None):
    """Yield (full_path, rel_path) for files in <project_folder>/<subfolder>.

    Shared scanner for every export path (PDF package, HTML embeds, tablet
    zip) so they all apply the same rules:
      - hidden files (leading dot) are skipped
      - when recursing, any directory named "archive" is skipped
      - rel_path always uses forward slashes (HTML / zip friendly)
      - exts: optional set of lowercase extensions (".pdf") to keep;
        None keeps every file
    """
    base = os.path.join(project_folder, subfolder)
    if not os.path.isdir(base):
        return

    def _want(name):
        if name.startswith("."):
            return False
        return exts is None or os.path.splitext(name)[1].lower() in exts

    if recurse:
        for root, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d.lower() != "archive")
            for f in sorted(files):
                if _want(f):
                    full = os.path.join(root, f)
                    yield full, os.path.relpath(full, project_folder).replace("\\", "/")
    else:
        for f in sorted(os.listdir(base)):
            full = os.path.join(base, f)
            if os.path.isfile(full) and _want(f):
                yield full, os.path.join(subfolder, f).replace("\\", "/")


# ── OS file / folder openers ───────────────────────────────────────


def _open_file(path):
    try:
        if sys.platform == "win32":  os.startfile(path)
        elif sys.platform == "darwin": subprocess.call(["open", path])
        else: subprocess.call(["xdg-open", path])
    except Exception:
        pass


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


# ── Tk UI helpers ──────────────────────────────────────────────────


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
    tk.Frame(hdr, bg=bg, height=14).pack()
    return hdr


# ── URL / combobox helpers ─────────────────────────────────────────


def _bind_url_open(entry_widget, url_var):
    """Ctrl+click on a URL entry opens it in the browser."""
    def _open(event):
        url = url_var.get().strip()
        if url:
            webbrowser.open(url)
    entry_widget.bind("<Control-Button-1>", _open)


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


def _bind_search_combobox(combo, get_values_fn):
    """Attach live search filtering to a ttk.Combobox.

    Uses the same non-focus-stealing custom popup as _ComboFilterHelper so
    that typing does not dismiss the suggestion list on each keystroke.
    get_values_fn is a zero-argument callable returning the current candidate list.
    """
    _ComboFilterHelper(combo, get_values_fn)


def _bind_filter_combobox(combo: ttk.Combobox, all_choices: list) -> None:
    """Attach a floating autocomplete popup to a ttk.Combobox (no focus stealing)."""
    _ComboFilterHelper(combo, all_choices)
