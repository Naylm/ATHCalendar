# Versions et mises à jour

## Source unique du numéro

Le champ `project.version` de `pyproject.toml` est la seule source du numéro
applicatif. Django le lit au démarrage et l'affiche dans le pied de page.

Convention `MAJEURE.MINEURE.CORRECTIF` :

- Correctif compatible : `0.2.0` vers `0.2.1`.
- Nouvelles fonctionnalités compatibles : `0.2.0` vers `0.3.0`.
- Changement incompatible : nouvelle version majeure, avec procédure adaptée.

Chaque publication comprend une entrée dans `CHANGELOG.md` et un tag Git
annoté `vX.Y.Z`. Un tag publié désigne un commit immuable : ne pas le déplacer,
le supprimer ni utiliser de force-push. Un correctif reçoit un nouveau numéro.
Le tag `v0.1.0` référence l'état de production initial `97cbf82`.

## Avant publication

1. Mettre à jour `project.version` et le journal.
2. Vérifier les migrations et les tests documentés dans `AGENTS.md`, ainsi que
   les tests navigateur des fonctionnalités modifiées.
3. Relire le diff et vérifier qu'aucun secret, base, export ou rapport local
   n'est suivi par Git.
4. Créer le commit puis le tag annoté, et pousser la branche et ce tag.
5. Vérifier que le commit distant et la version affichée correspondent.

## Mise à jour du serveur existant

Dans le terminal SSH du serveur, pour l'installation `/opt/athcalendar` :

```bash
cd /opt/athcalendar &&
docker compose exec web python manage.py backup_database &&
git pull --ff-only &&
docker compose build &&
docker compose run --rm web python manage.py migrate &&
docker compose up -d --wait --wait-timeout 90
```

Arrêter la procédure dès qu'une commande échoue. La sauvegarde est créée avec
l'API de sauvegarde SQLite dans le volume persistant des sauvegardes, avant
toute modification de schéma.

**Toujours migrer avant de redémarrer le nouveau code.** La version 0.2.0 ajoute
la colonne `planning_event.availability`. Sans migration, le nouveau code
affiche « no such column ». La migration 0005 conserve les dates et
inscriptions existantes ; leur disponibilité initiale reste Ouverte.

Cette procédure conserve `.env`, le secret, les volumes et le proxy Nginx.
Ne pas utiliser `docker compose down -v` et ne pas supprimer les volumes.
Une mise à jour habituelle ne nécessite pas de recréer le réseau Docker.
Si le réseau est recréé, vérifier la nouvelle adresse réelle du proxy et
adapter `DJANGO_TRUSTED_PROXIES` : ne jamais autoriser tous les réseaux.

Après la mise à jour :

```bash
docker compose ps
curl -fsS https://planningath.agartha.cc/healthz
```

Vérifier ensuite le numéro du pied de page, la connexion Bureau, un changement
de disponibilité, les couleurs et les deux exports. Actualiser les onglets
déjà ouverts sur les téléphones.

Conserver également une copie des sauvegardes hors du serveur. Revenir à un
ancien tag Git ne restaure pas les données : ne pas annuler de migrations ou
remplacer la base automatiquement. Toute restauration doit être planifiée et
validée séparément, après sauvegarde de l'état courant.

## Développement local

Après une nouvelle migration, depuis le dépôt Windows :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py backup_database
.\.venv\Scripts\python.exe manage.py migrate
```

Les sauvegardes, les données et les secrets restent ignorés par Git.
