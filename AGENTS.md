# ATHDispatch — ATH Calendar

Application Django 5.2 LTS + SQLite + Docker. Français intégral côté utilisateur.

## Environnement local

```text
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

## Commandes de vérification (validées)

```text
.venv/Scripts/python.exe manage.py check
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run
.venv/Scripts/python.exe manage.py migrate
.venv/Scripts/python.exe manage.py test planning.tests
.venv/Scripts/python.exe -m ruff check config planning manage.py
.venv/Scripts/python.exe -m ruff format --check config planning manage.py
DJANGO_DEBUG=false DJANGO_SECRET_KEY=<secret> DJANGO_ALLOWED_HOSTS=<hôte> .venv/Scripts/python.exe manage.py check --deploy
```

## Conventions

- Les mutations métier passent par `planning/services/` et s'exécutent dans
  `planning.db.write_transaction()` (BEGIN IMMEDIATE, transaction courte).
  Jamais de `select_for_update`, jamais d'`ATOMIC_REQUESTS`.
- Toute erreur métier est une `planning.errors.ServiceError` typée
  (`code`, `message` français, `status`, `fields`).
- Acteurs : `planning.services.actors.Actor` — `bureau()`, `system()`,
  `public(organizer)`. Une période archivée refuse toute écriture, Bureau
  compris ; seul `periods.reopen` la déverrouille.
- `Event.version` couvre date/type/état/fermeture ; les inscriptions d'autres
  personnes ne la modifient pas. `Registration.version` couvre état/note.
  `Period.revision` monte à chaque changement visible.
- Les tests de concurrence utilisent un fichier SQLite réel et des connexions
  par thread (`planning/tests/test_concurrency.py`).
- Pas de secrets commités : `secrets/`, `.env` et `data/` sont ignorés.
- En production, `DJANGO_SECRET_KEY` ou `DJANGO_SECRET_KEY_FILE` et
  `DJANGO_ALLOWED_HOSTS` sont obligatoires ; `/healthz` est exempt de la
  redirection HTTPS.

## État de l'implémentation

Lot 1 (socle) livré : modèles, migrations, services métier, sélecteurs, vues
minimales `/` et `/healthz`, Dockerfile et compose. Les interfaces publiques et
Bureau complètes, l'API, l'export, la sauvegarde et les tests navigateur sont
prévus dans les lots suivants.
