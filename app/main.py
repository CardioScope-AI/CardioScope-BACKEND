import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import joblib
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.cors import with_cors
from app.errors import register_error_handlers

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = BACKEND_ROOT.parent / "CardioScope-DATA-ML" / "artifacts" / "pipeline.joblib"


def model_path():
    configured = os.environ.get("MODEL_PATH")
    if not configured:
        return DEFAULT_MODEL_PATH
    path = Path(configured).expanduser()
    return path if path.is_absolute() else BACKEND_ROOT / path


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pipeline = None
    path = model_path()
    if path.is_file():
        try:
            pipeline = joblib.load(path)
            if not callable(getattr(pipeline, "predict", None)):
                raise ValueError("Le pipeline doit exposer predict")
            app.state.pipeline = pipeline
        except Exception:
            logging.exception("Pipeline inutilisable : API disponible sans modèle")
    yield


api = FastAPI(title="CardioScope", version="0.1.0", lifespan=lifespan)
register_error_handlers(api)


@api.get("/health")
def health():
    ready = api.state.pipeline is not None
    return JSONResponse(status_code=200 if ready else 503,
                        content={"status": "ready" if ready else "model_unavailable", "model_ready": ready})


app = with_cors(api)
