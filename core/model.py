"""Headless (de)serialisation for the .redline project format.

Extracted from RedLineApp._write / RedLineApp._open so a project can be read
and written without a Tk app — the prerequisite for testing, versioning and
sync. Pure standard library, no GUI imports.

Two key naming facts are encapsulated here:

* The in-memory "state" uses the RedLineApp *attribute* names
  (e.g. ``relay_registry``); the on-disk dict uses the historical *.redline*
  key names (e.g. ``relay_settings``). ``to_dict`` / ``from_dict`` translate.
* ``to_dict`` emits keys in the exact order older versions wrote them, so the
  output stays byte-for-byte compatible with existing files.

For a *complete* project ``to_dict(from_dict(data)) == data``; for an older
*partial* file the missing keys are filled with defaults (normalisation) and
the result is stable on every subsequent round-trip.
"""


import uuid

# Current on-disk schema version. Bumped when the .redline format changes in a
# way the loader must account for. Files written before this predate the key.
SCHEMA = 2


def new_id():
    """A fresh, stable entity id (hex UUID4)."""
    return uuid.uuid4().hex


def ensure_ids(state):
    """Idempotently give a loaded *state* stable identity — a ``plan_id`` and an
    ``id`` per job — and upgrade the schema marker. Existing ids are never
    changed, so re-running is a no-op. Mutates and returns *state*."""
    if not state.get("plan_id"):
        state["plan_id"] = new_id()
    if (state.get("schema") or 0) < SCHEMA:
        state["schema"] = SCHEMA
    for job in state.get("jobs", []):
        if not job.get("id"):
            job["id"] = new_id()
    return state


def _default_history():
    return {"device": [], "location": [], "pin": [], "panel": [], "wire": []}


def _default_tailboard_refs():
    return {"tailboard": {"url": ""}, "hbr": {"url": ""},
            "loa": {"url": ""}, "safety_regs": {"url": ""}}


def _default_title_page():
    return {"notes": "", "crows": []}


def to_dict(state):
    """In-memory state dict -> .redline JSON dict (canonical key order)."""
    return {
        "schema":                state.get("schema", SCHEMA),
        "plan_id":               state.get("plan_id"),
        "project":               state.get("project", ""),
        "title_page":            state.get("title_page", _default_title_page()),
        "drawing_registry":      state.get("drawing_registry", {}),
        "relay_settings":        state.get("relay_registry", {}),          # on-disk key
        "maintenance_standards": state.get("maintenance_standards_registry", {}),
        "engineering_standards": state.get("engineering_standards_registry", {}),
        "pts_files":             state.get("pts_files", {}),
        "tailboard_refs":        state.get("tailboard_refs", _default_tailboard_refs()),
        "history":               state.get("history", _default_history()),
        "jobs":                  state.get("jobs", []),
    }


def from_dict(data):
    """Loaded .redline JSON dict -> in-memory state dict (attribute names)."""
    return {
        "schema":                         data.get("schema", 1),
        "plan_id":                        data.get("plan_id"),
        "project":                        data.get("project", ""),
        "jobs":                           data.get("jobs", []),
        "drawing_registry":               data.get("drawing_registry", {}),
        "relay_registry":                 data.get("relay_settings", {}),   # on-disk key
        "maintenance_standards_registry": data.get("maintenance_standards", {}),
        "engineering_standards_registry": data.get("engineering_standards", {}),
        "pts_files":                      data.get("pts_files", {}),
        "history":                        data.get("history", _default_history()),
        "title_page":                     data.get("title_page", _default_title_page()),
        "tailboard_refs":                 data.get("tailboard_refs", _default_tailboard_refs()),
    }
