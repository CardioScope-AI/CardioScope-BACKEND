"""Validate a release snapshot before exposing its public model information."""

import hashlib
from io import BytesIO
import json
from typing import Annotated, Literal

import joblib
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.profile import COLUMNS

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False, strict=True)]
Count = Annotated[int, Field(ge=0, strict=True)]
PositiveCount = Annotated[int, Field(gt=0, strict=True)]
Text = Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Release(StrictModel):
    schema_version: Literal[1]
    model_version: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    artifact_sha256: Hash
    metadata_sha256: Hash
    test_evaluation_sha256: Hash
    limitations: Annotated[list[Text], Field(min_length=1)]


class CvMetrics(StrictModel):
    accuracy: Score | None = None
    precision: Score | None = None
    recall: Score | None = None
    f1: Score | None = None
    roc_auc: Score | None = None


class TestMetrics(CvMetrics):
    specificity: Score | None = None


class ConfusionMatrix(StrictModel):
    true_negative: Count
    false_positive: Count
    false_negative: Count
    true_positive: Count


class Evaluation(StrictModel):
    train_samples: PositiveCount
    test_samples: PositiveCount
    cv_folds: Annotated[int, Field(ge=2, strict=True)]
    cv_metrics: CvMetrics
    test_metrics: TestMetrics
    confusion_matrix: ConfusionMatrix | None

    @model_validator(mode="after")
    def coherent(self):
        if self.cv_folds > self.train_samples:
            raise ValueError("Invalid folds")
        if self.confusion_matrix and sum(self.confusion_matrix.model_dump().values()) != self.test_samples:
            raise ValueError("Invalid evaluation count")
        return self


class ModelInfo(StrictModel):
    schema_version: Literal[1]
    model_version: Text
    artifact_sha256: Hash
    model_name: Literal["RandomForestClassifier"]
    dataset: Literal["UCI Heart Disease / Cleveland"]
    features: list[str]
    target: dict[str, str]
    evaluation: Evaluation
    limitations: list[Text]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_json(data):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique_keys)


def load_snapshot(path):
    """Fixed sibling filenames, hashes checked on the exact bytes deserialized.

    Legacy exports retain prediction compatibility but expose no model-info.
    A present but invalid manifest rejects the entire declared release.
    Trusted joblib files only: hashes prove consistency, not publisher authenticity.
    """
    artifact = path.read_bytes()
    manifest_path = path.parent / "release.json"
    info = None
    if manifest_path.exists():
        release = Release.model_validate(read_json(manifest_path.read_bytes()))
        metadata_bytes = (path.parent / "metadata.json").read_bytes()
        report_bytes = (path.parent / "test_evaluation.json").read_bytes()
        for data, expected in ((artifact, release.artifact_sha256),
                               (metadata_bytes, release.metadata_sha256),
                               (report_bytes, release.test_evaluation_sha256)):
            if sha256(data) != expected:
                raise ValueError("Release hash mismatch")
        metadata, report = read_json(metadata_bytes), read_json(report_bytes)
        if (metadata["features"] != list(COLUMNS) or metadata["dataset"] != "Cleveland"
                or report["dataset"] != "Cleveland" or metadata["target"]["name"] != "num"
                or metadata["test_samples"] != report["test_samples"]):
            raise ValueError("Inconsistent release metadata")
        info = ModelInfo(
            schema_version=1, model_version=release.model_version,
            artifact_sha256=release.artifact_sha256, model_name=metadata["model"],
            dataset="UCI Heart Disease / Cleveland", features=metadata["features"],
            target={"0": "absence", "1": "présence"}, limitations=release.limitations,
            evaluation=Evaluation(
                train_samples=metadata["train_samples"], test_samples=metadata["test_samples"],
                cv_folds=metadata["cv_folds"], cv_metrics=metadata.get("cv_metrics", {}),
                test_metrics=report.get("metrics", {}), confusion_matrix=report.get("confusion_matrix")),
        )
    pipeline = joblib.load(BytesIO(artifact))
    if not callable(getattr(pipeline, "predict", None)):
        raise ValueError("Missing predict")
    if info:
        classifier = getattr(pipeline, "named_steps", {}).get("classifier")
        # The trained object, not merely its JSON label, must match the public card.
        from sklearn.ensemble import RandomForestClassifier
        import numpy as np
        if (not isinstance(classifier, RandomForestClassifier)
                or list(getattr(pipeline, "feature_names_in_", [])) != list(COLUMNS)
                or not np.array_equal(getattr(classifier, "classes_", []), [0, 1])):
            raise ValueError("Pipeline does not match release")
    return pipeline, info
