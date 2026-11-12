import hashlib
import hmac

from django.conf import settings

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


class SourceIPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        remote = request.META.get("REMOTE_ADDR", "")
        request.source_ip = remote
        if remote in settings.TRUSTED_PROXIES:
            forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
            first = forwarded.split(",")[0].strip()
            if first:
                request.source_ip = first
        return self.get_response(request)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Referrer-Policy"] = "same-origin"
        return response


def source_key(source_ip):
    return hmac.new(
        settings.SECRET_KEY.encode(), source_ip.encode(), hashlib.sha256
    ).hexdigest()
