import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.auth import (
    AuthError,
    ErrorEnvelope,
    auth_database,
    check_csrf,
    register_auth,
    require_admin,
    utcnow,
)
from app.cors import with_cors
from app.errors import register_error_handlers
from app.history import router as history_router
from app.model_info import ModelInfo, load_snapshot
from app.models import Simulation
from app.profile import COLUMNS, PatientProfile


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = (
    BACKEND_ROOT.parent / "CardioScope-DATA-ML" / "artifacts" / "pipeline.joblib"
)


def model_path():
    configured = os.environ.get("MODEL_PATH")
    if not configured:
        return DEFAULT_MODEL_PATH

    path = Path(configured).expanduser()
    return path if path.is_absolute() else BACKEND_ROOT / path


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pipeline = None
    app.state.model_info = None

    path = model_path()
    if path.is_file():
        try:
            pipeline, info = load_snapshot(path)
            app.state.pipeline = pipeline
            app.state.model_info = info
        except Exception:
            logging.exception("Pipeline inutilisable : API disponible sans modèle")

    yield


api = FastAPI(title="CardioScope", version="0.1.0", lifespan=lifespan)
register_error_handlers(api)
register_auth(api)
api.include_router(history_router)


class PredictionResponse(BaseModel):
    prediction: Literal[0, 1]
    simulation_id: UUID
    created_at: datetime
    model_version: str
    artifact_sha256: str


@api.get(
    "/model-info",
    response_model=ModelInfo,
    tags=["Modèle"],
    responses={
        401: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)
def model_info(response: Response, auth=Depends(require_admin)):
    info = api.state.model_info
    if info is None:
        raise AuthError(
            503,
            "model_unavailable",
            "Les informations du modèle sont indisponibles.",
        )

    response.headers["Cache-Control"] = "no-store"
    return info


@api.get("/health")
def health():
    ready = api.state.pipeline is not None
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "model_unavailable",
            "model_ready": ready,
        },
    )


@api.post(
    "/predict",
    response_model=PredictionResponse,
    responses={
        401: {
            "model": ErrorEnvelope,
            "description": "Session administrateur absente ou expirée.",
        },
        403: {
            "model": ErrorEnvelope,
            "description": "Protection CSRF refusée.",
        },
        503: {
            "model": ErrorEnvelope,
            "description": "Modèle ou stockage indisponible.",
        },
    },
)
def predict(
    profile: PatientProfile,
    request: Request,
    auth=Depends(require_admin),
    db=Depends(auth_database),
):
    # require_admin renvoie le compte admin et sa session.
    _, admin_session = auth

    # Vérifier l'origine et le jeton CSRF avant l'inférence.
    check_csrf(db, request, utcnow(), admin_session.id)

    pipeline = api.state.pipeline
    if pipeline is None:
        raise AuthError(
            503,
            "model_unavailable",
            "Le modèle est indisponible.",
        )

    info = api.state.model_info
    if info is None:
        raise AuthError(
            503,
            "model_unavailable",
            "Les informations du modèle sont indisponibles.",
        )

    ordered_profile = profile.ordered_columns()
    features = pd.DataFrame([ordered_profile], columns=COLUMNS)
    predictions = np.asarray(pipeline.predict(features))

    if predictions.size != 1:
        raise RuntimeError(
            "Le pipeline doit produire une seule classe pour un profil."
        )

    raw_prediction = predictions.reshape(-1)[0]
    if isinstance(raw_prediction, (bool, np.bool_)):
        raise RuntimeError("Le pipeline a produit une classe invalide.")

    try:
        numeric_prediction = float(raw_prediction)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Le pipeline a produit une classe invalide.") from exc

    if not np.isfinite(numeric_prediction) or numeric_prediction not in (0, 1):
        raise RuntimeError("Le pipeline a produit une classe invalide.")

    simulation = Simulation(
        prediction=int(numeric_prediction),
        model_version=info.model_version,
        artifact_sha256=info.artifact_sha256,
        **ordered_profile,
    )

    try:
        db.add(simulation)
        db.flush()
        db.commit()
    except SQLAlchemyError:
        try:
            db.rollback()
        except SQLAlchemyError:
            pass

        raise AuthError(
            503,
            "storage_unavailable",
            "Le stockage est indisponible.",
        ) from None

    return {
        "prediction": int(numeric_prediction),
        "simulation_id": simulation.id,
        "created_at": simulation.created_at,
        "model_version": simulation.model_version,
        "artifact_sha256": simulation.artifact_sha256,
    }


app = with_cors(api)