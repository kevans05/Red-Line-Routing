"""Lookup tables for facility codes, drawing type codes, and drawing subject codes.

These are placeholder values — the organisation fills in the real lists.
Structure matters more than the specific codes shown here.
"""

# ---------------------------------------------------------------------------
# Facility codes  {code: label}
# ---------------------------------------------------------------------------
FACILITIES: dict[str, str] = {
    "111j": "Site 111J (placeholder)",
    # Add more facility codes here
}

# ---------------------------------------------------------------------------
# Drawing type codes  {code: label}
# ---------------------------------------------------------------------------
DRAWING_TYPES: dict[str, str] = {
    "A":  "A — General Arrangement",
    "E":  "E — Electrical",
    "H":  "H — Horizontal",
    "I":  "I — Instrument",
    "M":  "M — Mechanical",
    "P":  "P — Piping",
    "S":  "S — Structural",
    "T":  "T — Telecom",
    # Add more type codes here
}

# ---------------------------------------------------------------------------
# Drawing subject codes  {code: label}
# ---------------------------------------------------------------------------
DRAWING_SUBJECTS: dict[str, str] = {
    "06": "06 — Protection & Control",
    "07": "07 — Metering",
    "08": "08 — Communications",
    "10": "10 — AC Power",
    "11": "11 — DC Power",
    "20": "20 — Grounding",
    # Add more subject codes here
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_type_label(code: str) -> str:
    """Return the human-readable label for a drawing type code, or the code itself."""
    return DRAWING_TYPES.get(code, code)


def get_subject_label(code: str) -> str:
    """Return the human-readable label for a drawing subject code, or the code itself."""
    return DRAWING_SUBJECTS.get(code, code)
