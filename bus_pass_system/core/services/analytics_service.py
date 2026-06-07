"""
Heavy analytics calculations extracted from admin_dashboard.
All functions are pure data-fetchers; they receive already-filtered
querysets or simple primitives and return plain Python dicts / lists.
"""

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractMonth
from django.utils import timezone

from subscriptions.models import (
    StudentProfile,
    Subscription,
    VerificationAnalytics,
    VerificationLog,
)


def get_subscription_counts(today):
    """
    Returns active_subscriptions, expired_subscriptions, total_students.
    """
    active_student_ids = Subscription.objects.filter(
        expiry_date__gte=today
    ).values_list("student_id", flat=True)

    active_subscriptions = (
        StudentProfile.objects.filter(subscription__expiry_date__gte=today)
        .distinct()
        .count()
    )

    expired_subscriptions = StudentProfile.objects.exclude(
        id__in=active_student_ids
    ).count()

    total_students = StudentProfile.objects.count()

    return {
        "active_subscriptions": active_subscriptions,
        "expired_subscriptions": expired_subscriptions,
        "total_students": total_students,
    }


def get_verification_counts_today(today):
    """
    Returns verifications_today, valid_today, invalid_today from VerificationLog.

    verifications_today = VerificationLog.objects.filter(
        timestamp__date=today
    ).count()

    valid_today = VerificationLog.objects.filter(
        timestamp__date=today, result="VALID"
    ).count()

    invalid_today = verifications_today - valid_today

    return {
        "verifications_today": verifications_today,
        "valid_today": valid_today,
        "invalid_today": invalid_today,
    }
    """

def get_monthly_subscription_data(current_year):
    """
    Returns per-month subscription counts for the given year.
    """
    monthly_data = (
        Subscription.objects.filter(issue_date__year=current_year)
        .annotate(issue_month=ExtractMonth("issue_date"))
        .values("issue_month")
        .annotate(total=Count("id"))
        .order_by("issue_month")
    )
    return list(monthly_data)


def get_peak_hours(last_n_days=7):
    """
    Returns hourly verification aggregates for the last N days.
    """
    cutoff = timezone.now().date() - timedelta(days=last_n_days)
    peak_hours = list(
        VerificationAnalytics.objects.filter(date__gte=cutoff)
        .values("hour")
        .annotate(
            total_valid=Sum("valid_count"),
            total_invalid=Sum("invalid_count"),
            total_expired=Sum("expired_count"),
        )
        .order_by("hour")
    )
    return peak_hours


def get_abuse_days(last_n_days=7):
    """
    Returns the days with highest invalid counts over the last N days.
    """
    cutoff = timezone.now().date() - timedelta(days=last_n_days)
    abuse_days = list(
        VerificationAnalytics.objects.filter(date__gte=cutoff)
        .values("date")
        .annotate(
            total_invalid=Sum("invalid_count"),
            total_valid=Sum("valid_count"),
        )
        .order_by("-total_invalid")[:7]
    )
    return abuse_days


def get_abuse_details(last_n_days=7):
    from subscriptions.models import VerificationLog
    cutoff = timezone.now().date() - timedelta(days=last_n_days)

    # ALREADY_USED_EXPIRED removed — expired subs now use static UUID QR
    # and always return EXPIRED result, never ALREADY_USED_EXPIRED.
    rows = (
        VerificationLog.objects.filter(
            timestamp__date__gte=cutoff,
            sub_result__in=("ALREADY_USED_VALID", "INVALID"),
        )
        .values("timestamp__date", "sub_result")
        .annotate(total=Count("id"))
        .order_by("timestamp__date")
    )

    from collections import defaultdict
    pivot = defaultdict(lambda: {"ALREADY_USED_VALID": 0, "INVALID": 0})
    for row in rows:
        pivot[row["timestamp__date"]][row["sub_result"]] = row["total"]

    result = []
    for date_val in sorted(pivot.keys(), reverse=True):
        counts = pivot[date_val]
        result.append({
            "date": date_val,
            "reused_valid": counts["ALREADY_USED_VALID"],
            "pure_invalid": counts["INVALID"],
            "total_suspicious": counts["ALREADY_USED_VALID"] + counts["INVALID"],
        })

    return result

def get_university_stats(last_n_days=30):
    """
    Returns per-university verification aggregates for the last N days.
    """
    cutoff = timezone.now().date() - timedelta(days=last_n_days)
    university_stats = list(
        VerificationAnalytics.objects.filter(
            date__gte=cutoff, university__isnull=False
        )
        .values("university__name")
        .annotate(
            total_valid=Sum("valid_count"),
            total_invalid=Sum("invalid_count"),
            total_expired=Sum("expired_count"),
        )
        .order_by("-total_valid")
    )
    return university_stats


def get_today_analytics(today):
    """
    Returns aggregated analytics counts and rates for today.
    """
    today_qs = VerificationAnalytics.objects.filter(date=today)
    agg = today_qs.aggregate(
        sv=Sum("valid_count"),
        si=Sum("invalid_count"),
        se=Sum("expired_count"),
    )

    valid = agg["sv"] or 0
    invalid = agg["si"] or 0
    expired = agg["se"] or 0
    total = valid + invalid + expired

    expired_rate = round(expired / total * 100, 1) if total else 0
    invalid_rate = round(invalid / total * 100, 1) if total else 0

    return {
        "analytics_valid_today": valid,
        "analytics_invalid_today": invalid,
        "analytics_expired_today": expired,
        "analytics_total_today": total,
        "expired_rate": expired_rate,
        "invalid_rate": invalid_rate,
    }


def get_admin_dashboard_stats(today):
    """
    Convenience wrapper: returns a single dict with all heavy analytics
    needed by admin_dashboard. The view still owns filtering/pagination.
    """
    current_year = today.year

    ctx = {}
    ctx.update(get_subscription_counts(today))
    #ctx.update(get_verification_counts_today(today))
    ctx["monthly_data"] = get_monthly_subscription_data(current_year)
    ctx["peak_hours"] = get_peak_hours(last_n_days=7)
    ctx["abuse_days"] = get_abuse_days(last_n_days=7)
    ctx["abuse_details"] = get_abuse_details(last_n_days=7)
    ctx["university_stats"] = get_university_stats(last_n_days=30)
    #ctx.update(get_today_analytics(today))
    return ctx