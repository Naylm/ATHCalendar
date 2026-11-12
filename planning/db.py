from contextlib import contextmanager

from django.db import OperationalError, transaction

from .errors import LockUnavailable


def _is_lock_error(exc):
    message = str(exc).lower()
    return "locked" in message or "busy" in message


@contextmanager
def write_transaction():
    try:
        with transaction.atomic():
            yield
    except OperationalError as exc:
        if _is_lock_error(exc):
            raise LockUnavailable(
                "Le service est momentanément occupé, veuillez réessayer."
            ) from exc
        raise
