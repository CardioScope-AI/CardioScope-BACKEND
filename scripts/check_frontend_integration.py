"""Vérifier une prédiction réelle directement et via le proxy Vite du frontend.

Lancer le backend sur 127.0.0.1:8000 et le frontend sur 127.0.0.1:5173
avant d'exécuter ce script. Seuls des profils fictifs sont envoyés.
"""

import argparse
import os
from urllib.parse import urlsplit

import httpx


PROFILE = {
    "age": 63, "sex": 1, "cp": 1, "trestbps": 145, "chol": 233,
    "fbs": 1, "restecg": 2, "thalach": 150, "exang": 0,
    "oldpeak": 2.3, "slope": 3, "ca": 0, "thal": 6,
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    parser.add_argument("--frontend", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    backend = args.backend.rstrip("/")
    frontend = args.frontend.rstrip("/")
    username = os.environ.get("CARDIOSCOPE_ADMIN_USERNAME", "")
    password = os.environ.get("CARDIOSCOPE_ADMIN_PASSWORD", "")
    require(username and password,
            "Définir CARDIOSCOPE_ADMIN_USERNAME et CARDIOSCOPE_ADMIN_PASSWORD pour le test protégé.")
    backend_parts, frontend_parts = urlsplit(backend), urlsplit(frontend)
    require(backend_parts.hostname == frontend_parts.hostname,
            "Backend et frontend doivent utiliser le même hôte pour partager le cookie de session.")
    frontend_origin = f"{frontend_parts.scheme}://{frontend_parts.netloc}"

    with httpx.Client(timeout=15) as client:
        page = client.get(frontend)
        require(page.status_code == 200 and "text/html" in page.headers.get("content-type", ""),
                "La page frontend n'est pas disponible.")

        direct_health = client.get(f"{backend}/health")
        proxy_health = client.get(f"{frontend}/api/health")
        expected_health = {"status": "ready", "model_ready": True}
        require(direct_health.status_code == 200 and direct_health.json() == expected_health,
                "Le backend n'a pas chargé le modèle.")
        require(proxy_health.status_code == 200 and proxy_health.json() == expected_health,
                "Le proxy Vite ne transmet pas correctement /api/health.")

        csrf = client.get(f"{backend}/admin/csrf")
        require(csrf.status_code == 200 and csrf.json().get("csrf_token"),
                "Impossible d'obtenir le jeton CSRF de connexion.")
        login = client.post(f"{backend}/admin/login",
                            json={"username": username, "password": password},
                            headers={"Origin": frontend_origin,
                                     "X-CSRF-Token": csrf.json()["csrf_token"]})
        require(login.status_code == 200,
                f"Connexion administrateur échouée : HTTP {login.status_code}.")

        direct = client.post(f"{backend}/predict", json=PROFILE)
        proxied = client.post(f"{frontend}/api/predict", json=PROFILE)
        require(direct.status_code == 200 and direct.json() in
                ({"prediction": 0}, {"prediction": 1}),
                f"La prédiction directe a échoué : HTTP {direct.status_code}.")
        require(proxied.status_code == 200 and proxied.json() == direct.json(),
                f"La prédiction via le frontend diffère : HTTP {proxied.status_code}.")

        invalid = dict(PROFILE, age="valeur-invalide")
        rejected = client.post(f"{frontend}/api/predict", json=invalid)
        require(rejected.status_code == 422 and
                rejected.json().get("error", {}).get("code") == "validation_error",
                "Le proxy ne transmet pas la réponse de validation 422.")
        require("valeur-invalide" not in rejected.text,
                "La réponse de validation révèle la valeur envoyée.")

    print("Page frontend : HTTP 200")
    print("Santé backend et proxy : modèle prêt")
    print(f"Prédiction directe et via /api identiques : {direct.json()}")
    print("Profil invalide via /api : HTTP 422, sans valeur renvoyée")
    print("Intégration HTTP frontend/backend réussie.")


if __name__ == "__main__":
    main()
