"""Login HTTP integration on a disposable PostgreSQL database, never the real admin."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.auth import SESSION_COOKIE, CSRF_COOKIE, digest, utcnow
from app.database import database_url, get_engine
from app.init_admin import initialize_admin
from app.main import app
from app.models import Admin, AdminSession, CsrfContext, LoginAttemptWindow

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://localhost:5173"
PASSWORD = "integration-only-password-123"


@unittest.skipUnless(os.environ.get("CARDIOSCOPE_TEST_POSTGRES") == "1", "Requires isolated PostgreSQL integration")
class LoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name = "cardioscope_test_" + uuid4().hex
        cls.control = sa.create_engine(database_url().set(database="postgres"), isolation_level="AUTOCOMMIT")
        with cls.control.connect() as c:
            c.exec_driver_sql(f'CREATE DATABASE "{cls.name}"')
        cls.env = patch.dict(os.environ, {"DB_NAME": cls.name, "AUTH_COOKIE_SECURE": "false"})
        cls.env.start()
        get_engine.cache_clear()
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        cls.engine = get_engine()
        with Session(cls.engine) as db:
            initialize_admin(db, "admin", PASSWORD)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        get_engine.cache_clear()
        cls.env.stop()
        with cls.control.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE "{cls.name}" WITH (FORCE)')
        cls.control.dispose()

    def setUp(self):
        with Session(self.engine) as db:
            for model in (CsrfContext, AdminSession, LoginAttemptWindow):
                db.execute(sa.delete(model))
            db.execute(sa.update(Admin).values(active=True))
            db.commit()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def csrf(self, client=None):
        response = (client or self.client).get("/admin/csrf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()["csrf_token"]

    def login(self, token, **payload):
        return self.client.post("/admin/login", json={"username": "admin", "password": PASSWORD, **payload},
                                headers={"Origin": ORIGIN, "X-CSRF-Token": token})

    def test_login_rotation_hashes_identity_and_cookie_flags(self):
        token = self.csrf()
        old_binding = self.client.cookies[CSRF_COOKIE]
        response = self.login(token)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["admin"]["username"], "admin")
        self.assertEqual(response.json()["session"]["idle_timeout_seconds"], 1800)
        self.assertNotIn("password", response.text)
        self.assertNotEqual(self.client.cookies[CSRF_COOKIE], old_binding)
        self.assertEqual(self.login(token).status_code, 403)
        self.assertEqual(self.client.get("/admin/me").status_code, 200)
        raw = self.client.cookies[SESSION_COOKIE]
        with Session(self.engine) as db:
            stored = db.scalars(sa.select(AdminSession)).one()
            self.assertEqual(stored.token_hash, digest(raw))
            self.assertNotEqual(stored.token_hash, raw)
            self.assertEqual((stored.expires_at - stored.created_at).total_seconds(), 28800)
        cookie = next(c for c in response.headers.get_list("set-cookie") if c.startswith(SESSION_COOKIE + "="))
        for flag in ("HttpOnly", "SameSite=lax", "Path=/", "Max-Age=28800"):
            self.assertIn(flag, cookie)
        with patch.dict(os.environ, {"AUTH_COOKIE_SECURE": "true"}):
            response = self.client.get("/admin/csrf")
            self.assertIn("Secure", response.headers["set-cookie"])

    def test_csrf_origin_cookie_and_expiry_required(self):
        token = self.csrf()
        self.assertEqual(self.client.post("/admin/login", json={"username": "admin", "password": PASSWORD}).status_code, 403)
        self.assertEqual(self.client.post("/admin/login", json={"username": "admin", "password": PASSWORD},
                         headers={"Origin": "https://evil.example", "X-CSRF-Token": token}).status_code, 403)
        other = TestClient(app)
        self.addCleanup(other.close)
        self.assertEqual(other.post("/admin/login", json={"username": "admin", "password": PASSWORD},
                         headers={"Origin": ORIGIN, "X-CSRF-Token": token}).status_code, 403)
        with Session(self.engine) as db:
            db.execute(sa.update(CsrfContext).values(created_at=utcnow()-timedelta(minutes=20), expires_at=utcnow()-timedelta(minutes=1)))
            db.commit()
        self.assertEqual(self.login(token).status_code, 403)

    def test_generic_failure_shared_rate_limit_and_expiry(self):
        token = self.csrf()
        unknown = self.login(token, username="unknown", password="wrong")
        wrong = self.login(token, password="wrong")
        self.assertEqual(unknown.json(), wrong.json())
        for _ in range(4):
            self.assertEqual(self.login(token, password="wrong").status_code, 401)
        response = self.login(token)
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["retry-after"]), 0)
        # A different client/worker with the same network still sees the DB limit.
        with TestClient(app) as other:
            other_token = self.csrf(other)
            self.assertEqual(other.post("/admin/login", json={"username": "admin", "password": PASSWORD},
                             headers={"Origin": ORIGIN, "X-CSRF-Token": other_token}).status_code, 429)
        with Session(self.engine) as db:
            db.execute(sa.update(LoginAttemptWindow).values(window_started_at=utcnow()-timedelta(hours=1), expires_at=utcnow()-timedelta(minutes=1)))
            db.commit()
        self.assertEqual(self.login(token).status_code, 200)

    def test_session_expiry_revocation_and_inactive_account(self):
        self.assertEqual(self.client.get("/admin/me").status_code, 401)
        for mode in ("idle", "absolute", "revoked", "inactive"):
            with self.subTest(mode=mode):
                self.client.cookies.clear()
                self.assertEqual(self.login(self.csrf()).status_code, 200)
                with Session(self.engine) as db:
                    row = db.scalar(sa.select(AdminSession).where(AdminSession.token_hash == digest(self.client.cookies[SESSION_COOKIE])))
                    if mode == "idle":
                        row.last_seen_at = utcnow() - timedelta(minutes=31)
                    elif mode == "absolute":
                        row.created_at = utcnow() - timedelta(hours=9)
                        row.expires_at = utcnow() - timedelta(hours=1)
                    elif mode == "revoked":
                        row.revoked_at = utcnow()
                    else:
                        db.execute(sa.update(Admin).values(active=False))
                    db.commit()
                self.assertEqual(self.client.get("/admin/me").status_code, 401)
                self.assertEqual(self.csrf() is not None, True)
        self.assertEqual(self.login(self.csrf()).status_code, 401)

    def test_validation_no_password_echo_and_database_failure(self):
        response = self.client.post("/admin/login", json={"username": "admin", "password": "SECRET" * 30})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("SECRET", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        with patch("app.auth.get_session", side_effect=ValueError("private config")):
            response = self.client.get("/admin/csrf")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "storage_unavailable")
        self.assertNotIn("private", response.text)

    def test_network_limit_and_concurrent_failures(self):
        token = self.csrf()
        self.assertEqual(self.login(token, password="wrong").status_code, 401)
        with Session(self.engine) as db:
            network = db.get(LoginAttemptWindow, digest("network:testclient"))
            network.failed_attempts = 29
            db.commit()
        self.assertEqual(self.login(token, username="other", password="wrong").status_code, 401)
        self.assertEqual(self.login(token, username="another").status_code, 429)
        with Session(self.engine) as db:
            db.execute(sa.delete(LoginAttemptWindow))
            now = utcnow()
            db.add(LoginAttemptWindow(key_hash=digest("pair:admin\0testclient"),
                   window_started_at=now, failed_attempts=4,
                   expires_at=now+timedelta(minutes=15)))
            db.commit()

        def attempt(_):
            with TestClient(app) as client:
                csrf = client.get("/admin/csrf").json()["csrf_token"]
                return client.post("/admin/login", json={"username": "admin", "password": "wrong"},
                    headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt, range(2))), [401, 429])
