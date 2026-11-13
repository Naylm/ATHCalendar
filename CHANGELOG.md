# Journal des versions

## 0.2.0 - 2026-11-13

- Disponibilité des dates pilotée par le Bureau : Ouverte, Indisponible ou
  Complète. Les états manuels verrouillent les modifications publiques sans
  supprimer les inscriptions ; la réouverture reste explicite.
- Contrôles côté serveur, versions concurrentes et historique des changements.
  Les anciennes dates et inscriptions sont conservées par la migration 0005.
- Nouvel onglet public « Par personne », filtré par période, y compris les
  archives. Il présente les inscriptions actives des dates visibles, pas les
  présences effectives. Les pseudos fusionnés sont regroupés.
- Export Excel structuré en deux tableaux : planning et historique des
  inscriptions. Dates Excel, filtres, en-têtes figés et protection contre les
  formules issues des données saisies.
- Tableau imprimable depuis le Bureau, également enregistrable en PDF.
  Les exports Bureau identifient les dates masquées et conservent l'historique.
- Choix des couleurs par pastilles nommées ; la couleur choisie apparaît
  réellement sur les types du planning et suit les changements en direct.
- Numéro de version visible dans l'application, issu de `pyproject.toml`.
  Procédure de publication et de mise à jour documentée dans `RELEASES.md`.

## 0.1.0

État initial de référence : commit `97cbf82`.

- Planning permanent par périodes, inscriptions par pseudo et espace Bureau.
- Dates générées, exceptions, archives, visibilité et types de parties.
- Descriptions des types, typographie mobile et palette olive/sable.
- Protection contre le zoom accidentel au double-tap, avec zoom à deux doigts
  conservé.
- Déploiement Docker, SQLite persistant, sauvegardes et prise en charge HTTPS
  derrière un proxy de confiance.
