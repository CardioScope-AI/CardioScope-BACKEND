import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline

from app.auth import require_admin
from app.main import api, app
from app.model_info import load_snapshot, sha256
from app.profile import COLUMNS


class ModelInfoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "pipeline.joblib"
        frame = pd.DataFrame([[0]*13, [1]*13, [2]*13, [3]*13], columns=COLUMNS)
        model = Pipeline([("classifier", RandomForestClassifier(n_estimators=2, random_state=42))])
        model.fit(frame, [0, 0, 1, 1])
        joblib.dump(model, self.path)
        self.metadata = dict(model="RandomForestClassifier", dataset="Cleveland", features=list(COLUMNS),
                             target={"name": "num"}, train_samples=4, test_samples=2, cv_folds=2,
                             cv_metrics={"accuracy": .8})
        self.report = dict(dataset="Cleveland", test_samples=2, metrics={"accuracy": .5},
                           confusion_matrix=dict(true_negative=1, false_positive=0, false_negative=1, true_positive=0))
        self.write_release()

    def write_release(self):
        metadata = json.dumps(self.metadata).encode()
        report = json.dumps(self.report).encode()
        (self.root / "metadata.json").write_bytes(metadata)
        (self.root / "test_evaluation.json").write_bytes(report)
        self.release = dict(schema_version=1, model_version="synthetic-test-v1",
                            artifact_sha256=sha256(self.path.read_bytes()), metadata_sha256=sha256(metadata),
                            test_evaluation_sha256=sha256(report), limitations=["Synthetic test only."])
        (self.root / "release.json").write_text(json.dumps(self.release), encoding="utf-8")

    def test_valid_snapshot_null_missing_scores_and_no_reload(self):
        api.dependency_overrides[require_admin] = lambda: (None, None)
        self.addCleanup(api.dependency_overrides.pop, require_admin)
        with patch.dict(os.environ, {"MODEL_PATH": str(self.path)}), TestClient(app) as client:
            response = client.get("/model-info")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["evaluation"]["cv_metrics"]["accuracy"], .8)
            self.assertIsNone(data["evaluation"]["cv_metrics"]["recall"])
            self.assertEqual(data["artifact_sha256"], self.release["artifact_sha256"])
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn(str(self.root), response.text)
            (self.root / "metadata.json").write_text("changed", encoding="utf-8")
            self.assertEqual(client.get("/model-info").json(), data)

    def test_anonymous_denied_before_card_access(self):
        with patch.dict(os.environ, {"MODEL_PATH": str(self.path)}), TestClient(app) as client:
            response = client.get("/model-info")
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error"]["code"], "unauthenticated")

    def test_legacy_prediction_retained_but_card_unavailable(self):
        (self.root / "release.json").unlink()
        self.assertIsNone(load_snapshot(self.path)[1])
        api.dependency_overrides[require_admin] = lambda: (None, None)
        self.addCleanup(api.dependency_overrides.pop, require_admin)
        with patch.dict(os.environ, {"MODEL_PATH": str(self.path)}), TestClient(app) as client:
            response = client.get("/model-info")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "model_unavailable")
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_hash_mismatch_rejected_before_joblib_loading(self):
        for filename in ("pipeline.joblib", "metadata.json", "test_evaluation.json"):
            with self.subTest(file=filename):
                path = self.root / filename
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                with patch("app.model_info.joblib.load") as loader:
                    with self.assertRaises(ValueError):
                        load_snapshot(self.path)
                    loader.assert_not_called()
                path.write_bytes(original)

    def test_invalid_declared_release_rejected_at_startup(self):
        (self.root / "metadata.json").write_text("tampered", encoding="utf-8")
        api.dependency_overrides[require_admin] = lambda: (None, None)
        self.addCleanup(api.dependency_overrides.pop, require_admin)
        with patch.dict(os.environ, {"MODEL_PATH": str(self.path)}):
            with self.assertLogs(level="ERROR"), TestClient(app) as client:
                self.assertEqual(client.get("/health").status_code, 503)
                response = client.get("/model-info")
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("tampered", response.text)

    def test_invalid_scores_counts_features_and_version_rejected(self):
        cases = [("score", lambda: self.metadata.update(cv_metrics={"accuracy": 1.5})),
                 ("count", lambda: self.report.update(test_samples=3)),
                 ("matrix", lambda: self.report["confusion_matrix"].update(true_positive=5)),
                 ("features", lambda: self.metadata.update(features=list(reversed(COLUMNS))))]
        original_metadata, original_report = json.dumps(self.metadata), json.dumps(self.report)
        for name, mutate in cases:
            with self.subTest(case=name):
                self.metadata, self.report = json.loads(original_metadata), json.loads(original_report)
                mutate()
                self.write_release()
                with self.assertRaises(ValueError):
                    load_snapshot(self.path)
        self.release["model_version"] = " "
        (self.root / "release.json").write_text(json.dumps(self.release), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_snapshot(self.path)
