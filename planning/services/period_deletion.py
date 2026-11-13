from dataclasses import dataclass

from planning.db import write_transaction
from planning.errors import ValidationFailed, VersionConflict
from planning.models import Event, Period, Registration, SiteSettings

from . import audit
from .actors import Actor
from .periods import _check_version, _require_bureau, _require_not_archived, get_period
from .validators import require_bool, require_int, require_str


@dataclass(frozen=True)
class PeriodDeletionPreview:
    period: Period
    event_count: int
    registration_count: int
    is_active: bool


def _preview(period):
    return PeriodDeletionPreview(
        period=period,
        event_count=Event.objects.filter(period_id=period.pk).count(),
        registration_count=Registration.objects.filter(event__period_id=period.pk).count(),
        is_active=SiteSettings.objects.filter(pk=1, active_period_id=period.pk).exists(),
    )


def deletion_preview(period_id, *, actor: Actor):
    _require_bureau(actor)
    with write_transaction():
        return _preview(get_period(period_id))


def delete_period(
    period_id,
    *,
    actor: Actor,
    expected_version,
    expected_revision,
    confirmation_label,
    confirm_delete,
):
    _require_bureau(actor)
    expected_version = require_int(expected_version, "Version", minimum=1, field="expected_version")
    expected_revision = require_int(
        expected_revision, "Révision", minimum=0, field="expected_revision"
    )
    confirmation_label = require_str(
        confirmation_label,
        "Le libellé de confirmation",
        min_len=1,
        max_len=120,
        field="confirmation_label",
        strip=False,
    )
    if not require_bool(confirm_delete, "Confirmation", field="confirm_delete"):
        raise ValidationFailed(
            "Confirmez explicitement la suppression définitive.",
            fields={"confirm_delete": "Confirmation obligatoire."},
        )
    with write_transaction():
        period = get_period(period_id)
        _require_not_archived(period)
        _check_version(period, expected_version)
        if period.revision != expected_revision:
            raise VersionConflict(
                "Le planning a changé ; rechargez et vérifiez les éléments avant de confirmer.",
                fields={"expected_revision": str(period.revision)},
            )
        if confirmation_label != period.label:
            raise ValidationFailed(
                "Recopiez exactement le libellé de la période pour confirmer sa suppression.",
                fields={"confirmation_label": "Le libellé ne correspond pas."},
            )
        preview = _preview(period)
        before = audit.snapshot(period)
        before.update(
            event_count=preview.event_count,
            registration_count=preview.registration_count,
            was_active=preview.is_active,
        )
        # Log before deletion while the target UUID still exists; both writes are atomic.
        audit.log(actor=actor, action="period.delete", target=period, before=before, after=None)
        period.delete()
