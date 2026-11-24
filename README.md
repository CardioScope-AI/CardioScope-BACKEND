# CardioScope · Backend

API FastAPI de disponibilité du service et de prédiction via le pipeline Scikit-learn. La route `POST /predict` suit le schéma de profil défini dans `app/profile.py` et la proposition du [contrat](../CardioScope-APP/docs/contrat-api.md).

## Base de données et migrations (BACKEND #16)

Le [guide PostgreSQL et Alembic](docs/base-donnees.md) décrit la configuration
DB_*, les tables admin/sessions/simulations/CSRF/limitation et les commandes de
migration. Appliquer `alembic upgrade head` explicitement : le démarrage API ne
crée ni tables ni compte administrateur. L’authentification et l’enregistrement
des simulations restent à implémenter dans les tâches suivantes.

## Initialiser le compte admin (BACKEND #17)

Après les migrations, lancer `python -m app.init_admin --username admin` dans
l’environnement backend configuré. Le mot de passe est saisi sans affichage et
stocké uniquement sous forme de hash Argon2id. Aucun secret par défaut ni compte
supplémentaire n’est créé. Voir le [guide d’initialisation](docs/initialisation-admin.md)
pour Docker, le venv et le mode fichier de secret. La connexion HTTP reste à livrer en #18.

## Démarrer le serveur sous Windows (PowerShell)
Depuis ce dépôt, avec Python 3.13.7 :
```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Ouvrir http://localhost:8000/docs ; http://localhost:8000/health renvoie 503 sans pipeline. Le serveur reste accessible. Aucun entraînement au démarrage.

## Modèle et Docker
Image Python `3.13.7-slim-bookworm`, scikit-learn `1.6.1`, joblib `1.4.2`, NumPy `2.2.6`, pandas `2.2.3`, SciPy `1.15.3`, identiques au ML. Modifier les versions dans les deux dépôts ensemble et réexporter le pipeline.

Chemin local par défaut : `../CardioScope-DATA-ML/artifacts/pipeline.joblib` depuis la racine du dépôt backend, configurable par `MODEL_PATH`. Ce chemin est résolu à partir du fichier Python et ne dépend pas du dossier de lancement. Un `MODEL_PATH` relatif est également résolu depuis la racine backend ; un chemin absolu est conservé. Compose monte le dossier artifacts en lecture seule dans `/models` et utilise `/models/pipeline.joblib`. Le modèle est chargé une fois au démarrage ; redémarrer le backend après export. N’utiliser que des artefacts de confiance : joblib peut exécuter du code lors du chargement.

`/health` vérifie le chargement et la présence de la méthode `predict`, pas la qualité ni la compatibilité du schéma des entrées. Un fichier absent ou invalide donne 503 ; `/docs` reste disponible. Les instructions Compose sont dans le dépôt App.

`POST /predict` reçoit les 13 champs JSON de `PatientProfile`. La requête est validée avant l’appel au modèle ; les valeurs sont remises dans l’ordre de `COLUMNS` et transmises comme une ligne pandas nommée. Une réponse valide contient uniquement `{"prediction": 0}` ou `{"prediction": 1}`. Sans pipeline, la route répond 503 au format d’erreur commun. L’artefact ML local a été testé avec les trois profils fictifs du frontend ; répéter ce test après chaque nouvel export du modèle.

## CORS et erreurs communes (tâche 07)

Les origines autorisées par défaut sont `http://localhost:5173` et `http://127.0.0.1:5173`. Pour les remplacer, définir `CORS_ORIGINS` avant le démarrage, avec une liste séparée par des virgules et sans slash final :

```powershell
$env:CORS_ORIGINS="http://localhost:5173,https://frontend.example"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

La variable remplace la liste par défaut. Une valeur vide désactive les origines autorisées ; `*` est refusé. Le fichier `.env.example` documente la variable mais n'est pas chargé automatiquement. Redémarrer le serveur après modification. Seules les méthodes applicatives GET/POST et l'en-tête Content-Type sont autorisés ; les pré-vérifications OPTIONS sont gérées par le middleware. Les credentials ne sont pas activés. CORS ne constitue pas une authentification et une origine non autorisée n'obtient pas d'autorisation de lecture dans le navigateur.

Le proxy Vite `/api` reste compatible : il évite les appels inter-origines côté navigateur. CORS sert aux appels directs à FastAPI depuis les origines autorisées.

`app.main:api` est l'instance FastAPI où ajouter les futures routes (`@api.post(...)`). `app.main:app` est l'application ASGI enveloppée par CORS, à utiliser avec Uvicorn comme auparavant. Cette enveloppe externe conserve les en-têtes CORS même sur les erreurs 500.

`app/errors.py` centralise les gestionnaires :

- Erreurs HTTP : statut et en-têtes conservés, message public générique ; 404 `not_found`, 405 `method_not_allowed`, 503 `model_unavailable`.
- Validation FastAPI : 422 `validation_error`, avec `fields` contenant la localisation et un message générique ; pas de copie des valeurs reçues.
- Erreur inattendue : 500 `internal_error`, message public générique et exception journalisée côté serveur. Ne pas activer le mode debug pour exposer l'API.

Exemple de réponse commune :

```json
{"error":{"code":"model_unavailable","message":"Le modèle est indisponible."}}
```

`/health` conserve son format existant (`status`, `model_ready`) et ses statuts 200/503 pour rester compatible avec le frontend. Les réponses explicites `JSONResponse` ne passent pas par les gestionnaires d'exceptions. La route `/predict` lève `HTTPException(status_code=503)` quand le pipeline n'est pas disponible ; le format d'erreur commun s'applique. Le frontend utilise les codes HTTP pour ses messages et n'a pas besoin de dépendre de la structure détaillée des erreurs.

### Vérifications

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Les tests couvrent les origines locales et personnalisées, les refus CORS, les erreurs HTTP, la validation 422, la journalisation des erreurs 500, ainsi que `/health`, `/docs` et le refus 503 de `/predict` sans modèle. Les routes auxiliaires de validation et d'erreur n'existent que dans l'application de test ; aucune route de démonstration n'est exposée en production. La suite teste aussi la réponse 0/1, l’ordre des 13 colonnes, le refus des sorties invalides et, si l’artefact local existe, trois prédictions avec le vrai modèle.

## Test de l’API avec le frontend (tâche 08)

La suite `tests/test_predict.py` vérifie le contrat de `POST /predict` sans dépendre du modèle local : elle injecte un faux pipeline et contrôle les réponses, la validation, les erreurs et CORS. Un test supplémentaire charge `../CardioScope-DATA-ML/artifacts/pipeline.joblib` lorsqu’il existe et envoie les trois profils fictifs de l’interface. Il est ignoré si cet artefact est absent.

Pour tester le trajet HTTP complet sur Windows, ouvrir deux terminaux PowerShell. Dans le premier, depuis `CardioScope-BACKEND`, lancer l’API avec le modèle exporté par l’équipe ML :

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Dans le second, depuis le dépôt voisin `CardioScope-FRONTEND`, lancer Vite :

```powershell
npm run dev
```

Pour le backend local en HTTP, configurer `AUTH_COOKIE_SECURE=false` avant son
démarrage. Laisser `true` en HTTPS. Après avoir appliqué les migrations et
initialisé l'administrateur, définir ses identifiants uniquement dans
l'environnement du terminal de vérification :

```powershell
$env:CARDIOSCOPE_ADMIN_USERNAME="admin"
$env:CARDIOSCOPE_ADMIN_PASSWORD="<mot-de-passe-admin>"
```

Dans un troisième terminal, depuis `CardioScope-BACKEND`, lancer la vérification :

```powershell
.\.venv\Scripts\python.exe scripts/check_frontend_integration.py
```

Le script utilise uniquement un profil fictif. Il se connecte d'abord comme administrateur avec CSRF, puis vérifie la page frontend, `/health`, la prédiction directe et via le proxy, et la réponse 422 pour un profil invalide. Il ne journalise pas le mot de passe. Un échec termine le script avec un code non nul. Les URL backend et frontend doivent utiliser le même nom d'hôte pour partager le cookie. Le proxy Vite cible `http://127.0.0.1:8000` par défaut ; adapter `--backend` et `--frontend` si les serveurs utilisent d’autres adresses. Arrêter les serveurs avec Ctrl+C et supprimer ensuite les deux variables d'environnement du terminal.

La vérification locale du 17 novembre 2026 a été réalisée avant la protection administrateur de `/predict`. Elle n'atteste pas le nouveau parcours de connexion. Les prédictions doivent maintenant être vérifiées avec le script ci-dessus et une session administrateur.

## Lancement sous Linux (tâche 09)

### Installation native

Prérequis : Git, Python **3.13.7** avec prise en charge de `venv`, et accès réseau pour les dépendances. Vérifier `python3.13 --version` ; utiliser la même version que Docker et l'équipe ML. Ne pas recopier le `.venv` Windows : créer un environnement propre sous Linux.

Depuis le dossier contenant les dépôts voisins :

```sh
git clone https://github.com/CardioScope-AI/CardioScope-BACKEND.git
cd CardioScope-BACKEND
python3.13 --version
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
sh scripts/run.sh
```

Ouvrir `http://127.0.0.1:8000/docs`. Arrêter le serveur avec Ctrl+C. Le script utilise directement `.venv/bin/python`, sans activation, et démarre sans rechargement automatique. Il se place dans le dépôt avant de lancer Uvicorn et accepte les chemins contenant des espaces. Les scripts shell sont conservés avec des fins de ligne LF.

Pour le développement avec rechargement :

```sh
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Modèle et configuration

Le backend démarre même sans le dépôt ML ou sans artefact : `/health` renvoie alors 503. Il n'entraîne et ne télécharge aucun modèle. Pour un modèle exporté par le responsable ML, définir un chemin accessible et redémarrer :

```sh
export MODEL_PATH="/chemin/vers/pipeline.joblib"
export CORS_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"
sh scripts/run.sh
```

Les variables sont lues au démarrage ; `.env.example` est une documentation, pas un fichier chargé automatiquement. Vérifier la casse exacte des noms sous Linux, les droits de lecture et les versions des dépendances avant de charger l'artefact.

Pour une démonstration depuis un autre poste du même réseau :

```sh
HOST=0.0.0.0 PORT=8000 sh scripts/run.sh
```

Depuis l'autre poste, utiliser `http://ADRESSE_IP_DU_SERVEUR:8000/docs`. Si le frontend appelle directement cette API, configurer `CORS_ORIGINS` avec l'origine exacte du frontend vue dans le navigateur (protocole, hôte et port, sans chemin). `localhost` désigne toujours le poste qui exécute le navigateur. Vérifier que le port 8000 est autorisé par la configuration réseau du laboratoire. Ce lancement est destiné à la démonstration, pas à un hébergement public de production.

### Vérification dans un second terminal

```sh
curl -i http://127.0.0.1:8000/health
curl -I http://127.0.0.1:8000/docs
curl -i http://127.0.0.1:8000/route-inexistante
curl -i -X OPTIONS http://127.0.0.1:8000/predict -H 'Origin: http://localhost:5173' -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: content-type'
```

Attendus : health 503 sans modèle (200 avec un artefact chargé exposant `predict`), docs 200, route inexistante 404 au format commun, pré-vérification CORS 200 avec l'origine autorisée. Un POST `/predict` avec un profil valide renvoie 503 sans modèle ou la classe prédite avec un artefact chargé.

Tests automatisés :

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

### Alternative Docker Linux

Avec Docker Engine démarré, depuis le dépôt backend :

```sh
docker build -t cardioscope-backend:dev .
docker run --rm --name cardioscope-backend -p 127.0.0.1:8000:8000 cardioscope-backend:dev
```

Ce lancement local sans montage fonctionne sans modèle. L'image utilise Python 3.13.7 et les versions figées de `requirements.txt`. Pour monter le dossier d'artefacts, depuis le dépôt backend avec le dépôt ML voisin :

```sh
docker run --rm --name cardioscope-backend -p 127.0.0.1:8000:8000 --mount "type=bind,source=$(cd ../CardioScope-DATA-ML/artifacts && pwd),target=/models,readonly" -e MODEL_PATH=/models/pipeline.joblib cardioscope-backend:dev
```

Pour démarrer frontend et backend ensemble, suivre le README de `CardioScope-APP` et lancer `docker compose up --build` depuis ce dépôt APP. Sa configuration actuelle publie les ports sur `127.0.0.1` : ils restent accessibles uniquement depuis la machine hôte.

### ?tat des v?rifications de la t?che 09

- Les huit tests automatis?s passent sous Windows, dont le contr?le du chemin du mod?le depuis un autre dossier de travail.
- Un v?ritable serveur Uvicorn a ?t? lanc? depuis un dossier temporaire avec `--app-dir` : `/docs` r?pond 200 et `/health` r?pond 503 avec le JSON attendu sans mod?le. Le processus de v?rification a ?t? arr?t?.
- La syntaxe de `scripts/run.sh` est valid?e par `sh -n` dans Ubuntu via WSL.
- Le lancement complet natif sous Linux avec Python 3.13.7 et ses d?pendances reste ? v?rifier sur le poste Linux. Aucun succ?s de d?ploiement Linux complet n'est revendiqu?.
- Le moteur Docker Desktop Linux n'?tait pas d?marr? : l'image et le lancement Docker n'ont pas pu ?tre valid?s pendant cette t?che.

## Connexion administrateur (#18)

Routes login, CSRF et identité : voir [le guide](docs/connexion-admin.md).
`POST /admin/logout` révoque la session active, supprime ses contextes CSRF et
expire les cookies. `/predict`, `/model-info` et `/admin/me` exigent une session
administrateur valide ; `/health`, `/admin/csrf` et `/admin/login` restent publics.
Le frontend actuel n'implémente pas encore le parcours de connexion : ses appels
à `/predict` recevront 401 jusqu'à cette intégration.

## Informations du modèle (#24)

`GET /model-info` est réservé à l’administrateur. Voir [le guide de la fiche et de
la release ML](docs/informations-modele.md). Les exports historiques sans manifeste
restent utilisables pour prédire, mais ne fournissent pas de fiche vérifiée (503).
