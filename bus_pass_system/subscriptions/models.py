import uuid
import calendar

from datetime import date

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


_barcode_validator = RegexValidator(
    regex=r'^\d{5,20}$',
    message='Barcode duhet të përmbajë vetëm numra (5-20 shifra).'
)


class University(models.Model):
    name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name


class StudentProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    barcode = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        validators=[_barcode_validator],
    )
    first_name  = models.CharField(max_length=100)
    last_name   = models.CharField(max_length=100)
    photo       = models.ImageField(upload_to='student_photos/', blank=True, null=True)
    university  = models.ForeignKey(University, on_delete=models.PROTECT)

    # Revocation lifecycle fields
    is_revoked         = models.BooleanField(default=False)
    revoked_at         = models.DateTimeField(null=True, blank=True)
    revocation_reason  = models.CharField(max_length=255, blank=True)
    revoked_by         = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='revoked_students',
    )

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class Subscription(models.Model):
    student          = models.ForeignKey(StudentProfile, on_delete=models.CASCADE)
    issue_date       = models.DateField(blank=True, null=True)
    expiry_date      = models.DateField(blank=True, null=True, db_index=True)
    
    # 🔥 fields për constraint
    year  = models.IntegerField(null=True, editable=False, db_index=True)
    month = models.IntegerField(null=True, editable=False, db_index=True)

    uuid_token       = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    qr_image         = models.ImageField(upload_to='qr_codes/', blank=True, null=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'year', 'month'],
                name='unique_subscription_per_student_per_month'
            )
        ]
        indexes = [
            models.Index(fields=['student', 'year', 'month']),
        ]


    def is_active(self):
        return self.expiry_date and self.expiry_date >= date.today()

    # NOTE: save() override kept minimal — date defaults only.
    # Duplicate-subscription guard and QR image generation are
    # handled by SubscriptionService.create_subscription() so that
    # business rules live in the service layer, not the ORM hook.
    def save(self, *args, **kwargs):
        if not self.issue_date:
            self.issue_date = date.today()

                    # 🔥 derivo month & year
        self.year = self.issue_date.year
        self.month = self.issue_date.month

        if not self.expiry_date:
            last_day = calendar.monthrange(
                self.issue_date.year,
                self.issue_date.month,
            )[1]
            self.expiry_date = date(
                self.issue_date.year,
                self.issue_date.month,
                last_day,
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student} - {self.issue_date}"

class VerificationLog(models.Model):
    RESULT_CHOICES = (
        ("VALID",   "Valid"),
        ("INVALID", "Invalid"),
        ("EXPIRED", "Expired"),
        ("REVOKED", "Revoked"),
    )

    SUB_RESULT_CHOICES = (
        ("VALID",                "I vlefshëm"),
        ("EXPIRED",              "I skaduar"),
        ("REVOKED",              "I pezulluar"),
        ("INVALID",              "I pavlefshëm"),
        ("ALREADY_USED_VALID",   "Ri-skanim (abone aktive)"),
    )

    controller    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    subscription  = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    scanned_token = models.UUIDField()
    result        = models.CharField(max_length=10, choices=RESULT_CHOICES)
    sub_result    = models.CharField(
        max_length=20,
        choices=SUB_RESULT_CHOICES,
        default="INVALID",
        db_index=True,
    )
    timestamp     = models.DateTimeField(default=timezone.now, db_index=True)
    ip_address    = models.GenericIPAddressField(null=True, blank=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['timestamp', 'result']),
            models.Index(fields=['controller', 'result', 'timestamp']),
            models.Index(fields=['ip_address', 'result', 'timestamp']),
            models.Index(fields=['ip_address', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.controller.username} - {self.result} - {self.timestamp}"


class TemporaryQRToken(models.Model):
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name='qr_tokens',
    )
    uuid_token   = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )
    hashed_token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    is_used    = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['uuid_token',    'is_used', 'expires_at']),
            models.Index(fields=['subscription',  'is_used', 'expires_at']),
            models.Index(fields=['hashed_token',  'is_used', 'expires_at']),
        ]

    def is_valid(self):
        return (not self.is_used) and (self.expires_at > timezone.now())

    def __str__(self):
        status = 'valid' if self.is_valid() else 'expired/used'
        return f'QRToken[{status}] sub={self.subscription_id} exp={self.expires_at}'


class VerificationAnalytics(models.Model):
    date          = models.DateField(db_index=True)
    hour          = models.IntegerField()
    valid_count   = models.IntegerField(default=0)
    invalid_count = models.IntegerField(default=0)
    expired_count = models.IntegerField(default=0)
    revoked_count = models.IntegerField(default=0)
    # Abuse sub-type counters — used by analytics_service.record()
    already_used_valid_count   = models.IntegerField(default=0)
    university    = models.ForeignKey(
        University,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
    )

    class Meta:
        unique_together = ('date', 'hour', 'university')
        indexes = [
            models.Index(fields=['date', 'hour']),
            models.Index(fields=['university', 'date']),
        ]

    @property
    def total(self):
        return (
            self.valid_count
            + self.invalid_count
            + self.expired_count
            + self.revoked_count
        )

    def __str__(self):
        uni = self.university.name if self.university else '—'
        return (
            f'{self.date} {self.hour:02d}h | {uni} | '
            f'V={self.valid_count} I={self.invalid_count} '
            f'E={self.expired_count} R={self.revoked_count}'
        )
    
class PerformanceLog(models.Model):
    OPERATION_CHOICES = (
        ("verification",          "Verifikim QR"),
        ("subscription_create",   "Krijim abonimi"),
        ("qr_generate",           "Gjenerim QR"),
        ("csv_import",            "Import CSV"),
        ("manual_student_create", "Shtim manual studenti"),
    )

    operation  = models.CharField(max_length=50, choices=OPERATION_CHOICES, db_index=True)
    duration_ms = models.FloatField()
    timestamp  = models.DateTimeField(default=timezone.now, db_index=True)
    extra      = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["operation", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.operation} → {self.duration_ms} ms @ {self.timestamp}"