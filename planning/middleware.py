import hashlib
import hmac
import ipaddress
import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def _parse_ip(value):
    try:
        return ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None


class TrustedProxyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.networks = []
        for entry in getattr(settings, "TRUSTED_PROXIES", []):
            try:
                self.networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                raise ImproperlyConfigured(
                    f"TRUSTED_PROXIES contient une entrée invalide : {entry!r}"
                ) from None

    def _trusted(self, address):
        return any(address in network for network in self.networks)

    def __call__(self, request):
        request.request_id = uuid.uuid4().hex
        remote = request.META.get("REMOTE_ADDR", "")
        remote_ip = _parse_ip(remote)
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if remote_ip is None or not self._trusted(remote_ip):
            request.META.pop("HTTP_X_FORWARDED_FOR", None)
            request.META.pop("HTTP_X_FORWARDED_PROTO", None)
            request.source_ip = remote
            return self.get_response(request)
        proto = request.META.get("HTTP_X_FORWARDED_PROTO", "")
        if proto.strip().lower() not in ("http", "https"):
            request.META.pop("HTTP_X_FORWARDED_PROTO", None)
        else:
            request.META["HTTP_X_FORWARDED_PROTO"] = proto.strip().lower()
        client = None
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        for hop in reversed(hops):
            hop_ip = _parse_ip(hop)
            if hop_ip is None:
                break
            if self._trusted(hop_ip):
                continue
            client = hop
            break
        request.source_ip = client or remote
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
    return hmac.new(settings.SECRET_KEY.encode(), source_ip.encode(), hashlib.sha256).hexdigest()
