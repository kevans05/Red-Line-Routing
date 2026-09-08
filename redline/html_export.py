"""Export Wizard HTML generators, tablet-zip builder, and related constants.

All functions are pure (no GUI) so they can be unit-tested headlessly.
"""
import json
import os
import urllib.parse
import zipfile
from datetime import datetime

from .formatting import _esc, _ROW_STYLE, _ROW_BORDER, _std_list
from .models import _get_prot_drawings
from .utils import _iter_project_files

# ── Page-size constants ────────────────────────────────────────────

_EW_PAGE_SIZES = [
    "Letter Portrait", "Letter Landscape",
    "11×17 Landscape", "11×17 Portrait",
]

_EW_PAGE_DIMS = {
    "Letter Portrait":  ("8.5in", "11in"),
    "Letter Landscape": ("11in",  "8.5in"),
    "11×17 Landscape":  ("17in",  "11in"),
    "11×17 Portrait":   ("11in",  "17in"),
}

# ── File-extension sets (used by _ew_embedded_files) ──────────────

_PDF_EXTS   = {".pdf"}
_DOC_EXTS   = {".doc", ".docx"}
_EMBED_EXTS = _PDF_EXTS | _DOC_EXTS

# ── Tablet zip constants ───────────────────────────────────────────

# (section key, project subfolder, recurse)
_TABLET_SECTION_DIRS = [
    ("drawings",    "Drawings",                                    True),
    ("relay",       "Relay Settings",                              False),
    ("maintenance", "Maintenance Standards",                       False),
    ("engineering", "Engineering Standards",                       False),
    ("tailboards",  os.path.join("Tailboards", "Completed"),       False),
    ("safety",      os.path.join("Safety Documents", "Completed"), False),
    ("other",       "Other Documents",                             False),
]

_TABLET_PKG_FORMAT  = "redline-tablet-package"
_TABLET_PKG_VERSION = 1


# ── HTML generators ────────────────────────────────────────────────


def _ew_url_cell(url: str, label: str, mode: str,
                 project_folder: str, subfolders: list) -> str:
    """URL cell: truncated text for paper, local-path link for tablet, hyperlink for digital."""
    if not url:
        return ""
    if mode == "paper":
        s = url[:72] + ("…" if len(url) > 72 else "")
        return _esc(s)
    if mode == "tablet" and project_folder:
        try:
            basename = os.path.basename(urllib.parse.urlparse(url).path)
            if basename:
                for sf in subfolders:
                    local = os.path.join(project_folder, sf, basename)
                    if os.path.isfile(local):
                        rel = (sf + "/" + basename).replace("\\", "/")
                        return f'<a href="{_esc(rel)}" class="doc-link">{_esc(label)} ↗</a>'
        except Exception:
            pass
    return f'<a href="{_esc(url)}" class="doc-link">{_esc(label)} ↗</a>'


def _ew_full_html(title: str, body_html: str, page_css: str, mode: str) -> str:
    vp = '<meta name="viewport" content="width=device-width,initial-scale=1">' \
         if mode == "tablet" else ""
    base_fs = "11pt" if mode == "tablet" else "9pt"
    tablet_css = """
    body { font-size: 11pt !important; }
    td, th { padding: 8px 10px !important; font-size: 10pt !important; }
    h2.sec-hdr { font-size: 14pt !important; }
    a.doc-link {
        display: inline-block; background: #2980b9; color: white !important;
        padding: 5px 12px; border-radius: 5px; text-decoration: none;
        font-size: 9pt; margin: 2px;
    }
    .qr-grid { gap: 20px; }
    .qr-card { width: 170px; padding: 12px; }
""" if mode == "tablet" else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
{vp}
<title>{_esc(title)}</title>
<style>
*,*::before,*::after{{box-sizing:border-box}}
body{{font-family:-apple-system,"Helvetica Neue",Arial,sans-serif;
     font-size:{base_fs};color:#1a252f;margin:0;background:#888}}
@media screen{{
  body{{padding:24px}}
  section{{background:white;margin:0 auto 24px;padding:0.7in;
          box-shadow:0 2px 14px rgba(0,0,0,.3);position:relative;overflow:hidden;
          min-width:6in;max-width:16.5in}}
  section.page-cover{{padding:0 0 0.7in}}
}}
@media print{{
  body{{background:white;padding:0}}
  section{{page-break-after:always}}
  section:last-child{{page-break-after:auto}}
  -webkit-print-color-adjust:exact;print-color-adjust:exact
  a{{color:#000!important;text-decoration:none}}
  input[type=checkbox]{{
    -webkit-appearance:none;appearance:none;
    border:1.5px solid #444;width:11px;height:11px;
    display:inline-block;vertical-align:middle}}
  tr.done td{{text-decoration:line-through;opacity:.55}}
}}
{page_css}
/* Cover */
.cover-hdr{{background:#1a252f;color:white;padding:2cm 0.7in 1.5cm;text-align:center;margin-bottom:.4in}}
.cover-title{{font-size:22pt;margin:0 0 6px;font-weight:bold}}
.cover-sub{{font-size:11pt;opacity:.75;margin:0}}
.cover-meta{{border-collapse:collapse;margin-bottom:18px}}
.cover-meta td{{padding:3px 12px 3px 0;font-size:10pt}}
.meta-lbl{{color:#666;font-weight:bold;white-space:nowrap}}
.cover-tbl{{border-collapse:collapse;width:100%;margin-bottom:18px;font-size:9pt}}
.cover-tbl th{{background:#1a252f;color:white;padding:4px 8px;text-align:left}}
.cover-tbl td{{padding:4px 8px;border:1px solid #ccc}}
h3{{font-size:10pt;margin:14px 0 4px;color:#1a252f;text-transform:uppercase;
    letter-spacing:.06em;border-bottom:1px solid #ccc;padding-bottom:3px}}
/* Table of Contents */
.toc-tbl{{border-collapse:collapse;width:100%;margin-bottom:18px}}
.toc-tbl td{{padding:7px 8px;border:none;border-bottom:1px solid #eee;font-size:10pt}}
.toc-num{{color:#1a252f;font-weight:bold;width:32px;text-align:right;
          padding-right:14px !important;font-size:11pt}}
.toc-tbl a{{color:#1a252f;text-decoration:none;font-weight:500}}
.toc-ext{{color:#888;font-style:italic}}
.toc-tbl tr:hover td{{background:#f0f4f8}}
/* General */
h2.sec-hdr{{font-size:13pt;color:#1a252f;border-bottom:2px solid #1a252f;
           padding-bottom:3px;margin:0 0 10px}}
table{{border-collapse:collapse;width:100%;margin-bottom:12px}}
th{{background:#1a252f;color:white;padding:5px 7px;text-align:left;font-size:8pt}}
td{{padding:4px 7px;border:1px solid #ddd;vertical-align:top;line-height:1.4;font-size:8pt}}
tr{{break-inside:avoid;page-break-inside:avoid}}
tr:nth-child(even){{background:#f7f9fc}}
.num{{text-align:center;font-weight:bold;color:#555}}
.wire{{font-family:"Courier New",monospace;font-size:7.5pt}}
.chk{{width:22px;text-align:center;padding:3px}}
.dim{{color:#666;font-style:italic;font-size:7.5pt}}
.std-ref{{color:#1a5276;font-size:7.5pt}}
.mb-warn{{background:#fff3cd;color:#7d4e00;font-weight:bold;padding:1px 5px;border-radius:3px}}
.empty-note{{color:#999;font-style:italic}}
a{{color:#1a5276}}
a.doc-link{{color:#2980b9}}
input[type=checkbox]{{width:13px;height:13px;cursor:pointer;accent-color:#1a252f}}
.qr-grid{{display:flex;flex-wrap:wrap;gap:14px;margin-top:12px}}
.qr-card{{width:155px;border:1px solid #ddd;border-radius:4px;padding:8px;text-align:center}}
.qr-lbl{{font-weight:bold;font-size:7.5pt;margin:4px 0 2px}}
.qr-url{{font-size:6pt;color:#666;word-break:break-all}}
.qr-note{{font-size:7.5pt;color:#888;font-style:italic;margin-bottom:10px}}
{tablet_css}
</style>
</head>
<body>
{body_html}
<script>
document.querySelectorAll('input[type=checkbox]').forEach(function(cb){{
  cb.addEventListener('change',function(){{
    var tr=this.closest('tr');
    if(tr)tr.classList[this.checked?'add':'remove']('done');
  }});
}});
</script>
</body></html>"""


def _ew_with_id(html: str, section_id: str) -> str:
    """Inject id attribute into the first <section tag in html."""
    if not section_id or not html:
        return html
    return html.replace("<section ", f'<section id="{section_id}" ', 1)


def _ew_cover(project, crows, date, toc_items, mode, css_class="page-cover",
              project_folder=""):
    def _crow_url_cell(c):
        url = c.get("url", "")
        if mode == "paper" or not url:
            return _esc(url)
        return f'<a href="{_esc(url)}">{_esc(url)}</a>'

    def _crow_files_cell(c):
        files = c.get("files", [])
        if not files:
            return ""
        if mode == "tablet" and project_folder:
            links = []
            for fname in files:
                local = os.path.join(project_folder, "CROW Outage", fname)
                rel   = os.path.join("CROW Outage", fname).replace("\\", "/")
                if os.path.isfile(local):
                    links.append(f'<a href="{_esc(rel)}">{_esc(fname)}</a>')
                else:
                    links.append(_esc(fname))
            return " ".join(links)
        return _esc(", ".join(files))

    has_files = any(c.get("files") for c in crows) if crows else False
    outage_rows = "".join(
        "<tr><td><b>{}</b></td><td>{}</td>{}</tr>".format(
            _esc(c.get("outage_number", "")),
            _crow_url_cell(c),
            f"<td>{_crow_files_cell(c)}</td>" if has_files else "",
        )
        for c in crows
    ) if crows else ""
    file_th = "<th>Documents</th>" if has_files else ""
    outage_html = (
        "<h3>Outage / CROW Numbers</h3>"
        "<table class='cover-tbl'><thead><tr><th>Outage #</th>"
        f"<th>URL</th>{file_th}</tr></thead>"
        f"<tbody>{outage_rows}</tbody></table>"
    ) if crows else ""
    toc_rows = "".join(
        f"<tr><td class='toc-num'>{i + 1}</td>"
        + (f"<td><a href='#{_esc(anch)}'>{_esc(label)}</a></td></tr>" if anch
           else f"<td><span class='toc-ext'>{_esc(label)}</span></td></tr>")
        for i, (label, anch) in enumerate(toc_items)
    ) if toc_items else ""
    toc_html = (
        "<h3>Table of Contents</h3>"
        f"<table class='toc-tbl'><tbody>{toc_rows}</tbody></table>"
    ) if toc_items else ""
    return (
        f'<section class="{css_class}">'
        f'<div class="cover-hdr">'
        f'<div style="font-size:32pt;margin-bottom:8px">&#9889;</div>'
        f'<div class="cover-title">{_esc(project) or "Red-Line-Routing"}</div>'
        f'<p class="cover-sub">Red-Line Routing Work Package</p>'
        f'</div>'
        f'<div style="padding:0 0.7in">'
        f'<table class="cover-meta"><tbody>'
        f'<tr><td class="meta-lbl">Generated</td><td>{_esc(date)}</td></tr>'
        f'</tbody></table>'
        f'{outage_html}{toc_html}'
        f'</div></section>\n'
    )


def _ew_work_orders(jobs, drawing_registry, mode, css_class="page-content"):
    reg = drawing_registry or {}

    def ep_r(ep):
        parts = []
        if ep.get("device"):   parts.append(f"<b>{_esc(ep['device'])}</b>")
        if ep.get("pin"):      parts.append(f"Pin {_esc(ep['pin'])}")
        if ep.get("location"): parts.append(_esc(ep["location"]))
        if ep.get("panel"):    parts.append(f"Panel {_esc(ep['panel'])}")
        if ep.get("drawing"):
            name = ep["drawing"]
            ri   = reg.get(name, {})
            rev  = f" Rev{_esc(ep['drawing_rev'])}" if ep.get("drawing_rev") else ""
            cell = f" [{_esc(ep['drawing_cell'])}]" if ep.get("drawing_cell") else ""
            url  = ep.get("drawing_url", "") or ri.get("url", "")
            ttl  = ri.get("title", "")
            ttl_h = f' <span class="dim">— {_esc(ttl)}</span>' if ttl else ""
            if url and mode != "paper":
                parts.append(f'<a href="{_esc(url)}">{_esc(name)}</a>{ttl_h}{rev}{cell}')
            else:
                parts.append(f'{_esc(name)}{ttl_h}{rev}{cell}')
        return "<br>".join(parts)

    def prot_r(prot):
        parts = []
        if prot.get("equipment"): parts.append(f"<b>{_esc(prot['equipment'])}</b>")
        if prot.get("location"):  parts.append(_esc(prot["location"]))
        if prot.get("panel"):     parts.append(f"Panel {_esc(prot['panel'])}")
        if prot.get("notes"):     parts.append(f"<i>{_esc(prot['notes'])}</i>")
        for d in _get_prot_drawings(prot):
            name = d.get("drawing", "")
            ri   = reg.get(name, {})
            url  = d.get("drawing_url", "") or ri.get("url", "")
            rev  = f" Rev{_esc(d['drawing_rev'])}" if d.get("drawing_rev") else ""
            if url and mode != "paper":
                parts.append(f'Dwg: <a href="{_esc(url)}">{_esc(name)}</a>{rev}')
            else:
                parts.append(f"Dwg: {_esc(name)}{rev}")
        for p in prot.get("iso_points", []):
            notes = f" — {_esc(p['notes'])}" if p.get("notes") else ""
            parts.append(f'<span class="dim">{_esc(p.get("iso_type","ISO"))} '
                         f'{_esc(p.get("reference",""))}{notes}</span>')
        if prot.get("mb_enabled"):
            remote = f" [{_esc(prot.get('mb_remote',''))}]" if prot.get("mb_remote") else ""
            parts.append(f'<span class="mb-warn">&#9888; MB INPUT — '
                         f'block/unblock required{remote}</span>')
        return "<br>".join(parts)

    tl = {
        "REMOVE":    "Remove Wire",    "ADD":       "Add Wire",
        "MOVE":      "Move Wire",      "BLOCK":     "Block Protection",
        "UNBLOCK":   "Restore Protection",
        "TESTING":   "Testing",        "ISOLATION": "Isolation",
        "CR_PROT":   "CR Protection",
    }
    rows = ""
    seq  = 1
    for job in jobs:
        jt  = job["type"]
        dsc = _esc(job.get("description", ""))
        ms  = _std_list(job, "maintenance_standards")
        es  = _std_list(job, "engineering_standards")
        stds = []
        if ms: stds.append("Maint: " + ", ".join(_esc(s) for s in ms))
        if es: stds.append("Eng: "   + ", ".join(_esc(s) for s in es))
        if stds:
            dsc += ("<br>" if dsc else "") + " &nbsp; ".join(
                f'<span class="std-ref">{s}</span>' for s in stds)

        def _tr(key, label, s_html, wire, e_html, _d=dsc):
            nonlocal seq
            bg = _ROW_STYLE.get(key, ("", ""))[0]
            ts = _ROW_STYLE.get(key, ("", ""))[1]
            bc = _ROW_BORDER.get(key, "#aaa")
            r = (f'<tr style="{bg}">'
                 f'<td class="chk" style="border-left:4px solid {bc}">'
                 f'<input type="checkbox"></td>'
                 f'<td class="num">{seq}</td>'
                 f'<td style="{ts}">{_esc(label)}</td>'
                 f'<td>{_d}</td><td>{s_html}</td>'
                 f'<td class="wire">{_esc(wire)}</td>'
                 f'<td>{e_html}</td></tr>')
            seq += 1
            return r

        if jt in ("REMOVE", "ADD"):
            notes = job.get("notes", "").strip()
            dsc_n = dsc + ("<br><em>" + _esc(notes) + "</em>" if notes else "")
            rows += _tr(jt, tl[jt],
                        ep_r(job.get("start", {})), job.get("wire", ""),
                        ep_r(job.get("end", {})), _d=dsc_n)
        elif jt == "MOVE":
            rows += _tr("MOVE-REMOVE", "Move — Remove",
                        ep_r(job.get("start", {})), job.get("wire", ""),
                        ep_r(job.get("end", {})))
            rows += _tr("MOVE-ADD", "Move — Add",
                        ep_r(job.get("add_start", {})), job.get("add_wire", ""),
                        ep_r(job.get("add_end", {})))
        elif jt in ("BLOCK", "UNBLOCK"):
            rows += _tr(jt, tl[jt], prot_r(job.get("protection", {})), "", "")
        elif jt == "TESTING":
            rows += _tr("TESTING", tl.get("TESTING", "Testing"),
                        _esc(job.get("notes", "")), "", "")
        elif jt == "ISOLATION":
            drawings = job.get("drawings", [])
            cell = "<br>".join(
                f"<b>{_esc(d.get('drawing',''))}</b>"
                + (f" Rev {_esc(d['drawing_rev'])}" if d.get("drawing_rev") else "")
                + (f" Cell {_esc(d['drawing_cell'])}" if d.get("drawing_cell") else "")
                for d in drawings)
            if job.get("notes"):
                cell += ("<br>" if cell else "") + _esc(job["notes"])
            rows += _tr("ISOLATION", tl.get("ISOLATION", "Isolation"), cell, "", "")
        elif jt == "CR_PROT":
            desks = job.get("desks", [])
            desk_parts = []
            for d in desks:
                phones = " / ".join(filter(None, [d.get("phone_int", ""),
                                                   d.get("phone_local", ""),
                                                   d.get("phone_toll", "")]))
                stations = ", ".join(d.get("stations", []))
                part = f"<b>{_esc(d.get('desk_name',''))}</b>"
                if d.get("desk_type"): part += f" ({_esc(d['desk_type'])})"
                if phones:   part += f"<br><small>&#128222; {_esc(phones)}</small>"
                if stations: part += f"<br><small>Stations: {_esc(stations)}</small>"
                desk_parts.append(part)
            cell = "<br>".join(desk_parts)
            crows = job.get("crows", [])
            if crows:
                cell += ("<br>" if cell else "") + "<small><b>CROWs:</b> " + _esc(", ".join(crows)) + "</small>"
            if job.get("notes"):
                cell += ("<br>" if cell else "") + f"<em>{_esc(job['notes'])}</em>"
            rows += _tr("CR_PROT", tl.get("CR_PROT", "CR Protection"), cell, "", "")

    return (
        f'<section class="{css_class}">'
        '<h2 class="sec-hdr">Work Orders</h2>'
        '<table>'
        '<thead><tr>'
        '<th class="chk">✓</th><th style="width:28px">#</th>'
        '<th style="width:110px">Type</th><th style="width:14%">Description</th>'
        '<th style="width:23%">Start Point / Device</th>'
        '<th style="width:80px">Wire</th>'
        '<th style="width:23%">End Point / Device</th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table></section>\n'
    )


def _ew_drawings_reg(reg, mode, project_folder="", css_class="page-content"):
    if not reg:
        return (f'<section class="{css_class}"><h2 class="sec-hdr">Drawings Register</h2>'
                '<p class="empty-note">No drawings registered.</p></section>\n')
    rows = ""
    for name, info in sorted(reg.items()):
        uc = _ew_url_cell(info.get("url", ""), "Open", mode, project_folder, ["Drawings"])
        rows += (f"<tr><td><b>{_esc(name)}</b></td>"
                 f"<td>{_esc(info.get('title',''))}</td>"
                 f"<td>{_esc(info.get('rev',''))}</td>"
                 f"<td>{uc}</td>"
                 f"<td>{_esc(info.get('notes',''))}</td></tr>")
    return (
        f'<section class="{css_class}"><h2 class="sec-hdr">Drawings Register</h2>'
        '<table><thead><tr>'
        '<th>Drawing #</th><th>Title</th><th>Rev</th><th>URL / Link</th><th>Notes</th>'
        f'</tr></thead><tbody>{rows}</tbody></table></section>\n'
    )


def _ew_relay_reg(reg, mode, project_folder="", css_class="page-content"):
    if not reg:
        return (f'<section class="{css_class}"><h2 class="sec-hdr">Relay Settings</h2>'
                '<p class="empty-note">No relay settings registered.</p></section>\n')
    rows = ""
    for dev_id, info in sorted(reg.items()):
        uc = _ew_url_cell(info.get("url", ""), "Open", mode, project_folder, ["Relay Settings"])
        rows += (f"<tr><td><b>{_esc(dev_id)}</b></td>"
                 f"<td>{_esc(info.get('title',''))}</td>"
                 f"<td>{_esc(info.get('revision',''))}</td>"
                 f"<td>{_esc(info.get('engineer',''))}</td>"
                 f"<td>{uc}</td></tr>")
    return (
        f'<section class="{css_class}"><h2 class="sec-hdr">Relay Settings</h2>'
        '<table><thead><tr>'
        '<th>Device ID</th><th>Title</th><th>Rev</th><th>Engineer</th><th>URL / Link</th>'
        f'</tr></thead><tbody>{rows}</tbody></table></section>\n'
    )


def _ew_standards(maint_reg, eng_reg, mode, project_folder="",
                  maint_css="page-content", eng_css="page-content"):
    parts = []
    if maint_reg:
        rows = ""
        for sid, info in sorted(maint_reg.items()):
            ut = info.get("url_telecom", "")
            ux = info.get("url_transmission", "")
            if mode == "paper":
                links = "; ".join(filter(None, [
                    (ut[:55] + "…" if len(ut) > 55 else ut) if ut else "",
                    (ux[:55] + "…" if len(ux) > 55 else ux) if ux else "",
                ]))
            else:
                lp = []
                sf = ["Maintenance Standards"]
                if ut: lp.append(_ew_url_cell(ut, "Telecom", mode, project_folder, sf))
                if ux: lp.append(_ew_url_cell(ux, "Trans",   mode, project_folder, sf))
                links = " ".join(lp)
            rows += (f"<tr><td><b>{_esc(sid)}</b></td>"
                     f"<td>{_esc(info.get('title',''))}</td>"
                     f"<td>{_esc(info.get('revision',''))}</td>"
                     f"<td>{links}</td>"
                     f"<td>{_esc(info.get('notes',''))}</td></tr>")
        parts.append(
            f'<section class="{maint_css}"><h2 class="sec-hdr">Maintenance Standards</h2>'
            '<table><thead><tr>'
            '<th>Standard ID</th><th>Title</th><th>Rev</th><th>Links</th><th>Notes</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></section>\n'
        )
    if eng_reg:
        rows = ""
        for sid, info in sorted(eng_reg.items()):
            uc = _ew_url_cell(info.get("url", ""), "Open", mode,
                               project_folder, ["Engineering Standards"])
            rows += (f"<tr><td><b>{_esc(sid)}</b></td>"
                     f"<td>{_esc(info.get('title',''))}</td>"
                     f"<td>{_esc(info.get('revision',''))}</td>"
                     f"<td>{_esc(info.get('standard_type',''))}</td>"
                     f"<td>{uc}</td>"
                     f"<td>{_esc(info.get('notes',''))}</td></tr>")
        parts.append(
            f'<section class="{eng_css}"><h2 class="sec-hdr">Engineering Standards</h2>'
            '<table><thead><tr>'
            '<th>Standard ID</th><th>Title</th><th>Rev</th><th>Type</th>'
            '<th>URL / Link</th><th>Notes</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></section>\n'
        )
    return "".join(parts)


def _ew_qr_sheet(items, css_class="page-content"):
    """QR code reference sheet (paper mode). Requires internet to render codes."""
    if not items:
        return ""
    cards = ""
    for label, url in items:
        if not url:
            continue
        qr = ("https://api.qrserver.com/v1/create-qr-code/"
              f"?size=100x100&data={urllib.parse.quote(url, safe='')}")
        cards += (
            f'<div class="qr-card">'
            f'<img src="{_esc(qr)}" width="100" height="100" alt="QR" loading="lazy">'
            f'<div class="qr-lbl">{_esc(label)}</div>'
            f'<div class="qr-url">{_esc(url)}</div>'
            f'</div>'
        )
    return (
        f'<section class="{css_class}"><h2 class="sec-hdr">Document URLs</h2>'
        '<p class="qr-note">QR codes require internet access when opening this file. '
        'Scan or type URLs to access documents.</p>'
        f'<div class="qr-grid">{cards}</div></section>\n'
    )


def _ew_embedded_files(project_folder, subfolder, mode, css_class="page-content",
                       recurse=False, crow_files=None):
    """Append embedded/linked local documents after a registry section.

    ``crow_files`` overrides directory scanning — pass a list of filenames
    already copied into ``<project_folder>/<subfolder>/``.
    """
    if not project_folder or mode == "digital":
        return ""

    full_dir = os.path.join(project_folder, subfolder)

    if crow_files is not None:
        found = []
        for fname in crow_files:
            fpath = os.path.join(full_dir, fname)
            if os.path.isfile(fpath):
                rel = os.path.join(subfolder, fname).replace("\\", "/")
                found.append((fname, rel))
    else:
        found = [(os.path.basename(full), rel)
                 for full, rel in _iter_project_files(
                     project_folder, subfolder, recurse=recurse, exts=_EMBED_EXTS)]

    if not found:
        return ""

    items = []
    for fname, rel in found:
        ext = os.path.splitext(fname)[1].lower()
        if ext in _PDF_EXTS:
            items.append(
                f'<div style="page-break-before:always;margin:0;padding:0">'
                f'<p style="font-size:7pt;color:#aaa;margin:0 0 2px;'
                f'font-family:monospace">{_esc(fname)}</p>'
                f'<embed src="{_esc(rel)}" type="application/pdf" '
                f'width="100%" style="height:10.5in;border:none;display:block">'
                f'</div>\n'
            )
        else:
            items.append(
                f'<p style="margin:4px 0"><a href="{_esc(rel)}">'
                f'&#128196; {_esc(fname)}</a>'
                f' <span style="font-size:8pt;color:#888">'
                f'(open in Word to print)</span></p>\n'
            )

    return f'<section class="{css_class}">{"".join(items)}</section>\n'


# ── Tablet zip builder ─────────────────────────────────────────────


def _build_tablet_zip(app, html, folder, inc, sections, project, date_s):
    """Package the tablet export as a single zip for transfer to a tablet.

    Zip layout (stable contract for external reader apps):
        manifest.json     – package description: format/version, project name,
                            export timestamp, section modes, file inventory
        index.html        – the tablet HTML; relative links resolve in-place
                            once the zip is extracted
        project.redline   – full project JSON, same format as a saved project
        <subfolders>/…    – documents for every section set to "print",
                            plus CROW Outage attachments (always on the cover)

    Returns the path of the written zip.
    """
    file_entries = []
    for key, sub, rec in _TABLET_SECTION_DIRS:
        if inc.get(key) != "print":
            continue
        for full, rel in _iter_project_files(folder, sub, recurse=rec):
            file_entries.append((full, rel, key))
    for full, rel in _iter_project_files(folder, "CROW Outage"):
        file_entries.append((full, rel, "crow"))

    tp = dict(app.title_page)
    try:
        tp["notes"] = app.title_notes.get("1.0", "end").strip()
    except Exception:
        pass
    project_json = json.dumps({
        "project":               project,
        "title_page":            tp,
        "drawing_registry":      app.drawing_registry,
        "relay_settings":        app.relay_registry,
        "maintenance_standards": app.maintenance_standards_registry,
        "engineering_standards": app.engineering_standards_registry,
        "history":               app.history,
        "jobs":                  app.jobs,
    }, indent=2)

    manifest = json.dumps({
        "format":         _TABLET_PKG_FORMAT,
        "format_version": _TABLET_PKG_VERSION,
        "generator":      "Red-Line-Routing Wire Planner",
        "project":        project,
        "exported":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entry_html":     "index.html",
        "entry_project":  "project.redline",
        "sections": [
            {"key": k, "label": lbl, "mode": inc.get(k, "skip")}
            for k, lbl in sections
        ],
        "files": [
            {"path": rel, "section": sec, "size": os.path.getsize(full)}
            for full, rel, sec in file_entries
        ],
    }, indent=2)

    zpath = os.path.join(folder, f"Tablet_{date_s}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest)
        zf.writestr("index.html", html)
        zf.writestr("project.redline", project_json)
        seen = set()
        for full, rel, _sec in file_entries:
            if rel in seen:
                continue
            seen.add(rel)
            zf.write(full, rel)
    return zpath
