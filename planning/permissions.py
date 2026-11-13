import functools
import json
import uuid as uuid_module
from dataclasses import replace

from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache

from .errors import RateLimited, ServiceError, ValidationFailed
from .middleware import source_key
from .services import rate_limits
from .services.actors import Actor
from .services.organizers import get_organizer


def is_bureau(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and user.groups.filter(name="Bureau").exists()
    )


def actor_for_request(request, organizer=None):
    if is_bureau(request.user):
        actor = Actor.bureau()
    elif organizer is not None:
        actor = Actor.public(organizer)
    else:
        actor = Actor.system()
    return replace(actor, request_id=getattr(request, "request_id", ""))


def json_error(error):
    body = {"code": error.code, "message": error.message, "fields": error.fields}
    return JsonResponse({"error": body}, status=error.status)


def bureau_page(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_bureau(request.user):
            return redirect("/bureau/connexion/")
        return view(request, *args, **kwargs)

    return never_cache(wrapper)


def bureau_api(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_bureau(request.user):
            return JsonResponse(
                {
                    "error": {
                        "code": "forbidden",
                        "message": "Accès réservé au Bureau.",
                        "fields": {},
                    }
                },
                status=403,
            )
        return view(request, *args, **kwargs)

    return wrapper


def service_errors(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except ServiceError as exc:
            return json_error(exc)

    return wrapper


def parse_json_body(request):
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValidationFailed("Corps de requête JSON invalide.") from None
    if not isinstance(body, dict):
        raise ValidationFailed("Corps de requête JSON invalide.")
    return body


def limit_rate(*, category, limit, window_seconds):
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            allowed = rate_limits.hit(
                category=category,
                key=source_key(getattr(request, "source_ip", "")),
                limit=limit,
                window_seconds=window_seconds,
            )
            if not allowed:
                raise RateLimited("Trop de requêtes ; veuillez patienter avant de réessayer.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def organizer_from_body(body, *, required=True):
    organizer_id = body.get("organizer_id")
    if not organizer_id:
        if required:
            raise ValidationFailed("Pseudo requis.", fields={"organizer_id": "Pseudo manquant."})
        return None
    try:
        uuid_module.UUID(str(organizer_id))
    except (ValueError, AttributeError, TypeError):
        raise ValidationFailed(
            "Pseudo invalide.", fields={"organizer_id": "Identifiant invalide."}
        ) from None
    return get_organizer(organizer_id)


def request_id():
    return uuid_module.uuid4().hex
