"""Core data-model factories and job-type constants."""


def _empty_tailboard_refs():
    """Per-project URLs of the living tailboard reference documents."""
    return {"tailboard":   {"url": ""},
            "hbr":         {"url": ""},
            "loa":         {"url": ""},
            "safety_regs": {"url": ""}}


def empty_endpoint():
    return {"device": "", "location": "", "pin": "", "panel": "",
            "drawing": "", "drawing_rev": "", "drawing_url": "", "drawing_cell": ""}


def empty_protection():
    return {"equipment": "", "location": "", "panel": "", "notes": "",
            "drawings": [], "iso_points": [], "mb_enabled": False, "mb_remote": "", "mb_notes": ""}


def empty_job(job_type="REMOVE"):
    if job_type in ("BLOCK", "UNBLOCK"):
        return {"type": job_type, "description": "", "protection": empty_protection()}
    if job_type == "TESTING":
        return {"type": job_type, "description": "", "notes": "", "pts_file": ""}
    if job_type == "ISOLATION":
        return {"type": job_type, "description": "", "drawings": [], "notes": ""}
    if job_type == "CR_PROT":
        return {"type": "CR_PROT", "description": "", "desks": [], "crows": [], "notes": ""}
    if job_type in ("DEVICE ADD", "DEVICE REMOVE"):
        return {"type": job_type, "description": "",
                "endpoint": empty_endpoint(), "notes": ""}
    job = {"type": job_type, "description": "", "wire": "",
           "start": empty_endpoint(), "end": empty_endpoint(), "notes": ""}
    if job_type == "MOVE":
        job["add_wire"] = ""
        job["add_start"] = empty_endpoint()
        job["add_end"] = empty_endpoint()
    return job


def _get_prot_drawings(prot):
    """Return the drawings list from a protection dict (handles old single-drawing format)."""
    drawings = prot.get("drawings", [])
    if not drawings and prot.get("drawing"):
        drawings = [{"drawing":      prot.get("drawing", ""),
                     "drawing_rev":  prot.get("drawing_rev", ""),
                     "drawing_url":  prot.get("drawing_url", ""),
                     "drawing_cell": prot.get("drawing_cell", "")}]
    return drawings


# Short job-type labels used in the Work Order and Implementation treeviews.
# "UNBLOCK" is kept as the internal key for backwards compatibility with saved
# .redline files, but displays as RESTORE.
JOB_TYPE_SHORT = {
    "REMOVE":        "REMOVE",
    "ADD":           "ADD",
    "MOVE":          "MOVE",
    "BLOCK":         "BLOCK PROT.",
    "UNBLOCK":       "RESTORE PROT.",
    "TESTING":       "TESTING",
    "ISOLATION":     "ISOLATION",
    "CR_PROT":       "CR PROTECTION",
    "DEVICE ADD":    "INSTALL DEVICE",
    "DEVICE REMOVE": "REMOVE DEVICE",
}
