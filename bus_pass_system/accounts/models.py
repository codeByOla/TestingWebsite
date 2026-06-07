import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    ROLE_CHOICES = (
        ('STUDENT', 'Student'),
        ('ADMIN', 'Admin'),
        ('CONTROLLER', 'Controller'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    employee_id = models.CharField(max_length=50, blank=True, null=True)

    def __str__(self):
        return f"{self.username} ({self.role})"


class PasswordResetToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reset_tokens')
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    def is_valid(self):
        if self.is_used:
            return False
        expiry = self.created_at + timezone.timedelta(minutes=15)
        return timezone.now() <= expiry

    def __str__(self):
        return f"Token for {self.user.username} ({'used' if self.is_used else 'active'})"


class PasswordResetLog(models.Model):
    RESULT_CHOICES = (
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
        ('BLOCKED', 'Blocked'),
    )
    STEP_CHOICES = (
        ('VERIFY', 'Verify'),
        ('RESET', 'Reset'),
    )

    user = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reset_logs'
    )
    username_input = models.CharField(max_length=150)
    barcode_input = models.CharField(max_length=100, blank=True)
    token = models.ForeignKey(
        PasswordResetToken, on_delete=models.SET_NULL,
        null=True, blank=True
    )
    result = models.CharField(max_length=10, choices=RESULT_CHOICES)
    step = models.CharField(max_length=10, choices=STEP_CHOICES)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.step}] {self.username_input} → {self.result} @ {self.timestamp}"