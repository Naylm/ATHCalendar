import os
import sqlite3
import stat
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

DEADLINE_SECONDS = 30


class Command(BaseCommand):
    help = "Sauvegarde cohérente de la base SQLite (API backup, compatible WAL)."

    def handle(self, *args, **options):
        source_path = str(connections["default"].settings_dict["NAME"])
        if source_path == ":memory:" or "mode=memory" in source_path:
            raise CommandError("Impossible de sauvegarder une base en mémoire.")
        if source_path.startswith("file:"):
            source_uri = source_path
            source_fs_path = source_path.split("?")[0].removeprefix("file:")
            if "mode=ro" not in source_path:
                source_uri = source_uri.split("?")[0] + "?mode=ro"
        else:
            source_fs_path = source_path
            source_uri = Path(source_path).resolve().as_uri() + "?mode=ro"
        if not os.path.exists(source_fs_path):
            raise CommandError("Base de données source introuvable.")
        backup_dir = Path(settings.BACKUP_DIR)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = backup_dir / f"athcalendar-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
        try:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise CommandError(f"Le fichier de sauvegarde {target.name} existe déjà.") from exc
        os.close(fd)
        try:
            source = sqlite3.connect(source_uri, uri=True)
            try:
                dest = sqlite3.connect(str(target))
                deadline = time.monotonic() + DEADLINE_SECONDS

                def progress(status, remaining, total):
                    if time.monotonic() > deadline:
                        raise CommandError("Délai de sauvegarde dépassé (30 s).")
                    return 0

                try:
                    source.backup(dest, pages=64, progress=progress)
                finally:
                    dest.close()
            finally:
                source.close()
            check = sqlite3.connect(str(target))
            try:
                integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                check.close()
            if integrity != "ok":
                raise CommandError(f"Sauvegarde invalide : {integrity}")
        except CommandError:
            target.unlink(missing_ok=True)
            raise
        except sqlite3.Error as exc:
            target.unlink(missing_ok=True)
            raise CommandError(f"Sauvegarde interrompue : {exc}") from exc
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        self.stdout.write(self.style.SUCCESS(f"Sauvegarde créée : {target}"))
