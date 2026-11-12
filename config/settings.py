import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("DJANGO_DATA_DIR", BASE_DIR / "data"))

DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"


def _load_secret_key():
    secret_file = os.environ.get("DJANGO_SECRET_KEY_FILE")
    if secret_file:
        secret = Path(secret_file).read_text(encoding="utf-8").strip()
    else:
        secret = os.environ.get("DJANGO_SECRET_KEY", "")
    if secret:
        if not DEBUG and (len(secret) < 50 or secret.startswith("django-insecure-")):
            raise RuntimeError(
                "Le secret Django de production doit comporter au moins 50 caractères aléatoires."
            )
        return secret
    if DEBUG:
        return "dev-" + secrets.token_hex(24)
    raise RuntimeError(
        "DJANGO_SECRET_KEY ou DJANGO_SECRET_KEY_FILE est obligatoire en production."
    )


SECRET_KEY = _load_secret_key()

_allowed_hosts_env = os.environ.get("DJANGO_ALLOWED_HOSTS", "")
if not DEBUG and not _allowed_hosts_env.strip():
    raise RuntimeError("DJANGO_ALLOWED_HOSTS explicite est obligatoire en production.")
ALLOWED_HOSTS = [
    host.strip() for host in _allowed_hosts_env.split(",") if host.strip()
] or ["127.0.0.1", "localhost"]
if not DEBUG:
    for loopback in ("127.0.0.1", "localhost"):
        if loopback not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(loopback)

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

TRUSTED_PROXIES = [
    proxy.strip()
    for proxy in os.environ.get("DJANGO_TRUSTED_PROXIES", "").split(",")
    if proxy.strip()
]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "planning",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "planning.middleware.SourceIPMiddleware",
    "planning.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(DATA_DIR / "db.sqlite3"),
        "OPTIONS": {"timeout": 5, "transaction_mode": "IMMEDIATE"},
    }
}

LANGUAGE_CODE = "fr"
TIME_ZONE = "Europe/Paris"
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
LOGIN_URL = "/bureau/connexion/"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

BUREAU_LOGIN_LIMIT = int(os.environ.get("ATH_BUREAU_LOGIN_LIMIT", "5"))
BUREAU_LOGIN_WINDOW = int(os.environ.get("ATH_BUREAU_LOGIN_WINDOW", "900"))
PUBLIC_WRITE_LIMIT = int(os.environ.get("ATH_PUBLIC_WRITE_LIMIT", "60"))
PUBLIC_WRITE_WINDOW = int(os.environ.get("ATH_PUBLIC_WRITE_WINDOW", "60"))
PSEUDO_CREATE_LIMIT = int(os.environ.get("ATH_PSEUDO_CREATE_LIMIT", "30"))
PSEUDO_CREATE_WINDOW = int(os.environ.get("ATH_PSEUDO_CREATE_WINDOW", "3600"))

BACKUP_DIR = Path(os.environ.get("ATH_BACKUP_DIR", str(DATA_DIR.parent / "backups")))

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
