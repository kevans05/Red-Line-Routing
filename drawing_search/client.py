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

import threading
import urllib.request
import urllib.parse
import urllib.error
from typing import Callable, Optional

from .models import DrawingResult, PagedResults
from .parser import parse_results, parse_paged

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
        cache=None,
    ):
        self.base_url   = base_url.rstrip("/")
        self.cookies    = cookies or {}
        self.timeout    = timeout
        self.user_agent = user_agent
        self.cache      = cache  # DrawingSearchCache | None

    # ── public API ────────────────────────────────────────────────

    def search(self, params: "SearchParams") -> list[DrawingResult]:
        """Execute a drawing search and return parsed results (backward compat)."""
        html = self._post(params._to_form_data())
        return parse_results(html, self.base_url)

    def search_paged(self, params: "SearchParams") -> PagedResults:
        """Execute a drawing search and return a PagedResults.

        Checks the cache first if one was supplied to __init__.
        """
        if self.cache is not None:
            cached = self.cache.get(params)
            if cached is not None:
                return PagedResults(
                    results=cached,
                    page=params.page,
                    page_size=params.page_size,
                    total_count=len(cached),
                    has_next=False,
                )

        html   = self._post(params._to_form_data())
        paged  = parse_paged(html, self.base_url, page=params.page,
                             page_size=params.page_size)

        if self.cache is not None:
            self.cache.put(params, paged.results)

        return paged

    def search_all_pages(self, params: "SearchParams", max_pages: int = 20) -> list[DrawingResult]:
        """Iterate pages until has_next is False or max_pages is reached."""
        import copy
        all_results: list[DrawingResult] = []
        p = copy.copy(params)
        for _ in range(max_pages):
            paged = self.search_paged(p)
            all_results.extend(paged.results)
            if not paged.has_next:
                break
            p.page += 1
        return all_results

    def search_async(
        self,
        params: "SearchParams",
        on_done: Callable[[PagedResults], None],
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> threading.Thread:
        """Run search_paged in a background thread.

        Calls on_done(PagedResults) or on_error(exc) on completion.
        Does NOT call tkinter after() — leave that to the caller.
        """
        def _run():
            try:
                result = self.search_paged(params)
                on_done(result)
            except Exception as exc:
                if on_error is not None:
                    on_error(exc)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t

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
        page: int = 0,
        page_size: int = 50,
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
        self.page                = page
        self.page_size           = page_size

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
        if self.page > 0:
            d["page"] = str(self.page)
        return d
