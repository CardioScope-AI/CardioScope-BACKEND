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

Chemin local par défaut : `../CardioScope-DATA-ML/artifacts/pipeline.joblib`, configurable par `MODEL_PATH`. Compose monte le dossier artifacts en lecture seule dans `/models` et utilise `/models/pipeline.joblib`. Le modèle est chargé une fois au démarrage ; redémarrer le backend après export. N’utiliser que des artefacts de confiance : joblib peut exécuter du code lors du chargement.

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

Sept tests passent : origines locales et origine personnalisée, refus d'origine/méthode, pré-vérifications JSON POST, erreurs HTTP et conservation des en-têtes, validation 422, erreur 500 journalisée avec CORS, disponibilité inchangée de health/docs sans modèle. Les routes de validation et d'erreur utilisées par les tests n'existent que dans l'application de test ; aucune route de démonstration n'est exposée en production. La validation de `/predict` restera à effectuer lors de son implémentation.
