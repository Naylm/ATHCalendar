from django.conf import settings as django_settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods

from . import selectors
from .permissions import (
    actor_for_request,
    limit_rate,
    organizer_from_body,
    parse_json_body,
    service_errors,
)
from .services import organizers as organizer_services
from .services import periods as period_services
from .services import registrations as registration_services
from .services.actors import Actor


def _period_payload(period):
    return {
        "id": str(period.pk),
        "label": period.label,
        "start_date": period.start_date.isoformat(),
        "end_date": period.end_date.isoformat(),
        "archived": period.is_archived,
        "version": period.version,
        "revision": period.revision,
    }


@require_GET
def periods_list(request):
    periods = [
        _period_payload(p)
        for p in period_services.Period.objects.order_by("start_date", "created_at")
    ]
    default = selectors.default_period()
    return JsonResponse(
        {
            "periods": periods,
            "default_period_id": str(default.pk) if default else None,
        }
    )


@service_errors
@require_GET
def period_planning(request, period_id):
    period = period_services.get_period(period_id)
    events = [
        selectors.event_payload(event)
        for event in selectors.period_events(period)
    ]
    return JsonResponse(
        {"period": _period_payload(period), "events": events}
    )


@service_errors
@require_GET
def period_revision(request, period_id):
    period = period_services.get_period(period_id)
    return JsonResponse({"revision": period.revision})


@require_GET
def organizers_list(request):
    return JsonResponse(
        {
            "organizers": [
                {"id": str(o.pk), "name": o.name}
                for o in selectors.public_organizers()
            ]
        }
    )


@service_errors
@require_http_methods(["POST"])
@limit_rate(
    category="pseudo_create",
    limit=django_settings.PSEUDO_CREATE_LIMIT,
    window_seconds=django_settings.PSEUDO_CREATE_WINDOW,
)
def organizer_create(request):
    body = parse_json_body(request)
    organizer = organizer_services.get_or_create_organizer(
        body.get("name", ""), actor=Actor.declared(body.get("name", "")[:80])
    )
    return JsonResponse(
        {"organizer": {"id": str(organizer.pk), "name": organizer.name}},
        status=201,
    )


@service_errors
@require_GET
def organizer_registrations(request, organizer_id):
    organizer = organizer_services.get_organizer(organizer_id)
    include_past = request.GET.get("history") == "1"
    regs = selectors.organizer_registrations(organizer, include_past=include_past)
    return JsonResponse(
        {
            "organizer": {"id": str(organizer.pk), "name": organizer.name},
            "registrations": [
                {
                    "event_id": str(reg.event_id),
                    "period_id": str(reg.event.period_id),
                    "period_label": reg.event.period.label,
                    "date": reg.event.date.isoformat(),
                    "type_label": reg.event.type_label or "À définir",
                    "state": reg.state,
                    "note": reg.note,
                    "version": reg.version,
                    "event_version": reg.event.version,
                    "status": reg.event.status,
                    "registrations_open": reg.event.registrations_open,
                    "archived": reg.event.period.is_archived,
                }
                for reg in regs
            ],
        }
    )


@service_errors
@require_http_methods(["PUT"])
@limit_rate(
    category="public_write",
    limit=django_settings.PUBLIC_WRITE_LIMIT,
    window_seconds=django_settings.PUBLIC_WRITE_WINDOW,
)
def event_registration(request, event_id):
    body = parse_json_body(request)
    organizer = organizer_from_body(body)
    state = body.get("state")
    if "expected_event_version" not in body:
        from .errors import ValidationFailed

        raise ValidationFailed(
            "La version de la date affichée est requise.",
            fields={"expected_event_version": "Champ manquant."},
        )
    registration = registration_services.set_registration(
        event_id,
        organizer.pk,
        state,
        actor=actor_for_request(request, organizer),
        note=body.get("note"),
        expected_version=body.get("expected_version"),
        expected_event_version=body.get("expected_event_version"),
    )
    return JsonResponse(
        {
            "registration": {
                "id": str(registration.pk),
                "event_id": str(registration.event_id),
                "state": registration.state,
                "note": registration.note,
                "version": registration.version,
            }
        }
    )


@service_errors
@require_http_methods(["POST"])
@limit_rate(
    category="public_write",
    limit=django_settings.PUBLIC_WRITE_LIMIT,
    window_seconds=django_settings.PUBLIC_WRITE_WINDOW,
)
def registrations_batch(request):
    body = parse_json_body(request)
    organizer = organizer_from_body(body)
    event_ids = body.get("event_ids")
    if not isinstance(event_ids, list):
        from .errors import ValidationFailed

        raise ValidationFailed(
            "Liste de dates invalide.", fields={"event_ids": "Liste attendue."}
        )
    versions = body.get("versions")
    if versions is not None and not isinstance(versions, dict):
        from .errors import ValidationFailed

        raise ValidationFailed(
            "Versions invalides.", fields={"versions": "Objet attendu."}
        )
    registrations = registration_services.batch_set(
        event_ids,
        organizer.pk,
        body.get("state"),
        actor=actor_for_request(request, organizer),
        note=body.get("note"),
        versions=versions,
    )
    return JsonResponse(
        {
            "registrations": [
                {
                    "id": str(reg.pk),
                    "event_id": str(reg.event_id),
                    "state": reg.state,
                    "note": reg.note,
                    "version": reg.version,
                }
                for reg in registrations
            ]
        }
    )
