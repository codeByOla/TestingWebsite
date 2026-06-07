from django.contrib import admin
from django.contrib.auth import get_user_model

from .models import (
    University,
    StudentProfile,
    Subscription,
    VerificationLog,
    TemporaryQRToken,
    VerificationAnalytics,
)

User = get_user_model()


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display  = [
        'first_name', 'last_name', 'barcode', 'university',
        'is_revoked', 'revoked_at', 'revoked_by',
    ]
    list_filter   = ['is_revoked', 'university']
    search_fields = ['first_name', 'last_name', 'barcode']

    fieldsets = (
        (None, {
            'fields': ('user', 'barcode', 'first_name', 'last_name', 'photo', 'university'),
        }),
        ('Revocation', {
            'fields':  ('is_revoked', 'revoked_at', 'revocation_reason', 'revoked_by'),
            'classes': ('collapse',),
        }),
    )
    # revoked_at and revoked_by are set programmatically via revocation_service
    readonly_fields = ['revoked_at', 'revoked_by']

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user":
            kwargs["queryset"] = User.objects.filter(role="STUDENT")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(TemporaryQRToken)
class TemporaryQRTokenAdmin(admin.ModelAdmin):
    list_display  = ['uuid_token', 'subscription', 'created_at', 'expires_at', 'is_used']
    list_filter   = ['is_used']
    readonly_fields = ['uuid_token', 'created_at']
    search_fields = [
        'subscription__student__first_name',
        'subscription__student__last_name',
    ]
    ordering = ['-created_at']


@admin.register(VerificationAnalytics)
class VerificationAnalyticsAdmin(admin.ModelAdmin):
    list_display = [
        'date', 'hour', 'university',
        'valid_count', 'invalid_count', 'expired_count', 'revoked_count',
        'already_used_valid_count',
    ]
    list_filter = ['date', 'university']
    ordering    = ['-date', 'hour']


admin.site.register(University)
admin.site.register(Subscription)
admin.site.register(VerificationLog)