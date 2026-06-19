"""PDF package generation (no GUI dependencies).

Requires the vendored pypdf/ folder next to wire_planner.py.
Falls back gracefully (_PYPDF_AVAILABLE = False) if pypdf cannot load.
"""
import io
import os
import struct  # noqa: F401 — kept for potential future use by callers
import subprocess
import sys
from datetime import datetime

from .formatting import _std_list
from .models import _get_prot_drawings
from .utils import _iter_project_files

# ── pypdf optional import ────────────────────────────────────────────
_PYPDF_AVAILABLE = False
_PYPDF_ERROR = ""
try:
    _pypdf_pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pypdf_pkg_dir not in sys.path:
        sys.path.insert(0, _pypdf_pkg_dir)
    from pypdf import PdfWriter as _PdfWriter, PdfReader as _PdfReader
    _PYPDF_AVAILABLE = True
except BaseException as _e:
    _PYPDF_ERROR = str(_e)
    _PdfWriter = _PdfReader = None

# ── Helvetica AFM character widths (units = 1/1000 em) ──────────────
_HELV_W = {
    ' ':278,'!':278,'"':355,'#':556,'$':556,'%':889,'&':667,"'":222,
    '(':333,')':333,'*':389,'+':584,',':278,'-':333,'.':278,'/':278,
    '0':556,'1':556,'2':556,'3':556,'4':556,'5':556,'6':556,'7':556,
    '8':556,'9':556,':':278,';':278,'<':584,'=':584,'>':584,'?':556,
    '@':1015,'A':667,'B':667,'C':722,'D':722,'E':667,'F':611,'G':778,
    'H':722,'I':278,'J':500,'K':667,'L':556,'M':833,'N':722,'O':778,
    'P':667,'Q':778,'R':722,'S':667,'T':611,'U':722,'V':667,'W':944,
    'X':667,'Y':667,'Z':611,'[':278,'\\':278,']':278,'^':469,'_':556,
    '`':333,'a':556,'b':556,'c':500,'d':556,'e':556,'f':278,'g':556,
    'h':556,'i':222,'j':222,'k':500,'l':222,'m':833,'n':556,'o':556,
    'p':556,'q':556,'r':333,'s':500,'t':278,'u':556,'v':500,'w':722,
    'x':500,'y':500,'z':500,'{':334,'|':260,'}':334,'~':584,
}


def _ptw(text, size, bold=False):
    """Width of a text string in Helvetica at given point size."""
    f = 1.05 if bold else 1.0
    return sum(_HELV_W.get(c, 556) for c in str(text)) * size * f / 1000.0


def _ptrunc(text, max_pts, size, bold=False):
    """Truncate text to fit within max_pts width; append '...' if cut."""
    text = str(text)
    if _ptw(text, size, bold) <= max_pts:
        return text
    ew = _ptw('...', size, bold)
    lo, hi = 0, len(text)
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if _ptw(text[:mid], size, bold) + ew <= max_pts:
            lo = mid
        else:
            hi = mid
    return (text[:lo] + '...') if lo > 0 else ''


def _pwrap(text, max_pts, size, bold=False, max_lines=10):
    """Word-wrap text to fit max_pts wide; returns a list of lines.

    Newlines in the input start a new output line (formatting is preserved).
    Overly long single words are hard-broken; output is capped at
    max_lines with an ellipsis on the final line.
    """
    all_lines = []
    for para in str(text).split("\n"):
        para = " ".join(para.split())
        if not para:
            all_lines.append("")
            continue
        lines, cur = [], ""
        for word in para.split(" "):
            test = (cur + " " + word) if cur else word
            if _ptw(test, size, bold) <= max_pts:
                cur = test
                continue
            if cur:
                lines.append(cur)
            while _ptw(word, size, bold) > max_pts and len(word) > 1:
                lo, hi = 1, len(word)
                while lo < hi - 1:
                    mid = (lo + hi) // 2
                    if _ptw(word[:mid], size, bold) <= max_pts:
                        lo = mid
                    else:
                        hi = mid
                lines.append(word[:lo])
                word = word[lo:]
            cur = word
        if cur:
            lines.append(cur)
        all_lines.extend(lines or [""])
    if len(all_lines) > max_lines:
        all_lines = all_lines[:max_lines]
        all_lines[-1] = _ptrunc(all_lines[-1] + "...", max_pts, size, bold)
    return all_lines or [""]


def _penc(text):
    """Encode a string for a PDF string literal (WinAnsi, octal for non-ASCII)."""
    out = []
    for ch in str(text).replace('\r', '').replace('\n', ' '):
        if ch == '\\': out.append('\\\\')
        elif ch == '(': out.append('\\(')
        elif ch == ')': out.append('\\)')
        elif 32 <= ord(ch) <= 126: out.append(ch)
        else:
            try: out.append(f'\\{ch.encode("cp1252")[0]:03o}')
            except (UnicodeEncodeError, LookupError): out.append('?')
    return ''.join(out)


class _PDFPage:
    """Mutable PDF page. All coords: x,y from TOP-LEFT, y increases downward."""

    DARK  = (26,  37,  47)    # #1a252f header/column-header bg
    WHITE = (255, 255, 255)
    LIGHT = (247, 249, 252)   # #f7f9fc alternating row bg
    GREY  = (102, 102, 102)   # muted text
    RULE  = (200, 200, 200)   # table gridlines

    ROW_BG = {
        "REMOVE":      (253, 232, 230),
        "ADD":         (232, 248, 238),
        "MOVE-REMOVE": (254, 240, 230),
        "MOVE-ADD":    (254, 251, 230),
        "BLOCK":       (254, 243, 230),
        "UNBLOCK":     (230, 246, 243),
        "TESTING":     (245, 238, 248),
        "ISOLATION":   (232, 244, 248),
        "CR_PROT":     (232, 241, 248),
    }
    ROW_ACC = {
        "REMOVE":      (192,  57,  43),
        "ADD":         ( 39, 174,  96),
        "MOVE-REMOVE": (230, 126,  34),
        "MOVE-ADD":    (212, 172,  13),
        "BLOCK":       (202, 111,  30),
        "UNBLOCK":     ( 20, 143, 119),
        "TESTING":     (125,  60, 152),
        "ISOLATION":   ( 26, 107, 138),
        "CR_PROT":     ( 26,  82, 118),
    }
    TYPE_LBL = {
        "REMOVE":      "Remove Wire",
        "ADD":         "Add Wire",
        "MOVE-REMOVE": "Move — Remove",
        "MOVE-ADD":    "Move — Add",
        "BLOCK":       "Block",
        "UNBLOCK":     "Restore",
        "TESTING":     "Testing",
        "ISOLATION":   "Isolation",
        "CR_PROT":     "CR Protection",
    }
    # Font indices: F1=Helvetica, F2=Helvetica-Bold, F3=Courier
    FR = 1; FB = 2; FM = 3

    def __init__(self, w, h):
        self.w = w; self.h = h; self._ops = []

    def _rgb(self, c):
        return f"{c[0]/255:.3f} {c[1]/255:.3f} {c[2]/255:.3f}"

    def _by(self, y, rh=0):
        """Top-down y -> PDF bottom-up y. rh = rect height for fill_rect."""
        return self.h - y - rh

    def _bl(self, y, sz):
        """Top of text box (top-down) -> PDF baseline."""
        return self.h - y - sz * 0.78

    def frect(self, x, y, w, h, color):
        self._ops += [f"{self._rgb(color)} rg",
                      f"{x:.2f} {self._by(y,h):.2f} {w:.2f} {h:.2f} re f"]

    def srect(self, x, y, w, h, color=RULE, lw=0.3):
        self._ops += [f"{lw:.2f} w {self._rgb(color)} RG",
                      f"{x:.2f} {self._by(y,h):.2f} {w:.2f} {h:.2f} re S"]

    def hline(self, x, y, length, color=RULE, lw=0.3):
        py = self._by(y)
        self._ops += [f"{lw:.2f} w {self._rgb(color)} RG",
                      f"{x:.2f} {py:.2f} m {x+length:.2f} {py:.2f} l S"]

    def text(self, x, y, s, fi=1, sz=9, color=DARK):
        if not s: return
        self._ops += ["BT", f"{self._rgb(color)} rg",
                      f"/F{fi} {sz:.1f} Tf",
                      f"{x:.2f} {self._bl(y,sz):.2f} Td",
                      f"({_penc(s)}) Tj", "ET"]

    def ctext(self, x, y, w, h, s, fi=1, sz=8, color=DARK,
              align="left", pad=3):
        """Vertically-centered, truncated, aligned text in a cell rect."""
        s = _ptrunc(str(s), w - 2*pad, sz, fi == self.FB)
        ty = y + (h - sz) * 0.5
        if align == "center":
            tx = x + (w - _ptw(s, sz, fi == self.FB)) / 2
        elif align == "right":
            tx = x + w - _ptw(s, sz, fi == self.FB) - pad
        else:
            tx = x + pad
        self.text(tx, ty, s, fi=fi, sz=sz, color=color)

    def stream(self):
        return "\n".join(self._ops)


class _SimplePDFBuilder:
    """Minimal self-contained PDF generator using standard Type1 fonts."""
    SIZES = {
        "Letter Portrait":  (612.0,  792.0),
        "Letter Landscape": (792.0,  612.0),
        "11x17 Landscape":  (1224.0, 792.0),   # ASCII x
        "11x17 Portrait":   (792.0, 1224.0),
        "11×17 Landscape":  (1224.0, 792.0),  # Unicode ×
        "11×17 Portrait":   (792.0, 1224.0),
    }
    _FNMS = ["Helvetica", "Helvetica-Bold", "Courier"]

    def __init__(self):
        self._pages = []

    def new_page(self, size="Letter Portrait") -> _PDFPage:
        w, h = self.SIZES.get(size, (612.0, 792.0))
        p = _PDFPage(w, h)
        self._pages.append(p)
        return p

    def build(self) -> bytes:
        NF = len(self._FNMS); NP = len(self._pages)
        total = 2 + NF + 2 * NP
        buf = bytearray(); offsets = {}

        def emit(d):
            buf.extend(d.encode('ascii') if isinstance(d, str) else d)

        def begin(oid):
            offsets[oid] = len(buf); emit(f"{oid} 0 obj\n")

        def end():
            emit("endobj\n")

        emit(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

        begin(1); emit("<< /Type /Catalog /Pages 2 0 R >>\n"); end()

        kids = " ".join(f"{3+NF+2*i} 0 R" for i in range(NP))
        begin(2); emit(f"<< /Type /Pages /Kids [{kids}] /Count {NP} >>\n"); end()

        for i, fn in enumerate(self._FNMS):
            begin(3+i)
            emit(f"<< /Type /Font /Subtype /Type1 /BaseFont /{fn}"
                 f" /Encoding /WinAnsiEncoding >>\n")
            end()

        fdict = " ".join(f"/F{i+1} {3+i} 0 R" for i in range(NF))
        res = f"<< /Font << {fdict} >> >>"

        for i, page in enumerate(self._pages):
            pid = 3 + NF + 2*i; cid = pid + 1
            stream = page.stream().encode('ascii')
            begin(cid)
            emit(f"<< /Length {len(stream)} >>\nstream\n")
            emit(stream); emit(b"\nendstream\n"); end()
            begin(pid)
            emit(f"<< /Type /Page /Parent 2 0 R"
                 f" /MediaBox [0 0 {page.w:.2f} {page.h:.2f}]"
                 f" /Contents {cid} 0 R /Resources {res} >>\n")
            end()

        xpos = len(buf)
        emit(f"xref\n0 {total+1}\n")
        emit(b"0000000000 65535 f \r\n")
        for oid in range(1, total+1):
            emit(f"{offsets.get(oid,0):010d} 00000 n \r\n")
        emit(f"trailer\n<< /Size {total+1} /Root 1 0 R >>\n"
             f"startxref\n{xpos}\n%%EOF\n")
        return bytes(buf)


def _ep_flat(ep) -> str:
    parts = []
    if ep.get("device"):   parts.append(ep["device"])
    if ep.get("pin"):      parts.append(f"Pin {ep['pin']}")
    if ep.get("location"): parts.append(ep["location"])
    if ep.get("panel"):    parts.append(f"Panel {ep['panel']}")
    if ep.get("drawing"):
        d = ep["drawing"]
        if ep.get("drawing_rev"):  d += f" Rev{ep['drawing_rev']}"
        if ep.get("drawing_cell"): d += f" [{ep['drawing_cell']}]"
        parts.append(d)
    return "  /  ".join(parts)


def _prot_flat(prot) -> str:
    parts = []
    if prot.get("equipment"): parts.append(prot["equipment"])
    if prot.get("location"):  parts.append(prot["location"])
    if prot.get("panel"):     parts.append(f"Panel {prot['panel']}")
    if prot.get("notes"):     parts.append(prot["notes"])
    for d in _get_prot_drawings(prot):
        name = d.get("drawing","")
        if d.get("drawing_rev"): name += f" Rev{d['drawing_rev']}"
        parts.append(f"Dwg:{name}")
    for pt in prot.get("iso_points",[]):
        parts.append(f"{pt.get('iso_type','ISO')} {pt.get('reference','')}".strip())
    if prot.get("mb_enabled"): parts.append("MB INPUT")
    return "  /  ".join(parts)


def _pdf_cover(bld, project, crows, date, toc_items, size="Letter Portrait"):
    margin = 43
    p = bld.new_page(size)
    w, h = p.w, p.h
    cw = w - 2*margin
    HDR = max(85.0, h * 0.11)

    p.frect(0, 0, w, HDR, _PDFPage.DARK)
    p.frect(0, HDR - 4, w, 4, (39, 174, 96))
    proj = _ptrunc(project or "Red-Line Routing", cw - 12, 20, True)
    p.text(margin, 16, proj, fi=_PDFPage.FB, sz=20, color=_PDFPage.WHITE)
    p.text(margin, 46, "Red-Line Routing — Work Package", fi=_PDFPage.FR,
           sz=10, color=(160, 175, 190))

    y = HDR + 18
    p.text(margin, y, "Generated:", fi=_PDFPage.FB, sz=9, color=_PDFPage.GREY)
    p.text(margin + 72, y, date, fi=_PDFPage.FR, sz=9, color=_PDFPage.DARK)
    y += 22

    if crows:
        y += 4
        p.text(margin, y, "CROW / OUTAGE NUMBERS", fi=_PDFPage.FB, sz=8,
               color=_PDFPage.GREY)
        y += 13
        col_url = cw - 130
        p.frect(margin, y, cw, 18, _PDFPage.DARK)
        p.ctext(margin,       y, 130,     18, "Outage #",
                fi=_PDFPage.FB, sz=8, color=_PDFPage.WHITE)
        p.ctext(margin+130,   y, col_url, 18, "URL",
                fi=_PDFPage.FB, sz=8, color=_PDFPage.WHITE)
        y += 18
        for ci, crow in enumerate(crows):
            bg = _PDFPage.LIGHT if ci % 2 == 0 else _PDFPage.WHITE
            p.frect(margin, y, cw, 16, bg)
            p.ctext(margin,     y, 130,     16, crow.get("outage_number",""),
                    fi=_PDFPage.FB, sz=8)
            p.ctext(margin+130, y, col_url, 16, crow.get("url",""),
                    fi=_PDFPage.FR, sz=7, color=(50,100,170))
            p.hline(margin, y+16, cw)
            y += 16
        p.srect(margin, y - len(crows)*16 - 18, cw, len(crows)*16 + 18)
        y += 16

    if toc_items:
        y += 4
        p.text(margin, y, "TABLE OF CONTENTS", fi=_PDFPage.FB, sz=8,
               color=_PDFPage.GREY)
        y += 13
        for i, (label, _anch) in enumerate(toc_items):
            bg = _PDFPage.LIGHT if i % 2 == 0 else _PDFPage.WHITE
            p.frect(margin, y, cw, 20, bg)
            p.ctext(margin,    y, 30,    20, str(i+1), fi=_PDFPage.FB, sz=10,
                    color=_PDFPage.DARK, align="center")
            p.ctext(margin+30, y, cw-30, 20, label,   fi=_PDFPage.FR, sz=10,
                    color=_PDFPage.DARK)
            p.hline(margin, y+20, cw)
            y += 20
        p.srect(margin, y - len(toc_items)*20, cw, len(toc_items)*20)


def _pdf_section_table(bld, title, col_specs, rows_iter, size="Letter Portrait"):
    """Render a multi-page table section.

    col_specs : list of (header_str, width_pts, align_str) -- widths must sum to content width.
    rows_iter : iterable of (row_key, [cell_value,...]) -- row_key None = plain alternating rows,
                string key from _PDFPage.ROW_BG = colored job row.
    """
    margin = 43 if "Letter" in size else 36
    HDR_H = 30; COL_H = 20; ROW_H = 17

    pw, ph = _SimplePDFBuilder.SIZES.get(size, (612.0, 792.0))
    cw = pw - 2*margin

    col_names  = [c[0] for c in col_specs]
    col_widths = [c[1] for c in col_specs]
    col_aligns = [c[2] for c in col_specs]

    state = {"p": None, "y": 0, "ri": 0}

    def new_pg(cont=False):
        p = bld.new_page(size)
        p.frect(0, 0, pw, HDR_H, _PDFPage.DARK)
        p.frect(0, HDR_H - 3, pw, 3, (39, 174, 96))
        lbl = title + (" (cont.)" if cont else "")
        p.text(margin, 8, lbl, fi=_PDFPage.FB, sz=13, color=_PDFPage.WHITE)
        y = HDR_H
        p.frect(margin, y, cw, COL_H, (40, 52, 65))
        x = margin
        for name, cwidth, align in zip(col_names, col_widths, col_aligns):
            p.ctext(x, y, cwidth, COL_H, name, fi=_PDFPage.FB, sz=8,
                   color=_PDFPage.WHITE, align=align)
            x += cwidth
        state["p"] = p
        state["y"] = y + COL_H

    new_pg()

    LH = 10.0   # line height for 8pt wrapped text
    PAD = 3.5   # top/bottom cell padding

    for row_key, cells in rows_iter:
        wrapped = []
        for ci, (val, cwidth) in enumerate(zip(cells, col_widths)):
            bold = (ci == 0 and not row_key)
            wrapped.append(_pwrap(val, cwidth - 6, 8, bold=bold))
        n_lines = max(len(w) for w in wrapped) if wrapped else 1
        rh = max(ROW_H, n_lines * LH + 2 * PAD)

        if state["y"] + rh > ph - margin:
            new_pg(cont=True)
        p = state["p"]; y = state["y"]; ri = state["ri"]

        if row_key and row_key in _PDFPage.ROW_BG:
            bg  = _PDFPage.ROW_BG[row_key]
            acc = _PDFPage.ROW_ACC.get(row_key)
        else:
            bg  = _PDFPage.LIGHT if ri % 2 == 0 else _PDFPage.WHITE
            acc = None

        p.frect(margin, y, cw, rh, bg)
        if acc:
            p.frect(margin, y, 4, rh, acc)

        x = margin
        for ci, (lines, cwidth, align) in enumerate(zip(wrapped, col_widths, col_aligns)):
            fi = _PDFPage.FB if (ci == 0 and not row_key) else _PDFPage.FR
            for li, line in enumerate(lines):
                if align == "center":
                    tx = x + (cwidth - _ptw(line, 8, fi == _PDFPage.FB)) / 2
                elif align == "right":
                    tx = x + cwidth - _ptw(line, 8, fi == _PDFPage.FB) - 3
                else:
                    tx = x + 3
                p.text(tx, y + PAD + li * LH, line, fi=fi, sz=8)
            x += cwidth

        p.hline(margin, y + rh, cw)
        state["y"] += rh; state["ri"] += 1


def _pdf_work_orders(bld, jobs, drw_reg, size="11x17 Landscape"):
    size = size.replace("×", "x")
    margin = 36 if "17" in size else 43
    HDR_H = 30; COL_H = 20; ROW_H = 18

    pw, ph = _SimplePDFBuilder.SIZES.get(size, (1224.0, 792.0))
    cw = pw - 2*margin

    fixed_w = 20 + 28 + 95 + 75
    rem = cw - fixed_w
    desc_w = int(rem * 0.21)
    ep_w   = (rem - desc_w) // 2
    total = 20 + 28 + 95 + desc_w + ep_w + 75 + ep_w
    desc_w += cw - total
    col_ws = [20, 28, 95, desc_w, ep_w, 75, ep_w]

    state = {"p": None, "y": 0}

    def new_pg(cont=False):
        p = bld.new_page(size)
        p.frect(0, 0, pw, HDR_H, _PDFPage.DARK)
        p.frect(0, HDR_H-3, pw, 3, (39, 174, 96))
        p.text(margin, 8, "Work Orders" + (" (cont.)" if cont else ""),
               fi=_PDFPage.FB, sz=13, color=_PDFPage.WHITE)
        y = HDR_H
        p.frect(margin, y, cw, COL_H, (40, 52, 65))
        x = margin
        for name, cwidth, align in zip(
            ["", "#", "Type", "Description",
             "Start Point / Device", "Wire", "End Point / Device"],
            col_ws,
            ["c","c","l","l","l","c","l"]):
            al = {"c":"center","l":"left"}.get(align,"left")
            p.ctext(x, y, cwidth, COL_H, name, fi=_PDFPage.FB, sz=8,
                   color=_PDFPage.WHITE, align=al)
            x += cwidth
        state["p"] = p; state["y"] = y + COL_H

    new_pg()

    reg = drw_reg or {}
    seq = 1

    LH = 9.0    # line height for 7pt wrapped text
    PAD = 4.0   # top/bottom cell padding

    def draw_row(jkey, desc, start_s, wire, end_s):
        nonlocal seq
        desc_lines  = _pwrap(desc,    col_ws[3] - 6, 7)
        start_lines = _pwrap(start_s, col_ws[4] - 6, 7)
        end_lines   = _pwrap(end_s,   col_ws[6] - 6, 7)
        n_lines = max(len(desc_lines), len(start_lines), len(end_lines), 1)
        rh = max(ROW_H, n_lines * LH + 2 * PAD)

        if state["y"] + rh > ph - margin:
            new_pg(cont=True)
        p = state["p"]; y = state["y"]
        bg  = _PDFPage.ROW_BG.get(jkey, _PDFPage.WHITE)
        acc = _PDFPage.ROW_ACC.get(jkey, _PDFPage.RULE)
        p.frect(margin, y, cw, rh, bg)
        p.frect(margin, y, 4, rh, acc)
        cbx = margin + 5; cby = y + 4
        p.srect(cbx, cby, 9, 9, color=(120,120,120), lw=0.7)
        x = margin + col_ws[0]
        p.ctext(x, y, col_ws[1], rh, str(seq), fi=_PDFPage.FB,
                sz=8, align="center")
        x += col_ws[1]
        lbl = _PDFPage.TYPE_LBL.get(jkey, jkey)
        col = _PDFPage.ROW_ACC.get(jkey, _PDFPage.DARK)
        p.ctext(x, y, col_ws[2], rh, lbl, fi=_PDFPage.FB, sz=8, color=col)
        x += col_ws[2]
        for li, line in enumerate(desc_lines):
            p.text(x + 3, y + PAD + li * LH, line, fi=_PDFPage.FR, sz=7)
        x += col_ws[3]
        for li, line in enumerate(start_lines):
            p.text(x + 3, y + PAD + li * LH, line, fi=_PDFPage.FR, sz=7)
        x += col_ws[4]
        p.ctext(x, y, col_ws[5], rh, wire, fi=_PDFPage.FM, sz=7, align="center")
        x += col_ws[5]
        for li, line in enumerate(end_lines):
            p.text(x + 3, y + PAD + li * LH, line, fi=_PDFPage.FR, sz=7)
        p.hline(margin, y + rh, cw)
        state["y"] += rh; seq += 1

    for job in jobs:
        jt   = job["type"]
        desc = job.get("description","")
        ms   = _std_list(job,"maintenance_standards")
        es   = _std_list(job,"engineering_standards")
        stds = []
        if ms: stds.append("Maint:" + ",".join(ms))
        if es: stds.append("Eng:" + ",".join(es))
        if stds: desc += (" | " if desc else "") + " | ".join(stds)

        if jt in ("REMOVE","ADD"):
            notes = job.get("notes","").strip()
            desc_n = desc + ("\n" + notes if notes else "")
            draw_row(jt, desc_n, _ep_flat(job.get("start",{})),
                     job.get("wire",""), _ep_flat(job.get("end",{})))
        elif jt == "MOVE":
            notes = job.get("notes", "").strip()
            desc_n = desc + ("\n" + notes if notes else "")
            draw_row("MOVE-REMOVE", desc_n,
                     _ep_flat(job.get("start",{})), job.get("wire",""),
                     _ep_flat(job.get("end",{})))
            draw_row("MOVE-ADD", "",
                     _ep_flat(job.get("add_start",{})), job.get("add_wire",""),
                     _ep_flat(job.get("add_end",{})))
        elif jt in ("BLOCK","UNBLOCK"):
            notes = job.get("notes", "").strip()
            prot_s = _prot_flat(job.get("protection", {}))
            if notes:
                prot_s += ("\n" if prot_s else "") + notes
            draw_row(jt, desc, prot_s, "", "")
        elif jt == "TESTING":
            draw_row(jt, desc, job.get("notes",""), "", "")
        elif jt == "ISOLATION":
            drawings = job.get("drawings", [])
            detail = "; ".join(
                d.get("drawing","")
                + (f" Rev {d['drawing_rev']}" if d.get("drawing_rev") else "")
                for d in drawings)
            if job.get("notes"):
                detail += ("\n" if detail else "") + job["notes"]
            draw_row(jt, desc, detail, "", "")
        elif jt == "CR_PROT":
            desks = job.get("desks", [])
            desk_parts = []
            for d in desks:
                name_str = d.get("desk_name", "")
                if d.get("desk_type"):
                    name_str += f" ({d['desk_type']})"
                phones = " / ".join(filter(None, [d.get("phone_int", ""),
                                                   d.get("phone_local", ""),
                                                   d.get("phone_toll", "")]))
                if phones:
                    name_str += f"  Ph: {phones}"
                desk_parts.append(name_str)
            desk_str = "\n".join(desk_parts)
            crows = job.get("crows", [])
            if crows:
                desk_str += ("\n" if desk_str else "") + "CROWs: " + ", ".join(crows)
            if job.get("notes"):
                desk_str += ("\n" if desk_str else "") + job["notes"]
            draw_row("CR_PROT", desc, desk_str, "", "")


def _pdf_drawings_reg(bld, reg, size="Letter Portrait"):
    if not reg: return
    margin = 43 if "Letter" in size else 36
    pw = _SimplePDFBuilder.SIZES.get(size, (612.0, 792.0))[0]
    cw = pw - 2*margin
    notes_w = min(160, int(cw * 0.20))
    rev_w   = 42
    num_w   = min(160, int(cw * 0.22))
    title_w = cw - num_w - rev_w - notes_w
    specs = [
        ("Drawing #", num_w, "left"),
        ("Title",     title_w, "left"),
        ("Rev",       rev_w,  "center"),
        ("Notes",     notes_w,"left"),
    ]
    def rows():
        for name, info in sorted(reg.items()):
            yield None, [name, info.get("title",""), info.get("rev",""),
                         info.get("notes","")]
    _pdf_section_table(bld, "Drawings Register", specs, rows(), size)


def _pdf_relay_reg(bld, reg, size="Letter Portrait"):
    if not reg: return
    margin = 43 if "Letter" in size else 36
    pw = _SimplePDFBuilder.SIZES.get(size, (612.0, 792.0))[0]
    cw = pw - 2*margin
    rev_w   = 42; eng_w = 110; phone_w = 110
    dev_w   = min(110, int(cw * 0.16))
    title_w = cw - dev_w - rev_w - eng_w - phone_w
    specs = [
        ("Device ID",      dev_w,   "left"),
        ("Title",          title_w, "left"),
        ("Rev",            rev_w,   "center"),
        ("Engineer",       eng_w,   "left"),
        ("Phone / Contact", phone_w, "left"),
    ]
    def rows():
        for did, info in sorted(reg.items()):
            yield None, [did, info.get("title",""), info.get("revision",""),
                         info.get("engineer",""), info.get("contact","")]
    _pdf_section_table(bld, "Relay Settings", specs, rows(), size)


def _pdf_standards(bld, maint_reg, eng_reg, size="Letter Portrait"):
    margin = 43 if "Letter" in size else 36
    pw = _SimplePDFBuilder.SIZES.get(size, (612.0, 792.0))[0]
    cw = pw - 2*margin
    rev_w = 42; sid_w = min(130, int(cw * 0.20))
    title_w = cw - sid_w - rev_w
    specs = [
        ("Standard ID", sid_w,   "left"),
        ("Title",       title_w, "left"),
        ("Rev",         rev_w,   "center"),
    ]
    if maint_reg:
        def mrows():
            for sid, info in sorted(maint_reg.items()):
                yield None, [sid, info.get("title",""), info.get("revision","")]
        _pdf_section_table(bld, "Maintenance Standards", specs, mrows(), size)
    if eng_reg:
        def erows():
            for sid, info in sorted(eng_reg.items()):
                yield None, [sid, info.get("title",""), info.get("revision","")]
        _pdf_section_table(bld, "Engineering Standards", specs, erows(), size)


def _merge_pdfs_bytes(pdf_bytes_list: list) -> bytes:
    """Merge list of PDF byte strings into one PDF using pypdf."""
    if not _PYPDF_AVAILABLE:
        return pdf_bytes_list[0] if pdf_bytes_list else b""
    from pypdf.generic import RectangleObject, NameObject
    writer = _PdfWriter()
    for data in pdf_bytes_list:
        if not data:
            continue
        try:
            reader = _PdfReader(io.BytesIO(data))
            for page in reader.pages:
                if "/MediaBox" not in page:
                    try:
                        mb = page.mediabox
                        page[NameObject("/MediaBox")] = RectangleObject(
                            (float(mb.left), float(mb.bottom),
                             float(mb.right), float(mb.top))
                        )
                    except Exception:
                        pass
                writer.add_page(page)
        except Exception:
            pass
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


_CONVERTIBLE_EXTS = {".pdf", ".txt", ".docx", ".doc"}


def _txt_to_pdf_bytes(path):
    """Render a plain-text file as a PDF using _SimplePDFBuilder."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return b""

    pw, ph = 612.0, 792.0
    margin = 50.0
    sz = 8.5
    line_h = sz * 1.5
    max_w = pw - 2 * margin
    HDR_H = 26.0
    content_top = HDR_H + 10
    max_y = ph - margin
    fname = os.path.basename(path)

    bld = _SimplePDFBuilder()
    state = {"p": None, "y": content_top}

    def new_pg():
        pg = bld.new_page("Letter Portrait")
        pg.frect(0, 0, pw, HDR_H, _PDFPage.DARK)
        pg.frect(0, HDR_H - 3, pw, 3, (39, 174, 96))
        pg.text(margin, 6, _ptrunc(fname, max_w, 10, True),
                fi=_PDFPage.FB, sz=10, color=_PDFPage.WHITE)
        state["p"] = pg
        state["y"] = content_top

    new_pg()

    def emit_line(line):
        if state["y"] + line_h > max_y:
            new_pg()
        state["p"].text(margin, state["y"], line, fi=_PDFPage.FM, sz=sz)
        state["y"] += line_h

    for raw_line in text.splitlines():
        if not raw_line.strip():
            state["y"] += line_h * 0.4
            continue
        words = raw_line.split(" ")
        current = ""
        for word in words:
            test = (current + " " + word).lstrip() if current else word
            if _ptw(test, sz) <= max_w:
                current = test
            else:
                if current:
                    emit_line(current)
                current = word if _ptw(word, sz) <= max_w else _ptrunc(word, max_w, sz)
        if current is not None:
            emit_line(current)

    return bld.build()


def _docx_to_pdf_bytes(path):
    """Try to convert a .docx/.doc file to PDF bytes.

    Attempts LibreOffice headless first, then PowerShell + Word on Windows.
    Returns PDF bytes on success, None if no converter is available.
    """
    import tempfile
    abs_path = os.path.abspath(path)

    for cmd in ("libreoffice", "soffice"):
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                r = subprocess.run(
                    [cmd, "--headless", "--convert-to", "pdf",
                     "--outdir", tmpdir, abs_path],
                    timeout=60, capture_output=True)
                if r.returncode == 0:
                    stem = os.path.splitext(os.path.basename(abs_path))[0]
                    out = os.path.join(tmpdir, stem + ".pdf")
                    if os.path.isfile(out):
                        with open(out, "rb") as fh:
                            return fh.read()
        except (FileNotFoundError, OSError):
            continue
        except subprocess.TimeoutExpired:
            break

    if sys.platform == "win32":
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                out = os.path.join(tmpdir, "out.pdf")
                ps = (
                    f'$w = New-Object -ComObject Word.Application; '
                    f'$w.Visible = $false; '
                    f'$d = $w.Documents.Open("{abs_path}"); '
                    f'$d.SaveAs2("{out}", 17); '
                    f'$d.Close(); $w.Quit()'
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps],
                    timeout=60, capture_output=True)
                if os.path.isfile(out):
                    with open(out, "rb") as fh:
                        return fh.read()
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass

    return None


def _convert_file_to_pdf(path):
    """Convert a supported file to PDF bytes.

    Returns (bytes, error_str). bytes is None when conversion fails.
    Supported: .pdf (pass-through), .txt, .docx, .doc
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            with open(path, "rb") as fh:
                return fh.read(), ""
        elif ext == ".txt":
            data = _txt_to_pdf_bytes(path)
            return (data, "") if data else (None, "text conversion failed")
        elif ext in (".docx", ".doc"):
            data = _docx_to_pdf_bytes(path)
            if data:
                return data, ""
            return None, "requires LibreOffice or Microsoft Word"
        else:
            return None, f"unsupported type {ext}"
    except Exception as exc:
        return None, str(exc)


def _collect_pdfs(folder, subfolder, recurse=False):
    """Return list of PDF bytes from a project subfolder.

    Collects .pdf files directly; converts .txt, .docx, .doc to PDF.
    """
    result = []
    for fpath, _rel in _iter_project_files(folder, subfolder, recurse=recurse,
                                           exts=_CONVERTIBLE_EXTS):
        data, _ = _convert_file_to_pdf(fpath)
        if data:
            result.append(data)
    return result


def _build_print_pdf(app, inc: dict, sizes: dict, crows: list,
                     folder: str) -> tuple:
    """Assemble a complete PDF print package.

    Returns (pdf_bytes, non_pdf_files_list).
    """
    bld   = _SimplePDFBuilder()
    parts = []
    nopdf = []

    toc_items = [("Work Orders", "")]
    for k, lbl in [
        ("drawings",    "Drawings Register"),
        ("relay",       "Relay Settings"),
        ("maintenance", "Maintenance Standards"),
        ("engineering", "Engineering Standards"),
    ]:
        if inc.get(k) == "print":
            toc_items.append((lbl, ""))
        elif inc.get(k) == "toc":
            toc_items.append((lbl + "  — printed separately", ""))
    _pdf_cover(
        bld,
        app.project_var.get().strip(),
        crows,
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        toc_items=toc_items,
        size=sizes.get("cover", "Letter Portrait"),
    )

    wo_size = sizes.get("work_orders", "11x17 Landscape").replace("×","x")
    _pdf_work_orders(bld, app.jobs, app.drawing_registry, wo_size)

    if inc.get("drawings") == "print":
        _pdf_drawings_reg(bld, app.drawing_registry,
                          sizes.get("drawings","Letter Portrait"))
        if folder:
            parts.extend(_collect_pdfs(folder, "Drawings", recurse=True))

    if inc.get("relay") == "print":
        _pdf_relay_reg(bld, app.relay_registry,
                       sizes.get("relay","Letter Portrait"))
        if folder:
            parts.extend(_collect_pdfs(folder, "Relay Settings"))

    if inc.get("maintenance") == "print":
        _pdf_standards(bld, app.maintenance_standards_registry, {},
                       sizes.get("maintenance","Letter Portrait"))
        if folder:
            parts.extend(_collect_pdfs(folder, "Maintenance Standards"))

    if inc.get("engineering") == "print":
        _pdf_standards(bld, {}, app.engineering_standards_registry,
                       sizes.get("engineering","Letter Portrait"))
        if folder:
            parts.extend(_collect_pdfs(folder, "Engineering Standards"))

    if folder:
        parts.extend(_collect_pdfs(folder, "CROW Outage"))

    if inc.get("tailboards") == "print" and folder:
        parts.extend(_collect_pdfs(folder, os.path.join("Tailboards", "Completed")))

    if inc.get("safety") == "print" and folder:
        parts.extend(_collect_pdfs(folder, os.path.join("Safety Documents", "Completed")))

    if inc.get("other") == "print" and folder:
        parts.extend(_collect_pdfs(folder, "Other Documents"))

    doc_pdf = bld.build()
    if parts and _PYPDF_AVAILABLE:
        final = _merge_pdfs_bytes([doc_pdf] + parts)
    else:
        final = doc_pdf

    return final, nopdf
