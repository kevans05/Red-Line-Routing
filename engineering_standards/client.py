"""HTTP client for the engineering standards tree API.

Usage
-----
from engineering_standards import EngineeringStandardsClient, EngineeringStandardsCache

cache  = EngineeringStandardsCache(ttl_hours=4)
client = EngineeringStandardsClient(
    base_url="https://DOMAIN.CORPRATEDOMAIN.ab.com/XXXX",
    headers={"Cookie": "..."},
    cache=cache,
)
series = client.fetch_series()
for s in series:
    standards = client.fetch_section(s.value)
    for std in standards:
        print(std.standard_id, std.description, std.url)
"""
import json
import threading
import urllib.request
import urllib.parse
import urllib.error
from typing import Callable, Optional

from .models import EngineeringSeries, EngineeringStandard
from .parser import parse_series, parse_section

_DEFAULT_TIMEOUT = 60
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
)


class EngineeringStandardsClient:
    """Fetch series and document lists from the engineering standards tree API."""

    def __init__(
        self,
        base_url: str,
        headers: Optional[dict] = None,
        timeout: int = _DEFAULT_TIMEOUT,
        cache=None,
    ):
        self.base_url = base_url.rstrip('/')
        self.headers = dict(headers) if headers else {}
        self.timeout = timeout
        self.cache = cache   # EngineeringStandardsCache | None

    # ── public API ────────────────────────────────────────────────

    def fetch_series(self) -> list:
        """Return all top-level series from series?group=00all."""
        data = self._get(f"{self.base_url}/series?group=00all")
        return parse_series(data)

    def fetch_section(self, series_value: str) -> list:
        """Return EngineeringStandard list for one series, using cache when fresh."""
        if self.cache is not None:
            cached = self.cache.get(series_value)
            if cached is not None:
                return cached
        encoded = urllib.parse.quote(series_value, safe='')
        data = self._get(f"{self.base_url}/sections?series={encoded}")
        results = parse_section(data, series_value, self.base_url)
        if self.cache is not None:
            self.cache.put(series_value, results)
        return results

    def fetch_all(
        self,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> list:
        """Fetch every series then every section, returning a flat list.

        on_progress(done, total, series_title) called after each series fetch.
        """
        series_list = self.fetch_series()
        all_results: list = []
        total = len(series_list)
        for i, series in enumerate(series_list):
            standards = self.fetch_section(series.value)
            all_results.extend(standards)
            if on_progress:
                on_progress(i + 1, total, series.title)
        return all_results

    def fetch_all_async(
        self,
        on_done: Callable[[list], None],
        on_error: Optional[Callable[[Exception], None]] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> threading.Thread:
        """Run fetch_all in a background thread."""
        def _run():
            try:
                results = self.fetch_all(on_progress=on_progress)
                on_done(results)
            except Exception as exc:
                if on_error:
                    on_error(exc)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t

    # ── internals ─────────────────────────────────────────────────

    def _get(self, url: str):
        req = urllib.request.Request(url, headers={
            'Accept': 'application/json, text/javascript, */*',
            'User-Agent': _USER_AGENT,
            **self.headers,
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode('utf-8', errors='replace'))
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode('utf-8', errors='replace')[:200]
            except Exception:
                body = ''
            raise RuntimeError(
                f"HTTP {exc.code} fetching {url}\n{body}"
            ) from exc
