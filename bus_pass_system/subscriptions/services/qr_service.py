"""
QR Service
----------
Responsible for:
- Generating cryptographically secure hashed tokens (HMAC-SHA256).
- Issuing and reusing active TemporaryQRToken instances.
- Rendering QR code images as raw bytes.

Moved from:
- generate_secure_token()          (was a module-level function in models.py)
- TemporaryQRToken.get_or_create_active()  (was a classmethod on the model)
- TemporaryQRToken.build_qr_bytes()        (was a classmethod on the model)
"""

import hashlib
import hmac
import uuid
from datetime import timedelta
from io import BytesIO

import qrcode
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from subscriptions.models import TemporaryQRToken


def generate_secure_token(uuid_value: uuid.UUID) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        str(uuid_value).encode(),
        hashlib.sha256,
    ).hexdigest()


def get_or_create_active_token(subscription) -> TemporaryQRToken:
    with transaction.atomic():
        now = timezone.now()

        TemporaryQRToken.objects.filter(
            subscription=subscription,
            is_used=False,
            expires_at__lte=now,
        ).update(is_used=True)

        active = (
            TemporaryQRToken.objects
            .select_for_update()
            .filter(
                subscription=subscription,
                is_used=False,
                expires_at__gt=now,
            )
            .first()
        )
        if active:
            return active

        TemporaryQRToken.objects.filter(
            subscription=subscription,
            is_used=False,
        ).update(is_used=True)

        new_uuid    = uuid.uuid4()
        secure_hash = generate_secure_token(new_uuid)

        return TemporaryQRToken.objects.create(
            subscription=subscription,
            uuid_token=new_uuid,
            hashed_token=secure_hash,
            expires_at=now + timedelta(seconds=60),
        )


def build_qr_bytes(token: TemporaryQRToken) -> bytes:
    """Build QR image bytes from a TemporaryQRToken (dynamic path)."""
    qr  = qrcode.make(str(token.hashed_token))
    buf = BytesIO()
    qr.save(buf, format='PNG')
    buf.seek(0)
    return buf.read()


def build_static_qr_bytes(subscription) -> bytes:
    """Build QR image bytes directly from Subscription.uuid_token (static path).
    
    Used only for expired subscriptions. The QR contains the UUID string
    directly — verify_token handles this via PATH B (UUID format detection).
    """
    qr  = qrcode.make(str(subscription.uuid_token))
    buf = BytesIO()
    qr.save(buf, format='PNG')
    buf.seek(0)
    return buf.read()
