# Journal des versions

## 0.2.5

- Note de date plus compacte : champ vide réduit à une ligne discrète,
  boutons OK/Annuler affichés uniquement pendant une modification,
  exemple explicite en espace réservé (les notes enregistrées restent
  préremplies).

## 0.2.4

- Note commune par date, éditable directement sur le planning public
  (1000 caractères, versionnée) : « besoin d'un/une expérimenté(e) », etc.
- Mêmes verrous que les autres actions publiques : date passée, annulée,
  archivée, masquée ou fermée par le Bureau reste en lecture seule.
- Le brouillon local est protégé en cas de changement concurrent : la
  synchronisation conserve la saisie, « Annuler » recharge la note enregistrée.

## 0.2.3

- Le code d'un type de partie accepte lettres accentuées, chiffres, points,
  tirets et tirets bas (ex. `event.spé`) ; la création affiche désormais les
  erreurs en clair dans le formulaire conservé.
- Suppression définitive des types de parties inutilisés par le Bureau,
  avec recopie du code et confirmation. Tout type référencé par une date —
  passée, masquée, annulée ou archivée — reste protégé ; Désactiver permet
  de le retirer des nouveaux choix sans toucher à l'historique.

## 0.2.2

- Suppression définitive d'une période par le Bureau, avec page de confirmation :
  recopie exacte du libellé, case de confirmation, version et révision attendues.
- Les périodes archivées doivent être rouvertes avant suppression ; la cascade
  retire la période, ses dates et ses inscriptions. Pseudos, types, autres
  périodes et journal du Bureau sont conservés.

## 0.2.1

- Options remontées en haut de la gestion des périodes.
- Retrait de l'onglet et du récapitulatif public « Par personne ».
- Exports Bureau et inscriptions conservés.

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
