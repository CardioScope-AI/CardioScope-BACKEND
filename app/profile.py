"""Profil patient validé pour la route POST /predict."""

import math
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict


def json_number(value):
    """Accepter un nombre JSON, entier ou décimal, sans convertir une chaîne."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("nombre attendu")
    return float(value)


def strict_int(value):
    """Accepter un entier JSON, sans convertir une chaîne, un booléen ou un décimal."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("entier attendu")
    return value


def code(allowed):
    """Limiter un entier JSON aux valeurs prévues par le contrat."""
    choices = frozenset(allowed)

    def validate(value):
        if isinstance(value, bool) or not isinstance(value, int) or value not in choices:
            raise ValueError("valeur non autorisée")
        return value

    return Annotated[int, BeforeValidator(validate)]


Number = Annotated[float, BeforeValidator(json_number)]
StrictInt = Annotated[int, BeforeValidator(strict_int)]

COLUMNS = (
    "age", "sex", "cp", "trestbps", "chol", "fbs", "restecg",
    "thalach", "exang", "oldpeak", "slope", "ca", "thal",
)


class PatientProfile(BaseModel):
    """Treize champs obligatoires du contrat API. Les clés JSON peuvent arriver dans n'importe quel ordre."""

    model_config = ConfigDict(extra="ignore")

    age: StrictInt
    sex: code((0, 1))
    cp: code((1, 2, 3, 4))
    trestbps: Number
    chol: Number
    fbs: code((0, 1))
    restecg: code((0, 1, 2))
    thalach: StrictInt
    exang: code((0, 1))
    oldpeak: Number
    slope: code((1, 2, 3))
    ca: code((0, 1, 2, 3))
    thal: code((3, 6, 7))

    def ordered_columns(self):
        """Reconstruire les colonnes dans l'ordre du contrat, indépendamment de l'ordre des clés."""
        return {name: getattr(self, name) for name in COLUMNS}
