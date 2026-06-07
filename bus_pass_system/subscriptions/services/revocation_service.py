"""
Revocation Service
------------------
Responsible for:
- Revoking a student's access with reason and audit trail.
- Restoring a previously revoked student.

Centralised here so that revocation rules are not scattered across views.
"""

from django.utils import timezone

from subscriptions.models import StudentProfile


def revoke_student(student: StudentProfile, admin_user, reason: str = "") -> None:
    """
    Mark *student* as revoked.

    Sets is_revoked, revoked_at, revocation_reason, and revoked_by
    in a single targeted UPDATE (update_fields) to avoid overwriting
    unrelated fields.
    """
    student.is_revoked        = True
    student.revoked_at        = timezone.now()
    student.revocation_reason = reason
    student.revoked_by        = admin_user
    student.save(update_fields=[
        "is_revoked",
        "revoked_at",
        "revocation_reason",
        "revoked_by",
    ])


def restore_student(student: StudentProfile) -> None:
    """
    Lift the revocation from *student*.

    Clears all revocation fields so the student can generate QR codes
    and be verified again.  A fresh TemporaryQRToken will be issued
    the next time get_or_create_active_token() is called for them.
    """
    student.is_revoked        = False
    student.revoked_at        = None
    student.revocation_reason = ""
    student.revoked_by        = None
    student.save(update_fields=[
        "is_revoked",
        "revoked_at",
        "revocation_reason",
        "revoked_by",
    ])