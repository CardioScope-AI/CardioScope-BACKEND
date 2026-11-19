"""Storage schema for the single-admin application; no authentication logic."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


class Admin(Base):
    __tablename__ = "admins"
    __table_args__ = (
        sa.CheckConstraint("singleton IS TRUE", name="singleton_true"),
        sa.CheckConstraint("length(trim(username)) > 0", name="username_not_empty"),
        sa.CheckConstraint("length(password_hash) > 0", name="password_hash_not_empty"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()"))
    singleton: Mapped[bool] = mapped_column(sa.Boolean, unique=True, server_default=sa.true())
    username: Mapped[str] = mapped_column(sa.String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(sa.Text)
    active: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.true())
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = (
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_format"),
        sa.CheckConstraint("expires_at > created_at", name="expiration_order"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()"))
    admin_id: Mapped[UUID] = mapped_column(sa.ForeignKey("admins.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class Simulation(Base):
    __tablename__ = "simulations"
    __table_args__ = (
        sa.CheckConstraint("prediction IN (0, 1)", name="binary_prediction"),
        sa.CheckConstraint("length(trim(model_version)) > 0", name="model_version_not_empty"),
        sa.CheckConstraint("artifact_sha256 ~ '^[0-9a-f]{64}$'", name="artifact_hash_format"),
        sa.CheckConstraint("sex IN (0, 1) AND fbs IN (0, 1) AND exang IN (0, 1)", name="binary_codes"),
        sa.CheckConstraint("cp IN (1, 2, 3, 4) AND restecg IN (0, 1, 2) AND slope IN (1, 2, 3) AND ca IN (0, 1, 2, 3) AND thal IN (3, 6, 7)", name="category_codes"),
        sa.CheckConstraint(" AND ".join(
            f"{name} > '-Infinity'::float8 AND {name} < 'Infinity'::float8"
            for name in ("trestbps", "chol", "oldpeak")
        ), name="finite_measurements"),
        sa.Index("ix_simulations_created_at_id", sa.desc("created_at"), sa.desc("id")),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()"))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    prediction: Mapped[int] = mapped_column(sa.SmallInteger)
    model_version: Mapped[str] = mapped_column(sa.String(200))
    artifact_sha256: Mapped[str] = mapped_column(sa.String(64))
    age: Mapped[int] = mapped_column(sa.Integer)
    sex: Mapped[int] = mapped_column(sa.SmallInteger)
    cp: Mapped[int] = mapped_column(sa.SmallInteger)
    trestbps: Mapped[float] = mapped_column(sa.Double)
    chol: Mapped[float] = mapped_column(sa.Double)
    fbs: Mapped[int] = mapped_column(sa.SmallInteger)
    restecg: Mapped[int] = mapped_column(sa.SmallInteger)
    thalach: Mapped[int] = mapped_column(sa.Integer)
    exang: Mapped[int] = mapped_column(sa.SmallInteger)
    oldpeak: Mapped[float] = mapped_column(sa.Double)
    slope: Mapped[int] = mapped_column(sa.SmallInteger)
    ca: Mapped[int] = mapped_column(sa.SmallInteger)
    thal: Mapped[int] = mapped_column(sa.SmallInteger)


class CsrfContext(Base):
    __tablename__ = "csrf_contexts"
    __table_args__ = (
        sa.CheckConstraint("binding_hash ~ '^[0-9a-f]{64}$' AND token_hash ~ '^[0-9a-f]{64}$'", name="hash_formats"),
        sa.CheckConstraint("expires_at > created_at", name="expiration_order"),
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, server_default=sa.text("gen_random_uuid()"))
    binding_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    token_hash: Mapped[str] = mapped_column(sa.String(64))
    session_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("admin_sessions.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)


class LoginAttemptWindow(Base):
    __tablename__ = "login_attempt_windows"
    __table_args__ = (
        sa.CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="key_hash_format"),
        sa.CheckConstraint("failed_attempts >= 0", name="nonnegative_attempts"),
        sa.CheckConstraint("expires_at > window_started_at", name="expiration_order"),
    )
    key_hash: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    failed_attempts: Mapped[int] = mapped_column(sa.Integer, server_default="0")
    blocked_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
