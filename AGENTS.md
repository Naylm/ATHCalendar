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
.venv/Scripts/python.exe -m unittest planning.tests_e2e.test_ui
.venv/Scripts/python.exe -m ruff check config planning manage.py
.venv/Scripts/python.exe -m ruff format --check config planning manage.py
DJANGO_DEBUG=false DJANGO_SECRET_KEY=<secret> DJANGO_ALLOWED_HOSTS=<hôte> .venv/Scripts/python.exe manage.py check --deploy
```

## Commandes applicatives

```text
.venv/Scripts/python.exe manage.py setup_bureau          # création interactive du mot de passe Bureau
.venv/Scripts/python.exe manage.py backup_database       # sauvegarde cohérente (SQLite backup API)
.venv/Scripts/python.exe manage.py cleanup_technical_data # purge sessions expirées + fenêtres de limitation
.venv/Scripts/python.exe manage.py runserver 127.0.0.1:8000
.venv/Scripts/python.exe -m playwright install chromium webkit  # navigateurs E2E
```

## Commandes Docker

```text
docker compose build
# Toujours migrer avant de démarrer : l'entrypoint ne migre pas.
docker compose run --rm web python manage.py migrate
DJANGO_ALLOWED_HOSTS=athcalendar.example.org docker compose up -d
docker compose exec web python manage.py setup_bureau      # mot de passe saisi interactivement, jamais affiché
docker compose exec web python manage.py backup_database
docker compose -f compose.yaml -f compose.proxy.yaml up -d   # réseau externe PROXY_NETWORK
docker compose -f compose.yaml -f compose.caddy.yaml up -d   # Caddy 80/443, APP_DOMAIN requis
```

Secret de production — généré dans `secrets/django_secret_key.txt` (monté par
Compose en `DJANGO_SECRET_KEY_FILE=/run/secrets/django_secret_key`), jamais
affiché ni écrasé si le fichier existe déjà :

```text
python3 -c "from pathlib import Path; import os,secrets; d=Path('secrets'); d.mkdir(exist_ok=True); fd=os.open(d/'django_secret_key.txt',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); f=os.fdopen(fd,'w',encoding='utf-8'); f.write(secrets.token_urlsafe(64)); f.close()"
```

Sur Linux, le fichier doit être lisible par l'utilisateur non privilégié du
conteneur. Vérifier son UID/GID avec
`docker compose run --rm --entrypoint id web`, puis adapter le propriétaire
du fichier si nécessaire, en conservant le mode `0600`. Les permissions
`uid`/`gid`/`mode` de Compose ne sont pas appliquées aux secrets issus d'un
fichier local monté en bind.

Puis copier `.env.example` vers `.env` (sans écraser un `.env` existant :
`cp -n .env.example .env`), y renseigner `DJANGO_ALLOWED_HOSTS`,
`DJANGO_CSRF_TRUSTED_ORIGINS` et `DJANGO_TRUSTED_PROXIES`, puis
`docker compose build`, `docker compose run --rm web python manage.py migrate`,
`docker compose up -d`. Ne pas modifier `DJANGO_SECRET_KEY_FILE` : le montage
Compose est fixe.

## Proxy inverse et en-têtes transmis

- `DJANGO_TRUSTED_PROXIES` : liste explicite d'IP ou CIDR, propre à chaque
  déploiement. Jamais `0.0.0.0/0` ni plage « tout » : seuls les pairs listés
  peuvent transmettre `X-Forwarded-For`/`X-Forwarded-Proto`.
- Voie proxy hôte (`compose.proxy.yaml`) : renseigner l'IP ou le CIDR du
  proxy externe (reverse proxy Nginx/Traefik hôte ou réseau `PROXY_NETWORK`),
  par exemple `DJANGO_TRUSTED_PROXIES=172.18.0.0/16` après vérification du
  sous-réseau réel du proxy.
- Voie Caddy (`compose.caddy.yaml`) : renseigner le sous-réseau interne du
  réseau Compose partagé (vérifier avec `docker network inspect`), par
  exemple `DJANGO_TRUSTED_PROXIES=10.0.0.0/24`. Les valeurs exactes dépendent
  de l'environnement — les mesurer, ne pas deviner.
- `DJANGO_CSRF_TRUSTED_ORIGINS` : origines HTTPS exactes du site exposé.

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

- Le secret de développement est généré et persisté dans `data/.dev-secret`
  (ignoré) ; la production exige un secret fort explicite (≥ 50 caractères).
- `planning.middleware.TrustedProxyMiddleware` doit rester le premier
  middleware : `DJANGO_TRUSTED_PROXIES` liste les IP/CIDR autorisés à
  transmettre `X-Forwarded-For`/`X-Forwarded-Proto` ; les en-têtes sont
  supprimés sinon.
- CSP `script-src`/`style-src 'self'` : aucun attribut ou style inline, les
  couleurs de types passent par les classes `type-c1..c5`
  (`planning.services.types.ALLOWED_COLORS`).
- APIs publiques : `expected_event_version` (≥ 1) et `expected_version` (≥ 0)
  sont obligatoires et strictement typés ; les lots exigent une entrée
  `versions` par date `{event, registration}`.
- Les tests navigateur vivent dans `planning/tests_e2e/` (serveur isolé,
  base temporaire, captures dans `test-results/e2e/`).

## État de l'implémentation

Version applicative : `project.version` dans `pyproject.toml`, source unique
du pied de page. Versions publiées par tags Git annotés `vX.Y.Z` et journal
`CHANGELOG.md` ; procédure de mise à jour dans `RELEASES.md`.

- `Event.availability` : `available`, `unavailable`, `full`, choix manuel du
  Bureau. Les deux états fermés interdisent les mutations publiques mais
  conservent les inscriptions. Ne pas confondre ce champ avec l'ancien
  booléen `registrations_open`, volontairement ignoré par le parcours public.
- Le récapitulatif public présente seulement les inscriptions actives sur
  dates non masquées d'une période, archives incluses ; les annulations sont
  signalées et les alias fusionnés ne doublent pas une personne sur une date.
- Les données des rapports sont centralisées dans `planning/reports.py`.
  Les exports Excel/imprimables du Bureau conservent toutes les dates et
  l'historique ; les données utilisateur Excel restent du texte, jamais des
  formules ni des hyperliens implicites.
- Les nouvelles couleurs restent dans `ALLOWED_COLORS` et les classes
  `type-c1..c5`, sans CSS inline.

Tests ciblés de la version 0.2.0 :

```text
.venv/Scripts/python.exe manage.py test planning.tests.test_manual_availability planning.tests.test_reports planning.tests.test_table_exports
.venv/Scripts/python.exe -m unittest planning.tests_e2e.test_release020
```

Application complète : planning public (liste, calendrier, filtres),
inscriptions trust-based par pseudo, « Mes inscriptions », espace Bureau
(périodes, dates, groupes, pseudos, types, journal, exports CSV/JSON),
commandes `setup_bureau`, `backup_database`, `cleanup_technical_data`.
Docker/Compose/Caddy : image construite et vérifiée — `docker compose config`
valide pour les trois variantes, pile isolée Gunicorn + Caddy TLS interne
testée (sessions/CSRF, en-têtes transmis, migration explicite, sauvegarde,
persistance après recréation, restauration sur volume séparé).
