"""History contract tests on a disposable SQLite table; no application DB is used."""

from datetime import datetime, timedelta, timezone
import unittest
from uuid import UUID

from fastapi.testclient import TestClient
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth import require_admin
from app.database import get_session
from app.main import api, app
from app.models import Simulation


PROFILE = dict(age=63, sex=1, cp=1, trestbps=145.0, chol=233.0, fbs=1,
               restecg=2, thalach=150, exang=0, oldpeak=2.3, slope=3, ca=0, thal=6)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine("sqlite+pysqlite://", poolclass=StaticPool,
                                       connect_args={"check_same_thread": False})
        # The production table has PostgreSQL-only checks. Recreate just its
        # columns here so HTTP queries run without touching a real database.
        metadata = sa.MetaData()
        table = sa.Table("simulations", metadata, *(
            sa.Column(column.name, column.type, primary_key=column.primary_key,
                      nullable=column.nullable) for column in Simulation.__table__.columns
        ))
        metadata.create_all(self.engine)
        self.now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        self.ids = [UUID(int=value) for value in (1, 2, 3)]
        with self.engine.begin() as connection:
            for identifier, prediction, created in zip(
                self.ids, (0, 1, 1),
                (self.now, self.now, self.now - timedelta(days=1)),
            ):
                connection.execute(table.insert().values(
                    id=identifier, created_at=created, prediction=prediction,
                    model_version="release-test", artifact_sha256="a" * 64, **PROFILE,
                ))

        def test_session():
            with Session(self.engine) as db:
                yield db

        api.dependency_overrides[get_session] = test_session
        api.dependency_overrides[require_admin] = lambda: (object(), object())
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.addCleanup(api.dependency_overrides.clear)
        self.addCleanup(self.engine.dispose)

    def test_list_order_pagination_filters_and_no_sensitive_data(self):
        response = self.client.get("/admin/simulations?page_size=1&page=1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["items"][0]["id"], str(self.ids[1]))
        self.assertEqual(data["items"][0]["created_at"], "2026-11-20T12:00:00Z")
        self.assertEqual(set(data["items"][0]),
                         {"id", "created_at", "prediction", "model_version", "summary"})
        self.assertEqual(self.client.get("/admin/simulations?page_size=1&page=2").json()
                         ["items"][0]["id"], str(self.ids[0]))
        self.assertEqual(self.client.get("/admin/simulations?page=9").json()["items"], [])
        filtered = self.client.get("/admin/simulations?prediction=1&created_from=2026-11-20T00:00:00Z")
        self.assertEqual(filtered.status_code, 200, filtered.text)
        self.assertEqual(filtered.json()["total"], 1)
        self.assertEqual(filtered.json()["items"][0]["id"], str(self.ids[1]))
        before = self.client.get("/admin/simulations?created_before=2026-11-20T12:00:00Z")
        self.assertEqual(before.json()["total"], 1)
        self.assertEqual(before.json()["items"][0]["id"], str(self.ids[2]))

    def test_detail_and_missing(self):
        response = self.client.get(f"/admin/simulations/{self.ids[0]}")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["profile"], PROFILE)
        self.assertEqual(data["artifact_sha256"], "a" * 64)
        self.assertEqual(data["created_at"], "2026-11-20T12:00:00Z")
        absent = self.client.get(f"/admin/simulations/{UUID(int=10)}")
        self.assertEqual(absent.status_code, 404)
        self.assertEqual(absent.json()["error"]["code"], "not_found")

    def test_invalid_inputs_and_auth(self):
        for suffix in ("?page=0", "?page_size=101", "?prediction=2",
                       "?created_from=invalid", "?created_from=2026-11-20T12:00:00",
                       "?created_from=2026-11-21T00:00:00Z&created_before=2026-11-20T00:00:00Z"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.client.get("/admin/simulations" + suffix).status_code, 422)
        self.assertEqual(self.client.get("/admin/simulations/not-a-uuid").status_code, 422)
        api.dependency_overrides.pop(require_admin)
        self.assertEqual(self.client.get("/admin/simulations").status_code, 401)
        self.assertEqual(self.client.get(f"/admin/simulations/{self.ids[0]}").status_code, 401)
        self.assertEqual(self.client.get("/admin/simulations/not-a-uuid").status_code, 401)
        self.assertEqual(self.client.get("/admin/simulations?page=0").status_code, 401)

    def test_storage_failure_is_503_without_sql_details(self):
        class BrokenSession:
            def scalar(self, *_):
                raise sa.exc.OperationalError("SELECT private", {}, Exception("password-private"))

            def get(self, *_):
                raise sa.exc.OperationalError("SELECT private", {}, Exception("password-private"))

        api.dependency_overrides[get_session] = lambda: BrokenSession()
        for path in ("/admin/simulations", f"/admin/simulations/{self.ids[0]}"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "storage_unavailable")
            self.assertNotIn("private", response.text)
