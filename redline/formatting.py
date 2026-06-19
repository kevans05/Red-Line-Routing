"""Job-preview text formatting and HTML colour theme."""
from .models import _get_prot_drawings

W = 62


def _bar(char="="):
    return char * W


def _ep_block(ep, label):
    lines = [f"  {label}"]
    for key, disp in [("device",       "  Device      "),
                      ("location",     "  Location    "),
                      ("pin",          "  Pin         "),
                      ("panel",        "  Panel       "),
                      ("drawing",      "  Drawing     "),
                      ("drawing_rev",  "  Drawing Rev "),
                      ("drawing_url",  "  Drawing URL "),
                      ("drawing_cell", "  Drawing Cell")]:
        lines.append(f"    {disp}: {ep.get(key, '')}")
    return "\n".join(lines)


def _prot_block(prot, label):
    lines = [f"  {label}"]
    for key, disp in [("equipment", "  Equipment   "),
                      ("location",  "  Location    "),
                      ("panel",     "  Panel       "),
                      ("notes",     "  Notes       ")]:
        lines.append(f"    {disp}: {prot.get(key, '')}")
    drawings = _get_prot_drawings(prot)
    if drawings:
        lines.append("")
        for i, d in enumerate(drawings, 1):
            n = f" #{i}" if len(drawings) > 1 else ""
            lines.append(f"    Drawing{n}")
            lines.append(f"      Name    : {d.get('drawing', '')}")
            if d.get("drawing_rev"):  lines.append(f"      Rev     : {d['drawing_rev']}")
            if d.get("drawing_url"):  lines.append(f"      URL     : {d['drawing_url']}")
            if d.get("drawing_cell"): lines.append(f"      Cell    : {d['drawing_cell']}")
    iso_points = prot.get("iso_points", [])
    if iso_points:
        lines.append("")
        lines.append("    Isolation / FT Points")
        for p in iso_points:
            equip = f"  ({p['equipment']})" if p.get("equipment") else ""
            notes = f"  — {p['notes']}" if p.get("notes") else ""
            lines.append(f"      {p.get('iso_type', 'ISO')} block  {p.get('reference', '')}{equip}{notes}")
    if prot.get("mb_enabled"):
        lines.append("")
        remote = f"  Remote: {prot['mb_remote']}" if prot.get("mb_remote") else ""
        notes  = f"  ({prot['mb_notes']})"        if prot.get("mb_notes")  else ""
        lines.append(f"  *** MIRRORED BIT — also required: MB INPUT block/unblock{remote}{notes} ***")
    return "\n".join(lines)


def _std_list(job, key):
    """Return a list of standard IDs for a job key, handling legacy single-string values."""
    val = job.get(key, [])
    if isinstance(val, str):
        return [val] if val.strip() else []
    return [v for v in val if v]


def format_job(index, job):
    jtype = job["type"]
    labels = {
        "REMOVE":        "REMOVE WIRE",
        "ADD":           "ADD WIRE",
        "MOVE":          "MOVE WIRE",
        "BLOCK":         "BLOCK PROTECTION",
        "UNBLOCK":       "RESTORE PROTECTION",
        "TESTING":       "TESTING / NOTE",
        "ISOLATION":     "ISOLATION",
        "CR_PROT":       "CONTROL ROOM PROTECTION",
        "DEVICE ADD":    "INSTALL DEVICE",
        "DEVICE REMOVE": "REMOVE DEVICE",
    }
    lines = [_bar(), f"  JOB #{index+1}   [{labels.get(jtype, jtype)}]", _bar()]
    if job.get("description"):
        lines += ["", "  DESCRIPTION", f"    {job['description']}"]
    if jtype in ("REMOVE", "ADD"):
        lines += ["", _ep_block(job.get("start", {}), "START POINT / DEVICE"),
                  "", f"  WIRE: {job.get('wire', '')}",
                  "", _ep_block(job.get("end", {}), "END POINT / DEVICE")]
        if job.get("notes"):
            lines += ["", "  NOTES", *[f"    {ln}" for ln in job["notes"].splitlines()]]
    elif jtype == "MOVE":
        lines += ["", "  " + "─" * 30 + "  REMOVE  " + "─" * (W - 42),
                  "", _ep_block(job.get("start", {}), "REMOVE: Start Point / Device"),
                  "", f"  WIRE (Remove): {job.get('wire', '')}",
                  "", _ep_block(job.get("end", {}), "REMOVE: End Point / Device"),
                  "", "  " + "─" * 31 + "  ADD  " + "─" * (W - 39),
                  "", _ep_block(job.get("add_start", {}), "ADD: Start Point / Device"),
                  "", f"  WIRE (Add):    {job.get('add_wire', '')}",
                  "", _ep_block(job.get("add_end", {}), "ADD: End Point / Device")]
    elif jtype in ("BLOCK", "UNBLOCK"):
        lbl = "BLOCK PROTECTION" if jtype == "BLOCK" else "RESTORE PROTECTION"
        lines += ["", _prot_block(job.get("protection", {}), lbl)]
    elif jtype in ("DEVICE ADD", "DEVICE REMOVE"):
        lines += ["", _ep_block(job.get("endpoint", {}), "DEVICE / LOCATION")]
        if job.get("notes"):
            lines += ["", "  NOTES", *[f"    {ln}" for ln in job["notes"].splitlines()]]
    elif jtype == "TESTING":
        if job.get("notes"):
            lines += ["", "  NOTES", *[f"    {ln}" for ln in job["notes"].splitlines()]]
        if job.get("pts_file"):
            lines += ["", f"  PTS FILE  {job['pts_file']}"]
    elif jtype == "ISOLATION":
        drawings = job.get("drawings", [])
        if drawings:
            lines += ["", "  DRAWINGS"]
            for d in drawings:
                line = f"    {d.get('drawing', '')}"
                if d.get("drawing_rev"):  line += f"  Rev {d['drawing_rev']}"
                if d.get("drawing_cell"): line += f"  Cell {d['drawing_cell']}"
                lines.append(line)
        if job.get("notes"):
            lines += ["", "  NOTES", *[f"    {ln}" for ln in job["notes"].splitlines()]]
    elif jtype == "CR_PROT":
        desks = job.get("desks", [])
        if desks:
            lines += ["", "  CONTROL ROOM DESKS"]
            for d in desks:
                phones = "  /  ".join(filter(None, [d.get("phone_int", ""),
                                                     d.get("phone_local", ""),
                                                     d.get("phone_toll", "")]))
                lines.append(f"    {d.get('desk_name', '')}  [{d.get('desk_type', '')}]")
                if phones:    lines.append(f"      Phones   : {phones}")
                if d.get("stations"):
                    lines.append(f"      Stations : {', '.join(d['stations'])}")
        crows = job.get("crows", [])
        if crows:
            lines += ["", "  CROW OUTAGES", *[f"    {c}" for c in crows]]
        if job.get("notes"):
            lines += ["", "  NOTES", *[f"    {ln}" for ln in job["notes"].splitlines()]]
    ms = _std_list(job, "maintenance_standards")
    es = _std_list(job, "engineering_standards")
    if ms or es:
        lines += ["", "  STANDARDS"]
        if ms: lines.append("    Maintenance: " + ", ".join(ms))
        if es: lines.append("    Engineering: " + ", ".join(es))
    lines.append("")
    return "\n".join(lines)


# Job row colour theme (shared by Export Wizard HTML generators)
_ROW_STYLE = {
    "REMOVE":      ("background:#fde8e6", "color:#922b21;font-weight:bold"),
    "ADD":         ("background:#e8f8ee", "color:#1e8449;font-weight:bold"),
    "MOVE-REMOVE": ("background:#fef0e6", "color:#a04000;font-weight:bold"),
    "MOVE-ADD":    ("background:#fefbe6", "color:#7d6608;font-weight:bold"),
    "BLOCK":       ("background:#fef3e6", "color:#a04000;font-weight:bold"),
    "UNBLOCK":     ("background:#e6f6f3", "color:#0e6655;font-weight:bold"),
    "TESTING":     ("background:#f5eef8", "color:#6c3483;font-weight:bold"),
    "ISOLATION":   ("background:#e8f4f8", "color:#1a6b8a;font-weight:bold"),
    "CR_PROT":     ("background:#e8f1f8", "color:#1a5276;font-weight:bold"),
}

_ROW_BORDER = {
    "REMOVE":      "#c0392b",
    "ADD":         "#27ae60",
    "MOVE-REMOVE": "#e67e22",
    "MOVE-ADD":    "#d4ac0d",
    "BLOCK":       "#ca6f1e",
    "UNBLOCK":     "#148f77",
    "TESTING":     "#7d3c98",
    "ISOLATION":   "#1a6b8a",
    "CR_PROT":     "#1a5276",
}


def _esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
