from dataclasses import dataclass

from planning.models import AuditEntry, Organizer


@dataclass(frozen=True)
class Actor:
    kind: str
    label: str = ""
    organizer: Organizer | None = None

    @classmethod
    def bureau(cls):
        return cls(kind=AuditEntry.ActorType.BUREAU, label="Bureau")

    @classmethod
    def system(cls):
        return cls(kind=AuditEntry.ActorType.SYSTEM)

    @classmethod
    def declared(cls, name):
        return cls(kind=AuditEntry.ActorType.PUBLIC, label=(name or "")[:80])

    @classmethod
    def public(cls, organizer):
        return cls(
            kind=AuditEntry.ActorType.PUBLIC,
            label=organizer.name,
            organizer=organizer,
        )

    @property
    def is_bureau(self):
        return self.kind == AuditEntry.ActorType.BUREAU
