# Initialiser l’administrateur — BACKEND #17

Cette commande crée explicitement l’unique compte admin après les migrations.
Elle ne s’exécute pas au démarrage API et n’ajoute aucune inscription publique.

## Fonctionnement

`python -m app.init_admin --username admin` demande le mot de passe deux fois,
sans affichage. Un environnement sans saisie masquée est refusé. Le nom accepte
1 à 100 lettres ASCII, chiffres, points, tirets ou underscores. Le mot de passe
contient 15 à 128 caractères sur une seule ligne ; Unicode et espaces sont
conservés. Il n’est ni tronqué, ni trimé, ni normalisé. Choisir un secret unique.

Seul un hash Argon2id avec sel aléatoire est stocké dans password_hash : 64 MiB,
3 itérations, parallélisme 4. Deux hachages du même mot de passe diffèrent.
Les fonctions de hachage et vérification sont partagées dans `app/passwords.py`
pour la future connexion (#18). Aucun mot de passe ou hash n’est affiché par la commande.

Si un admin existe, même inactif, la commande refuse et ne modifie ni compte,
ni mot de passe, ni sessions. La contrainte SQL singleton reste la garantie en cas
de créations concurrentes. Cette commande n’est pas une réinitialisation de mot de passe.

## Dans Docker

Depuis APP, avec `.env` configuré et les sources de cette branche backend :

```sh
docker compose build backend
docker compose up -d --wait db
docker compose run --rm --no-deps backend python -m alembic upgrade head
docker compose run --rm --no-deps backend python -m app.init_admin --username admin
```

Utiliser un terminal interactif pour les deux saisies. Succès : code de sortie 0
et confirmation générique. Échec : code 1, sans traceback SQL ni secret. Une
deuxième exécution refuse la création ; elle ne remplace pas le compte existant.

## Depuis le venv

Installer les nouvelles dépendances et exporter DB_HOST, DB_PORT, DB_NAME, DB_USER
et DB_PASSWORD selon le [guide PostgreSQL](base-donnees.md). Depuis BACKEND :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.init_admin --username admin
```

Sous Linux, utiliser `.venv/bin/python`. La base Docker n’étant pas publiée sur
l’hôte par défaut, privilégier la commande dans Docker pour le déploiement.

## Exécution sans terminal interactif

L’option `--password-file /run/secrets/admin-password` lit un fichier UTF-8
monté en lecture seule. Ce fichier contient exactement un mot de passe sur une
ligne avec une éventuelle fin de ligne LF/CRLF. Seule cette fin de ligne est
retirée ; les espaces du mot de passe restent présents. Taille maximale lue :
1024 octets, puis la politique 15–128 caractères est vérifiée.

Exemple : ajouter au `docker compose run` l’option
`--volume "/chemin/prive/admin-password:/run/secrets/admin-password:ro"`, avant
le nom du service, puis transmettre `--password-file /run/secrets/admin-password`
à la commande Python. Le fichier doit rester hors du dépôt, accessible uniquement
à l’exploitant et supprimé après usage. Il ne fait partie ni de l’image ni de Git.
Ne jamais passer le mot de passe lui-même en argument, dans un ticket ou dans le chat.

## Vérification et limites

Les tests vérifient le sel aléatoire, la vérification correcte/incorrecte du mot
de passe, les entrées invalides, la saisie masquée et la lecture de secret sans
révéler son contenu. Le test PostgreSQL opt-in vérifie la création effective du
compte haché et le refus d’une réinitialisation sur une base temporaire.

Ces contrôles PostgreSQL ont été exécutés avec succès, ainsi que la suite backend
existante. Aucun compte de démonstration n’est conservé dans la base CardioScope.
Les sept tests ciblés couvrent aussi le refus d’arguments ambigus sans afficher
un éventuel secret saisi par erreur.

L’image locale #17 a également été construite et démarrée. Face aux timeouts
des téléchargements, cette construction a réutilisé l’image #16 validée, puis
ajouté les wheels Linux Argon2 et le code de cette branche. Les versions de
requirements.txt correspondent exactement aux paquets installés ; pip check,
la commande --help et la vérification native du hachage Argon2id réussissent.
Le backend est healthy, Alembic indique 0001 (head) et Swagger répond 200 sur
http://localhost:8001/docs. Cette vérification n’a créé aucun compte admin réel.

Le vrai compte est créé par l’exploitant avec son propre secret, pas avec les
identifiants fictifs des tests. La présence du compte n’implémente pas encore
`/admin/login`, les cookies, CSRF, logout ou la protection des routes.

Références : [argon2-cffi](https://argon2-cffi.readthedocs.io/en/stable/howto.html),
[OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
