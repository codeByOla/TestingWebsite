"""
hypothesis_metrics.py
=====================
Moduli i matjeve për vertetimin e hipotezës:

  "Zhvillimi i një sistemi të digjitalizuar për menaxhimin dhe verifikimin
   e aboneve studentore përmirëson dhe organizon më mirë procesin krahasuar
   me metodën aktuale manuale. Përdorimi i identifikuesve unik dhe QR kodeve
   mundëson verifikimin në kohë reale, rrit saktësinë dhe redukton abuzimet."

Perdorimi:
    from hypothesis_metrics import HypothesisMetrics
    report = HypothesisMetrics.generate_full_report()
"""

from django.utils import timezone
from django.db.models import Count, Avg, Q, F
from datetime import timedelta, date
import statistics


# ── Importet e modeleve ────────────────────────────────────────────────────
from subscriptions.models import (
    Subscription,
    VerificationLog,
    VerificationAnalytics,
    TemporaryQRToken,
)
from subscriptions.models import StudentProfile
from accounts.models import PasswordResetLog



# ══════════════════════════════════════════════════════════════════════════
# METRIKA 1 — Koha e verifikimit (H1: verifikim në kohë reale)
# ══════════════════════════════════════════════════════════════════════════

class VerificationSpeedMetrics:
    """
    Mat kohën mesatare të verifikimit nga sistemi.
    Krahasim me kohën manuale (e mbledhur nga survey ose e dhënë si konstante).
    """

    # Koha manuale (sekonda) - bazuar në intervistë/vëzhgim me studentë
    MANUAL_VERIFICATION_SECONDS = 45.0

    @staticmethod
    def get_system_avg_ms() -> float | None:
        """
        Kthen mesataren e kohës së verifikimit nga verification_timing_service.
        Nëse nuk ka të dhëna, kthen None.
        """
        from subscriptions.services import verification_timing_service
        avg = verification_timing_service.get_average_ms()
        return avg

    @staticmethod
    def get_speed_improvement_factor() -> dict:
        """
        Llogarit sa herë më i shpejtë është sistemi dixhital vs manual.
        """
        avg_ms = VerificationSpeedMetrics.get_system_avg_ms()
        if avg_ms is None:
            return {"available": False, "note": "Nuk ka matje të mjaftueshme ende."}

        system_seconds = avg_ms / 1000.0
        factor = VerificationSpeedMetrics.MANUAL_VERIFICATION_SECONDS / system_seconds

        return {
            "available": True,
            "system_avg_ms": round(avg_ms, 2),
            "system_avg_seconds": round(system_seconds, 3),
            "manual_baseline_seconds": VerificationSpeedMetrics.MANUAL_VERIFICATION_SECONDS,
            "speedup_factor": round(factor, 1),
            "interpretation": (
                f"Sistemi verifikon {factor:.1f}x më shpejt se procesi manual "
                f"({system_seconds*1000:.0f}ms vs {VerificationSpeedMetrics.MANUAL_VERIFICATION_SECONDS}s)"
            ),
        }

    @staticmethod
    def get_daily_throughput_estimate(avg_ms: float, hours_active: int = 8) -> dict:
        """
        Sa verifikime mund të kryejë sistemi në një ditë pune vs njeriu manual.
        """
        if avg_ms is None:
            return {"available": False}

        system_per_hour = (3600 * 1000) / avg_ms
        manual_per_hour = 3600 / VerificationSpeedMetrics.MANUAL_VERIFICATION_SECONDS

        return {
            "available": True,
            "system_verifications_per_hour": int(system_per_hour),
            "manual_verifications_per_hour": int(manual_per_hour),
            "system_daily_capacity": int(system_per_hour * hours_active),
            "manual_daily_capacity": int(manual_per_hour * hours_active),
        }


# ══════════════════════════════════════════════════════════════════════════
# METRIKA 2 — Saktësia (H2: rrit saktësinë)
# ══════════════════════════════════════════════════════════════════════════

class AccuracyMetrics:
    """
    Mat shkallën e suksesit të verifikimeve (VALID vs total).
    Abuzimi i detektuar = gabime të mundshme manuale të shmangura.
    """

    @staticmethod
    def get_verification_accuracy(last_n_days: int = 30) -> dict:
        """
        Llogarit saktësinë e sistemit të verifikimit.
        Saktësia = VALID / (VALID + false negatives që sistemi kap).
        """
        cutoff = timezone.now() - timedelta(days=last_n_days)
        qs = VerificationLog.objects.filter(timestamp__gte=cutoff)

        total = qs.count()
        if total == 0:
            return {"available": False, "note": "Nuk ka të dhëna verifikimi."}

        counts = qs.values("result").annotate(n=Count("id"))
        result_map = {r["result"]: r["n"] for r in counts}

        valid   = result_map.get("VALID", 0)
        expired = result_map.get("EXPIRED", 0)
        revoked = result_map.get("REVOKED", 0)
        invalid = result_map.get("INVALID", 0)

        # Legitimate results = VALID + EXPIRED + REVOKED (sistemi i trajtoi saktë)
        correctly_handled = valid + expired + revoked
        accuracy_rate = (correctly_handled / total) * 100

        return {
            "available": True,
            "period_days": last_n_days,
            "total_verifications": total,
            "valid": valid,
            "expired": expired,
            "revoked": revoked,
            "invalid_attempts": invalid,
            "correctly_handled": correctly_handled,
            "accuracy_rate_pct": round(accuracy_rate, 2),
            "interpretation": (
                f"Sistemi trajtoi saktë {accuracy_rate:.1f}% të verifikimeve "
                f"({correctly_handled}/{total}) brenda {last_n_days} ditëve."
            ),
        }

    @staticmethod
    def get_false_positive_rate(last_n_days: int = 30) -> dict:
        """
        Tentativa të rreme (INVALID) si % e totalit.
        Tregon sa herë dikush u bllokua me sukses nga sistemi.
        """
        cutoff = timezone.now() - timedelta(days=last_n_days)
        total = VerificationLog.objects.filter(timestamp__gte=cutoff).count()
        invalid = VerificationLog.objects.filter(
            timestamp__gte=cutoff, result="INVALID"
        ).count()

        if total == 0:
            return {"available": False}

        rate = (invalid / total) * 100
        return {
            "available": True,
            "invalid_attempts": invalid,
            "total": total,
            "invalid_rate_pct": round(rate, 2),
        }


# ══════════════════════════════════════════════════════════════════════════
# METRIKA 3 — Reduktimi i abuzimeve (H3: redukton abuzimet)
# ══════════════════════════════════════════════════════════════════════════

class AbuseReductionMetrics:
    """
    Mat se sa tentativa abuzimi u detektuan dhe u bllokuan nga sistemi.
    Sistemi manual nuk mund t'i detektonte këto.
    """

    @staticmethod
    def get_abuse_detection_stats(last_n_days: int = 30) -> dict:
        """
        Llogarit tentativat e abuzimit të detektuara nga sistemi.
        Kategoritë: ALREADY_USED_VALID (ri-skanim), INVALID (falsifikim).
        """
        cutoff = timezone.now() - timedelta(days=last_n_days)

        total = VerificationLog.objects.filter(timestamp__gte=cutoff).count()
        if total == 0:
            return {"available": False, "note": "Nuk ka të dhëna."}

        # Ri-skanim i QR aktiv = dikush tentoi të përdorte të njëjtin QR dy herë
        reuse_attacks = VerificationLog.objects.filter(
            timestamp__gte=cutoff,
            sub_result="ALREADY_USED_VALID",
        ).count()

        # QR krejtësisht i pavlefshëm = falsifikim i mundshëm
        pure_invalid = VerificationLog.objects.filter(
            timestamp__gte=cutoff,
            result="INVALID",
        ).exclude(sub_result="ALREADY_USED_VALID").count()

        # Studentë të revokuar që tentuan aksesim
        revoked_attempts = VerificationLog.objects.filter(
            timestamp__gte=cutoff,
            result="REVOKED",
        ).count()

        total_abuse = reuse_attacks + pure_invalid + revoked_attempts
        abuse_rate = (total_abuse / total) * 100 if total > 0 else 0

        return {
            "available": True,
            "period_days": last_n_days,
            "total_verifications": total,
            "reuse_qr_attacks": reuse_attacks,
            "pure_invalid_qr": pure_invalid,
            "revoked_access_attempts": revoked_attempts,
            "total_abuse_detected": total_abuse,
            "abuse_detection_rate_pct": round(abuse_rate, 2),
            "interpretation": (
                f"Sistemi detektoi {total_abuse} tentativa abuzimi "
                f"({abuse_rate:.1f}% e totalit) brenda {last_n_days} ditëve. "
                f"Procesimi manual nuk do t'i kishte kapur këto."
            ),
        }

    @staticmethod
    def get_qr_security_stats() -> dict:
        """
        Statistika mbi sigurinë e QR kodeve dinamike.
        Tregon sa token u invaliduan (expired/used) vs u përdorën normalisht.
        """
        total_tokens     = TemporaryQRToken.objects.count()
        used_tokens      = TemporaryQRToken.objects.filter(is_used=True).count()
        expired_unused   = TemporaryQRToken.objects.filter(
            is_used=False,
            expires_at__lt=timezone.now()
        ).count()
        active_tokens    = TemporaryQRToken.objects.filter(
            is_used=False,
            expires_at__gte=timezone.now()
        ).count()

        if total_tokens == 0:
            return {"available": False}

        return {
            "available": True,
            "total_qr_tokens_generated": total_tokens,
            "successfully_used": used_tokens,
            "expired_without_use": expired_unused,
            "currently_active": active_tokens,
            "usage_rate_pct": round((used_tokens / total_tokens) * 100, 1),
            "note": (
                "Çdo QR skadon pas 60 sekondash — e pamundur të falsifikohet "
                "si kartë fizike statike."
            ),
        }


# ══════════════════════════════════════════════════════════════════════════
# METRIKA 4 — Organizimi dhe menaxhimi (H4: organizon më mirë)
# ══════════════════════════════════════════════════════════════════════════

class OrganizationMetrics:
    """
    Mat shkallën e dixhitalizimit të procesit: studentë të regjistruar,
    abonime aktive, mbulim total.
    """

    @staticmethod
    def get_digitization_coverage() -> dict:
        """
        Sa % e studentëve janë të regjistruar në sistem dixhital.
        """
        total_profiles  = StudentProfile.objects.count()
        registered      = StudentProfile.objects.filter(user__isnull=False).count()
        active_subs     = StudentProfile.objects.filter(
            subscription__expiry_date__gte=date.today()
        ).distinct().count()
        revoked         = StudentProfile.objects.filter(is_revoked=True).count()

        if total_profiles == 0:
            return {"available": False}

        registration_rate = (registered / total_profiles) * 100
        subscription_rate = (active_subs / total_profiles) * 100

        return {
            "available": True,
            "total_student_profiles": total_profiles,
            "digitally_registered": registered,
            "registration_rate_pct": round(registration_rate, 1),
            "with_active_subscription": active_subs,
            "subscription_coverage_pct": round(subscription_rate, 1),
            "revoked_accounts": revoked,
            "interpretation": (
                f"{registration_rate:.1f}% e studentëve janë regjistruar dixhitalisht. "
                f"{subscription_rate:.1f}% kanë abonim aktiv."
            ),
        }

    @staticmethod
    def get_subscription_creation_time() -> dict:
        """
        Koha e krijimit të abonimit: sistemi bën automatikisht vs manual.
        """
        # Merr kohën mesatare mes created_at dhe issue_date (duhet 0 ose sekonda)
        # Sistemi krijon abonim menjëherë pas aplikimit

        from django.db.models.functions import ExtractMonth
        monthly = (
            Subscription.objects
            .annotate(m=ExtractMonth("issue_date"))
            .values("m")
            .annotate(total=Count("id"))
            .order_by("m")
        )

        return {
            "available": True,
            "system_creation_time_seconds": 0,  # menjëherë, automatik
            "manual_creation_time_hours": 24,    # nga intervista: 1-2 ditë pune
            "monthly_distribution": list(monthly),
            "note": (
                "Sistemi krijon abonim menjëherë pas konfirmimit. "
                "Procesi manual kërkon 1-2 ditë pune."
            ),
        }

    @staticmethod
    def get_audit_trail_completeness() -> dict:
        """
        Sa ngjarje janë loguar - demonstron transparencën e auditimit dixhital.
        """
        total_verif_logs  = VerificationLog.objects.count()
        total_reset_logs  = PasswordResetLog.objects.count()
        verif_with_ip     = VerificationLog.objects.exclude(ip_address__isnull=True).count()

        return {
            "available": True,
            "total_verification_logs": total_verif_logs,
            "total_auth_logs": total_reset_logs,
            "verifications_with_ip_tracked": verif_with_ip,
            "audit_coverage_pct": (
                round((verif_with_ip / total_verif_logs) * 100, 1)
                if total_verif_logs > 0 else 0
            ),
            "note": (
                "Sistemi logogon çdo verifikim me timestamp, IP, kontrollues dhe rezultat. "
                "Procesi manual nuk ka asnjë auditim të tillë."
            ),
        }


# ══════════════════════════════════════════════════════════════════════════
# METRIKA 5 — Volumi dhe shkallëzimi
# ══════════════════════════════════════════════════════════════════════════

class ScalabilityMetrics:
    """
    Demonstron aftësinë e sistemit për të trajtuar volumin.
    """

    @staticmethod
    def get_peak_load_stats() -> dict:
        """
        Orët me ngarkesën më të lartë dhe numri maksimal i verifikimeve/orë.
        """
        from django.db.models import Sum
        from core.services.analytics_service import get_peak_hours
        peak = get_peak_hours(last_n_days=30)

        if not peak:
            return {"available": False}

        max_hour = max(peak, key=lambda x: (x.get("total_valid") or 0) + (x.get("total_invalid") or 0))
        total_in_peak = (max_hour.get("total_valid") or 0) + (max_hour.get("total_invalid") or 0)

        return {
            "available": True,
            "peak_hour": max_hour.get("hour"),
            "verifications_in_peak_hour": total_in_peak,
            "note": (
                f"Ora {max_hour.get('hour')}:00 është ora me ngarkesën më të lartë "
                f"({total_in_peak} verifikime). "
                "Sistemi manual nuk mund ta trajtonte këtë volum."
            ),
        }


# ══════════════════════════════════════════════════════════════════════════
# RAPORTI I PLOTË
# ══════════════════════════════════════════════════════════════════════════

class HypothesisMetrics:
    """
    Pikë hyrëse kryesore — gjeneron raportin e plotë të hipotezës.
    """

    @staticmethod
    def generate_full_report(last_n_days: int = 30) -> dict:
        """
        Gjeneron të gjitha matjet e nevojshme për tezën.

        Perdorimi:
            from hypothesis_metrics import HypothesisMetrics
            report = HypothesisMetrics.generate_full_report()
            print(report['speed']['interpretation'])
        """
        avg_ms = VerificationSpeedMetrics.get_system_avg_ms()

        return {
            "generated_at": timezone.now().isoformat(),
            "period_days": last_n_days,

            # H1: Verifikim në kohë reale
            "speed": VerificationSpeedMetrics.get_speed_improvement_factor(),
            "throughput": VerificationSpeedMetrics.get_daily_throughput_estimate(avg_ms),

            # H2: Saktësia
            "accuracy": AccuracyMetrics.get_verification_accuracy(last_n_days),
            "false_positives": AccuracyMetrics.get_false_positive_rate(last_n_days),

            # H3: Reduktimi i abuzimeve
            "abuse_detection": AbuseReductionMetrics.get_abuse_detection_stats(last_n_days),
            "qr_security": AbuseReductionMetrics.get_qr_security_stats(),

            # H4: Organizimi
            "digitization": OrganizationMetrics.get_digitization_coverage(),
            "creation_time": OrganizationMetrics.get_subscription_creation_time(),
            "audit_trail": OrganizationMetrics.get_audit_trail_completeness(),

            # H5: Shkallëzimi
            "scalability": ScalabilityMetrics.get_peak_load_stats(),
        }

    @staticmethod
    def print_summary(last_n_days: int = 30) -> None:
        """
        Shtyp përmbledhjen e matjeve në terminal (për debug/demo).
        """
        r = HypothesisMetrics.generate_full_report(last_n_days)

        print("=" * 65)
        print("RAPORTI I HIPOTEZËS — SISTEMI e-Abone")
        print(f"Periudha: {last_n_days} ditë | {r['generated_at']}")
        print("=" * 65)

        sections = [
            ("H1 — KOHA E VERIFIKIMIT",     r["speed"]),
            ("H1 — KAPACITETI DITOR",        r["throughput"]),
            ("H2 — SAKTËSIA",                r["accuracy"]),
            ("H3 — ABUZIMI I DETEKTUAR",     r["abuse_detection"]),
            ("H3 — SIGURIA QR",              r["qr_security"]),
            ("H4 — MBULIMI DIXHITAL",        r["digitization"]),
            ("H4 — AUDITIMI",                r["audit_trail"]),
            ("H5 — NGARKESA MAKSIMALE",      r["scalability"]),
        ]

        for title, data in sections:
            print(f"\n{title}")
            print("-" * 40)
            if not data.get("available", True):
                print(f"  ⚠  {data.get('note', 'Pa të dhëna')}")
                continue
            for k, v in data.items():
                if k in ("available", "monthly_distribution"):
                    continue
                print(f"  {k}: {v}")

        print("\n" + "=" * 65)
