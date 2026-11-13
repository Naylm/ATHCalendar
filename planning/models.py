import uuid

from django.db import models


class SiteSettings(models.Model):
    site_name = models.CharField(max_length=120, default="Calendrier organisateurs ATH")
    active_period = models.ForeignKey("Period", null=True, blank=True, on_delete=models.SET_NULL)
    default_min_organizers = models.PositiveSmallIntegerField(default=2)
    default_capacity = models.PositiveIntegerField(null=True, blank=True)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Period(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creation_key = models.UUIDField(unique=True)
    label = models.CharField(max_length=120)
    start_date = models.DateField()
    end_date = models.DateField()
    archived_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start_date__lte=models.F("end_date")),
                name="period_start_lte_end",
            )
        ]
        indexes = [models.Index(fields=["start_date", "end_date"])]
        ordering = ["start_date", "created_at"]

    @property
    def is_archived(self):
        return self.archived_at is not None


class GameType(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=7, default="#6b7c4a")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    @property
    def color_class(self):
        palette = ("#6b7c4a", "#b08a3e", "#526777", "#60766b", "#8a5b55")
        try:
            return f"type-c{palette.index(self.color.lower()) + 1}"
        except (ValueError, AttributeError):
            return "type-c1"


class Event(models.Model):
    class Availability(models.TextChoices):
        AVAILABLE = "available", "Ouverte"
        UNAVAILABLE = "unavailable", "Indisponible"
        FULL = "full", "Complète"

    class Origin(models.TextChoices):
        GENERATED = "generated"
        MANUAL = "manual"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled"
        CANCELLED = "cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    period = models.ForeignKey(Period, related_name="events", on_delete=models.CASCADE)
    date = models.DateField()
    game_type = models.ForeignKey(GameType, null=True, blank=True, on_delete=models.SET_NULL)
    type_label = models.CharField(max_length=120, blank=True)
    title = models.CharField(max_length=120, blank=True)
    public_note = models.TextField(max_length=1000, blank=True)
    min_organizers = models.PositiveSmallIntegerField(default=2)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    origin = models.CharField(max_length=10, choices=Origin, default=Origin.MANUAL)
    status = models.CharField(max_length=10, choices=Status, default=Status.SCHEDULED)
    registrations_open = models.BooleanField(default=True)
    availability = models.CharField(
        max_length=12, choices=Availability, default=Availability.AVAILABLE
    )
    is_hidden = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["period", "date"], name="event_unique_period_date"),
            models.CheckConstraint(
                condition=models.Q(availability__in=["available", "unavailable", "full"]),
                name="event_availability_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(min_organizers__gte=1), name="event_min_gte_1"
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__isnull=True)
                | models.Q(capacity__gte=models.F("min_organizers")),
                name="event_capacity_gte_min",
            ),
        ]
        indexes = [models.Index(fields=["period", "date", "status"])]
        ordering = ["date"]


class Organizer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=40)
    normalized_key = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    merged_into = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Registration(models.Model):
    class State(models.TextChoices):
        ACTIVE = "active"
        WITHDRAWN = "withdrawn"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, related_name="registrations", on_delete=models.CASCADE)
    organizer = models.ForeignKey(Organizer, related_name="registrations", on_delete=models.CASCADE)
    organizer_name = models.CharField(max_length=40)
    state = models.CharField(max_length=10, choices=State, default=State.ACTIVE)
    note = models.CharField(max_length=500, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "organizer"], name="registration_unique_event_organizer"
            )
        ]
        indexes = [
            models.Index(fields=["event", "state"]),
            models.Index(fields=["organizer", "state"]),
        ]


class AuditEntry(models.Model):
    class ActorType(models.TextChoices):
        BUREAU = "bureau"
        PUBLIC = "public"
        SYSTEM = "system"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor_type = models.CharField(max_length=10, choices=ActorType)
    actor_label = models.CharField(max_length=80, blank=True)
    action = models.CharField(max_length=60)
    target_type = models.CharField(max_length=40)
    target_id = models.CharField(max_length=64, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)


class RateLimitBucket(models.Model):
    category = models.CharField(max_length=40)
    key = models.CharField(max_length=128)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["category", "key", "window_start"],
                name="ratelimit_unique_bucket",
            )
        ]
        indexes = [models.Index(fields=["category", "key", "window_start"])]
