from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from . import selectors
from .errors import ServiceError
from .services import events as event_services
from .services import organizers as organizer_services
from .services import periods as period_services


def _period_nav(period, current_id):
    periods = list(
        period_services.Period.objects.order_by("start_date", "created_at")
    )
    return {
        "periods": [
            {
                "id": str(p.pk),
                "label": p.label + (" (archive)" if p.is_archived else ""),
                "selected": str(p.pk) == str(current_id),
            }
            for p in periods
        ],
        "current": period,
    }


@require_GET
def index(request):
    try:
        period = selectors.default_period(request.GET.get("period"))
    except ServiceError:
        period = None
    view = request.GET.get("vue", "liste")
    events = list(selectors.period_events(period)) if period else []
    for event in events:
        event.payload = selectors.event_payload(event)
    return render(
        request,
        "planning/index.html",
        {
            "period": period,
            "events": events,
            "view": view,
            "nav": _period_nav(None, period.pk if period else ""),
            "organizers": selectors.public_organizers(),
        },
    )


@require_GET
def event_detail(request, event_id):
    event = event_services.get_event(event_id)
    payload = selectors.event_payload(event)
    my_reg = None
    organizer_id = request.GET.get("organizer")
    if organizer_id:
        try:
            organizer = organizer_services.get_organizer(organizer_id)
            from .models import Registration

            my_reg = Registration.objects.filter(
                event=event, organizer_id__in=organizer_services.alias_ids(organizer)
            ).first()
        except ServiceError:
            my_reg = None
    return render(
        request,
        "planning/event_detail.html",
        {
            "event": event,
            "payload": payload,
            "my_registration": my_reg,
            "organizers": selectors.public_organizers(),
            "period": event.period,
        },
    )


@require_GET
def my_registrations(request):
    organizer = None
    regs = []
    period_filter = request.GET.get("period")
    history = request.GET.get("historique") == "1"
    organizer_id = request.GET.get("organizer")
    if organizer_id:
        try:
            organizer = organizer_services.get_organizer(organizer_id)
            organizer = organizer_services.canonical(organizer)
            qs = selectors.organizer_registrations(organizer, include_past=history)
            if period_filter:
                qs = qs.filter(event__period_id=period_filter)
            regs = list(qs)
        except ServiceError:
            organizer = None
    return render(
        request,
        "planning/my_registrations.html",
        {
            "organizer": organizer,
            "registrations": regs,
            "history": history,
            "organizers": selectors.public_organizers(),
            "periods": period_services.Period.objects.order_by(
                "start_date", "created_at"
            ),
            "period_filter": period_filter or "",
        },
    )


@require_GET
def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "error"}, status=503)


@require_GET
def robots_txt(request):
    return HttpResponse(
        "User-agent: *\nDisallow: /\n", content_type="text/plain"
    )
