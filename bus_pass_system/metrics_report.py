"""
metrics_report.py
─────────────────
Script i pavarur për matjen e metrikave të hipotezës së e-Abone.
Lexon direkt nga DB përmes Django ORM — përfshirë kohët reale të operacioneve.

Ekzekutim:
    python metrics_report.py
    python metrics_report.py --days 7
    python metrics_report.py --json
    python metrics_report.py --save
    python metrics_report.py --days 30 --json --save
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

# ── Bootstrap Django ───────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bus_pass_system.settings")

import django
django.setup()

from django.utils import timezone
from django.db.models import Count, Avg, Min, Max

from subscriptions.models import (
    VerificationLog,
    Subscription,
    StudentProfile,
    PerformanceLog,
)
from accounts.models import PasswordResetLog


# ══════════════════════════════════════════════════════════════════════════
# Helper — statistika për një operacion nga PerformanceLog
# ══════════════════════════════════════════════════════════════════════════

def _perf_stats(operation: str, last_n_days: int) -> dict:
    cutoff = timezone.now() - timedelta(days=last_n_days)
    qs     = PerformanceLog.objects.filter(
        operation=operation,
        timestamp__gte=cutoff,
    )
    agg = qs.aggregate(
        avg_ms=Avg("duration_ms"),
        min_ms=Min("duration_ms"),
        max_ms=Max("duration_ms"),
        count=Count("id"),
    )
    if not agg["count"]:
        return {"available": False, "count": 0}

    return {
        "available": True,
        "count":     agg["count"],
        "avg_ms":    round(agg["avg_ms"], 2),
        "min_ms":    round(agg["min_ms"], 2),
        "max_ms":    round(agg["max_ms"], 2),
    }


# ══════════════════════════════════════════════════════════════════════════
# H1 — Verifikimi në kohë reale
# ══════════════════════════════════════════════════════════════════════════

def h1_verification(last_n_days: int) -> dict:
    cutoff = timezone.now() - timedelta(days=last_n_days)
    qs     = VerificationLog.objects.filter(timestamp__gte=cutoff)
    total  = qs.count()

    if total == 0:
        return {
            "available": False,
            "note":      "Nuk ka verifikime në periudhën e zgjedhur.",
        }

    by_result  = qs.values("result").annotate(count=Count("id")).order_by("result")
    result_map = {r["result"]: r["count"] for r in by_result}

    valid   = result_map.get("VALID",   0)
    expired = result_map.get("EXPIRED", 0)
    invalid = result_map.get("INVALID", 0)
    revoked = result_map.get("REVOKED", 0)

    valid_pct = round(valid / total * 100, 1) if total else 0

    # Kohët reale nga PerformanceLog
    perf = _perf_stats("verification", last_n_days)

    return {
        "available":      True,
        "period_days":    last_n_days,
        "total_scans":    total,
        "valid":          valid,
        "expired":        expired,
        "invalid":        invalid,
        "revoked":        revoked,
        "valid_rate_pct": valid_pct,
        "perf":           perf,
    }


# ══════════════════════════════════════════════════════════════════════════
# H2 — Automatizimi + Shkalla e Automatizimit
# ══════════════════════════════════════════════════════════════════════════

def h2_automation(last_n_days: int) -> dict:
    cutoff_date = timezone.now().date() - timedelta(days=last_n_days)

    total_students        = StudentProfile.objects.count()
    students_imported_csv = StudentProfile.objects.filter(user__isnull=True).count()
    students_self_reg     = StudentProfile.objects.filter(user__isnull=False).count()

    subscriptions_created = Subscription.objects.filter(
        issue_date__gte=cutoff_date
    ).count()

    qr_auto_generated = Subscription.objects.filter(
        issue_date__gte=cutoff_date
    ).exclude(qr_image="").exclude(qr_image__isnull=True).count()

    # Shkalla e automatizimit
    if subscriptions_created > 0:
        automation_rate = round(qr_auto_generated / subscriptions_created * 100, 1)
    else:
        automation_rate = None

    if automation_rate == 100.0:
        automation_narrative = (
            "100% e abonimeve të aprovuara u krijuan automatikisht "
            "dhe u pajisën me QR pa asnjë ndërhyrje manuale."
        )
    elif automation_rate is not None:
        automation_narrative = (
            f"{automation_rate}% e abonimeve u pajisën me QR automatikisht."
        )
    else:
        automation_narrative = "Nuk ka abonime në periudhën e zgjedhur."

    # Kohët reale nga PerformanceLog
    perf_sub    = _perf_stats("subscription_create",   last_n_days)
    perf_qr     = _perf_stats("qr_generate",           last_n_days)
    perf_csv    = _perf_stats("csv_import",            last_n_days)
    perf_manual = _perf_stats("manual_student_create", last_n_days)

    return {
        "available":              True,
        "period_days":            last_n_days,
        "total_students":         total_students,
        "students_imported_csv":  students_imported_csv,
        "students_self_register": students_self_reg,
        "subscriptions_created":  subscriptions_created,
        "qr_auto_generated":      qr_auto_generated,
        "automation_rate_pct":    automation_rate,
        "automation_narrative":   automation_narrative,
        "perf_subscription":      perf_sub,
        "perf_qr":                perf_qr,
        "perf_csv":               perf_csv,
        "perf_manual":            perf_manual,
    }


# ══════════════════════════════════════════════════════════════════════════
# H3 — Anti-Abuse
# ══════════════════════════════════════════════════════════════════════════

def h3_abuse(last_n_days: int) -> dict:
    cutoff = timezone.now() - timedelta(days=last_n_days)
    total  = VerificationLog.objects.filter(timestamp__gte=cutoff).count()

    if total == 0:
        return {
            "available":   False,
            "period_days": last_n_days,
            "note":        "Nuk ka të dhëna verifikimi.",
        }

    replay_attempts = VerificationLog.objects.filter(
        timestamp__gte=cutoff,
        sub_result="ALREADY_USED_VALID",
    ).count()

    invalid_qr = VerificationLog.objects.filter(
        timestamp__gte=cutoff,
        result="INVALID",
    ).exclude(sub_result="ALREADY_USED_VALID").count()

    revoked_attempts = VerificationLog.objects.filter(
        timestamp__gte=cutoff,
        result="REVOKED",
    ).count()

    total_detected = replay_attempts + invalid_qr + revoked_attempts
    abuse_rate     = round(total_detected / total * 100, 2) if total else 0

    top_ips = list(
        VerificationLog.objects.filter(
            timestamp__gte=cutoff,
            result__in=("INVALID", "REVOKED"),
        )
        .exclude(ip_address__isnull=True)
        .exclude(ip_address="")
        .values("ip_address")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    top_controllers_invalid = list(
        VerificationLog.objects.filter(
            timestamp__gte=cutoff,
            result="INVALID",
        )
        .values("controller__username")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    return {
        "available":               True,
        "period_days":             last_n_days,
        "total_verifications":     total,
        "replay_attempts":         replay_attempts,
        "invalid_qr_tokens":       invalid_qr,
        "revoked_attempts":        revoked_attempts,
        "total_detected":          total_detected,
        "abuse_rate_pct":          abuse_rate,
        "top_suspicious_ips":      top_ips,
        "top_controllers_invalid": top_controllers_invalid,
    }


# ══════════════════════════════════════════════════════════════════════════
# H4 — Audit Trail
# ══════════════════════════════════════════════════════════════════════════

def h4_audit() -> dict:
    total_verif = VerificationLog.objects.count()
    total_auth  = PasswordResetLog.objects.count()

    by_result = list(
        VerificationLog.objects
        .values("result")
        .annotate(count=Count("id"))
        .order_by("result")
    )

    by_sub = list(
        VerificationLog.objects
        .values("sub_result")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    controllers = list(
        VerificationLog.objects
        .values("controller__username")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    return {
        "available":               True,
        "total_verification_logs": total_verif,
        "total_auth_logs":         total_auth,
        "result_breakdown":        by_result,
        "sub_result_breakdown":    by_sub,
        "active_controllers":      controllers,
    }


# ══════════════════════════════════════════════════════════════════════════
# Gjenerimi i raportit
# ══════════════════════════════════════════════════════════════════════════

def generate(last_n_days: int = 30) -> dict:
    return {
        "generated_at": timezone.now().isoformat(),
        "period_days":  last_n_days,
        "system":       "e-Abone Bus Pass System",
        "h1_verification_activity": h1_verification(last_n_days),
        "h2_automation":            h2_automation(last_n_days),
        "h3_abuse_detection":       h3_abuse(last_n_days),
        "h4_audit_trail":           h4_audit(),
    }


# ══════════════════════════════════════════════════════════════════════════
# Printer
# ══════════════════════════════════════════════════════════════════════════

def _sep(char="═", n=62):
    print(char * n)

def _line(label: str, value, width: int = 38):
    print(f"  {label:<{width}}: {value}")

def _perf_line(label: str, perf: dict):
    """Shfaq një rresht timing nëse ka të dhëna."""
    if perf.get("available"):
        print(
            f"  {'  ' + label:<38}: "
            f"avg {perf['avg_ms']} ms  |  "
            f"min {perf['min_ms']} ms  |  "
            f"max {perf['max_ms']} ms  |  "
            f"{perf['count']} matje"
        )
    else:
        print(f"  {'  ' + label:<38}: — (nuk ka matje ende)")

def print_report(report: dict):
    _sep()
    print("  e-Abone — RAPORT METRIKASH")
    _sep()
    _line("Gjeneruar",  report["generated_at"])
    _line("Periudha",   f"{report['period_days']} ditët e fundit")
    _line("Sistemi",    report["system"])
    _sep()

    # ── H1 ────────────────────────────────────────────────────────────────
    print()
    print("  H1 — VERIFIKIMI NË KOHË REALE")
    print("  " + "─" * 50)
    h1 = report["h1_verification_activity"]
    if h1["available"]:
        _line("Total skanime",            h1["total_scans"])
        _line("Të vlefshme  (VALID)",     f"{h1['valid']}  →  {h1['valid_rate_pct']}% e totalit")
        _line("Të skaduara  (EXPIRED)",   h1["expired"])
        _line("Të pavlefshme (INVALID)",  h1["invalid"])
        _line("Të revookuara (REVOKED)",  h1["revoked"])
        print()
        print("  Kohët e verifikimit (nga DB):")
        _perf_line("Verifikim QR", h1["perf"])
    else:
        print(f"  ⚠  {h1['note']}")

    # ── H2 ────────────────────────────────────────────────────────────────
    print()
    print("  H2 — AUTOMATIZIMI I PROCESIT")
    print("  " + "─" * 50)
    h2 = report["h2_automation"]

    _line("Total studentë",          h2["total_students"])
    _line("Importuar me CSV",        h2["students_imported_csv"])
    _line("Të vetë-regjistruar",     h2["students_self_register"])
    print()
    _line("Abonime të krijuara",     h2["subscriptions_created"])
    _line("QR të gjeneruara auto",   h2["qr_auto_generated"])
    print()

    rate = h2["automation_rate_pct"]
    if rate is not None:
        _line("★ Shkalla e automatizimit", f"{rate}%")
    print(f"\n  ✔  {h2['automation_narrative']}")

    print()
    print("  Kohët e operacioneve (nga DB):")
    _perf_line("Krijimi i abonimit",          h2["perf_subscription"])
    _perf_line("Gjenerimi i QR",              h2["perf_qr"])
    _perf_line("Importi CSV",                 h2["perf_csv"])
    _perf_line("Shtimi manual i studentit",   h2["perf_manual"])

    # ── H3 ────────────────────────────────────────────────────────────────
    print()
    print("  H3 — DETEKTIMI I ABUZIMEVE")
    print("  " + "─" * 50)
    h3 = report["h3_abuse_detection"]
    if h3["available"]:
        _line("Total verifikime",               h3["total_verifications"])
        _line("Replay attacks",                 h3["replay_attempts"])
        _line("QR të panjohura (falsifikim)",   h3["invalid_qr_tokens"])
        _line("Tentativa profil revokuar",      h3["revoked_attempts"])
        _line("Total abuzime të detektuara",    h3["total_detected"])
        _line("Shkalla e abuzimit",             f"{h3['abuse_rate_pct']}%")

        if h3["top_suspicious_ips"]:
            print()
            print("  Top IP të dyshimta:")
            for row in h3["top_suspicious_ips"]:
                print(f"    {row['ip_address']:<24} → {row['count']} tentativa")

        if h3["top_controllers_invalid"]:
            print()
            print("  Controllers me invalid të lartë:")
            for row in h3["top_controllers_invalid"]:
                print(f"    {row['controller__username']:<24} → {row['count']}")
    else:
        print(f"  ⚠  {h3['note']}")

    # ── H4 ────────────────────────────────────────────────────────────────
    print()
    print("  H4 — AUDIT TRAIL & GJURMUESHMËRIA")
    print("  " + "─" * 50)
    h4 = report["h4_audit_trail"]
    _line("Log verifikimesh (total)",  h4["total_verification_logs"])
    _line("Log autentikimi (total)",   h4["total_auth_logs"])

    if h4["result_breakdown"]:
        print()
        print("  Breakdown rezultatesh:")
        for row in h4["result_breakdown"]:
            print(f"    {row['result']:<25} → {row['count']}")

    if h4["sub_result_breakdown"]:
        print()
        print("  Breakdown sub-rezultatesh:")
        for row in h4["sub_result_breakdown"]:
            print(f"    {row['sub_result']:<25} → {row['count']}")

    if h4["active_controllers"]:
        print()
        print("  Controllers aktivë:")
        for row in h4["active_controllers"]:
            print(f"    {row['controller__username']:<24} → {row['count']} skanime")

    print()
    _sep()
    print("  FUND I RAPORTIT")
    _sep()
    print()


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="e-Abone — Raport metrikash nga DB"
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Numri i ditëve të analizuara (default: 30)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output në format JSON"
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Ruaj raportin si skedar (TXT ose JSON)"
    )
    args = parser.parse_args()

    report = generate(last_n_days=args.days)

    if args.json:
        output = json.dumps(report, indent=2, ensure_ascii=False, default=str)
        print(output)
        if args.save:
            ts       = timezone.now().strftime("%Y%m%d-%H%M%S")
            filename = f"e-abone-evidence-{ts}.json"
            Path(filename).write_text(output, encoding="utf-8")
            print(f"\n✔  Ruajtur: {filename}", file=sys.stderr)
    else:
        print_report(report)
        if args.save:
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                print_report(report)
            ts       = timezone.now().strftime("%Y%m%d-%H%M%S")
            filename = f"e-abone-evidence-{ts}.txt"
            Path(filename).write_text(buf.getvalue(), encoding="utf-8")
            print(f"✔  Ruajtur: {filename}")


if __name__ == "__main__":
    main()