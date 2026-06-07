"""
core/tests.py
=============
6 unit tests covering the dashboard routing, role-based access
control, and the analytics service that powers the admin dashboard.

Thesis relevance: validates RQ-1 (correct role-routing inside the
digitalised system) and the analytics layer that gives admins
visibility into real-time usage.
"""

import logging

import logging

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from subscriptions.models import (
    StudentProfile,
    Subscription,
    University,
    VerificationLog,
    VerificationAnalytics,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(username, role, password="pass12345"):
    return User.objects.create_user(username=username, password=password, role=role)


def _make_controller(username="ctrl1"):
    return User.objects.create_user(
        username=username, password="pass12345", role="CONTROLLER", employee_id="E1"
    )


# ---------------------------------------------------------------------------
# Test 1 — Student dashboard renders the correct template
# ---------------------------------------------------------------------------

class StudentDashboardTest(TestCase):
    """
    A logged-in STUDENT must land on student_dashboard.html, not be
    redirected elsewhere.
    """

    def setUp(self):
        self.student = _make_user("std1", "STUDENT")

    def test_student_sees_student_dashboard(self):
        self.client.login(username="std1", password="pass12345")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "student_dashboard.html")


# ---------------------------------------------------------------------------
# Test 2 — Admin dashboard redirect
# ---------------------------------------------------------------------------

class AdminDashboardTest(TestCase):
    """
    A logged-in ADMIN must be redirected to admin_dashboard (separate view).
    """

    def setUp(self):
        self.admin = _make_user("adm1", "ADMIN")
        University.objects.create(name="UT")   # admin dashboard queries universities

    def test_admin_redirected_to_admin_dashboard(self):
        self.client.login(username="adm1", password="pass12345")
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("admin_dashboard"))


# ---------------------------------------------------------------------------
# Test 3 — Unauthenticated user cannot access the dashboard
# ---------------------------------------------------------------------------

class UnauthenticatedDashboardTest(TestCase):
    """
    Any unauthenticated GET to /dashboard/ must result in a redirect to
    the home/login page — not a 200.
    """

    def test_anonymous_redirected_from_dashboard(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)


# ---------------------------------------------------------------------------
# Test 4 — role_required decorator blocks wrong roles
# ---------------------------------------------------------------------------

class RoleRequiredDecoratorTest(TestCase):
    """
    A STUDENT hitting an ADMIN-only URL must receive 403 Forbidden.
    Thesis RQ-1: strict role enforcement is essential in a multi-actor system.
    """

    def setUp(self):
# Çaktivizo logging për 'django.request' gjatë këtij testi
        logging.getLogger('django.request').setLevel(logging.ERROR)
        self.student = _make_user("std2", "STUDENT")
        University.objects.create(name="UT2")

    def test_student_cannot_access_admin_dashboard(self):
        self.client.login(username="std2", password="pass12345")
        response = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_controller_cannot_access_admin_dashboard(self):
        _make_controller("ctrl_block")
        self.client.login(username="ctrl_block", password="pass12345")
        response = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Test 5 — Controller dashboard shows today's scan totals
# ---------------------------------------------------------------------------

class ControllerDashboardTest(TestCase):
    """
    The controller dashboard context must correctly aggregate today's
    VerificationLog entries for that specific controller.
    Thesis RQ-2: controllers need accurate real-time scan counts.
    """

    def setUp(self):
        self.ctrl = _make_controller("ctrl2")
        self.uni = University.objects.create(name="UT3")
        self.student_user = _make_user("stud_c", "STUDENT")
        self.profile = StudentProfile.objects.create(
            user=self.student_user,
            barcode="77777",
            first_name="Ana",
            last_name="Kola",
            university=self.uni,
        )
        self.sub = Subscription.objects.create(student=self.profile)

    def _log(self, result):
        VerificationLog.objects.create(
            controller=self.ctrl,
            subscription=self.sub,
            scanned_token=self.sub.uuid_token,
            result=result,
            ip_address="127.0.0.1",
        )

    def test_controller_dashboard_totals(self):
        self._log("VALID")
        self._log("VALID")
        self._log("EXPIRED")

        self.client.login(username="ctrl2", password="pass12345")
        response = self.client.get(reverse("controller_dashboard"))

        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertEqual(ctx["total_scans_today"], 3)
        self.assertEqual(ctx["valid_today"], 2)
        self.assertEqual(ctx["expired_today"], 1)


# ---------------------------------------------------------------------------
# Test 6 — Analytics service aggregates correctly (unit-level)
# ---------------------------------------------------------------------------

class AnalyticsServiceTest(TestCase):
    """
    get_admin_dashboard_stats() must return a dict with all expected keys
    and correct counts derived from live DB data.
    Thesis RQ-1 & RQ-2: the analytics layer is the digital visibility
    tool that replaces manual paper-based tracking.
    """

    def setUp(self):
        from core.services import analytics_service as svc
        self.svc = svc

        self.uni = University.objects.create(name="UT4")
        self.student_user = _make_user("stud_a", "STUDENT")
        self.profile = StudentProfile.objects.create(
            user=self.student_user,
            barcode="88888",
            first_name="Besi",
            last_name="Mema",
            university=self.uni,
        )
        self.sub = Subscription.objects.create(student=self.profile)

        # Seed a VerificationAnalytics row for today
        today = timezone.now().date()
        VerificationAnalytics.objects.create(
            date=today,
            hour=10,
            university=self.uni,
            valid_count=5,
            invalid_count=2,
            expired_count=1,
            revoked_count=0,
        )

    def test_dashboard_stats_contain_required_keys(self):
        """
        RQ-1: dashboard stats must expose subscription counts, monthly trends,
        peak-hour data, abuse breakdown, and university distribution.
        Daily per-scan counters were intentionally removed — admin focus is
        student management, not live scan monitoring (that belongs to the
        controller dashboard).
        """
        today = timezone.now().date()
        stats = self.svc.get_admin_dashboard_stats(today)

        required_keys = [
            "active_subscriptions",
            "expired_subscriptions",
            "total_students",
            "monthly_data",
            "peak_hours",
            "abuse_details",
            "university_stats",
        ]
        for key in required_keys:
            self.assertIn(key, stats, msg=f"Missing key: {key}")

    def test_abuse_details_is_list(self):
        """
        RQ-2: abuse_details must be a list (possibly empty) — it drives the
        admin's fraud-detection panel which is central to the hypothesis that
        the system 'reduces abuses'.
        """
        today = timezone.now().date()
        stats = self.svc.get_admin_dashboard_stats(today)
        self.assertIsInstance(stats["abuse_details"], list)

    def test_active_subscription_count(self):
        today = timezone.now().date()
        stats = self.svc.get_admin_dashboard_stats(today)
        # We created one active subscription in setUp
        self.assertEqual(stats["active_subscriptions"], 1)
        self.assertEqual(stats["total_students"], 1)