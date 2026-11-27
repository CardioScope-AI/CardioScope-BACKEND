"""Authenticated, read-only access to saved simulations."""

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.auth import AuthError, ErrorEnvelope, check_csrf, require_admin, utcnow
from app.database import get_session
from app.models import Simulation
from app.profile import COLUMNS, PatientProfile


router = APIRouter(prefix="/admin/simulations", tags=["Historique"])


class SimulationSummary(BaseModel):
    age: int
    trestbps: float
    chol: float


class SimulationItem(BaseModel):
    id: UUID
    created_at: datetime
    prediction: Literal[0, 1]
    model_version: str
    summary: SimulationSummary


class SimulationPage(BaseModel):
    items: list[SimulationItem]
    page: int
    page_size: int
    total: int


class SimulationDetail(BaseModel):
    id: UUID
    created_at: datetime
    prediction: Literal[0, 1]
    model_version: str
    artifact_sha256: str
    profile: PatientProfile


def utc_date(value: datetime) -> str:
    """Use the contract's UTC Z representation, including for DB round-trips."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def checked_date(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise AuthError(422, "validation_error", "Les dates doivent préciser le fuseau UTC.")
    return value.astimezone(timezone.utc)


def storage_unavailable():
    raise AuthError(503, "storage_unavailable", "Le stockage est indisponible.") from None


@router.get("", response_model=SimulationPage, responses={
    401: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope},
    503: {"model": ErrorEnvelope},
})
def list_simulations(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    prediction: Annotated[int | None, Query(ge=0, le=1)] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_before: Annotated[datetime | None, Query()] = None,
    auth=Depends(require_admin),
    db=Depends(get_session),
):
    start, end = checked_date(created_from), checked_date(created_before)
    if start is not None and end is not None and start >= end:
        raise AuthError(422, "validation_error", "Les bornes de date sont inversées.")

    filters = []
    if prediction is not None:
        filters.append(Simulation.prediction == prediction)
    if start is not None:
        filters.append(Simulation.created_at >= start)
    if end is not None:
        filters.append(Simulation.created_at < end)

    try:
        total = db.scalar(sa.select(sa.func.count()).select_from(Simulation).where(*filters))
        rows = db.scalars(sa.select(Simulation).where(*filters).order_by(
            Simulation.created_at.desc(), Simulation.id.desc(),
        ).offset((page - 1) * page_size).limit(page_size)).all()
    except SQLAlchemyError:
        storage_unavailable()

    return {
        "items": [{
            "id": row.id, "created_at": utc_date(row.created_at),
            "prediction": row.prediction, "model_version": row.model_version,
            "summary": {"age": row.age, "trestbps": row.trestbps, "chol": row.chol},
        } for row in rows],
        "page": page, "page_size": page_size, "total": total,
    }


@router.get("/{simulation_id}", response_model=SimulationDetail, responses={
    401: {"model": ErrorEnvelope}, 404: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope}, 503: {"model": ErrorEnvelope},
})
def get_simulation(simulation_id: UUID, auth=Depends(require_admin), db=Depends(get_session)):
    try:
        row = db.get(Simulation, simulation_id)
    except SQLAlchemyError:
        storage_unavailable()
    if row is None:
        raise AuthError(404, "not_found", "Simulation introuvable.")
    return {
        "id": row.id, "created_at": utc_date(row.created_at),
        "prediction": row.prediction, "model_version": row.model_version,
        "artifact_sha256": row.artifact_sha256,
        "profile": {name: getattr(row, name) for name in COLUMNS},
    }


@router.delete("/{simulation_id}", status_code=204, response_class=Response, responses={
    204: {"description": "Simulation supprimée."},
    401: {"model": ErrorEnvelope}, 403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope},
    503: {"model": ErrorEnvelope},
})
def delete_simulation(
    simulation_id: UUID,
    request: Request,
    auth=Depends(require_admin),
    db=Depends(get_session),
) -> Response:
    _, admin_session = auth
    check_csrf(db, request, utcnow(), admin_session.id)

    try:
        row = db.get(Simulation, simulation_id)
        if row is None:
            raise AuthError(404, "not_found", "Simulation introuvable.")
        db.delete(row)
        db.commit()
    except SQLAlchemyError:
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        storage_unavailable()

    return Response(status_code=204)
