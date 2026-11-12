from datetime import UTC, datetime

from django.utils import timezone

from planning.db import write_transaction
from planning.models import RateLimitBucket


def _window_start(window_seconds):
    now = timezone.now()
    aligned = int(now.timestamp()) // window_seconds * window_seconds
    return datetime.fromtimestamp(aligned, tz=UTC)


def hit(*, category, key, limit, window_seconds):
    window_start = _window_start(window_seconds)
    with write_transaction():
        RateLimitBucket.objects.filter(
            category=category, key=key, window_start__lt=window_start
        ).delete()
        bucket, _ = RateLimitBucket.objects.get_or_create(
            category=category,
            key=key,
            window_start=window_start,
            defaults={"count": 0},
        )
        if bucket.count >= limit:
            return False
        bucket.count += 1
        bucket.save(update_fields=["count"])
        return True


def cleanup(older_than):
    return RateLimitBucket.objects.filter(window_start__lt=older_than).delete()
