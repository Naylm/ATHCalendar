import uuid
from datetime import date

from planning.errors import ValidationFailed


def parse_uuid(value, label="Identifiant"):
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ValidationFailed(f"{label} invalide.", fields={"id": str(value)[:80]}) from None


def require_str(value, label, *, max_len, min_len=0, field="value", strip=True):
    if not isinstance(value, str):
        raise ValidationFailed(f"{label} invalide.", fields={field: "Type invalide."})
    cleaned = " ".join(value.split()) if strip else value
    if len(cleaned) < min_len or len(cleaned) > max_len:
        raise ValidationFailed(
            f"{label} doit contenir entre {min_len} et {max_len} caractères.",
            fields={field: "Longueur invalide."},
        )
    return cleaned


def optional_str(value, label, *, max_len, field="value"):
    if value is None:
        return ""
    return require_str(value, label, max_len=max_len, min_len=0, field=field, strip=False)


def require_int(value, label, *, minimum=None, field="value"):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationFailed(f"{label} invalide.", fields={field: "Type invalide."})
    if minimum is not None and value < minimum:
        raise ValidationFailed(
            f"{label} doit être supérieur ou égal à {minimum}.",
            fields={field: "Valeur invalide."},
        )
    return value


def require_bool(value, label, *, field="value"):
    if not isinstance(value, bool):
        raise ValidationFailed(f"{label} invalide.", fields={field: "Type invalide."})
    return value


def require_date(value, label, *, field="date"):
    if not isinstance(value, date):
        raise ValidationFailed(f"{label} invalide.", fields={field: "Date invalide."})
    return value
