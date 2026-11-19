# Base PostgreSQL et migrations — BACKEND #16

## Rôle des fichiers

- `app/database.py` construit l’URL avec SQLAlchemy URL.create à partir des
  variables DB_*, sans concaténer le mot de passe. La connexion est paresseuse,
  les paramètres SQL ne sont pas affichés dans les erreurs SQLAlchemy et les
  connexions demandent UTC. `get_session` fournit une session par requête ;
  l’appelant doit confirmer explicitement ses écritures par commit.
- `app/models.py` définit les cinq tables ci-dessous.
- `alembic.ini` et `migrations/` versionnent la création et les évolutions du schéma.
  Le schéma n’est jamais créé par create_all ou automatiquement au démarrage API.

| Table | Usage et garanties |
| --- | --- |
| admins | UUID, nom unique, hash du mot de passe, actif ; colonne singleton obligatoirement vraie et unique, donc au plus un admin même lors d’insertions concurrentes |
| admin_sessions | Admin référencé, hash du jeton unique, dates de création, expiration, dernière activité et révocation |
| simulations | UUID, date, classe 0/1, version du modèle, SHA-256, 13 colonnes du profil typées et obligatoires |
| csrf_contexts | Hash du cookie de liaison, hash du token CSRF, expiration, lien facultatif vers une session |
| login_attempt_windows | Clé de limitation hachée, début de fenêtre, compteur, blocage et expiration |

Les sessions et contextes liés sont supprimés en cascade si leur parent disparaît.
Les simulations ne dépendent pas du compte : une suppression de compte ne doit
pas supprimer l’historique sans la procédure de conservation prévue.
Les contextes anonymes ont session_id NULL et ne donnent aucun accès admin.

Les 13 entrées sont stockées en colonnes plutôt qu’en JSON. Les futures routes
reconstruiront l’objet `profile` du contrat à partir de ces noms ; ne pas changer
le corps HTTP pour reproduire les colonnes internes. Aucune borne clinique
supplémentaire n’est inventée : les catégories suivent `PatientProfile`, les
mesures décimales excluent NaN et les infinis. model_version est une chaîne
non vide d’au plus 200 caractères ; les empreintes sont 64 hexadécimaux minuscules.
L’index historique trie created_at puis id ; les expirations et clés étrangères
utiles au nettoyage sont indexées.

## Validation réalisée le 19 novembre 2026

La migration 0001 a été appliquée à PostgreSQL 17 dans Docker. Les cinq tables
applicatives sont présentes et vides, ainsi que la table technique alembic_version.
Le contrôle Alembic ne détecte aucune différence avec les modèles Python.

Les trois tests de configuration et d’intégration PostgreSQL ont réussi. Le test
d’intégration crée une base temporaire portant un UUID, vérifie les insertions,
les contraintes (dont un seul admin, classes, catégories, hash, valeurs finies et
clés étrangères), les cascades, upgrade répété, downgrade puis upgrade. Cette
base de test est supprimée ; aucun downgrade n’a été appliqué à CardioScope.

La suite backend complète compte 24 tests : 23 réussis, un test PostgreSQL ignoré
par défaut et exécuté avec succès séparément. L’API et le pipeline existants sont
donc aussi contrôlés. Les commandes Alembic ont été exécutées depuis le venv
Windows vers PostgreSQL Docker via un port local temporaire ; la publication
temporaire de la base est retirée après les vérifications.
Le Dockerfile est préparé avec les migrations, mais sa nouvelle image n’a pas
encore été reconstruite dans cette vérification.

## Paramètres de connexion

Installer `requirements.txt` : SQLAlchemy, Alembic et psycopg binaire sont inclus.
La configuration attend DB_HOST, DB_NAME, DB_USER, DB_PASSWORD ; DB_PORT vaut 5432
par défaut. `.env.example` est un modèle : le code ne charge pas automatiquement
`.env`. Exporter les variables avant de lancer une commande hors Compose.
Ne jamais placer de mot de passe dans alembic.ini, Git ou un argument de commande.

Compose APP fournit ces variables, avec DB_HOST=db sur son réseau privé. La base
n’est pas publiée sur l’hôte par défaut : `db` ne se résout pas depuis Windows.
Pour un outil local, utiliser une publication temporaire limitée à 127.0.0.1 et
adapter DB_HOST/DB_PORT ; ne pas ouvrir PostgreSQL sur le réseau public.

## Appliquer la migration

Depuis `CardioScope-APP`, après récupération de cette branche backend :

```sh
docker compose build backend
docker compose up -d --wait db
docker compose run --rm --no-deps backend python -m alembic upgrade head
docker compose run --rm --no-deps backend python -m alembic current
docker compose up -d backend
```

La migration initiale porte la révision `0001`. Répéter upgrade head ne recrée pas
les tables. La table technique alembic_version garde la révision appliquée. La
migration ne crée aucun administrateur ni simulation ; BACKEND #17 initialisera
le compte, sans mot de passe par défaut. Vérifier la base cible et sauvegarder une
base contenant déjà des données avant toute future migration.

Sur l’hôte, depuis BACKEND avec les variables exportées et `.venv` installé :

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
```

Sous Linux, utiliser `.venv/bin/python`. Pour une nouvelle évolution : modifier
les modèles, générer une révision avec `python -m alembic revision --autogenerate
-m "description"`, relire impérativement le SQL proposé puis tester sur une base
temporaire. Conserver les anciennes migrations ; ne pas réécrire une révision
déjà appliquée par l’équipe.

`python -m alembic downgrade base` supprime les cinq tables et leurs données :
réserver cette commande à une base de test jetable. Le test automatique ci-dessous
effectue ce contrôle uniquement sur sa propre base temporaire.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Les tests PostgreSQL sont désactivés par défaut. Pour les activer, exporter la
configuration DB_* d’un serveur de développement et CARDIOSCOPE_TEST_POSTGRES=1,
puis lancer `python -m unittest discover -s tests -p test_database.py -v`.
Le compte de test doit pouvoir créer/supprimer une base. Le test crée un nom
`cardioscope_test_<uuid>`, applique les migrations, contrôle les contraintes et
le cycle downgrade/upgrade, puis supprime uniquement cette base. Il ne fait pas
de downgrade sur DB_NAME. Ne pas utiliser un compte de production pour ces tests.

## Périmètre restant

Cette tâche prépare le stockage. Elle n’implémente ni l’initialisation admin (#17),
ni login/CSRF et limitation effective (#18), ni expiration/logout et protection des
routes (#19/#20), ni l’écriture et la lecture de l’historique (#21–#23).
Les colonnes de hash ne hachent pas automatiquement les valeurs : les services
d’authentification devront générer des jetons sûrs et ne stocker que leurs hash.
La clé des compteurs devra être construite de façon sûre, par exemple avec un
HMAC serveur, sans stocker les adresses réseau dans les simulations.

Les dates d’expiration et index ne déclenchent aucune purge automatiquement.
L’application doit encore implémenter les règles APP #17 (90 jours de simulations,
14 jours de logs et 7 jours de sauvegardes) et la planification de purge.
Les routes actuelles et /health conservent leur comportement ; /health ne vérifie
pas la disponibilité de PostgreSQL et ne prouve pas que les migrations sont appliquées.

Références : [SQLAlchemy URL.create](https://docs.sqlalchemy.org/en/20/core/engines.html#creating-urls-programmatically),
[Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html),
[psycopg](https://www.psycopg.org/psycopg3/docs/basic/install.html).
