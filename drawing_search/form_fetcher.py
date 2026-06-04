"""Fetch facility / drawing-type / drawing-subject options from the live search form.

Usage
-----
from drawing_search.form_fetcher import fetch_form_options

opts = fetch_form_options(
    base_url="https://CORPRATEINTERNAL.COM",
    cookies={"filenet-es": "...", "_WL_AUTHCOOKIE_filenet-es": "..."},
)
# opts == {
#   "facilities":       {"100": "SITE NAME-100", ...},
#   "drawing_types":    {"A": "Architectural", ...},
#   "drawing_subjects": {"00": "Index Listing", ...},
# }
"""

import re
import ssl
import urllib.request
import urllib.error
from html.parser import HTMLParser
from typing import Callable, Optional

_FORM_PATH = "/search/searchGT.html"

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
)

# Maps HTML select id/name → result key
_SELECT_MAP = {
    "facility":       "facilities",
    "drawingType":    "drawing_types",
    "drawingSubject": "drawing_subjects",
}


class _OptionParser(HTMLParser):
    """Collect <option> values from targeted <select> elements."""

    def __init__(self):
        super().__init__()
        self._in_select: Optional[str] = None  # result key or None
        self._in_option: bool = False
        self._opt_val: str = ""
        self._opt_text: str = ""
        self.options: dict[str, dict[str, str]] = {v: {} for v in _SELECT_MAP.values()}

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "select":
            name = attrs_d.get("id") or attrs_d.get("name", "")
            self._in_select = _SELECT_MAP.get(name)
        elif tag == "option" and self._in_select is not None:
            self._in_option = True
            self._opt_val = attrs_d.get("value", "")
            self._opt_text = ""

    def handle_endtag(self, tag):
        if tag == "select":
            self._in_select = None
        elif tag == "option" and self._in_select is not None:
            val = self._opt_val.strip()
            raw = self._opt_text.strip()
            if val and raw:
                # Strip leading "CODE - " or "CODE – " if the label repeats the code
                label = re.sub(r"^" + re.escape(val) + r"\s*[-–]\s*", "", raw).strip()
                self.options[self._in_select][val] = label or raw
            self._in_option = False

    def handle_data(self, data):
        if self._in_option:
            self._opt_text += data


def fetch_form_options(
    base_url: str,
    cookies: Optional[dict[str, str]] = None,
    timeout: int = 30,
    user_agent: str = _DEFAULT_UA,
    extra_headers: Optional[dict[str, str]] = None,
    verify_ssl: bool = True,
    log: Optional[Callable[[str], None]] = None,
) -> dict[str, dict[str, str]]:
    """GET the search form page and return parsed dropdown options.

    Returns a dict with keys ``facilities``, ``drawing_types``,
    ``drawing_subjects``; each maps option code → human-readable label.

    ``extra_headers`` are merged in and take precedence over the defaults.
    ``verify_ssl=False`` disables certificate verification (for corporate
    internal sites with self-signed certs).
    ``log`` receives debug lines during the request if provided.

    Raises ``urllib.error.URLError`` / ``urllib.error.HTTPError`` on failure.
    """
    _log = log or (lambda _: None)

    url = base_url.rstrip("/") + _FORM_PATH
    cookie_h = "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())

    headers = {
        "Accept":        "text/html,application/xhtml+xml,*/*;q=0.8",
        "User-Agent":    user_agent,
        "Cache-Control": "no-cache",
        **({"Cookie": cookie_h} if cookie_h else {}),
        **(extra_headers or {}),
    }

    _log(f"GET {url}")
    for k, v in headers.items():
        display = (v[:40] + "…") if k.lower() == "cookie" and len(v) > 40 else v
        _log(f"  {k}: {display}")

    ctx = None
    if not verify_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _log("  [SSL verification disabled]")

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        final_url = resp.url
        status    = resp.status
        _log(f"HTTP {status}  →  {final_url}")
        charset = "utf-8"
        for part in resp.headers.get("Content-Type", "").split(";"):
            p = part.strip()
            if p.lower().startswith("charset="):
                charset = p.split("=", 1)[1].strip().strip('"')
        html = resp.read().decode(charset, errors="replace")

    if final_url != url:
        _log(f"WARNING: server redirected — possible auth failure\n"
             f"  expected: {url}\n  got:      {final_url}")

    parser = _OptionParser()
    parser.feed(html)
    result = dict(parser.options)

    total = sum(len(v) for v in result.values())
    if total == 0:
        _log("WARNING: response contained no dropdown options.\n"
             "  The server may have returned a login page instead of the search form.\n"
             f"  First 300 chars of response:\n  {html[:300].strip()}")

    return result
