"""
accounts/tests.py
=================
5 unit tests covering the most critical authentication and
password-reset behaviours in the accounts app.

Thesis relevance: validates the secure, role-separated access model
that underpins the e-Abone digitalisation (RQ 1).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import PasswordResetToken, PasswordResetLog
from subscriptions.models import StudentProfile, University

User = get_user_model()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_student(username="s1", password="StrongPass123!"):
    return User.objects.create_user(username=username, password=password, role="STUDENT")


def _make_admin(username="a1", password="StrongPass123!"):
    return User.objects.create_user(username=username, password=password, role="ADMIN")


# ---------------------------------------------------------------------------
# Test 1 — Role separation: admin credentials rejected at student login
# ---------------------------------------------------------------------------

class RoleSeparationTest(TestCase):
    """
    An ADMIN user must NOT be able to authenticate through the student login
    endpoint, even with correct credentials.
    Thesis RQ-1: role-based access control is central to system integrity.
    """

    def setUp(self):
        _make_student("student1")
        _make_admin("admin1")

    def test_admin_cannot_login_via_student_endpoint(self):
        response = self.client.post(
            reverse("student_login"),
            {"username": "admin1", "password": "StrongPass123!"},
        )
        # Must stay on the login page (no redirect to dashboard)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Kredenciale te pasakta ose perdoruesi nuk eshte student",
        )

    def test_student_can_login_via_student_endpoint(self):
        response = self.client.post(
            reverse("student_login"),
            {"username": "student1", "password": "StrongPass123!"},
        )
        self.assertRedirects(response, reverse("dashboard"))


# ---------------------------------------------------------------------------
# Test 2 — Controller login requires employee_id
# ---------------------------------------------------------------------------

class ControllerLoginTest(TestCase):
    """
    A CONTROLLER must supply a matching employee_id; without it the
    login must be rejected even if username/password are correct.
    """

    def setUp(self):
        self.ctrl = User.objects.create_user(
            username="ctrl1",
            password="StrongPass123!",
            role="CONTROLLER",
            employee_id="EMP-001",
        )

    def test_controller_login_fails_without_employee_id(self):
        response = self.client.post(
            reverse("controller_login"),
            {"username": "ctrl1", "password": "StrongPass123!", "employee_id": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kredenciale te pasakta per kontrolluesin")

    def test_controller_login_succeeds_with_correct_employee_id(self):
        """
        RQ-1: a controller with valid credentials + employee_id must be
        redirected to dashboard (which in turn routes to controller_dashboard).
        We do NOT follow redirects so we can assert the immediate 302 → dashboard.
        """
        response = self.client.post(
            reverse("controller_login"),
            {"username": "ctrl1", "password": "StrongPass123!", "employee_id": "EMP-001"},
        )
        self.assertRedirects(
            response,
            reverse("dashboard"),
            status_code=302,
            fetch_redirect_response=False,
        )


# ---------------------------------------------------------------------------
# Test 3 — PasswordResetToken validity window
# ---------------------------------------------------------------------------

class PasswordResetTokenTest(TestCase):
    """
    A token must be valid when freshly created and invalid after 15 minutes.
    Thesis RQ-1: secure, time-limited self-service password reset is part of
    the digitalised workflow.
    """

    def setUp(self):
        self.user = _make_student("s_reset")

    def test_fresh_token_is_valid(self):
        token = PasswordResetToken.objects.create(user=self.user)
        self.assertTrue(token.is_valid())

    def test_expired_token_is_invalid(self):
        token = PasswordResetToken.objects.create(user=self.user)
        # Wind the clock back beyond the 15-minute window
        PasswordResetToken.objects.filter(pk=token.pk).update(
            created_at=timezone.now() - timezone.timedelta(minutes=16)
        )
        token.refresh_from_db()
        self.assertFalse(token.is_valid())

    def test_used_token_is_invalid(self):
        token = PasswordResetToken.objects.create(user=self.user, is_used=True)
        self.assertFalse(token.is_valid())


# ---------------------------------------------------------------------------
# Test 4 — Password reset rate-limiting (IP-based)
# ---------------------------------------------------------------------------

class PasswordResetRateLimitTest(TestCase):
    """
    After _MAX_ATTEMPTS (5) failed VERIFY attempts from the same IP, the
    endpoint must return a BLOCKED response rather than processing the form.
    Thesis RQ-1: abuse-prevention is critical for a public-facing digital service.
    """

    def setUp(self):
        self.university = University.objects.create(name="UT")
        self.user = _make_student("s_rl")
        StudentProfile.objects.create(
            user=self.user,
            barcode="55555",
            first_name="Rate",
            last_name="Limit",
            university=self.university,
        )

    def _flood_failed_logs(self, ip="127.0.0.1", count=5):
        for _ in range(count):
            PasswordResetLog.objects.create(
                user=self.user,
                username_input=self.user.username,
                barcode_input="wrong",
                result="FAILED",
                step="VERIFY",
                ip_address=ip,
            )

    def test_blocked_after_max_failed_attempts(self):
        self._flood_failed_logs()
        response = self.client.post(
            reverse("reset_password_request"),
            {
                "username": self.user.username,
                "barcode": "55555",
                "first_name": "Rate",
                "last_name": "Limit",
            },
            REMOTE_ADDR="127.0.0.1",
        )
        self.assertContains(
            response,
            "Shumë përpjekje të dështuara",
        )

    def test_not_blocked_below_max_attempts(self):
        """4 failures should NOT trigger a block."""
        self._flood_failed_logs(count=4)
        response = self.client.post(
            reverse("reset_password_request"),
            {
                "username": self.user.username,
                "barcode": "WRONG",   # will fail verification, not rate-limit
                "first_name": "Rate",
                "last_name": "Limit",
            },
            REMOTE_ADDR="127.0.0.1",
        )
        # Must not show the rate-limit message
        self.assertNotContains(response, "Shumë përpjekje të dështuara")


# ---------------------------------------------------------------------------
# Test 5 — Registration with invalid barcode is rejected
# ---------------------------------------------------------------------------

class StudentRegistrationTest(TestCase):
    """
    Attempting to register with a barcode that doesn't exist in the
    StudentProfile table must be rejected with a clear error message.
    Thesis RQ-1: only pre-enrolled (institutionally verified) students
    may create accounts.
    """

    def setUp(self):
        self.university = University.objects.create(name="UT")

    def test_registration_with_nonexistent_barcode_fails(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        fake_photo = SimpleUploadedFile(
            "photo.jpg", b"fake", content_type="image/jpeg"
        )
        response = self.client.post(
            reverse("register_student"),
            {
                "username": "newstudent",
                "password": "StrongPass123!",
                "confirm_password": "StrongPass123!",
                "barcode": "DOESNOTEXIST",
                "first_name": "John",
                "last_name": "Doe",
                "university": self.university.pk,
                "photo": fake_photo,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Barcode i pavlefshëm")

    def test_registration_with_already_used_barcode_fails(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        existing_user = _make_student("existing_owner")
        profile = StudentProfile.objects.create(
            user=existing_user,
            barcode="99999",
            first_name="John",
            last_name="Doe",
            university=self.university,
        )

        fake_photo = SimpleUploadedFile(
            "photo.jpg", b"fake", content_type="image/jpeg"
        )
        response = self.client.post(
            reverse("register_student"),
            {
                "username": "hijacker",
                "password": "StrongPass123!",
                "confirm_password": "StrongPass123!",
                "barcode": profile.barcode,
                "first_name": "John",
                "last_name": "Doe",
                "university": self.university.pk,
                "photo": fake_photo,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ky barcode është tashmë i regjistruar")