import csv
import uuid

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import forms, selectors
from .errors import Conflict, ServiceError
from .middleware import source_key
from .models import (
    AuditEntry,
    Event,
    GameType,
    Organizer,
    Period,
    Registration,
    SiteSettings,
)
from .permissions import actor_for_request, bureau_page, is_bureau
from .services import events as event_services
from .services import organizers as organizer_services
from .services import periods as period_services
from .services import registrations as registration_services


def _flash_error(request, exc):
    messages.error(request, exc.message)


def _bureau_actor(request):
    return actor_for_request(request)


@require_http_methods(["GET", "POST"])
def bureau_login(request):
    if is_bureau(request.user):
        return redirect("/bureau/")
    form = forms.LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        from .services import rate_limits

        allowed = rate_limits.hit(
            category="bureau_login",
            key=source_key(getattr(request, "source_ip", "")),
            limit=django_settings.BUREAU_LOGIN_LIMIT,
            window_seconds=django_settings.BUREAU_LOGIN_WINDOW,
        )
        if not allowed:
            messages.error(
                request, "Trop de tentatives ; veuillez réessayer plus tard."
            )
        else:
            user = authenticate(
                request,
                username="bureau",
                password=form.cleaned_data["password"],
            )
            if user is not None and is_bureau(user):
                login(request, user)
                return redirect("/bureau/")
            messages.error(request, "Mot de passe incorrect.")
    return render(request, "bureau/login.html", {"form": form})


@require_POST
def bureau_logout(request):
    logout(request)
    return redirect("/")


@bureau_page
@require_GET
def dashboard(request):
    periods = list(
        Period.objects.annotate(event_count=Count("events")).order_by("start_date")
    )
    for period in periods:
        period.understaffed = sum(
            1
            for event in period.events.all()
            if event.status == Event.Status.SCHEDULED
            and event.registrations.filter(
                state=Registration.State.ACTIVE
            ).count()
            < event.min_organizers
        )
    return render(
        request,
        "bureau/dashboard.html",
        {"periods": periods, "settings": SiteSettings.get()},
    )


@bureau_page
@require_http_methods(["GET", "POST"])
def period_create(request):
    form = forms.PeriodCreateForm(request.POST or None)
    preview = None
    if request.method == "POST" and form.is_valid():
        action = request.POST.get("action", "preview")
        preview = period_services.preview_period(
            start_date=form.cleaned_data["start_date"],
            end_date=form.cleaned_data["end_date"],
        )
        if action == "create":
            if not form.cleaned_data["selected_dates"]:
                messages.error(
                    request,
                    "Prévisualisez les week-ends avant de créer le planning.",
                )
                return render(
                    request,
                    "bureau/period_form.html",
                    {"form": form, "preview": preview},
                )
            try:
                period_services.create_period(
                    actor=_bureau_actor(request),
                    label=form.cleaned_data["label"],
                    start_date=form.cleaned_data["start_date"],
                    end_date=form.cleaned_data["end_date"],
                    creation_key=form.cleaned_data["creation_key"] or uuid.uuid4(),
                    selected_dates=form.cleaned_data["selected_dates"],
                    confirm_overlap=form.cleaned_data["confirm_overlap"],
                )
                return redirect("/bureau/")
            except Conflict as exc:
                if exc.code == "overlap":
                    messages.warning(request, exc.message)
                else:
                    _flash_error(request, exc)
            except ServiceError as exc:
                _flash_error(request, exc)
    return render(
        request,
        "bureau/period_form.html",
        {"form": form, "preview": preview},
    )


@bureau_page
@require_http_methods(["GET", "POST"])
def period_detail(request, period_id):
    try:
        period = period_services.get_period(period_id)
    except ServiceError as exc:
        _flash_error(request, exc)
        return redirect("/bureau/")
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "update":
                form = forms.PeriodEditForm(request.POST)
                if form.is_valid():
                    period_services.update_period(
                        period.pk,
                        actor=_bureau_actor(request),
                        expected_version=form.cleaned_data["expected_version"],
                        label=form.cleaned_data["label"],
                        start_date=form.cleaned_data["start_date"],
                        end_date=form.cleaned_data["end_date"],
                    )
            elif action == "set_active":
                period_services.set_active(period.pk, actor=_bureau_actor(request))
            elif action == "archive":
                period_services.archive(period.pk, actor=_bureau_actor(request))
            elif action == "reopen":
                period_services.reopen(period.pk, actor=_bureau_actor(request))
            elif action == "generate_missing":
                period_services.complete_missing_weekends(
                    period.pk, actor=_bureau_actor(request)
                )
            elif action == "close_all":
                period_services.set_period_registrations(
                    period.pk, actor=_bureau_actor(request), is_open=False
                )
            elif action == "open_all":
                period_services.set_period_registrations(
                    period.pk, actor=_bureau_actor(request), is_open=True
                )
        except ServiceError as exc:
            _flash_error(request, exc)
        return redirect(f"/bureau/periodes/{period.pk}/")
    period.refresh_from_db()
    events = list(selectors.period_events(period))
    for event in events:
        event.payload = selectors.event_payload(event)
    form = forms.PeriodEditForm(
        initial={
            "label": period.label,
            "start_date": period.start_date,
            "end_date": period.end_date,
            "expected_version": period.version,
        }
    )
    return render(
        request,
        "bureau/period_detail.html",
        {
            "period": period,
            "events": events,
            "form": form,
            "event_form": forms.EventForm(
                initial={"min_organizers": 2}
            ),
            "game_types": GameType.objects.filter(is_active=True),
        },
    )


@bureau_page
@require_http_methods(["POST"])
def event_create(request, period_id):
    try:
        period = period_services.get_period(period_id)
        form = forms.EventForm(request.POST)
        if form.is_valid():
            event_services.create_event(
                period.pk,
                actor=_bureau_actor(request),
                date_value=form.cleaned_data["date"],
                game_type_id=(
                    form.cleaned_data["game_type"].pk
                    if form.cleaned_data["game_type"]
                    else None
                ),
                title=form.cleaned_data["title"],
                public_note=form.cleaned_data["public_note"],
                min_organizers=form.cleaned_data["min_organizers"],
                capacity=form.cleaned_data["capacity"],
            )
        else:
            messages.error(request, "Formulaire de date invalide.")
    except ServiceError as exc:
        _flash_error(request, exc)
    return redirect(f"/bureau/periodes/{period_id}/")


@bureau_page
@require_http_methods(["GET", "POST"])
def event_edit(request, event_id):
    try:
        event = event_services.get_event(event_id)
    except ServiceError as exc:
        _flash_error(request, exc)
        return redirect("/bureau/")
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "update":
                form = forms.EventForm(request.POST)
                if form.is_valid():
                    event_services.update_event(
                        event.pk,
                        actor=_bureau_actor(request),
                        expected_version=form.cleaned_data["expected_version"],
                        date_value=form.cleaned_data["date"],
                        game_type_id=(
                            form.cleaned_data["game_type"].pk
                            if form.cleaned_data["game_type"]
                            else None
                        ),
                        title=form.cleaned_data["title"],
                        public_note=form.cleaned_data["public_note"],
                        min_organizers=form.cleaned_data["min_organizers"],
                        capacity=form.cleaned_data["capacity"],
                    )
                    return redirect(f"/bureau/dates/{event.pk}/")
                messages.error(request, "Formulaire invalide.")
            elif action == "cancel":
                event_services.cancel_event(event.pk, actor=_bureau_actor(request))
            elif action == "restore":
                event_services.restore_event(event.pk, actor=_bureau_actor(request))
            elif action == "open":
                event_services.set_registrations_open(
                    event.pk, actor=_bureau_actor(request), is_open=True
                )
            elif action == "close":
                event_services.set_registrations_open(
                    event.pk, actor=_bureau_actor(request), is_open=False
                )
            elif action == "registration":
                reg_form = forms.BureauRegistrationForm(request.POST)
                if reg_form.is_valid():
                    registration_services.set_registration(
                        event.pk,
                        reg_form.cleaned_data["organizer"],
                        reg_form.cleaned_data["state"],
                        actor=_bureau_actor(request),
                        note=reg_form.cleaned_data["note"] or None,
                    )
            if action != "update":
                return redirect(f"/bureau/dates/{event.pk}/")
        except ServiceError as exc:
            _flash_error(request, exc)
        return redirect(f"/bureau/dates/{event.pk}/")
    event.refresh_from_db()
    form = forms.EventForm(
        initial={
            "date": event.date,
            "game_type": event.game_type_id,
            "title": event.title,
            "public_note": event.public_note,
            "min_organizers": event.min_organizers,
            "capacity": event.capacity,
            "expected_version": event.version,
        }
    )
    regs = event.registrations.select_related("organizer").order_by("created_at")
    return render(
        request,
        "bureau/event_form.html",
        {
            "event": event,
            "form": form,
            "registrations": regs,
            "organizers": Organizer.objects.filter(
                is_active=True, merged_into__isnull=True
            ).order_by("name"),
        },
    )


@bureau_page
@require_http_methods(["POST"])
def events_batch(request, period_id):
    try:
        period = period_services.get_period(period_id)
        ids = request.POST.getlist("event_ids")
        result = event_services.batch_update(
            ids,
            actor=_bureau_actor(request),
            game_type_id=(
                request.POST["game_type"]
                if "game_type" in request.POST and request.POST["game_type"]
                else event_services.UNSET
            ),
            min_organizers=(
                int(request.POST["min_organizers"])
                if request.POST.get("min_organizers")
                else None
            ),
            capacity=(
                int(request.POST["capacity"])
                if request.POST.get("capacity")
                else event_services.UNSET
            ),
            registrations_open=(
                {"open": True, "close": False}.get(request.POST.get("registrations"))
                if request.POST.get("registrations")
                else None
            ),
            cancel=(
                {"cancel": True, "restore": False}.get(request.POST.get("status"))
                if request.POST.get("status")
                else None
            ),
        )
        for skipped in result["skipped"]:
            messages.warning(request, skipped["reason"])
    except (ServiceError, ValueError) as exc:
        if isinstance(exc, ServiceError):
            _flash_error(request, exc)
        else:
            messages.error(request, "Paramètres groupés invalides.")
    return redirect(f"/bureau/periodes/{period_id}/")


@bureau_page
@require_http_methods(["GET", "POST"])
def organizers_list(request):
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "create":
                form = forms.OrganizerNameForm(request.POST)
                if form.is_valid():
                    organizer_services.create_organizer(
                        form.cleaned_data["name"], actor=_bureau_actor(request)
                    )
            elif action == "rename":
                organizer_services.rename_organizer(
                    request.POST["organizer"],
                    actor=_bureau_actor(request),
                    name=request.POST.get("name", ""),
                )
            elif action == "deactivate":
                organizer_services.set_organizer_active(
                    request.POST["organizer"],
                    actor=_bureau_actor(request),
                    active=False,
                )
            elif action == "activate":
                organizer_services.set_organizer_active(
                    request.POST["organizer"],
                    actor=_bureau_actor(request),
                    active=True,
                )
            elif action == "merge":
                note_choices = {
                    key.removeprefix("note_"): value
                    for key, value in request.POST.items()
                    if key.startswith("note_")
                }
                organizer_services.merge_organizers(
                    request.POST["source"],
                    request.POST["target"],
                    actor=_bureau_actor(request),
                    note_choices=note_choices or None,
                )
        except ServiceError as exc:
            _flash_error(request, exc)
        return redirect("/bureau/pseudos/")
    organizers = Organizer.objects.order_by("name")
    conflicts = None
    if request.GET.get("fusion_source") and request.GET.get("fusion_target"):
        try:
            source = organizer_services.get_organizer(request.GET["fusion_source"])
            target = organizer_services.canonical(
                organizer_services.get_organizer(request.GET["fusion_target"])
            )
            shared = []
            target_regs = {
                r.event_id: r for r in Registration.objects.filter(organizer=target)
            }
            for reg in Registration.objects.select_related(
                "event", "event__period"
            ).filter(organizer=source):
                if reg.event.period.is_archived:
                    continue
                existing = target_regs.get(reg.event_id)
                if existing is not None and existing.note != reg.note:
                    shared.append(
                        {
                            "event": reg.event,
                            "source_note": reg.note,
                            "target_note": existing.note,
                        }
                    )
            conflicts = {"source": source, "target": target, "shared": shared}
        except ServiceError as exc:
            _flash_error(request, exc)
    return render(
        request,
        "bureau/organizers.html",
        {"organizers": organizers, "conflicts": conflicts},
    )


@bureau_page
@require_http_methods(["GET", "POST"])
def types_list(request):
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "create":
                form = forms.GameTypeForm(request.POST)
                if form.is_valid():
                    GameType.objects.create(**form.cleaned_data)
                else:
                    messages.error(request, "Formulaire de type invalide.")
            elif action == "edit":
                game_type = GameType.objects.get(pk=request.POST["type"])
                form = forms.GameTypeEditForm(request.POST)
                if form.is_valid():
                    game_type.name = form.cleaned_data["name"]
                    game_type.description = form.cleaned_data["description"]
                    game_type.color = form.cleaned_data["color"]
                    game_type.save()
                else:
                    messages.error(request, "Formulaire de type invalide.")
            elif action == "deactivate":
                GameType.objects.filter(pk=request.POST["type"]).update(
                    is_active=False
                )
            elif action == "activate":
                GameType.objects.filter(pk=request.POST["type"]).update(
                    is_active=True
                )
        except (GameType.DoesNotExist, ValueError):
            messages.error(request, "Type introuvable.")
        return redirect("/bureau/types/")
    return render(
        request,
        "bureau/types.html",
        {"types": GameType.objects.order_by("name"), "form": forms.GameTypeForm()},
    )


@bureau_page
@require_GET
def journal(request):
    entries = AuditEntry.objects.order_by("-created_at")
    if request.GET.get("period"):
        try:
            period = period_services.get_period(request.GET["period"])
            event_ids = [
                str(pk) for pk in period.events.values_list("pk", flat=True)
            ]
            entries = entries.filter(
                Q(target_type="Period", target_id=str(period.pk))
                | Q(target_type="Event", target_id__in=event_ids)
                | Q(target_type="Registration")
            )
        except ServiceError:
            pass
    if request.GET.get("event"):
        entries = entries.filter(target_type="Event", target_id=request.GET["event"])
    if request.GET.get("action"):
        entries = entries.filter(action__icontains=request.GET["action"])
    if request.GET.get("pseudo"):
        entries = entries.filter(actor_label__icontains=request.GET["pseudo"])
    return render(
        request,
        "bureau/journal.html",
        {
            "entries": entries[:500],
            "periods": Period.objects.order_by("start_date"),
        },
    )


def _csv_safe(value):
    text = "" if value is None else str(value)
    stripped = text.lstrip()
    if stripped.startswith(("=", "+", "-", "@")) or text[:1] in ("\t", "\r"):
        return "'" + text
    return text


@bureau_page
@require_GET
def export_period_csv(request, period_id):
    try:
        period = period_services.get_period(period_id)
    except ServiceError as exc:
        return JsonResponse({"error": exc.message}, status=exc.status)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="periode-{period.pk}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(
        ["date", "type", "titre", "statut", "inscriptions_ouvertes",
         "minimum", "capacité", "pseudo", "état", "note"]
    )
    for event in selectors.period_events(period):
        regs = list(event.registrations.all())
        if not regs:
            writer.writerow(
                [
                    _csv_safe(event.date), _csv_safe(event.type_label),
                    _csv_safe(event.title), event.status,
                    event.registrations_open, event.min_organizers,
                    event.capacity or "", "", "", "",
                ]
            )
        for reg in regs:
            writer.writerow(
                [
                    _csv_safe(event.date), _csv_safe(event.type_label),
                    _csv_safe(event.title), event.status,
                    event.registrations_open, event.min_organizers,
                    event.capacity or "", _csv_safe(reg.organizer_name),
                    reg.state, _csv_safe(reg.note),
                ]
            )
    return response


@bureau_page
@require_GET
def export_business_json(request):
    data = {
        "exported_at": timezone.now().isoformat(),
        "periods": [
            {
                "id": str(p.pk),
                "label": p.label,
                "start_date": p.start_date.isoformat(),
                "end_date": p.end_date.isoformat(),
                "archived_at": p.archived_at.isoformat() if p.archived_at else None,
                "version": p.version,
                "revision": p.revision,
            }
            for p in Period.objects.order_by("start_date")
        ],
        "game_types": [
            {
                "id": str(t.pk),
                "code": t.code,
                "name": t.name,
                "description": t.description,
                "color": t.color,
                "is_active": t.is_active,
            }
            for t in GameType.objects.all()
        ],
        "organizers": [
            {
                "id": str(o.pk),
                "name": o.name,
                "is_active": o.is_active,
                "merged_into": str(o.merged_into_id) if o.merged_into_id else None,
            }
            for o in Organizer.objects.all()
        ],
        "events": [
            {
                "id": str(e.pk),
                "period_id": str(e.period_id),
                "date": e.date.isoformat(),
                "type_label": e.type_label,
                "title": e.title,
                "public_note": e.public_note,
                "min_organizers": e.min_organizers,
                "capacity": e.capacity,
                "origin": e.origin,
                "status": e.status,
                "registrations_open": e.registrations_open,
                "version": e.version,
            }
            for e in Event.objects.order_by("date")
        ],
        "registrations": [
            {
                "id": str(r.pk),
                "event_id": str(r.event_id),
                "organizer_id": str(r.organizer_id),
                "organizer_name": r.organizer_name,
                "state": r.state,
                "note": r.note,
                "version": r.version,
                "created_at": r.created_at.isoformat(),
            }
            for r in Registration.objects.order_by("created_at")
        ],
        "audit": [
            {
                "id": str(a.pk),
                "created_at": a.created_at.isoformat(),
                "actor_type": a.actor_type,
                "actor_label": a.actor_label,
                "action": a.action,
                "target_type": a.target_type,
                "target_id": a.target_id,
                "before": a.before,
                "after": a.after,
                "request_id": a.request_id,
            }
            for a in AuditEntry.objects.order_by("created_at")
        ],
    }
    response = JsonResponse(data)
    response["Content-Disposition"] = 'attachment; filename="athcalendar-export.json"'
    return response


@bureau_page
@require_http_methods(["GET", "POST"])
def password_change(request):
    form = forms.PasswordChangeBureauForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = request.user
        if not user.check_password(form.cleaned_data["current_password"]):
            messages.error(request, "Mot de passe actuel incorrect.")
        else:
            user.set_password(form.cleaned_data["new_password"])
            user.save()
            logout(request)
            messages.success(
                request, "Mot de passe modifié ; veuillez vous reconnecter."
            )
            return redirect("/bureau/connexion/")
    return render(request, "bureau/password.html", {"form": form})
