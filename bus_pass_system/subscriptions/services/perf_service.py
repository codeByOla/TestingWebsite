"""
perf_service.py
───────────────
Shërbim për regjistrimin e kohëve të operacioneve në DB.
Importohet nga shërbimet dhe views ekzistuese.
"""
from __future__ import annotations

import time
import logging
from functools import wraps
from contextlib import contextmanager

logger = logging.getLogger(__name__)


def record(operation: str, duration_ms: float, extra: str = "") -> None:
    """Ruan një matje në DB. Nuk hedh exception kurrë."""
    try:
        from subscriptions.models import PerformanceLog
        PerformanceLog.objects.create(
            operation=operation,
            duration_ms=round(duration_ms, 3),
            extra=extra,
        )
    except Exception as exc:
        logger.warning("perf_service.record failed: %s", exc)


def timed(operation: str, extra: str = ""):
    """
    Dekorator — mat kohën e funksionit dhe e ruan në DB.

    @timed("verification")
    def verify_token(...): ...
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            start  = time.perf_counter()
            result = fn(*args, **kwargs)
            ms     = (time.perf_counter() - start) * 1_000
            record(operation, ms, extra)
            return result
        return wrapper
    return decorator


@contextmanager
def measure(operation: str, extra: str = ""):
    """
    Context manager — mat kohën e një blloku kodi dhe e ruan në DB.
    Përdoret kur nuk mund të vendosim dekorator (p.sh. brenda view-it).

    Shembull:
        from subscriptions.services.perf_service import measure

        with measure("csv_import"):
            # ... i gjithë kodi i importit
            created_count, skipped_count = 0, 0
            for row in reader:
                ...
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - start) * 1_000
        record(operation, ms, extra)