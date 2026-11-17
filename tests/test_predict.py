"""Contrat HTTP de prédiction et vérification facultative du pipeline réel."""

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from app.main import DEFAULT_MODEL_PATH, api, app
from app.profile import COLUMNS


PROFILE = {
    "age": 63, "sex": 1, "cp": 1, "trestbps": 145, "chol": 233,
    "fbs": 1, "restecg": 2, "thalach": 150, "exang": 0,
    "oldpeak": 2.3, "slope": 3, "ca": 0, "thal": 6,
}


class RecordingPipeline:
    def __init__(self, prediction=1):
        self.prediction = prediction
        self.frames = []

    def predict(self, frame):
        self.frames.append(frame.copy())
        return np.asarray([self.prediction])


@contextmanager
def client_with_pipeline(pipeline):
    """Isoler chaque test du modèle enregistré sur la machine."""
    with tempfile.TemporaryDirectory() as directory:
        absent = str(Path(directory) / "absent.joblib")
        with patch.dict(os.environ, {"MODEL_PATH": absent}):
            with TestClient(app, raise_server_exceptions=False) as client:
                api.state.pipeline = pipeline
                try:
                    yield client
                finally:
                    api.state.pipeline = None


class PredictionEndpointTests(unittest.TestCase):
    def test_valid_json_reaches_pipeline_in_contract_order_and_returns_both_classes(self):
        payload = {name: PROFILE[name] for name in reversed(COLUMNS)}
        payload["num"] = 4  # La cible et les champs supplémentaires ne sont pas des entrées.
        for expected in (0, 1):
            with self.subTest(expected=expected):
                pipeline = RecordingPipeline(expected)
                with client_with_pipeline(pipeline) as client:
                    self.assertEqual(client.get("/health").json(),
                                     {"status": "ready", "model_ready": True})
                    response = client.post("/predict", json=payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"prediction": expected})
                self.assertEqual(len(pipeline.frames), 1)
                frame = pipeline.frames[0]
                self.assertEqual(list(frame.columns), list(COLUMNS))
                self.assertEqual(frame.loc[0, "age"], PROFILE["age"])
                self.assertEqual(frame.loc[0, "oldpeak"], PROFILE["oldpeak"])
                self.assertNotIn("num", frame.columns)

    def test_invalid_profile_returns_422_without_calling_pipeline_or_echoing_input(self):
        pipeline = RecordingPipeline()
        payload = dict(PROFILE)
        payload["age"] = "private-value"
        with client_with_pipeline(pipeline) as client:
            response = client.post("/predict", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.assertEqual(response.json()["error"]["fields"][0]["location"],
                         ["body", "age"])
        self.assertNotIn("private-value", response.text)
        self.assertEqual(pipeline.frames, [])

    def test_model_output_outside_binary_contract_returns_generic_500(self):
        for value in (2, True, float("nan")):
            with self.subTest(value=value):
                with client_with_pipeline(RecordingPipeline(value)) as client:
                    with self.assertLogs("app.errors", level="ERROR"):
                        response = client.post("/predict", json=PROFILE)
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json()["error"]["code"], "internal_error")
                self.assertNotIn("pipeline", response.text.lower())

    def test_frontend_origin_can_preflight_and_post_to_predict(self):
        origin = "http://localhost:5173"
        with client_with_pipeline(RecordingPipeline(1)) as client:
            preflight = client.options("/predict", headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            })
            response = client.post("/predict", json=PROFILE, headers={"Origin": origin})
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(preflight.headers["access-control-allow-origin"], origin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], origin)
        self.assertEqual(response.json(), {"prediction": 1})


class RealPipelineTests(unittest.TestCase):
    @unittest.skipUnless(DEFAULT_MODEL_PATH.is_file(), "Artefact ML local absent")
    def test_exported_model_accepts_frontend_example_profiles(self):
        """Exercer le véritable joblib lorsqu'il existe sur la machine de travail."""
        profiles = [
            {"age": 45, "sex": 0, "cp": 2, "trestbps": 120, "chol": 180,
             "fbs": 0, "restecg": 0, "thalach": 165, "exang": 0,
             "oldpeak": 0.0, "slope": 1, "ca": 0, "thal": 3},
            {"age": 67, "sex": 1, "cp": 4, "trestbps": 160, "chol": 286,
             "fbs": 0, "restecg": 2, "thalach": 108, "exang": 1,
             "oldpeak": 1.5, "slope": 2, "ca": 3, "thal": 7},
            {"age": 54, "sex": 1, "cp": 3, "trestbps": 130, "chol": 215,
             "fbs": 0, "restecg": 0, "thalach": 155, "exang": 0,
             "oldpeak": 0.6, "slope": 1, "ca": 0, "thal": 3},
        ]
        with patch.dict(os.environ, {"MODEL_PATH": ""}):
            with TestClient(app, raise_server_exceptions=False) as client:
                health = client.get("/health")
                self.assertEqual(health.status_code, 200)
                self.assertTrue(health.json()["model_ready"])
                for profile in profiles:
                    with self.subTest(age=profile["age"]):
                        response = client.post("/predict", json=profile)
                        self.assertEqual(response.status_code, 200)
                        self.assertIn(response.json(),
                                      ({"prediction": 0}, {"prediction": 1}))


if __name__ == "__main__":
    unittest.main()
