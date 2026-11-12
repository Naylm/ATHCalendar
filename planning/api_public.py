from dataclasses import replace

from django.conf import settings as django_settings
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_http_methods

from . import selectors
from .errors import ValidationFailed
from .permissions import (
    actor_for_request,
    is_bureau,
    limit_rate,
    organizer_from_body,
    parse_json_body,
    service_errors,
)
from .services import events as event_services
from .services import organizers as organizer_services
from .services import periods as period_services
from .services import registrations as registration_services
from .services.actors import Actor
from .services.validators import require_int, require_str

PRIVILEGED_KEYS = {"actor", "actor_type", "role", "is_bureau", "bureau", "user"}


def _reject_privileged(body):
    for key in PRIVILEGED_KEYS:
        if key in body:
            raise ValidationFailed(
                "Ce champ ne peut pas être défini par le client.",
                fields={key: "Champ non autorisé."},
            )


def _strict_version(value, label, *, minimum):
    return require_int(value, label, minimum=minimum, field=label)


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
    events_qs, _ = selectors.filtered_events(period, request.GET.get("filtre"))
    events = [selectors.event_payload(event) for event in events_qs]
    return JsonResponse({"period": _period_payload(period), "events": events})


@service_errors
@require_GET
def period_revision(request, period_id):
    period = period_services.get_period(period_id)
    return JsonResponse({"revision": period.revision})


@cache_control(no_store=True)
@service_errors
@require_GET
def period_live(request, period_id):
    period = period_services.get_period(period_id)
    revision = period.revision
    response_data = {"revision": revision, "changed": True}
    revision_param = request.GET.get("revision")
    if revision_param is not None:
        if not (
            revision_param.isascii() and revision_param.isdigit() and len(revision_param) <= 20
        ):
            raise ValidationFailed("Révision invalide.", fields={"revision": "Format invalide."})
        if int(revision_param) == revision:
            response_data["changed"] = False
    if response_data["changed"]:
        context = selectors.planning_context(period, request.GET.get("filtre"))
        response_data["html"] = render_to_string("planning/_months.html", context, request=request)
    response = JsonResponse(response_data)
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def organizers_list(request):
    return JsonResponse(
        {"organizers": [{"id": str(o.pk), "name": o.name} for o in selectors.public_organizers()]}
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
    _reject_privileged(body)
    name = require_str(body.get("name"), "Le pseudo", min_len=2, max_len=40, field="name")
    actor = replace(
        Actor.declared(name),
        request_id=getattr(request, "request_id", "") or "",
    )
    organizer = organizer_services.get_or_create_organizer(name, actor=actor)
    return JsonResponse(
        {"organizer": {"id": str(organizer.pk), "name": organizer.name}},
        status=201,
    )


@service_errors
@require_GET
def organizer_registrations(request, organizer_id):
    organizer = organizer_services.canonical(organizer_services.get_organizer(organizer_id))
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
    _reject_privileged(body)
    organizer = organizer_from_body(body)
    if "expected_event_version" not in body:
        raise ValidationFailed(
            "La version de la date affichée est requise.",
            fields={"expected_event_version": "Champ manquant."},
        )
    if "expected_version" not in body:
        raise ValidationFailed(
            "La version de l'inscription affichée est requise.",
            fields={"expected_version": "Champ manquant."},
        )
    expected_event_version = _strict_version(
        body["expected_event_version"], "expected_event_version", minimum=1
    )
    expected_version = _strict_version(body["expected_version"], "expected_version", minimum=0)
    registration = registration_services.set_registration(
        event_id,
        organizer.pk,
        body.get("state"),
        actor=actor_for_request(request, organizer),
        note=body.get("note"),
        expected_version=expected_version,
        expected_event_version=expected_event_version,
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
@require_http_methods(["PUT"])
@limit_rate(
    category="public_write",
    limit=django_settings.PUBLIC_WRITE_LIMIT,
    window_seconds=django_settings.PUBLIC_WRITE_WINDOW,
)
def event_type(request, event_id):
    body = parse_json_body(request)
    _reject_privileged(body)
    for key in body:
        if key not in ("game_type_id", "expected_event_version"):
            raise ValidationFailed(
                "Ce champ ne peut pas être défini par le client.",
                fields={key: "Champ non autorisé."},
            )
    if "game_type_id" not in body or "expected_event_version" not in body:
        raise ValidationFailed(
            "Le type et la version de la date affichée sont requis.",
            fields={
                "game_type_id": "Champ manquant.",
                "expected_event_version": "Champ manquant.",
            },
        )
    game_type_id = body["game_type_id"]
    if game_type_id is not None and (not isinstance(game_type_id, str) or not game_type_id):
        raise ValidationFailed(
            "Type de partie invalide.", fields={"game_type_id": "Type invalide."}
        )
    expected_event_version = _strict_version(
        body["expected_event_version"], "expected_event_version", minimum=1
    )
    if request.user.is_authenticated and is_bureau(request.user):
        actor = actor_for_request(request)
    else:
        actor = replace(
            Actor.declared("Visiteur"),
            request_id=getattr(request, "request_id", "") or "",
        )
    event = event_services.set_public_game_type(
        event_id,
        actor=actor,
        game_type_id=game_type_id,
        expected_version=expected_event_version,
    )
    return JsonResponse({"event": selectors.event_payload(event)})


def _strict_versions_map(versions, event_ids):
    if not isinstance(versions, dict):
        raise ValidationFailed(
            "Les versions affichées sont requises pour chaque date.",
            fields={"versions": "Objet attendu."},
        )
    parsed = {}
    for raw_id in event_ids:
        key = str(raw_id)
        entry = versions.get(key)
        if not isinstance(entry, dict):
            raise ValidationFailed(
                "Version manquante pour une date sélectionnée.",
                fields={"versions": key},
            )
        parsed[key] = {
            "event": _strict_version(entry.get("event"), "event", minimum=1),
            "registration": _strict_version(entry.get("registration"), "registration", minimum=0),
        }
    return parsed


@service_errors
@require_http_methods(["POST"])
@limit_rate(
    category="public_write",
    limit=django_settings.PUBLIC_WRITE_LIMIT,
    window_seconds=django_settings.PUBLIC_WRITE_WINDOW,
)
def registrations_batch(request):
    body = parse_json_body(request)
    _reject_privileged(body)
    organizer = organizer_from_body(body)
    event_ids = body.get("event_ids")
    if not isinstance(event_ids, list) or not all(isinstance(eid, str) for eid in event_ids):
        raise ValidationFailed("Liste de dates invalide.", fields={"event_ids": "Liste attendue."})
    versions = _strict_versions_map(body.get("versions"), event_ids)
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
