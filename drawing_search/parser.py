"""Parse the HTML search-results table returned by the corporate drawing search."""
import re
from html.parser import HTMLParser
from .models import DrawingResult

# Column indices in the result table (0-based, after the hidden ID and checkbox columns)
_COL_DRAWING_NUM   = 0
_COL_TITLE         = 1
_COL_FACILITY      = 2
_COL_DRAWING_TYPE  = 3
_COL_DRAWING_SUBJ  = 4
_COL_PAPER_SIZE    = 5
_COL_REVISION      = 6
_COL_CONFIDENTIAL  = 7
_COL_STATE         = 8
_COL_SIGNED_OUT    = 9
_COL_PHYS_LOC      = 10
_COL_LEGACY_DOC    = 11

_FETCH_PATH = "searchGT/fetchDocument.html"


class _TableParser(HTMLParser):
    """Minimal state-machine parser that walks the results <tbody>."""

    def __init__(self, base_url: str):
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self.results: list[DrawingResult] = []

        # parser state
        self._in_tbody   = False
        self._in_tr      = False
        self._in_td      = False
        self._col        = -1          # logical column index (skips hidden + checkbox)
        self._skip_cols  = 2           # hidden ID col + checkbox col
        self._raw_cols   = 0           # raw <td> counter in current row
        self._current    = DrawingResult()
        self._text_buf   = ""
        self._in_a       = False
        self._href       = ""

    # ── HTMLParser callbacks ──────────────────────────────────────

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tbody":
            self._in_tbody = True
        elif tag == "tr" and self._in_tbody:
            self._in_tr   = True
            self._raw_cols = 0
            self._col      = -1
            self._current  = DrawingResult()
        elif tag == "td" and self._in_tr:
            self._in_td   = True
            self._raw_cols += 1
            self._text_buf = ""
            # First two columns (hidden ID + checkbox) are skipped for data extraction
            if self._raw_cols > self._skip_cols:
                self._col += 1
        elif tag == "a" and self._in_td:
            self._in_a = True
            self._href = attrs.get("href", "")

    def handle_endtag(self, tag):
        if tag == "tbody":
            self._in_tbody = False
        elif tag == "tr" and self._in_tbody:
            if self._current.drawing_number:
                self.results.append(self._current)
            self._in_tr = False
        elif tag == "td" and self._in_tr:
            text = self._text_buf.strip()
            self._assign_col(self._col, text)
            self._in_td  = False
            self._in_a   = False
            self._href   = ""
        elif tag == "a":
            self._in_a = False

    def handle_data(self, data):
        if self._in_td:
            self._text_buf += data

    # ── column assignment ─────────────────────────────────────────

    def _assign_col(self, col: int, text: str):
        r = self._current
        if col == _COL_DRAWING_NUM:
            r.drawing_number = text
            # Build absolute document URL from the href captured inside the <a>
            if self._href:
                if self._href.startswith("http"):
                    r.document_url = self._href
                else:
                    r.document_url = f"{self._base_url}/{self._href.lstrip('/')}"
                # Extract documentId value for the document_id field
                m = re.search(r"documentId=([^&]+)", self._href, re.IGNORECASE)
                if m:
                    r.document_id = m.group(1)
        elif col == _COL_TITLE:        r.title              = text
        elif col == _COL_FACILITY:     r.facility           = text
        elif col == _COL_DRAWING_TYPE: r.drawing_type       = text
        elif col == _COL_DRAWING_SUBJ: r.drawing_subject    = text
        elif col == _COL_PAPER_SIZE:   r.paper_size         = text
        elif col == _COL_REVISION:     r.revision           = text
        elif col == _COL_CONFIDENTIAL: r.confidentiality    = text
        elif col == _COL_STATE:        r.state              = text
        elif col == _COL_SIGNED_OUT:   r.signed_out         = text.strip().lower() == "yes"
        elif col == _COL_PHYS_LOC:     r.physical_location  = text
        elif col == _COL_LEGACY_DOC:   r.legacy_doc_number  = text.rstrip(";").strip()


def parse_results(html: str, base_url: str) -> list[DrawingResult]:
    """Return a list of DrawingResult objects parsed from a search-result HTML page."""
    p = _TableParser(base_url)
    p.feed(html)
    return p.results
