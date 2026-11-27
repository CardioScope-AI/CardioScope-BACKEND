"""A fixed startup snapshot of validated comparison cards, never predictors."""
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.model_info import ModelInfo, StrictModel, Text, read_release_info


class ModelCatalog(StrictModel):
    schema_version: Literal[1] = 1
    active_model_version: Text
    models: list[ModelInfo] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent(self):
        versions = [info.model_version for info in self.models]
        if len(set(versions)) != len(versions) or versions.count(self.active_model_version) != 1:
            raise ValueError("Invalid active model or duplicate versions")
        return self


def load_catalog(active_info, candidate_paths):
    if active_info is None:
        raise ValueError("Active release information unavailable")
    cards = [active_info]
    for path in candidate_paths:
        info = read_release_info(Path(path))
        if info is None or info.model_name != "XGBClassifier":
            raise ValueError("Expected a declared XGBoost comparison release")
        cards.append(info)
    return ModelCatalog(active_model_version=active_info.model_version, models=cards)
