"""Contrat HTTP de prédiction et vérification facultative du pipeline réel."""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.auth import AuthError, auth_database, require_admin
from app.main import DEFAULT_MODEL_PATH, api, app
from app.profile import COLUMNS


PROFILE = {
    "age": 63,
    "sex": 1,
    "cp": 1,
    "trestbps": 145,
    "chol": 233,
    "fbs": 1,
    "restecg": 2,
    "thalach": 150,
    "exang": 0,
    "oldpeak": 2.3,
    "slope": 3,
    "ca": 0,
    "thal": 6,
}


class RecordingPipeline:
    def __init__(self, prediction=1):
        self.prediction = prediction
        self.frames = []

    def predict(self, frame):
        self.frames.append(frame.copy())
        return np.asarray([self.prediction])


class FakeSession:
    """Fausse session DB pour tester la route sans PostgreSQL."""

    def __init__(self, fail_commit=False):
        self.simulations = []
        self.fail_commit = fail_commit
        self.commit_calls = 0
        self.rollback_calls = 0

    def add(self, simulation):
        self.simulations.append(simulation)

    def flush(self):
        for simulation in self.simulations:
            if simulation.id is None:
                simulation.id = uuid4()
            if simulation.created_at is None:
                simulation.created_at = datetime.now(timezone.utc)

    def commit(self):
        self.commit_calls += 1
        if self.fail_commit:
            raise SQLAlchemyError("Erreur de test pendant commit")

    def rollback(self):
        self.rollback_calls += 1
        self.simulations.clear()


@contextmanager
def client_with_pipeline(pipeline, *, fail_commit=False):
    """Isole le test du vrai modèle, de la vraie base et du CSRF réel."""
    with tempfile.TemporaryDirectory() as directory:
        absent = str(Path(directory) / "absent.joblib")
        storage = FakeSession(fail_commit=fail_commit)
        admin_session = SimpleNamespace(id=uuid4())

        def fake_database():
            yield storage

        with patch.dict(os.environ, {"MODEL_PATH": absent}):
            api.dependency_overrides[require_admin] = lambda: (
                None,
                admin_session,
            )
            api.dependency_overrides[auth_database] = fake_database

            try:
                with TestClient(app, raise_server_exceptions=False) as client:
                    api.state.pipeline = pipeline
                    api.state.model_info = SimpleNamespace(
                        model_version="test-release",
                        artifact_sha256="a" * 64,
                    )

                    with patch("app.main.check_csrf") as csrf_mock:
                        yield client, storage, csrf_mock
            finally:
                api.state.pipeline = None
                api.state.model_info = None
                api.dependency_overrides.pop(require_admin, None)
                api.dependency_overrides.pop(auth_database, None)


class PredictionEndpointTests(unittest.TestCase):
    def test_valid_json_is_saved_and_returns_both_classes(self):
        payload = {name: PROFILE[name] for name in reversed(COLUMNS)}
        payload["num"] = 4

        for expected in (0, 1):
            with self.subTest(expected=expected):
                pipeline = RecordingPipeline(expected)

                with client_with_pipeline(pipeline) as (
                    client,
                    storage,
                    _csrf_mock,
                ):
                    response = client.post("/predict", json=payload)

                self.assertEqual(response.status_code, 200)
                result = response.json()

                self.assertEqual(result["prediction"], expected)
                self.assertTrue(result["simulation_id"])
                self.assertTrue(result["created_at"])
                self.assertEqual(result["model_version"], "test-release")
                self.assertEqual(result["artifact_sha256"], "a" * 64)

                self.assertEqual(len(storage.simulations), 1)
                self.assertEqual(storage.commit_calls, 1)
                self.assertEqual(storage.rollback_calls, 0)

                saved = storage.simulations[0]
                self.assertEqual(saved.prediction, expected)
                self.assertEqual(saved.model_version, "test-release")
                self.assertEqual(saved.artifact_sha256, "a" * 64)

                for name in COLUMNS:
                    self.assertEqual(getattr(saved, name), PROFILE[name])

                self.assertEqual(len(pipeline.frames), 1)
                self.assertEqual(list(pipeline.frames[0].columns), list(COLUMNS))
                self.assertNotIn("num", pipeline.frames[0].columns)

    def test_invalid_profile_returns_422_without_saving_or_calling_pipeline(self):
        pipeline = RecordingPipeline()
        payload = dict(PROFILE)
        payload["age"] = "private-value"

        with client_with_pipeline(pipeline) as (
            client,
            storage,
            _csrf_mock,
        ):
            response = client.post("/predict", json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.assertEqual(
            response.json()["error"]["fields"][0]["location"],
            ["body", "age"],
        )
        self.assertNotIn("private-value", response.text)
        self.assertEqual(pipeline.frames, [])
        self.assertEqual(storage.simulations, [])

    def test_invalid_model_output_returns_500_without_saving(self):
        for value in (2, True, float("nan")):
            with self.subTest(value=value):
                with client_with_pipeline(RecordingPipeline(value)) as (
                    client,
                    storage,
                    _csrf_mock,
                ):
                    with self.assertLogs("app.errors", level="ERROR"):
                        response = client.post("/predict", json=PROFILE)

                self.assertEqual(response.status_code, 500)
                self.assertEqual(
                    response.json()["error"]["code"],
                    "internal_error",
                )
                self.assertNotIn("pipeline", response.text.lower())
                self.assertEqual(storage.simulations, [])

    def test_missing_model_returns_503_without_saving(self):
        with client_with_pipeline(None) as (
            client,
            storage,
            _csrf_mock,
        ):
            response = client.post("/predict", json=PROFILE)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"],
            "model_unavailable",
        )
        self.assertEqual(storage.simulations, [])

    def test_database_commit_failure_returns_storage_error(self):
        with client_with_pipeline(
            RecordingPipeline(1),
            fail_commit=True,
        ) as (client, storage, _csrf_mock):
            response = client.post("/predict", json=PROFILE)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"],
            "storage_unavailable",
        )
        self.assertEqual(storage.commit_calls, 1)
        self.assertEqual(storage.rollback_calls, 1)
        self.assertEqual(storage.simulations, [])

    def test_csrf_refusal_does_not_save_simulation(self):
        with client_with_pipeline(RecordingPipeline(1)) as (
            client,
            storage,
            csrf_mock,
        ):
            csrf_mock.side_effect = AuthError(
                403,
                "csrf_failed",
                "Origine ou protection CSRF invalide.",
            )
            response = client.post("/predict", json=PROFILE)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "csrf_failed")
        self.assertEqual(storage.simulations, [])

    def test_frontend_origin_can_preflight_and_post_to_predict(self):
        origin = "http://localhost:5173"

        with client_with_pipeline(RecordingPipeline(1)) as (
            client,
            storage,
            _csrf_mock,
        ):
            preflight = client.options(
                "/predict",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            response = client.post(
                "/predict",
                json=PROFILE,
                headers={"Origin": origin},
            )

        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(
            preflight.headers["access-control-allow-origin"],
            origin,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            origin,
        )
        self.assertEqual(response.json()["prediction"], 1)
        self.assertEqual(len(storage.simulations), 1)


class RealPipelineTests(unittest.TestCase):
    @unittest.skipUnless(
        DEFAULT_MODEL_PATH.is_file(),
        "Artefact ML local absent",
    )
    def test_exported_model_accepts_frontend_example_profiles(self):
        """Exerce le vrai modèle avec un stockage de test."""
        profiles = [
            {
                "age": 45, "sex": 0, "cp": 2, "trestbps": 120, "chol": 180,
                "fbs": 0, "restecg": 0, "thalach": 165, "exang": 0,
                "oldpeak": 0.0, "slope": 1, "ca": 0, "thal": 3,
            },
            {
                "age": 67, "sex": 1, "cp": 4, "trestbps": 160, "chol": 286,
                "fbs": 0, "restecg": 2, "thalach": 108, "exang": 1,
                "oldpeak": 1.5, "slope": 2, "ca": 3, "thal": 7,
            },
            {
                "age": 54, "sex": 1, "cp": 3, "trestbps": 130, "chol": 215,
                "fbs": 0, "restecg": 0, "thalach": 155, "exang": 0,
                "oldpeak": 0.6, "slope": 1, "ca": 0, "thal": 3,
            },
        ]

        storage = FakeSession()
        admin_session = SimpleNamespace(id=uuid4())

        def fake_database():
            yield storage

        with patch.dict(os.environ, {"MODEL_PATH": ""}):
            api.dependency_overrides[require_admin] = lambda: (
                None,
                admin_session,
            )
            api.dependency_overrides[auth_database] = fake_database

            try:
                with TestClient(app, raise_server_exceptions=False) as client:
                    api.state.model_info = SimpleNamespace(
                        model_version="test-real-model",
                        artifact_sha256="b" * 64,
                    )

                    with patch("app.main.check_csrf"):
                        health = client.get("/health")
                        self.assertEqual(health.status_code, 200)
                        self.assertTrue(health.json()["model_ready"])

                        for profile in profiles:
                            with self.subTest(age=profile["age"]):
                                response = client.post(
                                    "/predict",
                                    json=profile,
                                )
                                self.assertEqual(response.status_code, 200)
                                self.assertIn(
                                    response.json()["prediction"],
                                    (0, 1),
                                )

                self.assertEqual(len(storage.simulations), len(profiles))
            finally:
                api.state.pipeline = None
                api.state.model_info = None
                api.dependency_overrides.pop(require_admin, None)
                api.dependency_overrides.pop(auth_database, None)


if __name__ == "__main__":
    unittest.main()