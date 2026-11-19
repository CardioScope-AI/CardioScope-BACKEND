"""Configuration tests and opt-in migration checks on an isolated PostgreSQL DB."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from alembic import command
from alembic.config import Config
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import database_url, get_engine
from app.models import Admin, AdminSession, Base, CsrfContext, LoginAttemptWindow, Simulation


ROOT = Path(__file__).resolve().parents[1]
PROFILE = dict(age=63, sex=1, cp=1, trestbps=145, chol=233, fbs=1,
               restecg=2, thalach=150, exang=0, oldpeak=2.3, slope=3, ca=0, thal=6)


class DatabaseConfigurationTests(unittest.TestCase):
    def test_url_preserves_special_characters_without_concatenation(self):
        secret = "test-only:p@ss/%#"
        url = database_url(dict(DB_HOST="localhost", DB_USER="admin", DB_NAME="demo",
                                DB_PASSWORD=secret))
        self.assertEqual(url.password, secret)
        self.assertEqual(url.port, 5432)
        self.assertNotIn(secret, str(url))

    def test_missing_configuration_and_invalid_ports_do_not_echo_secrets(self):
        for env in ({}, dict(DB_HOST="db", DB_NAME="demo", DB_USER="admin",
                             DB_PASSWORD="test-secret", DB_PORT="70000")):
            with self.assertRaises(ValueError) as error:
                database_url(env)
            self.assertNotIn("test-secret", str(error.exception))


@unittest.skipUnless(os.environ.get("CARDIOSCOPE_TEST_POSTGRES") == "1",
                     "Set CARDIOSCOPE_TEST_POSTGRES=1 for isolated PostgreSQL migration tests")
class PostgreSQLMigrationTests(unittest.TestCase):
    def test_migration_constraints_cascade_and_round_trip(self):
        # Never downgrade or remove the configured application database.
        name = "cardioscope_test_" + uuid.uuid4().hex
        control = sa.create_engine(database_url().set(database="postgres"),
                                   isolation_level="AUTOCOMMIT", hide_parameters=True)
        with control.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        try:
            with patch.dict(os.environ, {"DB_NAME": name}):
                get_engine.cache_clear()
                config = Config(str(ROOT / "alembic.ini"))
                command.upgrade(config, "head")
                command.upgrade(config, "head")
                engine = get_engine()
                self.assertTrue(set(Base.metadata.tables) <= set(sa.inspect(engine).get_table_names()))
                now = datetime.now(timezone.utc)
                expiry = now + timedelta(hours=8)

                def simulation(**overrides):
                    values = dict(PROFILE, prediction=1, model_version="test-release",
                                  artifact_sha256="a" * 64)
                    values.update(overrides)
                    return Simulation(**values)

                with Session(engine, expire_on_commit=False) as session:
                    self.assertEqual(session.scalar(sa.select(sa.func.count()).select_from(Admin)), 0)
                    admin = Admin(username="test-admin", password_hash="test-placeholder-not-a-password")
                    session.add(admin)
                    session.flush()
                    login = AdminSession(admin_id=admin.id, token_hash="b" * 64,
                                         created_at=now, expires_at=expiry)
                    session.add(login)
                    session.flush()
                    session.add_all([
                        CsrfContext(binding_hash="c" * 64, token_hash="d" * 64,
                                    session_id=login.id, created_at=now, expires_at=expiry),
                        LoginAttemptWindow(key_hash="e" * 64, expires_at=expiry),
                        simulation(),
                    ])
                    session.commit()
                    admin_id = admin.id

                with Session(engine) as session:
                    saved = session.scalars(sa.select(Simulation)).one()
                    self.assertEqual(saved.prediction, 1)
                    self.assertIsNotNone(saved.created_at.utcoffset())
                    self.assertEqual(saved.chol, PROFILE["chol"])

                invalid_rows = [
                    Admin(username="another", password_hash="test-placeholder"),
                    Admin(username="bypass", password_hash="test-placeholder", singleton=False),
                    AdminSession(admin_id=admin_id, token_hash="b" * 64, expires_at=expiry),
                    AdminSession(admin_id=uuid.uuid4(), token_hash="f" * 64, expires_at=expiry),
                    simulation(prediction=2), simulation(cp=9), simulation(chol=None),
                    simulation(oldpeak=float("nan")), simulation(trestbps=float("inf")),
                    simulation(artifact_sha256="invalid"), simulation(model_version=" "),
                    CsrfContext(binding_hash="1" * 64, token_hash="2" * 64,
                                session_id=uuid.uuid4(), expires_at=expiry),
                    LoginAttemptWindow(key_hash="3" * 64, failed_attempts=-1, expires_at=expiry),
                ]
                for row in invalid_rows:
                    with self.subTest(model=type(row).__name__):
                        with Session(engine) as session:
                            with self.assertRaises(IntegrityError):
                                session.add(row)
                                session.commit()

                with Session(engine) as session:
                    session.execute(sa.delete(Admin).where(Admin.id == admin_id))
                    session.commit()
                    for model in (AdminSession, CsrfContext):
                        self.assertEqual(session.scalar(sa.select(sa.func.count()).select_from(model)), 0)
                    self.assertEqual(session.scalar(sa.select(sa.func.count()).select_from(Simulation)), 1)

                command.check(config)
                command.downgrade(config, "base")
                self.assertFalse(set(Base.metadata.tables) & set(sa.inspect(engine).get_table_names()))
                command.upgrade(config, "head")
                command.check(config)
        finally:
            if get_engine.cache_info().currsize:
                get_engine().dispose()
            get_engine.cache_clear()
            # The identifier is constructed only from a fixed prefix and uuid.hex.
            with control.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
            control.dispose()


if __name__ == "__main__":
    unittest.main()
