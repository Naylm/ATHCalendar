from datetime import timedelta

from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.utils import timezone

from planning.models import RateLimitBucket


class Command(BaseCommand):
    help = "Nettoie uniquement les données techniques expirées (sessions, fenêtres de limitation)."

    def handle(self, *args, **options):
        now = timezone.now()
        sessions_deleted, _ = Session.objects.filter(expire_date__lt=now).delete()
        buckets_deleted, _ = RateLimitBucket.objects.filter(
            window_start__lt=now - timedelta(hours=24)
        ).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Sessions expirées supprimées : {sessions_deleted} ; "
                f"fenêtres de limitation purgées : {buckets_deleted}."
            )
        )
