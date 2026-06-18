"""Thread-safe in-memory cache for engineering standards results."""
import time
import threading
from .models import EngineeringStandard

_DEFAULT_TTL_HOURS = 4.0


class EngineeringStandardsCache:
    """Keyed by series_value; entries expire after ttl_hours."""

    def __init__(self, ttl_hours: float = _DEFAULT_TTL_HOURS):
        self._lock = threading.Lock()
        self._data: dict = {}   # series_value -> (timestamp, list[EngineeringStandard])
        self._ttl = ttl_hours * 3600

    def get(self, series_value: str):
        with self._lock:
            entry = self._data.get(series_value)
            if entry is None:
                return None
            ts, results = entry
            if time.time() - ts > self._ttl:
                return None
            return list(results)

    def put(self, series_value: str, results: list) -> None:
        with self._lock:
            self._data[series_value] = (time.time(), list(results))

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def is_stale(self, series_value: str) -> bool:
        with self._lock:
            entry = self._data.get(series_value)
            if entry is None:
                return True
            return time.time() - entry[0] > self._ttl

    def cached_series(self) -> list:
        """Return list of series_values that are currently cached and fresh."""
        with self._lock:
            now = time.time()
            return [k for k, (ts, _) in self._data.items()
                    if now - ts <= self._ttl]

    # ── persistence helpers ──────────────────────────────────────────

    def to_dict(self) -> dict:
        with self._lock:
            return {
                k: {'ts': ts, 'results': [r.__dict__ for r in results]}
                for k, (ts, results) in self._data.items()
            }

    def from_dict(self, data: dict) -> None:
        with self._lock:
            for k, v in data.items():
                try:
                    ts = float(v['ts'])
                    results = [EngineeringStandard(**r) for r in v['results']]
                    self._data[k] = (ts, results)
                except Exception:
                    pass
