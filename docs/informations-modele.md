# Informations du modèle — BACKEND #24

## Route

`GET /model-info` nécessite une session administrateur valide. Sans cookie : 401
unauthenticated avant toute lecture de la fiche. La réponse respecte le contrat
APP : schema_version, model_version, artifact_sha256, model_name, dataset, features,
target, evaluation et limitations. Les métriques CV et test restent séparées ; une
métrique absente est null, jamais zéro inventé. Réponse Cache-Control: no-store.
Aucun chemin de fichier, contenu joblib ni secret n’est exposé.

## Chargement cohérent

Au démarrage, le backend lit un instantané des fichiers voisins du MODEL_PATH :

- le modèle pointé par MODEL_PATH ;
- metadata.json et test_evaluation.json, aux formats historiques du dépôt ML ;
- release.json : manifeste de liaison décrit ci-dessous.

Les empreintes sont vérifiées sur les octets exacts qui seront lus/désérialisés.
Le backend ne relit pas une fiche indépendante à chaque requête. Une mise à jour
des fichiers nécessite un redémarrage pour charger la nouvelle release.

Le manifeste constitue l’interface d’intégration avec DATA-ML #22, qui produit
désormais la release rf-cleveland-v1 après vérification des rapports. Sélectionner
son dossier complet dans MODEL_PATH ; les exports historiques à la racine ne
fournissent toujours pas cette liaison. La validation de production reste distincte.

```json
{
  "schema_version": 1,
  "model_version": "IDENTIFIANT_IMMUABLE_DE_LA_RELEASE",
  "artifact_sha256": "64_caracteres_hexadecimaux_minuscules_a_remplacer",
  "metadata_sha256": "64_caracteres_hexadecimaux_minuscules_a_remplacer",
  "test_evaluation_sha256": "64_caracteres_hexadecimaux_minuscules_a_remplacer",
  "limitations": ["Démonstrateur pédagogique ; ceci ne constitue pas un diagnostic médical."]
}
```

Les empreintes illustratives ci-dessus ne sont volontairement pas des hash valides.
Le producteur ML doit calculer les vrais SHA-256 après export, valider la cohérence
des données/cible/évaluation, puis publier l’ensemble dans un répertoire de release
immuable. Ne pas modifier des fichiers d’une release en place. Les empreintes
garantissent une liaison entre fichiers, pas l’authenticité de leur auteur : charger
uniquement un joblib provenant d’une source de confiance.

Contrôles backend : ordre exact des 13 variables, dataset Cleveland, cible num,
effectifs cohérents, valeurs de métriques finies entre 0 et 1, matrice entière
non négative dont la somme égale le test. Le pipeline doit contenir un classifier
RandomForestClassifier ajusté avec classes 0/1 et les 13 noms d’entrée attendus.
Une validation backend ne remplace pas la recette ML des artefacts.

## Compatibilité et indisponibilité

- Export historique sans release.json : prédiction existante conservée ;
  /model-info authentifié renvoie 503 model_unavailable. Aucun identifiant inventé.
- Manifeste présent mais invalide, fichier absent, hash différent ou données
  incohérentes : release rejetée au démarrage, modèle et fiche indisponibles.
- Base indisponible pendant l’authentification : 503 storage_unavailable.

Le statut /health existant porte sur la capacité de prédiction, pas sur la
disponibilité de la fiche ni de PostgreSQL. Les tâches ML restent nécessaires
pour fournir une release vérifiée et obtenir le succès réel de /model-info.

## Tests

`python -m unittest discover -s tests -p test_model_info.py -v`

Les tests utilisent un petit modèle synthétique, des scores fictifs et un
répertoire temporaire. Ils vérifient 200, refus anonyme, legacy 503, empreintes,
validation, absence de chemins et stabilité de la fiche chargée. Ils ne créent
aucune release du vrai modèle et ne modifient pas ses artefacts.

### Intégration Docker réelle, 20 novembre 2026

La release rf-cleveland-v1 a été sélectionnée via MODEL_PATH dans APP, et l’image
backend a été actualisée. Une recette HTTP sur la même image et la vraie release,
avec une base PostgreSQL et un admin temporaires, a validé CSRF, login, /me et
/model-info authentifié en 200. La route anonyme renvoie 401. Version et hash
correspondent au manifeste ; accuracy de test 0,8689 et réponse no-store.
Compte et base temporaires ont été supprimés. Le compte réel n’a pas été modifié.
Le rapport et le script reproductible sont dans APP :
docs/verification-model-info-docker.json et scripts/verify_model_info_release.py.
Cela ne valide pas encore le formulaire frontend ni HTTPS en production.
