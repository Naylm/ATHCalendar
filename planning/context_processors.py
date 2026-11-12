def ath_site(request):
    from .models import SiteSettings

    try:
        site = SiteSettings.objects.filter(pk=1).only("site_name").first()
    except Exception:
        site = None
    return {"ath_site_name": site.site_name if site else "ATH Calendar"}
