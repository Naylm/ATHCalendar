from django.conf import settings


def ath_site(request):
    from .models import Event, SiteSettings

    try:
        site = SiteSettings.objects.filter(pk=1).only("site_name").first()
    except Exception:
        site = None
    return {
        "ath_site_name": site.site_name if site else "Calendrier organisateurs ATH",
        "app_version": settings.APP_VERSION,
        "event_availability_choices": Event.Availability.choices,
    }
