"""Low-cardinality process metrics and allowlisted structured log events."""

import json
import logging
import threading
from collections import Counter

logger = logging.getLogger("supportdesk")


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counts = Counter()
        self._latency_total_ms = 0.0
        self._latency_max_ms = 0.0
        self._requests = 0

    def request(self, route: str, status: int, elapsed_ms: float, request_id: str):
        with self._lock:
            self._counts[f"http:{route}:{status}"] += 1
            self._requests += 1
            self._latency_total_ms += elapsed_ms
            self._latency_max_ms = max(self._latency_max_ms, elapsed_ms)
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "route": route,
                    "status": status,
                    "latency_ms": round(elapsed_ms, 2),
                }
            )
        )

    def increment(self, name: str):
        with self._lock:
            self._counts[name] += 1

    def snapshot(self):
        with self._lock:
            return {
                "scope": "single_process_since_start",
                "requests": self._requests,
                "counts": dict(self._counts),
                "latency_ms_mean": round(self._latency_total_ms / max(1, self._requests), 2),
                "latency_ms_max": round(self._latency_max_ms, 2),
            }
