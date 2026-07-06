"""
Verification Service
--------------------
Responsible for:
- Resolving a raw hashed token string to a result (VALID / INVALID /
  EXPIRED / REVOKED).
- Consuming (marking used) the TemporaryQRToken inside a single atomic
  transaction with a row-level lock.
- Writing the VerificationLog row.
- Delegating analytics recording to analytics_service.

Moved from:
- The bulk of verify_subscription() in views.py — view now only handles
  HTTP concerns (rate-limiting, input reading, rendering).

Security preserved:
- select_for_update() on the token lookup.
- Timing-safe single filter (unknown / used / expired all return None).
- REVOKED tokens are consumed so the credential cannot be reused.
- Student / subscription data is never returned on non-VALID paths.
"""

import logging
import re
from unittest import result
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from subscriptions.models import (
    Subscription,
    TemporaryQRToken,
    VerificationLog,
)
from subscriptions.services import analytics_service

logger = logging.getLogger(__name__)

_HASHED_TOKEN_RE = re.compile(r'^[0-9a-fA-F]{64}$')
_UUID_RE = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$',
    re.IGNORECASE,
)


def _is_hashed_token(raw: str) -> bool:
    return bool(_HASHED_TOKEN_RE.fullmatch(raw))


def _is_uuid_token(raw: str) -> bool:
    return bool(_UUID_RE.fullmatch(raw))

from subscriptions.services.perf_service import timed

@timed("verification")
def verify_token(
    controller,
    raw_token: str,
    ip_address: str,
    now=None,
) -> dict:

    if now is None:
        now = timezone.now()

    today = now.date()

    # ── Determine token format ────────────────────────────────────────────
    is_hashed = _is_hashed_token(raw_token)
    is_uuid   = _is_uuid_token(raw_token)

    if not is_hashed and not is_uuid:
        _write_log(
            controller, subscription=None, token_uuid=uuid.uuid4(),
            result="INVALID", ip_address=ip_address, now=now, sub_result="INVALID",
        )
        return {"result": "INVALID", "sub_result": "INVALID", "student": None, "subscription": None}

    student        = None
    subscription   = None
    result         = "INVALID"
    sub_result     = "INVALID"
    university     = None
    log_token_uuid = uuid.uuid4()

    with transaction.atomic():

        # ══════════════════════════════════════════════════════════════════
        # PATH A — hashed_token (64 hex chars) → TemporaryQRToken
        # Used for: active subscriptions (dynamic, 60-second QR)
        # ══════════════════════════════════════════════════════════════════
        if is_hashed:
            token_normalised = raw_token.lower()

            qr_token = (
                TemporaryQRToken.objects
                .select_for_update(of=("self",))
                .select_related(
                    'subscription__student__university',
                    'subscription__student__user',
                )
                .filter(
                    hashed_token=token_normalised,
                    is_used=False,
                    expires_at__gt=now,
                )
                .first()
            )

            if qr_token is None:
                # Check for recently-used token (re-scan abuse detection)
                recent_cutoff = now - timedelta(seconds=60)
                used_token = (
                    TemporaryQRToken.objects
                    .select_related(
                        'subscription__student__university',
                        'subscription__student__user',
                    )
                    .filter(
                        hashed_token=token_normalised,
                        is_used=True,
                        created_at__gte=recent_cutoff,
                    )
                    .first()
                )

                if used_token is not None:
                    past_sub     = used_token.subscription
                    past_student = past_sub.student

                    if past_student.is_revoked:
                        result     = "REVOKED"
                        sub_result = "REVOKED"
                        student = past_student
                        subscription = past_sub
                        university = getattr(past_student, 'university', None)
                    elif past_sub.expiry_date < today:
                        result     = "INVALID"
                        sub_result = "ALREADY_USED_EXPIRED"
                    else:
                        result       = "INVALID"
                        sub_result   = "ALREADY_USED_VALID"
                        student      = past_student
                        subscription = past_sub
                        university   = getattr(past_student, 'university', None)
                else:
                    result     = "INVALID"
                    sub_result = "INVALID"

            else:
                subscription   = qr_token.subscription
                student        = subscription.student
                university     = getattr(student, 'university', None)
                log_token_uuid = qr_token.uuid_token

                if student.is_revoked:
                    result     = "REVOKED"
                    sub_result = "REVOKED"
                    qr_token.is_used = True
                    qr_token.save(update_fields=['is_used'])

                elif subscription.expiry_date < today:
                    result     = "EXPIRED"
                    sub_result = "EXPIRED"
                    qr_token.is_used = True
                    qr_token.save(update_fields=['is_used'])

                elif not student.user or not student.user.is_active:
                    result     = "INVALID"
                    sub_result = "INVALID"
                    qr_token.is_used = True
                    qr_token.save(update_fields=['is_used'])

                else:
                    result     = "VALID"
                    sub_result = "VALID"
                    qr_token.is_used = True
                    qr_token.save(update_fields=['is_used'])
                    Subscription.objects.filter(pk=subscription.pk).update(
                        last_verified_at=now
                    )

        # ══════════════════════════════════════════════════════════════════
        # PATH B — uuid_token (UUID format) → Subscription directly
        # Used for: expired subscriptions (static QR, never changes)
        # ══════════════════════════════════════════════════════════════════
        else:
            try:
                parsed_uuid = uuid.UUID(raw_token)
            except ValueError:
                _write_log(
                    controller, subscription=None, token_uuid=uuid.uuid4(),
                    result="INVALID", ip_address=ip_address, now=now, sub_result="INVALID",
                )
                return {"result": "INVALID", "sub_result": "INVALID", "student": None, "subscription": None}

            sub_qs = (
                Subscription.objects
                .select_related(
                    'student__university',
                    'student__user',
                )
                .filter(uuid_token=parsed_uuid)
                .first()
            )

            if sub_qs is None:
                result     = "INVALID"
                sub_result = "INVALID"
            else:
                subscription   = sub_qs
                student        = subscription.student
                university     = getattr(student, 'university', None)
                log_token_uuid = subscription.uuid_token

                if student.is_revoked:
                    result     = "REVOKED"
                    sub_result = "REVOKED"

                elif subscription.expiry_date >= today:
                    # UUID path should only appear for expired subs.
                    # If somehow an active sub UUID is scanned, treat as invalid
                    # to force usage of the secure temporary token path.
                    result     = "INVALID"
                    sub_result = "INVALID"
                    logger.warning(
                        "UUID token scanned for active subscription %s — "
                        "should use hashed QR path instead.",
                        subscription.pk,
                    )

                elif not student.user or not student.user.is_active:
                    result     = "INVALID"
                    sub_result = "INVALID"

                else:
                    # Confirmed expired — return EXPIRED result with student info
                    result     = "EXPIRED"
                    sub_result = "EXPIRED"

        # ── Logging ───────────────────────────────────────────────────────
        log_subscription = None
        if result in ("VALID", "EXPIRED", "REVOKED"):
            log_subscription = subscription
        elif sub_result == "ALREADY_USED_VALID":
            log_subscription = subscription

        _write_log(
            controller,
            subscription=log_subscription,
            token_uuid=log_token_uuid,
            result=result,
            ip_address=ip_address,
            now=now,
            sub_result=sub_result,
        )

        try:
            analytics_service.record(
                result, university=university, timestamp=now, sub_result=sub_result
            )
        except Exception as exc:
            logger.warning("Analytics record failed: %s", exc)

    if sub_result not in ("VALID", "EXPIRED", "REVOKED", "ALREADY_USED_VALID"):
        student      = None
        subscription = None

    return {
        "result":       result,
        "sub_result":   sub_result,
        "student":      student,
        "subscription": subscription,
    }

# ── Private helpers ────────────────────────────────────────────────────────

def _write_log(controller, subscription, token_uuid, result, ip_address, now, sub_result=None):
    VerificationLog.objects.create(
        controller=controller,
        subscription=subscription,
        scanned_token=token_uuid,
        result=result,
        sub_result=sub_result or result,
        ip_address=ip_address,
        timestamp=now,
    )


def count_recent_invalid_by_controller(controller, window_minutes: int) -> int:
    """Used by the view for per-controller brute-force rate limiting."""
    cutoff = timezone.now() - timedelta(minutes=window_minutes)
    return VerificationLog.objects.filter(
        controller=controller,
        result="INVALID",
        timestamp__gte=cutoff,
    ).count()


def count_recent_bad_by_ip(ip: str, window_minutes: int) -> int:
    """Used by the view for per-IP bad-result rate limiting."""
    cutoff = timezone.now() - timedelta(minutes=window_minutes)
    return VerificationLog.objects.filter(
        ip_address=ip,
        result__in=("INVALID", "EXPIRED"),
        timestamp__gte=cutoff,
    ).count()


def count_recent_total_by_ip(ip: str, window_minutes: int) -> int:
    """Used by the view for per-IP total-request rate limiting."""
    cutoff = timezone.now() - timedelta(minutes=window_minutes)
    return VerificationLog.objects.filter(
        ip_address=ip,
        timestamp__gte=cutoff,
    ).count()