import json
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractMonth
from django.shortcuts import redirect, render
from django.utils import timezone

from core.decorators import role_required
from core.services import analytics_service
from subscriptions.models import (
    StudentProfile,
    Subscription,
    University,
    VerificationLog,
    VerificationAnalytics,
)
from core.services import hypothesis_metrics as hyp_metrics
from subscriptions.services import verification_timing_service


def home(request):
    return render(request, "home.html")


@login_required
def dashboard(request):
    if request.user.role == "STUDENT":
        return render(request, "student_dashboard.html")
    elif request.user.role == "ADMIN":
        return redirect("admin_dashboard")
    elif request.user.role == "CONTROLLER":
        return redirect("controller_dashboard")
    return redirect("home")


@login_required
@role_required("ADMIN")
def admin_dashboard(request):
    today = timezone.now().date()

    context = analytics_service.get_admin_dashboard_stats(today)

    students = StudentProfile.objects.select_related(
        "university", "user"
    ).prefetch_related("subscription_set")

    search_query = request.GET.get("search")
    if search_query:
        students = students.filter(
            Q(barcode__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(user__username__icontains=search_query)
        )

    university_filter = request.GET.get("university")
    if university_filter:
        students = students.filter(university__id=university_filter)

    status_filter = request.GET.get("status")
    if status_filter == "active":
        students = students.filter(
            subscription__expiry_date__gte=today
        ).distinct()
    elif status_filter == "inactive":
        active_ids = Subscription.objects.filter(
            expiry_date__gte=today
        ).values_list("student_id", flat=True)
        students = students.exclude(id__in=active_ids)

    allowed_sort_fields = {
        "first_name": "first_name",
        "-first_name": "-first_name",
        "last_name": "last_name",
        "-last_name": "-last_name",
        "created": "id",
        "-created": "-id",
    }
    sort_by = request.GET.get("sort")
    if sort_by in allowed_sort_fields:
        students = students.order_by(allowed_sort_fields[sort_by])
    else:
        students = students.order_by("last_name")

    paginator = Paginator(students, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    for student in page_obj:
        student.has_active_subscription = any(
            sub.expiry_date >= today for sub in student.subscription_set.all()
        )

    context.update(
        {
            "students": page_obj,
            "page_obj": page_obj,
            "universities": University.objects.all(),
        }
    )

    return render(request, "admin_dashboard.html", context)


@login_required
@role_required("CONTROLLER")
def controller_dashboard(request):
    today = timezone.now().date()

    total_scans_today = VerificationLog.objects.filter(
        controller=request.user, timestamp__date=today
    ).count()

    valid_today = VerificationLog.objects.filter(
        controller=request.user, timestamp__date=today, result="VALID"
    ).count()

    expired_today = VerificationLog.objects.filter(
        controller=request.user, timestamp__date=today, result="EXPIRED"
    ).count()

    invalid_today = VerificationLog.objects.filter(
        controller=request.user, timestamp__date=today, result="INVALID"
    ).count()

    revoked_today = VerificationLog.objects.filter(
        controller=request.user, timestamp__date=today, result="REVOKED"
    ).count()


    timing_stats = verification_timing_service.get_timing_stats()
    hyp_report = hyp_metrics.HypothesisMetrics.generate_full_report(last_n_days=30)

    context = {
        "total_scans_today":       total_scans_today,
        "valid_today":             valid_today,
        "expired_today":           expired_today,
        "invalid_today":           invalid_today,
        "revoked_today":           revoked_today,
        "avg_verification_ms":     timing_stats["avg_verification_ms"],
        "verification_count":      timing_stats["verification_count"],
        "hyp":                     hyp_report,
    }

    return render(request, "controller/dashboard.html", context)


