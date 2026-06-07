"""
Analytics Service
-----------------
Responsible for:
- Incrementing per-hour, per-university verification counters.

Moved from:
- VerificationAnalytics.record() classmethod in models.py — aggregation
  logic is a business concern, not a schema concern.
"""

import logging

from django.db import models as django_models
from django.utils import timezone

from subscriptions.models import VerificationAnalytics

logger = logging.getLogger(__name__)


def record(result: str, university, timestamp=None, sub_result: str = None) -> None:
    if timestamp is None:
        timestamp = timezone.now()

    local = timezone.localtime(timestamp)

    obj, _ = VerificationAnalytics.objects.get_or_create(
        date=local.date(),
        hour=local.hour,
        university=university,
        defaults={
            'valid_count':                 0,
            'invalid_count':               0,
            'expired_count':               0,
            'revoked_count':               0,
            'already_used_valid_count':    0,
        },
    )

    if result == 'VALID':
        VerificationAnalytics.objects.filter(pk=obj.pk).update(
            valid_count=django_models.F('valid_count') + 1
        )
    elif result == 'EXPIRED':
        VerificationAnalytics.objects.filter(pk=obj.pk).update(
            expired_count=django_models.F('expired_count') + 1
        )
    elif result == 'REVOKED':
        VerificationAnalytics.objects.filter(pk=obj.pk).update(
            revoked_count=django_models.F('revoked_count') + 1
        )
    elif result == 'INVALID':
        # Distinguish sub-types for abuse analytics
        if sub_result == 'ALREADY_USED_VALID':
            VerificationAnalytics.objects.filter(pk=obj.pk).update(
                invalid_count=django_models.F('invalid_count') + 1,
                already_used_valid_count=django_models.F('already_used_valid_count') + 1,
            )
        #elif sub_result == 'ALREADY_USED_EXPIRED':
            #VerificationAnalytics.objects.filter(pk=obj.pk).update(
                #invalid_count=django_models.F('invalid_count') + 1,
                #already_used_expired_count=django_models.F('already_used_expired_count') + 1,
            #)
        else:
            VerificationAnalytics.objects.filter(pk=obj.pk).update(
                invalid_count=django_models.F('invalid_count') + 1
            )