import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.auth import require_admin
from app.main import api, app
from app.model_catalog import load_catalog
from app.model_info import load_snapshot, sha256
import test_model_info as fixtures


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ModelInfoTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root / 'candidate'
        self.root.mkdir()
        self.path = self.root / 'pipeline.joblib'
        # The comparison artifact is checked as bytes and never executed.
        self.path.write_bytes(b'not a serialized Python object')
        metadata = dict(self.fixture.metadata, model='XGBClassifier', cv_metrics={'accuracy': .75, 'specificity': .5})
        for name, content in [('metadata.json', metadata), ('test_evaluation.json', self.fixture.report)]:
            (self.root / name).write_text(json.dumps(content), encoding='utf-8')
        manifest = dict(self.fixture.release, model_version='xgb-synthetic-v1',
                        artifact_sha256=sha256(self.path.read_bytes()),
                        metadata_sha256=sha256((self.root / 'metadata.json').read_bytes()),
                        test_evaluation_sha256=sha256((self.root / 'test_evaluation.json').read_bytes()))
        (self.root / 'release.json').write_text(json.dumps(manifest), encoding='utf-8')

    def authorize(self):
        api.dependency_overrides[require_admin] = lambda: (None, None)
        self.addCleanup(api.dependency_overrides.pop, require_admin)

    def test_comparison_artifact_never_deserialized_and_tampering_rejected(self):
        active = load_snapshot(self.fixture.path)[1]
        with patch('app.model_info.joblib.load') as loader:
            result = load_catalog(active, [self.path])
            self.assertEqual(len(result.models), 2)
            with self.assertRaises(ValueError):
                load_catalog(active, [self.path, self.path])
            with self.assertRaises(ValueError):
                load_snapshot(self.path)
            loader.assert_not_called()
            self.path.write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                load_catalog(active, [self.path])

    def test_authenticated_catalog_snapshot_and_active_prediction_unchanged(self):
        self.authorize()
        with patch.dict(os.environ, MODEL_PATH=str(self.fixture.path), COMPARISON_MODEL_PATHS=str(self.path)), TestClient(app) as client:
            response = client.get('/models')
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(data['active_model_version'], self.fixture.release['model_version'])
            self.assertEqual([m['model_name'] for m in data['models']], ['RandomForestClassifier', 'XGBClassifier'])
            self.assertEqual(client.get('/model-info').json()['model_name'], 'RandomForestClassifier')
            self.assertEqual(client.get('/health').status_code, 200)
            self.path.write_bytes(b'changed after startup')
            self.assertEqual(client.get('/models').json(), data)

    def test_unavailable_candidate_does_not_disable_active_model(self):
        self.authorize()
        with patch.dict(os.environ, MODEL_PATH=str(self.fixture.path), COMPARISON_MODEL_PATHS=str(self.root / 'missing')):
            with self.assertLogs(level='ERROR'), TestClient(app) as client:
                self.assertEqual(client.get('/models').status_code, 503)
                self.assertEqual(client.get('/health').status_code, 200)
                self.assertEqual(client.get('/model-info').status_code, 200)

    def test_anonymous_denied(self):
        with patch.dict(os.environ, MODEL_PATH=str(self.fixture.path), COMPARISON_MODEL_PATHS=str(self.path)), TestClient(app) as client:
            self.assertEqual(client.get('/models').status_code, 401)
