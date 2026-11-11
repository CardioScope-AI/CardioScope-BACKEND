"""Configuration des origines autorisées, lue au démarrage."""

import os

from starlette.middleware.cors import CORSMiddleware


def with_cors(api):
    origins = [origin.strip() for origin in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",") if origin.strip()]
    if "*" in origins:
        raise ValueError("CORS_ORIGINS doit contenir des origines explicites, sans '*'.")
    # Envelopper l'application entière conserve les en-têtes CORS sur les 500.
    return CORSMiddleware(
        api,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
