# CardioScope · Backend

API, traitement des requêtes, chargement du modèle entraîné et futures prédictions. FastAPI minimal ; `/predict` sera implémenté après validation du [contrat](../CardioScope-APP/docs/contrat-api.md).

## Démarrage Windows (PowerShell)
Depuis ce dépôt, avec Python 3.13.7 :
```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Ouvrir http://localhost:8000/docs ; http://localhost:8000/health renvoie 503 sans pipeline. Le serveur reste accessible. Aucun entraînement au démarrage.

## Modèle et Docker
Image Python `3.13.7-slim-bookworm`, scikit-learn `1.6.1`, joblib `1.4.2`, NumPy `2.2.6`, SciPy `1.15.3`, identiques au ML. Modifier les versions dans les deux dépôts ensemble et réexporter le pipeline.

Chemin local par défaut : `../CardioScope-DATA-ML/artifacts/pipeline.joblib` depuis la racine du dépôt backend, configurable par `MODEL_PATH`. Ce chemin est résolu à partir du fichier Python et ne dépend pas du dossier de lancement. Un `MODEL_PATH` relatif est également résolu depuis la racine backend ; un chemin absolu est conservé. Compose monte le dossier artifacts en lecture seule dans `/models` et utilise `/models/pipeline.joblib`. Le modèle est chargé une fois au démarrage ; redémarrer le backend après export. N’utiliser que des artefacts de confiance : joblib peut exécuter du code lors du chargement.

`/health` vérifie le chargement et la présence de la méthode `predict`, pas la qualité ni la compatibilité du schéma des entrées. Un fichier absent ou invalide donne 503 ; `/docs` reste disponible. Les instructions Compose sont dans le dépôt App.

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

`/health` conserve son format existant (`status`, `model_ready`) et ses statuts 200/503 pour rester compatible avec le frontend. Les réponses explicites `JSONResponse` ne passent pas par les gestionnaires d'exceptions. Pour une future route de prédiction, lever `HTTPException(status_code=503)` utilise le format commun. `/predict` n'est pas ajouté dans cette tâche et le format reste à coordonner avec son responsable. Le frontend actuel utilise les codes HTTP pour ses messages et n'a pas besoin de dépendre de la structure détaillée des erreurs.

### Vérifications

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Seize tests passent : origines locales et origine personnalisée, refus d'origine/méthode, pré-vérifications JSON POST, erreurs HTTP et conservation des en-têtes, validation 422, erreur 500 journalisée avec CORS, disponibilité inchangée de health/docs sans modèle. Les routes de validation et d'erreur utilisées par les tests n'existent que dans l'application de test ; aucune route de démonstration n'est exposée en production. La validation de `/predict` restera à effectuer lors de son implémentation.

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

Attendus : health 503 sans modèle (200 avec un artefact chargé exposant `predict`), docs 200, route inexistante 404 au format commun, pré-vérification CORS 200 avec l'origine autorisée. Une pré-vérification réussie ne prouve pas que `/predict` existe : cette route n'est pas encore implémentée dans ce dépôt.

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

- Les seize tests automatisés passent sous Windows, dont le contr?le du chemin du mod?le depuis un autre dossier de travail.
- Un v?ritable serveur Uvicorn a ?t? lanc? depuis un dossier temporaire avec `--app-dir` : `/docs` r?pond 200 et `/health` r?pond 503 avec le JSON attendu sans mod?le. Le processus de v?rification a ?t? arr?t?.
- La syntaxe de `scripts/run.sh` est valid?e par `sh -n` dans Ubuntu via WSL.
- Le lancement complet natif sous Linux avec Python 3.13.7 et ses d?pendances reste ? v?rifier sur le poste Linux. Aucun succ?s de d?ploiement Linux complet n'est revendiqu?.
- Le moteur Docker Desktop Linux n'?tait pas d?marr? : l'image et le lancement Docker n'ont pas pu ?tre valid?s pendant cette t?che.
