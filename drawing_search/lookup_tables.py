"""Lookup tables for facility codes, drawing type codes, and drawing subject codes.

The static dicts below are placeholder fallbacks.  Call ``fetch_form_options``
(from ``drawing_search.form_fetcher``) to pull live data from the corporate
server, then ``save_cached_options`` to persist it.  On next import
``FACILITIES``, ``DRAWING_TYPES``, and ``DRAWING_SUBJECTS`` are updated from
the cache automatically.
"""

import json
import os

_OPTIONS_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".redlinerouting_drawing_options.json"
)

# ---------------------------------------------------------------------------
# Static fallback tables  {code: label}
# (overwritten below if a cache file exists)
# ---------------------------------------------------------------------------

FACILITIES: dict[str, str] = {
    "111j": "Site 111J (placeholder)",
    # Add more facility codes here, or fetch live via fetch_form_options()
}

DRAWING_TYPES: dict[str, str] = {
    "A": "Architectural",
    "E": "Electrical",
    "H": "Horizontal",
    "I": "Instrument",
    "M": "Mechanical",
    "P": "Piping",
    "S": "Structural",
    "T": "Telecom",
}

DRAWING_SUBJECTS: dict[str, str] = {
    "06": "Protection & Control",
    "07": "Metering",
    "08": "Communications",
    "10": "AC Power",
    "11": "DC Power",
    "20": "Grounding",
}

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def load_cached_options(path: str = _OPTIONS_CACHE_PATH) -> dict | None:
    """Load previously fetched options from the JSON cache file.

    Returns a dict with keys ``facilities``, ``drawing_types``,
    ``drawing_subjects``, or ``None`` if the file doesn't exist / is invalid.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "drawing_types" in data:
            return data
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return None


def save_cached_options(
    data: dict[str, dict[str, str]],
    path: str = _OPTIONS_CACHE_PATH,
) -> None:
    """Save fetched form options to the JSON cache file."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_type_label(code: str) -> str:
    """Return the human-readable label for a drawing type code, or the code itself."""
    return DRAWING_TYPES.get(code, code)


def get_subject_label(code: str) -> str:
    """Return the human-readable label for a drawing subject code, or the code itself."""
    return DRAWING_SUBJECTS.get(code, code)


# ---------------------------------------------------------------------------
# On import: overlay the static tables with any cached live data
# ---------------------------------------------------------------------------

def _apply_cached_options() -> None:
    cached = load_cached_options()
    if cached is None:
        return
    if cached.get("facilities"):
        FACILITIES.clear(); FACILITIES.update(cached["facilities"])
    if cached.get("drawing_types"):
        DRAWING_TYPES.clear(); DRAWING_TYPES.update(cached["drawing_types"])
    if cached.get("drawing_subjects"):
        DRAWING_SUBJECTS.clear(); DRAWING_SUBJECTS.update(cached["drawing_subjects"])


_apply_cached_options()
