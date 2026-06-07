"""
Token Cleanup Service
---------------------
Responsible for:
- Purging stale (old + consumed) TemporaryQRToken rows from the database.
- Marking tokens that have passed their expires_at as used.

Moved from:
- TemporaryQRToken.cleanup_stale_tokens()   (classmethod on model)
- TemporaryQRToken.cleanup_expired_tokens() (classmethod on model)

These are maintenance / infrastructure concerns, not schema concerns.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from subscriptions.models import TemporaryQRToken

logger = logging.getLogger(__name__)


def cleanup_stale_tokens(older_than_days: int = 7) -> int:
    """
    Hard-delete TemporaryQRToken rows that are both consumed (is_used=True)
    and older than *older_than_days*.  Returns the number of rows deleted.

    Called probabilistically from the verify view to avoid a dedicated
    management command for low-traffic deployments.
    """
    cutoff = timezone.now() - timedelta(days=older_than_days)
    deleted_count, _ = TemporaryQRToken.objects.filter(
        created_at__lt=cutoff,
        is_used=True,
    ).delete()
    return deleted_count


def cleanup_expired_tokens() -> int:
    """
    Mark as used any TemporaryQRToken rows whose expires_at has passed
    but that were never consumed (is_used=False).  Returns the number
    of rows updated.

    Keeps the active-token query fast by ensuring the
    (hashed_token, is_used, expires_at) index stays selective.
    """
    cutoff = timezone.now() - timedelta(days=1)
    updated_count = TemporaryQRToken.objects.filter(
        expires_at__lt=cutoff,
        is_used=False,
    ).update(is_used=True)
    return updated_count


def run_probabilistic_cleanup(probability: float = 0.005) -> None:
    """
    Run cleanup_stale_tokens() with the given *probability* on each call.
    Designed to be called from the hot verify path so no cron job is needed.
    """
    import random
    if random.random() < probability:
        try:
            deleted = cleanup_stale_tokens(older_than_days=7)
            if deleted:
                logger.info("QR token cleanup removed %d stale rows", deleted)
        except Exception as exc:
            logger.warning("QR token cleanup failed: %s", exc)


def run_probabilistic_expire(probability: float = 0.005) -> None:
    """
    Run cleanup_expired_tokens() with the given *probability* on each call.
    """
    import random
    if random.random() < probability:
        try:
            updated = cleanup_expired_tokens()
            if updated:
                logger.info("QR token expiry sweep marked %d tokens used", updated)
        except Exception as exc:
            logger.warning("QR token expiry sweep failed: %s", exc)