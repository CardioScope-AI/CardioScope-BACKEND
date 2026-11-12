import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.cors import with_cors
from app.errors import register_error_handlers
from app.main import app, model_path, BACKEND_ROOT, DEFAULT_MODEL_PATH


class Measurement(BaseModel):
    age: int


def test_application():
    api = FastAPI()
    register_error_handlers(api)

    @api.post('/validate')
    def validate(value: Measurement):
        return value

    @api.get('/unavailable')
    def unavailable():
        raise HTTPException(503, detail='internal model path')

    @api.get('/crash')
    def crash():
        raise RuntimeError('private technical details')

    @api.get('/auth')
    def auth():
        raise HTTPException(401, headers={'WWW-Authenticate': 'Bearer'})

    return with_cors(api)


class ErrorsAndCorsTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {'CORS_ORIGINS': 'http://localhost:5173,http://127.0.0.1:5173'}):
            self.client = TestClient(test_application(), raise_server_exceptions=False)

    def test_preflight_allows_local_origins_and_json_post(self):
        for origin in ['http://localhost:5173', 'http://127.0.0.1:5173']:
            response = self.client.options('/validate', headers={
                'Origin': origin, 'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'content-type',
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['access-control-allow-origin'], origin)
            self.assertNotIn('access-control-allow-credentials', response.headers)

    def test_preflight_denies_other_origin_and_method(self):
        for origin, method in [('https://other.example', 'POST'), ('http://localhost:5173', 'DELETE')]:
            response = self.client.options('/validate', headers={
                'Origin': origin, 'Access-Control-Request-Method': method,
            })
            self.assertEqual(response.status_code, 400)
        response = self.client.get('/missing', headers={'Origin': 'https://other.example'})
        self.assertNotIn('access-control-allow-origin', response.headers)

    def test_http_errors_and_headers(self):
        for path, status, code in [('/missing', 404, 'not_found'), ('/unavailable', 503, 'model_unavailable')]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json()['error']['code'], code)
            self.assertNotIn('internal model path', response.text)
        response = self.client.post('/missing')
        self.assertEqual(response.status_code, 404)
        response = self.client.post('/unavailable')
        self.assertEqual(response.status_code, 405)
        self.assertIn('GET', response.headers['allow'])
        self.assertEqual(self.client.get('/auth').headers['www-authenticate'], 'Bearer')

    def test_validation_error_omits_input(self):
        response = self.client.post('/validate', json={'age': 'private-value'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['error']['code'], 'validation_error')
        self.assertEqual(response.json()['error']['fields'][0]['location'], ['body', 'age'])
        self.assertNotIn('private-value', response.text)

    def test_internal_error_is_logged_and_has_cors_headers(self):
        with self.assertLogs('app.errors', level='ERROR') as logs:
            response = self.client.get('/crash', headers={'Origin': 'http://localhost:5173'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['error']['code'], 'internal_error')
        self.assertNotIn('private technical details', response.text)
        self.assertIn('private technical details', '\n'.join(logs.output))
        self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:5173')

    def test_configurable_origins(self):
        with patch.dict(os.environ, {'CORS_ORIGINS': ' https://demo.example , '}):
            client = TestClient(test_application())
        response = client.get('/missing', headers={'Origin': 'https://demo.example'})
        self.assertEqual(response.headers['access-control-allow-origin'], 'https://demo.example')
        with patch.dict(os.environ, {'CORS_ORIGINS': '*'}):
            with self.assertRaises(ValueError):
                test_application()

    def test_existing_health_and_docs_remain_available_without_model(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'MODEL_PATH': str(Path(directory) / 'absent.joblib')}):
                with TestClient(app) as client:
                    response = client.get('/health')
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json(), {'status': 'model_unavailable', 'model_ready': False})
                    self.assertEqual(client.get('/docs').status_code, 200)
                    self.assertEqual(client.get('/openapi.json').status_code, 200)
                    payload = {
                        'age': 63, 'sex': 1, 'cp': 1, 'trestbps': 145, 'chol': 233,
                        'fbs': 1, 'restecg': 2, 'thalach': 150, 'exang': 0,
                        'oldpeak': 2.3, 'slope': 3, 'ca': 0, 'thal': 6,
                    }
                    response = client.post('/predict', json=payload)
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json()['error']['code'], 'model_unavailable')

    def test_model_paths_do_not_depend_on_working_directory(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {'MODEL_PATH': ''}):
                    self.assertEqual(model_path(), DEFAULT_MODEL_PATH)
                    self.assertTrue(model_path().is_absolute())
                with patch.dict(os.environ, {'MODEL_PATH': 'artifacts/pipeline.joblib'}):
                    self.assertEqual(model_path(), BACKEND_ROOT / 'artifacts/pipeline.joblib')
                absolute = Path(directory) / 'pipeline.joblib'
                with patch.dict(os.environ, {'MODEL_PATH': str(absolute)}):
                    self.assertEqual(model_path(), absolute)
            finally:
                os.chdir(previous)


if __name__ == '__main__':
    unittest.main()
