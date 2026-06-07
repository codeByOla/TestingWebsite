"""
Views — HTTP boundary only.

Each view is responsible for:
1. Authenticating / authorising the request (via decorators).
2. Rate-limiting where applicable.
3. Reading request data.
4. Calling the appropriate service function.
5. Rendering the response.

No business logic lives here.
"""

import base64
import csv
import io
import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from urllib3 import request
from django.db import IntegrityError
from django.db.transaction import savepoint, savepoint_commit, savepoint_rollback

from subscriptions.services import verification_timing_service
from core.decorators import role_required
from .forms import CSVStudentValidator, CSVUploadForm, StudentForm
from .models import StudentProfile, Subscription, VerificationLog
from .services import (
    analytics_service,
    qr_service,
    revocation_service,
    subscription_service,
    token_cleanup_service,
    verification_service,
)
from django.db import transaction
from .models import University
from subscriptions.services.perf_service import measure

logger = logging.getLogger(__name__)
User = get_user_model()

# ── Rate-limit constants (view-layer concerns) ─────────────────────────────
_VERIFY_WINDOW_MINUTES          = 5
_VERIFY_MAX_INVALID_CONTROLLER  = 10
_VERIFY_MAX_BAD_IP              = 20
_VERIFY_MAX_TOTAL_IP            = 60


def _get_client_ip(request) -> str:
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


# ── Student views ──────────────────────────────────────────────────────────

@login_required
@role_required("STUDENT")
def apply_subscription(request):
    try:
        profile = request.user.studentprofile
    except StudentProfile.DoesNotExist:
        messages.error(request, "Profili i studentit nuk u gjet")
        return redirect("dashboard")

    if profile.is_revoked:
        messages.error(request, "Abonimi juaj është revokuar nga administratori.")
        return redirect("my_subscription")

    existing = Subscription.objects.filter(
        student=profile,
        expiry_date__gte=timezone.now().date(),
    ).exists()
    if existing:
        messages.error(request, "Ju tashmë keni abonim aktiv.")
        return redirect("my_subscription")

    if request.method == "POST":
        try:
            subscription_service.create_subscription(profile)
            messages.success(request, "Abonimi u krijua me sukses!")
        except ValidationError as e:
            messages.error(request, str(e))
        except Exception as e:
            logger.error("Subscription creation failed for profile %s: %s", profile.pk, e)
            messages.error(request, "Ndodhi një gabim. Provo përsëri.")
        return redirect("my_subscription")

    return render(request, "apply_subscription.html")


@login_required
@role_required("STUDENT")
def my_subscription(request):
    try:
        profile = request.user.studentprofile
    except StudentProfile.DoesNotExist:
        return render(request, "no_subscription.html")

    if profile.is_revoked:

        reason = profile.revocation_reason
        revoked_at = profile.revoked_at

        if reason and revoked_at:
            messages.error(
                request,
                f"Abonimi juaj është revokuar nga administratori më "
                f"{revoked_at.strftime('%d %b %Y, %H:%M')}/ Arsyeja: {reason}"
            )
        elif reason:
            messages.error(
                request,
            f"Abonimi juaj është revokuar nga administratori / Arsyeja: {reason}"
            )
        else:
            messages.error(request, "Abonimi juaj është revokuar nga administratori.")

        return render(request, "my_subscription.html", {
            "profile":        profile,
            "subscription":   None,
            "today":          timezone.now().date(),
            "qr_image_b64":   None,
            "qr_expires_iso": None,
            "is_revoked":     True,
            "qr_is_static":   False,
        })

    today = timezone.now().date()

    subscription = (
        Subscription.objects
        .filter(student=profile)
        .order_by("-issue_date")
        .first()
    )

    qr_image_b64   = None
    qr_expires_iso = None
    qr_is_static   = False  # False = dynamic (TemporaryQRToken), True = static (UUID)

    if subscription:
        is_active = subscription.expiry_date and subscription.expiry_date >= today

        if is_active:
            # Active subscription → dynamic temporary QR (60 seconds)
            token          = qr_service.get_or_create_active_token(subscription)
            qr_image_b64   = base64.b64encode(qr_service.build_qr_bytes(token)).decode()
            qr_expires_iso = token.expires_at.isoformat()
            qr_is_static   = False
        else:
            # Expired subscription → static QR from Subscription.uuid_token
            qr_image_b64 = base64.b64encode(
                qr_service.build_static_qr_bytes(subscription)
            ).decode()
            qr_expires_iso = None
            qr_is_static   = True

    return render(request, "my_subscription.html", {
        "profile":        profile,
        "subscription":   subscription,
        "today":          today,
        "qr_image_b64":   qr_image_b64,
        "qr_expires_iso": qr_expires_iso,
        "is_revoked":     False,
        "qr_is_static":   qr_is_static,
    })


@login_required
@role_required("STUDENT")
@require_POST
def refresh_qr(request):
    try:
        profile = request.user.studentprofile
    except StudentProfile.DoesNotExist:
        return JsonResponse({"error": "no_profile"}, status=403)

    if profile.is_revoked:
        return JsonResponse({"error": "revoked"}, status=403)

    # refresh_qr is only called for active subscriptions (template guards this).
    # Expired subscriptions show a static QR that never needs refreshing.
    subscription = (
        Subscription.objects
        .filter(student=profile, expiry_date__gte=timezone.now().date())
        .order_by("-issue_date")
        .first()
    )
    if not subscription:
        return JsonResponse({"error": "no_active_subscription"}, status=403)

    from .models import TemporaryQRToken
    from datetime import timedelta
    one_min_ago = timezone.now() - timedelta(minutes=1)
    recent = TemporaryQRToken.objects.filter(
        subscription=subscription,
        created_at__gte=one_min_ago,
    ).count()
    if recent >= 4:
        return JsonResponse({"error": "rate_limited"}, status=429)

    try:
        token  = qr_service.get_or_create_active_token(subscription)
        qr_b64 = base64.b64encode(qr_service.build_qr_bytes(token)).decode()
        return JsonResponse({
            "qr_image_b64": qr_b64,
            "expires_iso":  token.expires_at.isoformat(),
            "hashed_token": token.hashed_token,
        })
    except Exception as e:
        logger.error("refresh_qr failed for sub %s: %s", subscription.pk, e)
        return JsonResponse({"error": "server_error"}, status=500)

# ── Admin views ────────────────────────────────────────────────────────────

@login_required
@role_required("ADMIN")
def import_students(request):
    if request.method == "POST":
        form = CSVUploadForm(request.POST, request.FILES)
        if form.is_valid():
            file = request.FILES["file"]
            if not file.name.endswith(".csv"):
                messages.error(request, "Skedari duhet te jete ne format CSV.")
                return redirect("import_students")
            try:
                decoded_file = file.read().decode("utf-8")
                io_string    = io.StringIO(decoded_file)
                reader       = csv.DictReader(io_string)
                required     = {"barcode", "first_name", "last_name", "university"}
                if not required.issubset(reader.fieldnames):
                    messages.error(request, "Struktura e skedarit CSV eshte e pavlefshme.")
                    return redirect("import_students")

                created_count = 0
                skipped_count = 0

                with measure("csv_import"):                          # ← SHTUAR
                    with transaction.atomic():
                        for row_num, row in enumerate(reader, start=2):
                            try:
                                barcode         = (row.get("barcode")     or "").strip()
                                first_name      = (row.get("first_name")  or "").strip()
                                last_name       = (row.get("last_name")   or "").strip()
                                university_name = (row.get("university")  or "").strip()
                            except Exception:
                                skipped_count += 1
                                logger.warning("CSV rresht %d could not be parsed, skipping.", row_num)
                                continue

                            val = CSVStudentValidator({
                                "barcode":    barcode,
                                "first_name": first_name,
                                "last_name":  last_name,
                                "university": university_name,
                            })
                            if not val.is_valid():
                                skipped_count += 1
                                logger.warning("Row %d errors: %s", row_num, val.errors)
                                continue

                            try:
                                sid = savepoint()
                                university, _ = University.objects.get_or_create(name=university_name)
                                StudentProfile.objects.create(
                                    barcode    = val.cleaned_data["barcode"],
                                    first_name = val.cleaned_data["first_name"],
                                    last_name  = val.cleaned_data["last_name"],
                                    university = university,
                                )
                                savepoint_commit(sid)
                                created_count += 1
                            except IntegrityError as e:
                                savepoint_rollback(sid)
                                skipped_count += 1
                                logger.warning("Row %d IntegrityError (duplicate?): %s", row_num, e)
                            except Exception as e:
                                savepoint_rollback(sid)
                                skipped_count += 1
                                logger.error("Row %d unexpected error: %s", row_num, e)

                messages.success(
                    request,
                    f"{created_count} Studentët u ngarkuan me sukses. {skipped_count} U anashkalua",
                )
                return redirect("admin_dashboard")
            except Exception as e:
                messages.error(
                    request,
                    f"Ndodhi nje gabim gjate importit. Asnje student nuk u ruajt. Error: {str(e)}",
                )
                return redirect("import_students")

        return render(request, "import_students.html", {"form": form})

    form = CSVUploadForm()
    return render(request, "import_students.html", {"form": form})


# ── create_student — shto with measure() rreth form.save() ───────────────

@login_required
@role_required("ADMIN")
def create_student(request):
    if request.method == "POST":
        form = StudentForm(request.POST)
        if form.is_valid():
            with measure("manual_student_create"):                   # ← SHTUAR
                form.save()
            messages.success(request, "Studenti u shtua me sukses.")
            return redirect("admin_dashboard")
    else:
        form = StudentForm()
    return render(request, "admin/create_student.html", {"form": form})


@login_required
@role_required("ADMIN")
def update_student(request, pk):
    student = get_object_or_404(StudentProfile, pk=pk)
    if request.method == "POST":
        form = StudentForm(request.POST, instance=student)
        if form.is_valid():
            form.save()
            messages.success(request, "Studenti u perditesua me sukses.")
            return redirect("admin_dashboard")
    else:
        form = StudentForm(instance=student)
    return render(request, "admin/update_student.html", {"form": form})


@login_required
@role_required("ADMIN")
def delete_student(request, pk):
    student = get_object_or_404(StudentProfile, pk=pk)
    if request.method == "POST":
        student.delete()
        messages.success(request, "Studenti u fshi me sukses.")
        return redirect("admin_dashboard")
    return render(request, "admin/delete_student.html", {"student": student})


@login_required
@role_required("ADMIN")
@require_POST
def revoke_student(request, pk):
    student = get_object_or_404(StudentProfile, pk=pk)
    reason  = request.POST.get("reason", "").strip()
    revocation_service.revoke_student(student, admin_user=request.user, reason=reason)
    messages.success(
        request,
        f"Aksesi i studentit {student.first_name} {student.last_name} u revokua.",
    )
    return redirect("admin_dashboard")


@login_required
@role_required("ADMIN")
@require_POST
def restore_student(request, pk):
    student = get_object_or_404(StudentProfile, pk=pk)
    revocation_service.restore_student(student)
    messages.success(
        request,
        f"Aksesi i studentit {student.first_name} {student.last_name} u rikthye.",
    )
    return redirect("admin_dashboard")

@login_required
@role_required("CONTROLLER")
def controller_logs(request):
    logs = (
        VerificationLog.objects
        .filter(controller=request.user)
        .select_related("subscription", "subscription__student")
        .order_by("-timestamp")[:100]
    )
    return render(request, "controller/logs.html", {"logs": logs})


@login_required
@role_required("CONTROLLER")
def verify_subscription(request):
    if request.method != "POST":
        return render(request, "controller/verify.html")

    ip  = _get_client_ip(request)
    now = timezone.now()

    if verification_service.count_recent_invalid_by_controller(
        request.user, _VERIFY_WINDOW_MINUTES
    ) >= _VERIFY_MAX_INVALID_CONTROLLER:
        messages.error(request, "Shume tentativa te pavlefshme. Provo perseri pas pak.")
        return render(request, "controller/verify.html")

    if verification_service.count_recent_bad_by_ip(
        ip, _VERIFY_WINDOW_MINUTES
    ) >= _VERIFY_MAX_BAD_IP:
        messages.error(request, "Shume tentativa te pavlefshme nga ky IP. Provo perseri pas pak.")
        return render(request, "controller/verify.html")

    if verification_service.count_recent_total_by_ip(
        ip, _VERIFY_WINDOW_MINUTES
    ) >= _VERIFY_MAX_TOTAL_IP:
        messages.error(request, "Kufiri i kërkesave u arrit nga ky IP. Provo perseri pas pak.")
        return render(request, "controller/verify.html")

    raw_token = (request.POST.get("uuid_token") or "").strip()

    # ── Delegate all domain logic to the service ───────────────────────────
    #ADDED
    timed_verify_fn = verification_timing_service.timed_verify(
        verification_service.verify_token
    )
    outcome = timed_verify_fn(
        controller=request.user,
        raw_token=raw_token,
        ip_address=ip,
        now=now,
    )

    # ── Probabilistic maintenance (view triggers, service executes) ────────
    token_cleanup_service.run_probabilistic_cleanup(probability=0.05)

    return render(request, "controller/verify.html", {
        "result":       outcome["result"],
        "sub_result":   outcome["sub_result"],
        "student":      outcome["student"],
        "subscription": outcome["subscription"],
    })





