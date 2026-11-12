import os
import sqlite3
import stat
from pathlib import Path
from datetime import datetime, timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = "Sauvegarde cohérente de la base SQLite (API backup, compatible WAL)."

    def handle(self, *args, **options):
        source_path = connections["default"].settings_dict["NAME"]
        backup_dir = Path(settings.BACKUP_DIR)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = backup_dir / f"athcalendar-{stamp}.sqlite3"
        if target.exists():
            raise CommandError(f"Le fichier {target} existe déjà.")
        source = sqlite3.connect(
            source_path, uri=source_path.startswith("file:")
        )
        try:
            dest = sqlite3.connect(str(target))
            try:
                source.backup(dest)
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
            target.unlink(missing_ok=True)
            raise CommandError(f"Sauvegarde invalide : {integrity}")
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        self.stdout.write(self.style.SUCCESS(f"Sauvegarde créée : {target}"))
