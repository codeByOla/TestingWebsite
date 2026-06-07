"""
Subscription Service
--------------------
Responsible for:
- Enforcing the one-active-subscription-per-student business rule.
- Persisting a new Subscription with correct date defaults.
- Generating and attaching the static QR image after first save.

Moved from:
- Subscription.save() override in models.py — the duplicate-guard and
  QR-image generation were business rules, not pure schema behaviour.
  save() now only sets date defaults and calls super().
"""

import qrcode

from datetime import date
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction

from subscriptions.models import Subscription, StudentProfile
from subscriptions.services.perf_service import timed

@timed("subscription_create")
def create_subscription(student: StudentProfile) -> Subscription:
    """
    Create and return a new active Subscription for *student*.

    Raises ValidationError if the student already has an active subscription.

    Steps:
    1. Acquire a row-level lock on any conflicting active subscription.
    2. Raise ValidationError if one is found.
    3. Save the new Subscription (date defaults applied by model.save()).
    4. Generate and attach the static QR image.
    """
    with transaction.atomic():
        # Step 1 & 2 — duplicate guard with pessimistic lock
        already_active = (
            Subscription.objects
            .select_for_update()
            .filter(
                student=student,
                expiry_date__gte=date.today(),
            )
            .exists()
        )
        if already_active:
            raise ValidationError("Studenti tashmë ka një abonim aktiv.")

        # Step 3 — persist (model.save() fills in date defaults)
        subscription = Subscription(student=student)
        subscription.save()

    # Step 4 — generate static QR image outside the lock
    # (ImageField.save triggers another UPDATE, keep it outside atomic if possible)
    if not subscription.qr_image:
        _attach_qr_image(subscription)

    return subscription

@timed("qr_generate")
def _attach_qr_image(subscription: Subscription) -> None:
    """Generate a static QR PNG from the subscription UUID and save it."""
    qr     = qrcode.make(str(subscription.uuid_token))
    buffer = BytesIO()
    qr.save(buffer, format='PNG')
    buffer.seek(0)
    file_name = f"{subscription.uuid_token}.png"
    subscription.qr_image.save(file_name, File(buffer), save=True)