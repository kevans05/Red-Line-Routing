"""Job/endpoint/protection/registry shapes shared by server/ and client/.

Ported from wire_planner.py's empty_job()/empty_endpoint()/empty_protection()/
JOB_TYPE_SHORT so job dicts stay compatible with existing .redline files —
these are deliberately plain dicts (not dataclasses) for the same reason:
a job's shape varies by `type`, and .redline files already store this exact
JSON shape, so round-tripping through dataclasses would just add a
translation layer with nothing to translate.
"""

# Short job-type labels used in UI treeviews/lists.
# Single source of truth — the internal type key "UNBLOCK" is kept for
# backwards compatibility with saved .redline files, but displays as RESTORE.
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

JOB_TYPES = tuple(JOB_TYPE_SHORT.keys())


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


def empty_registry_entry():
    """Shape shared by drawings/maintenance_standards/engineering_standards rows."""
    return {"title": "", "rev": "", "url": "", "notes": ""}


# ── job_steps: new in the server rebuild, the desktop app never had this ──
# Per-job checklist entries with full attribution (who added, who's
# assigned, who completed, when). Status is one of STEP_STATUSES.

STEP_STATUSES = ("pending", "in_progress", "complete")


def empty_job_step():
    return {
        "id": "",
        "job_id": "",
        "description": "",
        "status": "pending",
        "added_by": "",
        "added_at": "",
        "assigned_to": "",
        "completed_by": "",
        "completed_at": "",
    }


# ── RBAC roles ──────────────────────────────────────────────────────

ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"
ROLE_APPROVER = "approver"
ROLE_VIEWER = "viewer"

ROLES = (ROLE_ADMIN, ROLE_EDITOR, ROLE_APPROVER, ROLE_VIEWER)

# Roles are cumulative in privilege for the simple checks server/rbac.py
# needs (e.g. "at least editor"); admin implies editor/approver/viewer, etc.
ROLE_RANK = {ROLE_VIEWER: 0, ROLE_APPROVER: 1, ROLE_EDITOR: 2, ROLE_ADMIN: 3}
