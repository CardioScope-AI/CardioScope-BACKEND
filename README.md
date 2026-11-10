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
