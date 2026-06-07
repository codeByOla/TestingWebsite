from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
import logging

from bus_pass_system.subscriptions import models

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Fshin TemporaryQRToken qe jane te skaduar ose shume te vjeter."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Fshin tokenat me te vjeter se N dite"
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Vetem shfaq pa fshirje"
        )

    def handle(self, *args, **options):
        from subscriptions.models import TemporaryQRToken

        now = timezone.now()
        cutoff = now - timedelta(days=options["days"])

        qs = TemporaryQRToken.objects.filter(
            created_at__lt=cutoff
        ).filter(
            # TOKENS QË NUK KANË MË VLERË
            models.Q(is_used=True) |
            models.Q(expires_at__lt=now)
        )

        count = qs.count()

        if options["dry_run"]:
            self.stdout.write(
                f"[dry-run] Do fshiheshin {count} tokens older than {options['days']} days or expired/used."
            )
            return

        deleted, _ = qs.delete()

        self.stdout.write(self.style.SUCCESS(
            f"U fshine {deleted} TemporaryQRToken records."
        ))

        logger.info("cleanup_qr_tokens: deleted %d records", deleted)