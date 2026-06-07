"""
subscriptions/tests.py
======================
28 unit & integration tests covering every major behaviour of the
e-Abone subscription, QR-token and verification subsystem.

Thesis mapping
--------------
RQ-1  How the digitalised application/management/verification system works.
RQ-2  How UUIDs and rotating QR codes improve real-time verification,
      accuracy, and abuse reduction.
Hypothesis: the system provides faster, more accurate and abuse-resistant
            verification compared to the previous manual process.
"""

import logging
import uuid
from datetime import timedelta, date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from subscriptions.models import (
    StudentProfile,
    Subscription,
    TemporaryQRToken,
    University,
    VerificationLog,
    VerificationAnalytics,
)
from subscriptions.forms import CSVStudentValidator
from subscriptions.services import qr_service, verification_service, revocation_service

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_user(username, role="STUDENT", password="pass12345"):
    return User.objects.create_user(username=username, password=password, role=role)


def _make_controller(username="ctrl1"):
    return User.objects.create_user(
        username=username, password="pass12345",
        role="CONTROLLER", employee_id="E1",
    )


def _make_profile(user, barcode="11111", uni=None):
    if uni is None:
        uni, _ = University.objects.get_or_create(name="UT")
    return StudentProfile.objects.create(
        user=user, barcode=barcode,
        first_name="John", last_name="Doe", university=uni,
    )


def _active_sub(profile):
    return Subscription.objects.create(student=profile)


def _expired_sub(profile):
    return Subscription.objects.create(
        student=profile,
        issue_date=date.today() - timedelta(days=40),
        expiry_date=date.today() - timedelta(days=1),
    )


def _hashed(token: TemporaryQRToken) -> str:
    return token.hashed_token


# ===========================================================================
# GROUP 1 — Subscription model & service (6 tests)
# RQ-1: the system must correctly create, enforce and expire subscriptions.
# ===========================================================================

class SubscriptionModelTest(TestCase):

    def setUp(self):
        self.uni = University.objects.create(name="UT1")
        self.user = _make_user("s_model")
        self.profile = _make_profile(self.user, "10001", self.uni)

    # 1 ── Active subscription is recognised as active
    def test_active_subscription_is_active(self):
        sub = _active_sub(self.profile)
        self.assertTrue(sub.is_active())

    # 2 ── Expired subscription is not active
    def test_expired_subscription_is_not_active(self):
        sub = _expired_sub(self.profile)
        self.assertFalse(sub.is_active())

    # 3 ── Subscription save() fills issue_date and expiry_date automatically
    def test_save_sets_dates_automatically(self):
        sub = Subscription.objects.create(student=self.profile)
        self.assertIsNotNone(sub.issue_date)
        self.assertIsNotNone(sub.expiry_date)
        self.assertGreater(sub.expiry_date, sub.issue_date)

    # 4 ── Duplicate active subscription raises an error (service guard)
    def test_duplicate_active_subscription_raises(self):
        from subscriptions.services import subscription_service
        subscription_service.create_subscription(self.profile)
        with self.assertRaises(ValidationError):
            subscription_service.create_subscription(self.profile)

    # 5 ── UniqueConstraint: one subscription per student per month
    def test_unique_constraint_per_student_per_month(self):
        Subscription.objects.create(student=self.profile)
        from django.db import IntegrityError
        with self.assertRaises(Exception):
            # Force same year+month to trigger DB-level constraint
            Subscription.objects.create(student=self.profile)

    # 6 ── save() derives year and month fields correctly
    def test_save_derives_year_and_month(self):
        sub = Subscription.objects.create(student=self.profile)
        self.assertEqual(sub.year, sub.issue_date.year)
        self.assertEqual(sub.month, sub.issue_date.month)


# ===========================================================================
# GROUP 2 — Rotating QR token lifecycle (7 tests)
# RQ-2: UUID + rotating QR tokens are the core anti-abuse mechanism.
# ===========================================================================

class QRTokenLifecycleTest(TestCase):

    def setUp(self):
        import logging
        logging.getLogger('django.request').setLevel(logging.ERROR)
        self.uni = University.objects.create(name="UT2")
        self.user = _make_user("s_qr")
        self.profile = _make_profile(self.user, "20001", self.uni)
        self.sub = _active_sub(self.profile)

    # 7 ── A fresh token is valid
    def test_fresh_token_is_valid(self):
        token = qr_service.get_or_create_active_token(self.sub)
        self.assertTrue(token.is_valid())

    # 8 ── get_or_create_active_token returns the SAME token on second call
    def test_same_token_returned_within_window(self):
        t1 = qr_service.get_or_create_active_token(self.sub)
        t2 = qr_service.get_or_create_active_token(self.sub)
        self.assertEqual(t1.pk, t2.pk)

    # 9 ── After a token is used, a NEW token is generated
    def test_new_token_after_use(self):
        t1 = qr_service.get_or_create_active_token(self.sub)
        TemporaryQRToken.objects.filter(pk=t1.pk).update(is_used=True)
        t2 = qr_service.get_or_create_active_token(self.sub)
        self.assertNotEqual(t1.uuid_token, t2.uuid_token)

    # 10 ── After a token expires, a NEW token is generated
    def test_new_token_after_expiry(self):
        t1 = qr_service.get_or_create_active_token(self.sub)
        TemporaryQRToken.objects.filter(pk=t1.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        t2 = qr_service.get_or_create_active_token(self.sub)
        self.assertNotEqual(t1.uuid_token, t2.uuid_token)

    # 11 ── Token has a hashed_token (HMAC-SHA256, 64 hex chars)
    def test_token_has_valid_hash(self):
        token = qr_service.get_or_create_active_token(self.sub)
        self.assertIsNotNone(token.hashed_token)
        self.assertEqual(len(token.hashed_token), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in token.hashed_token))

    # 12 ── build_qr_bytes returns non-empty PNG bytes
    def test_build_qr_bytes_returns_png(self):
        token = qr_service.get_or_create_active_token(self.sub)
        raw = qr_service.build_qr_bytes(token)
        self.assertGreater(len(raw), 0)
        # PNG magic bytes
        self.assertTrue(raw.startswith(b'\x89PNG'))

    # 13 ── refresh_qr endpoint rate-limits at 4 requests/min
    def test_refresh_qr_rate_limited(self):
        self.client.login(username="s_qr", password="pass12345")
        one_min_ago = timezone.now() - timedelta(seconds=30)
        for _ in range(4):
            t = TemporaryQRToken.objects.create(
                subscription=self.sub,
                hashed_token=qr_service.generate_secure_token(uuid.uuid4()),
                expires_at=timezone.now() + timedelta(seconds=60),
            )
            TemporaryQRToken.objects.filter(pk=t.pk).update(created_at=one_min_ago)
        response = self.client.post(reverse("refresh_qr"))
        self.assertEqual(response.status_code, 429)


# ===========================================================================
# GROUP 3 — Token verification logic (8 tests)
# RQ-2: accurate, tamper-resistant verification in real time.
# ===========================================================================

class VerificationServiceTest(TestCase):

    def setUp(self):
        self.uni = University.objects.create(name="UT3")
        self.ctrl = _make_controller("ctrl_vs")
        self.student_user = _make_user("s_vs")
        self.profile = _make_profile(self.student_user, "30001", self.uni)

    # 14 ── Valid active subscription → result VALID
    def test_valid_active_subscription(self):
        sub = _active_sub(self.profile)
        token = qr_service.get_or_create_active_token(sub)
        outcome = verification_service.verify_token(
            self.ctrl, _hashed(token), "127.0.0.1"
        )
        self.assertEqual(outcome["result"], "VALID")
        self.assertIsNotNone(outcome["student"])

    # 15 ── Expired subscription → result EXPIRED
    def test_expired_subscription(self):
        sub = _expired_sub(self.profile)
        # Create a fresh QR token pointing at the expired sub
        qr = TemporaryQRToken.objects.create(
            subscription=sub,
            hashed_token=qr_service.generate_secure_token(uuid.uuid4()),
            expires_at=timezone.now() + timedelta(seconds=60),
        )
        outcome = verification_service.verify_token(
            self.ctrl, qr.hashed_token, "127.0.0.1"
        )
        self.assertEqual(outcome["result"], "EXPIRED")

    # 16 ── Completely unknown hash → result INVALID
    def test_unknown_token_returns_invalid(self):
        fake_hash = "a" * 64
        outcome = verification_service.verify_token(
            self.ctrl, fake_hash, "127.0.0.1"
        )
        self.assertEqual(outcome["result"], "INVALID")

    # 17 ── Malformed input (not 64 hex chars) → result INVALID
    def test_malformed_token_returns_invalid(self):
        outcome = verification_service.verify_token(
            self.ctrl, "NOT-A-VALID-HASH", "127.0.0.1"
        )
        self.assertEqual(outcome["result"], "INVALID")

    # 18 ── Once used, the same token cannot be reused (replay protection)
    def test_token_cannot_be_reused(self):
        sub = _active_sub(self.profile)
        token = qr_service.get_or_create_active_token(sub)
        h = _hashed(token)

        r1 = verification_service.verify_token(self.ctrl, h, "127.0.0.1")
        r2 = verification_service.verify_token(self.ctrl, h, "127.0.0.1")

        self.assertEqual(r1["result"], "VALID")
        # Second scan of the same hash must not be VALID
        self.assertNotEqual(r2["result"], "VALID")

    # 19 ── Revoked student → result REVOKED
    def test_revoked_student_returns_revoked(self):
        sub = _active_sub(self.profile)
        revocation_service.revoke_student(self.profile, admin_user=self.ctrl)
        token = qr_service.get_or_create_active_token(sub)

        # Force the token to be unused (revocation doesn't touch tokens)
        TemporaryQRToken.objects.filter(pk=token.pk).update(is_used=False)
        token.refresh_from_db()

        outcome = verification_service.verify_token(
            self.ctrl, token.hashed_token, "127.0.0.1"
        )
        self.assertEqual(outcome["result"], "REVOKED")

    # 20 ── Every verification writes a VerificationLog entry
    def test_verification_writes_log(self):
        sub = _active_sub(self.profile)
        token = qr_service.get_or_create_active_token(sub)
        before = VerificationLog.objects.count()
        verification_service.verify_token(self.ctrl, _hashed(token), "127.0.0.1")
        self.assertEqual(VerificationLog.objects.count(), before + 1)

    # 21 ── Every verification updates VerificationAnalytics
    def test_verification_updates_analytics(self):
        sub = _active_sub(self.profile)
        token = qr_service.get_or_create_active_token(sub)
        verification_service.verify_token(self.ctrl, _hashed(token), "127.0.0.1")
        today = timezone.now().date()
        self.assertTrue(
            VerificationAnalytics.objects.filter(date=today).exists()
        )


# ===========================================================================
# GROUP 4 — Revocation service (3 tests)
# RQ-1: admin must be able to revoke and restore student access.
# ===========================================================================

class RevocationServiceTest(TestCase):

    def setUp(self):
        self.uni = University.objects.create(name="UT4")
        self.admin = _make_user("admin_r", "ADMIN")
        self.student_user = _make_user("s_rev")
        self.profile = _make_profile(self.student_user, "40001", self.uni)

    # 22 ── Revoke sets is_revoked=True and records metadata
    def test_revoke_sets_flag_and_metadata(self):
        revocation_service.revoke_student(
            self.profile, admin_user=self.admin, reason="Abuse"
        )
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_revoked)
        self.assertEqual(self.profile.revocation_reason, "Abuse")
        self.assertEqual(self.profile.revoked_by, self.admin)
        self.assertIsNotNone(self.profile.revoked_at)

    # 23 ── Restore clears all revocation fields
    def test_restore_clears_revocation(self):
        revocation_service.revoke_student(self.profile, admin_user=self.admin)
        revocation_service.restore_student(self.profile)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_revoked)
        self.assertIsNone(self.profile.revoked_at)
        self.assertIsNone(self.profile.revoked_by)

    # 24 ── Revoked student cannot apply for a subscription (view guard)
    def test_revoked_student_cannot_apply(self):
        revocation_service.revoke_student(self.profile, admin_user=self.admin)
        self.client.login(username="s_rev", password="pass12345")
        response = self.client.post(reverse("apply_subscription"))
        self.assertRedirects(response, reverse("my_subscription"))


# ===========================================================================
# GROUP 5 — Access control on subscription views (4 tests)
# RQ-1: strict role gates.
# ===========================================================================

class SubscriptionAccessControlTest(TestCase):

    def setUp(self):
        import logging
        logging.getLogger('django.request').setLevel(logging.ERROR)
        self.uni = University.objects.create(name="UT5")
        self.student_user = _make_user("s_ac")
        self.profile = _make_profile(self.student_user, "50001", self.uni)

    # 25 ── Anonymous user cannot access apply_subscription
    def test_anonymous_cannot_apply(self):
        response = self.client.get(reverse("apply_subscription"))
        self.assertEqual(response.status_code, 302)

    # 26 ── Student without a profile is redirected from apply
    def test_student_without_profile_redirected(self):
        orphan = _make_user("orphan_ac")
        self.client.login(username="orphan_ac", password="pass12345")
        response = self.client.get(reverse("apply_subscription"))
        self.assertRedirects(response, reverse("dashboard"))

    # 27 ── Student already with active sub cannot apply again
    def test_student_with_active_sub_cannot_apply_again(self):
        _active_sub(self.profile)
        self.client.login(username="s_ac", password="pass12345")
        response = self.client.post(reverse("apply_subscription"))
        self.assertRedirects(response, reverse("my_subscription"))

    # 28 ── Controller cannot access apply_subscription (wrong role)
    def test_controller_cannot_apply_subscription(self):
        _make_controller("ctrl_ac")
        self.client.login(username="ctrl_ac", password="pass12345")
        response = self.client.get(reverse("apply_subscription"))
        self.assertEqual(response.status_code, 403)


# ===========================================================================
# GROUP 6 — CSV import validator (5 tests)
# RQ-1: bulk student import with institutional data integrity.
# ===========================================================================

class CSVStudentValidatorTest(TestCase):

    def setUp(self):
        self.uni = University.objects.create(name="UT6")
        StudentProfile.objects.create(
            barcode="123456",
            first_name="Existing",
            last_name="User",
            university=self.uni,
        )

    # 29 ── Valid data passes
    def test_valid_data_passes(self):
        form = CSVStudentValidator({
            "barcode": "999999",
            "first_name": "Ardit",
            "last_name": "Hoxha",
            "university": "UT6",
        })
        self.assertTrue(form.is_valid())

    # 30 ── Duplicate barcode is rejected
    def test_duplicate_barcode_rejected(self):
        form = CSVStudentValidator({
            "barcode": "123456",
            "first_name": "New",
            "last_name": "User",
            "university": "UT6",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("barcode", form.errors)

    # 31 ── Barcode with letters is rejected
    def test_barcode_with_letters_rejected(self):
        form = CSVStudentValidator({
            "barcode": "ABC12",
            "first_name": "Ardit",
            "last_name": "Hoxha",
            "university": "UT6",
        })
        self.assertFalse(form.is_valid())

    # 32 ── First name with digits is rejected
    def test_first_name_with_digits_rejected(self):
        form = CSVStudentValidator({
            "barcode": "888888",
            "first_name": "Ardit123",
            "last_name": "Hoxha",
            "university": "UT6",
        })
        self.assertFalse(form.is_valid())

    # 33 ── Whitespace-only fields are rejected
    def test_whitespace_only_fields_rejected(self):
        form = CSVStudentValidator({
            "barcode": "   ",
            "first_name": "   ",
            "last_name": "   ",
            "university": "   ",
        })
        self.assertFalse(form.is_valid())


# ===========================================================================
# GROUP 7 — Verification rate-limiting (2 tests)
# RQ-2: rate limiting protects the verification endpoint from abuse.
# ===========================================================================

class VerificationRateLimitTest(TestCase):

    def setUp(self):
        self.uni = University.objects.create(name="UT7")
        self.ctrl = _make_controller("ctrl_rl")
        self.student_user = _make_user("s_rl2")
        self.profile = _make_profile(self.student_user, "60001", self.uni)
        self.sub = _active_sub(self.profile)

    def _flood_invalid_logs(self, count=10):
        for _ in range(count):
            VerificationLog.objects.create(
                controller=self.ctrl,
                subscription=None,
                scanned_token=uuid.uuid4(),
                result="INVALID",
                ip_address="10.0.0.1",
            )

    # 34 ── count_recent_invalid_by_controller returns correct count
    def test_count_invalid_by_controller(self):
        self._flood_invalid_logs(count=3)
        count = verification_service.count_recent_invalid_by_controller(
            self.ctrl, window_minutes=5
        )
        self.assertEqual(count, 3)

    # 35 ── count_recent_bad_by_ip counts INVALID + EXPIRED
    def test_count_bad_by_ip(self):
        for result in ("INVALID", "EXPIRED", "VALID"):
            VerificationLog.objects.create(
                controller=self.ctrl,
                scanned_token=uuid.uuid4(),
                result=result,
                ip_address="10.0.0.2",
            )
        count = verification_service.count_recent_bad_by_ip("10.0.0.2", window_minutes=5)
        self.assertEqual(count, 2)   # INVALID + EXPIRED, not VALID


# ===========================================================================
# GROUP 8 — End-to-end verification via the HTTP view (2 tests)
# RQ-2: the full request→response cycle must reflect the token state.
# ===========================================================================

class VerifySubscriptionViewTest(TestCase):

    def setUp(self):
        self.uni = University.objects.create(name="UT8")
        self.ctrl = _make_controller("ctrl_view")
        self.student_user = _make_user("s_view")
        self.profile = _make_profile(self.student_user, "70001", self.uni)

    # 36 ── Valid QR hash in POST → template shows VALID message
    def test_valid_qr_shows_valid_message(self):
        sub = _active_sub(self.profile)
        token = qr_service.get_or_create_active_token(sub)

        self.client.login(username="ctrl_view", password="pass12345")
        response = self.client.post(
            reverse("verify_subscription"),
            {"uuid_token": token.hashed_token},
        )
        self.assertContains(response, "ABONIM I VLEFSHËM")

    # 37 ── Garbage input in POST → template shows INVALID message
    def test_invalid_input_shows_invalid_message(self):
        self.client.login(username="ctrl_view", password="pass12345")
        response = self.client.post(
            reverse("verify_subscription"),
            {"uuid_token": "gggggggg" * 8},   # 64 chars but not a real hash
        )
        self.assertContains(response, "QR I PAVLEFSHËM")