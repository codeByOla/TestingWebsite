import logging
import time

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from accounts.models import PasswordResetLog, PasswordResetToken
from subscriptions.models import StudentProfile

logger = logging.getLogger(__name__)
User = get_user_model()

_ATTEMPT_WINDOW_MINUTES = 5
_MAX_ATTEMPTS = 5
_GENERIC_ERROR = "Të dhënat e futura nuk janë të vlefshme."


# ---------------------------------------------------------------------------
# IP / user-agent helpers
# ---------------------------------------------------------------------------

def get_client_ip(request: object) -> str:
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def get_user_agent(request: object) -> str:
    return request.META.get("HTTP_USER_AGENT", "")[:500]


# ---------------------------------------------------------------------------
# Rate-limit counters
# ---------------------------------------------------------------------------

def count_recent_failed_attempts(user_id: int) -> int:
    cutoff = timezone.now() - timezone.timedelta(minutes=_ATTEMPT_WINDOW_MINUTES)
    return PasswordResetLog.objects.filter(
        user_id=user_id,
        result__in=("FAILED", "BLOCKED"),
        timestamp__gte=cutoff,
    ).count()


def count_recent_failed_attempts_by_ip(ip: str) -> int:
    cutoff = timezone.now() - timezone.timedelta(minutes=_ATTEMPT_WINDOW_MINUTES)
    return PasswordResetLog.objects.filter(
        ip_address=ip,
        result__in=("FAILED", "BLOCKED"),
        timestamp__gte=cutoff,
    ).count()


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def invalidate_existing_tokens(user) -> None:
    PasswordResetToken.objects.filter(
        user=user,
        is_used=False,
    ).update(is_used=True)


def cleanup_expired_tokens() -> None:
    cutoff = timezone.now() - timezone.timedelta(minutes=15)
    PasswordResetToken.objects.filter(
        created_at__lt=cutoff,
        is_used=False,
    ).update(is_used=True)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def log_attempt(
    user,
    username_input: str,
    barcode_input: str,
    token,
    result: str,
    step: str,
    request,
) -> None:
    PasswordResetLog.objects.create(
        user=user,
        username_input=username_input,
        barcode_input=barcode_input,
        token=token,
        result=result,
        step=step,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
    )


# ---------------------------------------------------------------------------
# Step 1 — verify identity and issue a reset token
# ---------------------------------------------------------------------------

class RateLimitedError(Exception):
    """Raised when the request should be blocked due to too many attempts."""


class VerificationError(Exception):
    """Raised when identity verification fails."""


def handle_reset_request(form, request):
    """
    Validate identity, apply rate limits, invalidate old tokens, and create a
    new PasswordResetToken.

    Returns the new PasswordResetToken on success.
    Raises RateLimitedError or VerificationError on failure (caller must log).
    """
    username_input = form.cleaned_data["username"].strip()
    barcode_input = form.cleaned_data["barcode"].strip()
    first_name = form.cleaned_data["first_name"].strip().lower()
    last_name = form.cleaned_data["last_name"].strip().lower()
    ip = get_client_ip(request)

    # IP-based block (checked before user resolution)
    if count_recent_failed_attempts_by_ip(ip) >= _MAX_ATTEMPTS:
        log_attempt(None, username_input, barcode_input, None, "BLOCKED", "VERIFY", request)
        raise RateLimitedError()

    resolved_user = None

    try:
        resolved_user = User.objects.get(username=username_input, role="STUDENT")

        # User-id-based block once the user is resolved
        if count_recent_failed_attempts(resolved_user.id) >= _MAX_ATTEMPTS:
            log_attempt(resolved_user, username_input, barcode_input, None, "BLOCKED", "VERIFY", request)
            raise RateLimitedError()

        profile = StudentProfile.objects.get(user=resolved_user)

        if profile.barcode != barcode_input:
            raise ValueError("barcode mismatch")

        if (profile.first_name or "").strip().lower() != first_name:
            raise ValueError("first_name mismatch")

        if (profile.last_name or "").strip().lower() != last_name:
            raise ValueError("last_name mismatch")

    except RateLimitedError:
        raise
    except (User.DoesNotExist, StudentProfile.DoesNotExist, ValueError):
        log_attempt(resolved_user, username_input, barcode_input, None, "FAILED", "VERIFY", request)
        raise VerificationError()

    with transaction.atomic():
        invalidate_existing_tokens(resolved_user)
        cleanup_expired_tokens()
        reset_token = PasswordResetToken.objects.create(user=resolved_user)

    log_attempt(resolved_user, username_input, barcode_input, reset_token, "SUCCESS", "VERIFY", request)
    return reset_token


# ---------------------------------------------------------------------------
# Step 2 — confirm the new password
# ---------------------------------------------------------------------------

class TokenInvalidError(Exception):
    """Raised when the reset token is missing, expired, or already used."""


def handle_reset_confirm(reset_token, form, request):
    """
    Validate the new password against the token, then atomically consume the
    token and update the user's password.

    Returns the resolved user on success.
    Raises RateLimitedError, TokenInvalidError, or re-raises on unexpected errors.
    """
    resolved_user = reset_token.user
    username_input = resolved_user.username

    if count_recent_failed_attempts(resolved_user.id) >= _MAX_ATTEMPTS:
        log_attempt(resolved_user, username_input, "", reset_token, "BLOCKED", "RESET", request)
        raise RateLimitedError()

    if not reset_token.is_valid():
        log_attempt(resolved_user, username_input, "", reset_token, "FAILED", "RESET", request)
        raise TokenInvalidError()

    new_password = form.cleaned_data["new_password"]

    with transaction.atomic():
        # Re-fetch under lock to guard against concurrent reuse
        fresh_token = PasswordResetToken.objects.select_for_update().get(pk=reset_token.pk)

        if not fresh_token.is_valid():
            log_attempt(resolved_user, username_input, "", reset_token, "FAILED", "RESET", request)
            raise TokenInvalidError()

        resolved_user.set_password(new_password)
        resolved_user.save()
        fresh_token.is_used = True
        fresh_token.save()

    log_attempt(resolved_user, username_input, "", reset_token, "SUCCESS", "RESET", request)
    return resolved_user