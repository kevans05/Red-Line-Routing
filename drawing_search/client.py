"""HTTP client for the corporate drawing search (POST form interface).

Usage
-----
from drawing_search import DrawingSearchClient, SearchParams

client = DrawingSearchClient(
    base_url="https://CORPRATEINTERNAL.COM",
    cookies={"filenet-es": "...", "_WL_AUTHCOOKIE_filenet-es": "..."},
)
results = client.search(SearchParams(facility="111j", drawing_type="A", drawing_subject="06"))
for r in results:
    print(r.drawing_number, r.title, r.document_url)
"""

import urllib.request
import urllib.parse
import urllib.error
from typing import Optional

from .models import DrawingResult
from .parser import parse_results

_SEARCH_PATH = "/search/searchGT.html"
_DEFAULT_TIMEOUT = 60  # seconds


class DrawingSearchClient:
    """Thin wrapper around the corporate drawing search POST endpoint."""

    def __init__(
        self,
        base_url: str,
        cookies: Optional[dict[str, str]] = None,
        timeout: int = _DEFAULT_TIMEOUT,
        user_agent: str = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
        ),
    ):
        self.base_url   = base_url.rstrip("/")
        self.cookies    = cookies or {}
        self.timeout    = timeout
        self.user_agent = user_agent

    # ── public API ────────────────────────────────────────────────

    def search(self, params: "SearchParams") -> list[DrawingResult]:
        """Execute a drawing search and return parsed results."""
        html = self._post(params._to_form_data())
        return parse_results(html, self.base_url)

    # ── internals ─────────────────────────────────────────────────

    def _post(self, form_data: dict[str, str]) -> str:
        url      = self.base_url + _SEARCH_PATH
        body     = urllib.parse.urlencode(form_data).encode("utf-8")
        cookie_h = "; ".join(f"{k}={v}" for k, v in self.cookies.items())

        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type":  "application/x-www-form-urlencoded",
                "Accept":        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "max-age=0",
                "Origin":        self.base_url,
                "Referer":       self.base_url + _SEARCH_PATH,
                "User-Agent":    self.user_agent,
                **({"Cookie": cookie_h} if cookie_h else {}),
            },
        )

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            charset = _charset_from_headers(resp.headers)
            return resp.read().decode(charset, errors="replace")


def _charset_from_headers(headers) -> str:
    ct = headers.get("Content-Type", "")
    for part in ct.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1].strip().strip('"')
    return "utf-8"


class SearchParams:
    """
    Parameters for a single drawing search query.

    All fields match the HTML form fields exactly so that the mapping stays
    transparent and easy to extend.
    """

    def __init__(
        self,
        *,
        state: str = "Released",
        facility: str = "",
        drawing_type: str = "",
        drawing_subject: str = "",
        sheet_number: str = "",
        serial_from: str = "",
        serial_to: str = "",
        drawing_num_op: str = "starts with",
        drawing_num: str = "",
        title_op: str = "contains all",
        title: str = "",
        title2_op: str = "contains all",
        title2: str = "",
        manufacturer_name: str = "",
        manufacturer_doc_num: str = "",
        remarks_contain: str = "",
        legacy_document_num: str = "",
        show_issue_reason: str = "notShow",
    ):
        self.state               = state
        self.facility            = facility
        self.drawing_type        = drawing_type
        self.drawing_subject     = drawing_subject
        self.sheet_number        = sheet_number
        self.serial_from         = serial_from
        self.serial_to           = serial_to
        self.drawing_num_op      = drawing_num_op
        self.drawing_num         = drawing_num
        self.title_op            = title_op
        self.title               = title
        self.title2_op           = title2_op
        self.title2              = title2
        self.manufacturer_name   = manufacturer_name
        self.manufacturer_doc_num = manufacturer_doc_num
        self.remarks_contain     = remarks_contain
        self.legacy_document_num = legacy_document_num
        self.show_issue_reason   = show_issue_reason

    def _to_form_data(self) -> dict[str, str]:
        """Reproduce the exact POST body the browser sends."""
        d = {
            "state":                   self.state,
            "sheetNumber":             self.sheet_number,
            "facility":                self.facility,
            "drawingType":             self.drawing_type,
            "_drawingType":            "1",
            "drawingSubject":          self.drawing_subject,
            "_drawingSubject":         "1",
            "serialFrom":              self.serial_from,
            "serialTo":                self.serial_to,
            "drawingNumOp":            self.drawing_num_op,
            "drawingNum":              self.drawing_num,
            "titleOp":                 self.title_op,
            "title":                   self.title,
            "title2Op":                self.title2_op,
            "title2":                  self.title2,
            "manufacturerName":        self.manufacturer_name,
            "manufacturerDocumentNum": self.manufacturer_doc_num,
            "remarksContain":          self.remarks_contain,
            "legacyDocumentNum":       self.legacy_document_num,
            "showIssueReasonOptions":  self.show_issue_reason,
            "search":                  "Search",
        }
        return d
