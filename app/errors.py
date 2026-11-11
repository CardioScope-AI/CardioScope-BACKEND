"""Réponses communes aux erreurs, utilisables par les futures routes."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)

HTTP_ERRORS = {
    400: ("bad_request", "Requête incorrecte."),
    404: ("not_found", "Ressource introuvable."),
    405: ("method_not_allowed", "Méthode non autorisée."),
    503: ("model_unavailable", "Le modèle est indisponible."),
}


def error_response(status, code, message, *, fields=None, headers=None):
    error = {"code": code, "message": message}
    if fields is not None:
        error["fields"] = fields
    return JSONResponse(status_code=status, content={"error": error}, headers=headers)


def register_error_handlers(api: FastAPI):
    @api.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code, message = HTTP_ERRORS.get(
            exc.status_code, ("http_error", "La requête n’a pas pu être traitée.")
        )
        # Ne pas exposer automatiquement exc.detail, qui peut être technique.
        return error_response(exc.status_code, code, message, headers=exc.headers)

    @api.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [
            {"location": list(error["loc"]), "message": "Valeur absente ou invalide."}
            for error in exc.errors()
        ]
        # Exclure les valeurs reçues et les contextes internes de Pydantic.
        return error_response(
            422, "validation_error", "Les données envoyées sont invalides.", fields=fields
        )

    @api.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.error("Erreur interne sur %s %s", request.method, request.url.path,
                     exc_info=(type(exc), exc, exc.__traceback__))
        return error_response(500, "internal_error", "Une erreur interne est survenue.")
