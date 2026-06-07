import time
import logging

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render

from subscriptions.models import StudentProfile
from .forms import (
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    StudentRegisterForm,
)
from .models import PasswordResetToken
from .services import auth_service

from io import BytesIO
from django.core.files.base import ContentFile
from PIL import Image

logger = logging.getLogger(__name__)
User = get_user_model()


# ---------------------------------------------------------------------------
# Internal helpers (login-only, not shared with password-reset service)
# ---------------------------------------------------------------------------

def _authenticate_role(request, username, password, required_role, extra_check=None):
    user = authenticate(request, username=username, password=password)
    if user is not None and user.role == required_role:
        if extra_check is None or extra_check(user):
            return user
    time.sleep(0.1)
    return None


# ---------------------------------------------------------------------------
# Login views
# ---------------------------------------------------------------------------

def student_login(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = _authenticate_role(request, username, password, "STUDENT")
        if user:
            login(request, user)
            return redirect("dashboard")
        messages.error(request, "Kredenciale te pasakta ose perdoruesi nuk eshte student")
    return render(request, "student_login.html")


def admin_login(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = _authenticate_role(request, username, password, "ADMIN")
        if user:
            login(request, user)
            return redirect("dashboard")
        messages.error(request, "Kredenciale te pasakta per administratorin")
    return render(request, "admin_login.html")


def controller_login(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        employee_id = request.POST.get("employee_id", "").strip()
        user = _authenticate_role(
            request,
            username,
            password,
            "CONTROLLER",
            extra_check=lambda u: bool(u.employee_id) and u.employee_id == employee_id,
        )
        if user:
            login(request, user)
            return redirect("dashboard")
        messages.error(request, "Kredenciale te pasakta per kontrolluesin")
    return render(request, "controller_login.html")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_student(request):
    if request.method == "POST":
        form = StudentRegisterForm(request.POST, request.FILES)
        if form.is_valid():
            username = form.cleaned_data["username"]
            password = form.cleaned_data["password"]
            barcode = form.cleaned_data["barcode"]
            photo = form.cleaned_data["photo"]

            try:
                profile = StudentProfile.objects.select_for_update().get(barcode=barcode)
            except StudentProfile.DoesNotExist:
                logger.warning("U përpoq të regjistrohesha pa barkod: %s", barcode)
                messages.error(request, "Barcode i pavlefshëm.")
                return render(request, "register.html", {"form": form})

            try:
                with transaction.atomic():
                    if profile.user is not None:
                        messages.error(request, "Ky barcode është tashmë i regjistruar.")
                        return render(request, "register.html", {"form": form})

                    user = User.objects.create_user(
                        username=username,
                        password=password,
                        role="STUDENT",
                    )

                    img = Image.open(photo)
                    img.verify()
                    photo.seek(0)
                    img = Image.open(photo)
                    img = img.convert("RGB")
                    img = img.resize((150, 150), Image.LANCZOS)

                    buffer = BytesIO()
                    img.save(buffer, format="JPEG")
                    buffer.seek(0)

                    file_name = f"{barcode}_photo.jpg"
                    profile.user = user
                    profile.photo.save(file_name, ContentFile(buffer.read()), save=False)
                    profile.save()

            except Exception as e:
                logger.error("Registration failed for barcode %s: %s", barcode, str(e))
                messages.error(request, "Ndodhi një gabim gjatë regjistrimit. Provo përsëri.")
                return render(request, "register.html", {"form": form})

            messages.success(request, "Llogaria juaj u krijua me sukses. Tani mund te identifikoheni.")
            return redirect("student_login")
    else:
        form = StudentRegisterForm()
    return render(request, "register.html", {"form": form})


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

def logout_view(request):
    logout(request)
    return redirect("home")


# ---------------------------------------------------------------------------
# Password reset — thin views, delegate to auth_service
# ---------------------------------------------------------------------------

def reset_password_request(request):
    if request.method == "POST":
        form = PasswordResetRequestForm(request.POST)

        
        if form.is_valid():
            
            try:
                reset_token = auth_service.handle_reset_request(form, request)
                return redirect("reset_password_confirm", token=reset_token.token)

            except auth_service.RateLimitedError:
                messages.error(request, "Shumë përpjekje të dështuara. Provo përsëri pas 5 minutash.")

            except auth_service.VerificationError:
                messages.error(request, auth_service._GENERIC_ERROR)

    else:
        form = PasswordResetRequestForm()

    return render(request, "reset_password_request.html", {"form": form})


def reset_password_confirm(request, token):
    try:
        reset_token = PasswordResetToken.objects.select_related("user").get(token=token)
    except PasswordResetToken.DoesNotExist:
        messages.error(request, auth_service._GENERIC_ERROR)
        return redirect("reset_password_request")

    if request.method == "POST":
        form = PasswordResetConfirmForm(request.POST)

        if form.is_valid():
            try:
                auth_service.handle_reset_confirm(reset_token, form, request)
                messages.success(request, "Fjalëkalimi u ndryshua me sukses. Tani mund të identifikoheni.")
                return redirect("student_login")

            except auth_service.RateLimitedError:
                messages.error(request, "Shumë përpjekje të dështuara. Provo përsëri pas 5 minutash.")
                return redirect("reset_password_request")

            except auth_service.TokenInvalidError:
                messages.error(request, auth_service._GENERIC_ERROR)
                return redirect("reset_password_request")

        else:
            # Form invalid — log a failed RESET attempt
            auth_service.log_attempt(
                reset_token.user,
                reset_token.user.username,
                "",
                reset_token,
                "FAILED",
                "RESET",
                request,
            )

    else:
        # GET — validate token before showing the form
        resolved_user = reset_token.user
        if auth_service.count_recent_failed_attempts(resolved_user.id) >= auth_service._MAX_ATTEMPTS:
            auth_service.log_attempt(
                resolved_user, resolved_user.username, "", reset_token, "BLOCKED", "RESET", request
            )
            messages.error(request, "Shumë përpjekje të dështuara. Provo përsëri pas 5 minutash.")
            return redirect("reset_password_request")

        if not reset_token.is_valid():
            auth_service.log_attempt(
                resolved_user, resolved_user.username, "", reset_token, "FAILED", "RESET", request
            )
            messages.error(request, auth_service._GENERIC_ERROR)
            return redirect("reset_password_request")

        form = PasswordResetConfirmForm()

    return render(request, "reset_password_confirm.html", {"form": form, "token": token})
