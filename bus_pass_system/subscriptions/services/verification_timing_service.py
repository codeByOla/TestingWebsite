"""
verification_timing_service.py

Minimal timing helper for thesis metrics.
Records how long verify_token() takes and exposes a daily average.
Does NOT touch existing verification logic.
"""
import logging
import time
from functools import wraps

from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory accumulator (resets on server restart — acceptable for thesis)
# ---------------------------------------------------------------------------

_timing_store = {
    "count": 0,
    "total_ms": 0.0,
}


def record_timing(duration_ms: float) -> None:
    """Add one timing sample to the in-memory store."""
    _timing_store["count"]    += 1
    _timing_store["total_ms"] += duration_ms


def get_average_ms() -> float | None:
    """Return average verification time in milliseconds, or None if no data."""
    if _timing_store["count"] == 0:
        return None
    return round(_timing_store["total_ms"] / _timing_store["count"], 2)


def get_timing_stats() -> dict:
    """Return dict with count and average_ms for template/view use."""
    return {
        "verification_count":      _timing_store["count"],
        "avg_verification_ms":     get_average_ms(),
    }


def timed_verify(verify_fn):
    """
    Decorator — wraps verify_token() to measure wall-clock time.
    Usage: applied once in verify_subscription view, not in the service itself.
    """
    @wraps(verify_fn)
    def wrapper(*args, **kwargs):
        start  = time.perf_counter()
        result = verify_fn(*args, **kwargs)
        end    = time.perf_counter()
        duration_ms = (end - start) * 1000
        try:
            record_timing(duration_ms)
            logger.debug("verify_token duration: %.2f ms", duration_ms)
        except Exception:
            pass  # timing must never break verification
        return result
    return wrapper