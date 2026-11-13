from planning.models import AuditEntry

SAFE_FIELDS = {
    "Period": [
        "id",
        "label",
        "start_date",
        "end_date",
        "archived_at",
        "version",
        "revision",
    ],
    "Event": [
        "id",
        "period_id",
        "date",
        "game_type_id",
        "type_label",
        "title",
        "public_note",
        "min_organizers",
        "capacity",
        "origin",
        "status",
        "registrations_open",
        "availability",
        "is_hidden",
        "version",
    ],
    "Organizer": ["id", "name", "normalized_key", "is_active", "merged_into_id"],
    "SiteSettings": [
        "site_name",
        "default_min_organizers",
        "default_capacity",
        "active_period_id",
    ],
    "Registration": [
        "id",
        "event_id",
        "organizer_id",
        "organizer_name",
        "state",
        "note",
        "version",
    ],
    "GameType": ["id", "code", "name", "description", "color", "is_active"],
}


def snapshot(instance):
    if instance is None:
        return None
    fields = SAFE_FIELDS.get(type(instance).__name__, [])
    data = {}
    for name in fields:
        value = getattr(instance, name, None)
        data[name] = str(value) if value is not None else None
    return data


def log(*, actor, action, target, before=None, after=None, request_id=""):
    return AuditEntry.objects.create(
        request_id=request_id or getattr(actor, "request_id", "") or "",
        actor_type=actor.kind,
        actor_label=actor.label[:80],
        action=action,
        target_type=type(target).__name__,
        target_id=str(getattr(target, "pk", "")),
        before=before,
        after=after,
    )
