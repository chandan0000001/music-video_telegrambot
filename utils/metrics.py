"""
Lightweight In-Memory Metrics & Timing Collector
Tracks counters and operation latencies (metadata, download, FFmpeg, DB, HTTP, queue wait)
without requiring external infrastructure or services.
"""
import time
import threading
from contextlib import contextmanager
from typing import Dict, Any, Iterator


class MetricsRegistry:
    """Thread-safe lightweight metrics store for observability and benchmarking."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {}
        self._timings: Dict[str, Dict[str, float]] = {}

    def incr(self, name: str, value: int = 1) -> None:
        """Increment named counter."""
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + value

    def record_timing(self, name: str, duration_ms: float) -> None:
        """Record an elapsed duration in milliseconds for named metric."""
        ms = max(0.0, float(duration_ms))
        with self._lock:
            stat = self._timings.get(name)
            if stat is None:
                self._timings[name] = {
                    "count": 1.0,
                    "total_ms": ms,
                    "max_ms": ms,
                }
            else:
                stat["count"] += 1.0
                stat["total_ms"] += ms
                if ms > stat["max_ms"]:
                    stat["max_ms"] = ms

    @contextmanager
    def track_time(self, name: str) -> Iterator[None]:
        """Context manager measuring block execution time in milliseconds."""
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record_timing(name, (time.perf_counter() - start) * 1000.0)

    def snapshot(self) -> Dict[str, Any]:
        """Return a consistent JSON-serializable snapshot of counters and averaged timings."""
        with self._lock:
            timings_out: Dict[str, Dict[str, float]] = {}
            for k, v in self._timings.items():
                count = int(v["count"])
                timings_out[k] = {
                    "count": count,
                    "total_ms": round(v["total_ms"], 2),
                    "avg_ms": round(v["total_ms"] / count, 2) if count else 0.0,
                    "max_ms": round(v["max_ms"], 2),
                }
            return {
                "counters": dict(self._counters),
                "timings": timings_out,
            }

    def reset(self) -> None:
        """Clear all recorded metrics (primarily for unit tests)."""
        with self._lock:
            self._counters.clear()
            self._timings.clear()


metrics = MetricsRegistry()
