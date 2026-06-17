"""Data models for engineering standards search results."""
import re
from dataclasses import dataclass, field

_HTML_TAG = re.compile(r'<[^>]+>')


def _strip_html(s: str) -> str:
    return _HTML_TAG.sub('', s).strip() if s else ''


@dataclass
class EngineeringSeries:
    value: str       # used as ?series= query param, e.g. "00 General"
    title: str       # clean title (HTML stripped)


@dataclass
class EngineeringStandard:
    standard_id: str        # e.g. "ES 00-A0001 R00"
    description: str        # e.g. "Legal Notice"
    series_value: str       # parent series value
    node_value: str         # GUID from "value" field — used as document ID
    release: str
    major_version: int
    document_state: str     # "Active", "Superseded", etc.
    document_date: int      # unix ms timestamp (0 if absent)
    version_series_id: str
    revision_serial_id: str
    url: str = ""           # download/view URL
