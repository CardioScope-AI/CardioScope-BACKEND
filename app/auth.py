"""Server-side administrator login, CSRF contexts and shared login limits."""

from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
import secrets
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_session
from app.errors import error_response
from app.models import Admin, AdminSession, CsrfContext, LoginAttemptWindow
from app.passwords import hash_password, verify_password

SESSION_COOKIE = "cardioscope_admin"
CSRF_COOKIE = "cardioscope_csrf"
ABSOLUTE_SECONDS = 28800
IDLE_SECONDS = 1800
CSRF_SECONDS = 600
WINDOW_SECONDS = 900
# A real Argon2 operation is performed even for an unknown username.
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))
class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


router = APIRouter(prefix="/admin", tags=["Administration"], responses={
    status: {"model": ErrorEnvelope, "description": description}
    for status, description in {
        401: "Identifiants ou session invalides", 403: "Protection CSRF refusée",
        429: "Trop de tentatives ; Retry-After en secondes", 503: "Stockage indisponible",
    }.items()
})


class AuthError(Exception):
    def __init__(self, status, code, message, headers=None):
        self.status, self.code, self.message = status, code, message
        self.headers = headers


def deny_csrf():
    raise AuthError(403, "csrf_failed", "Origine ou protection CSRF invalide.")


def utcnow():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def auth_database():
    try:
        yield from get_session()
    except (SQLAlchemyError, ValueError):
        raise AuthError(503, "storage_unavailable", "Le stockage est indisponible.") from None


def secure_cookies():
    # Opt out only for explicitly configured local HTTP development.
    return os.environ.get("AUTH_COOKIE_SECURE", "true").lower() != "false"


def set_cookie(response, name, value, seconds):
    response.set_cookie(name, value, max_age=seconds, httponly=True,
                        secure=secure_cookies(), samesite="lax", path="/")


def find_session(db, request, now):
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 256:
        return None
    return db.execute(sa.select(AdminSession, Admin).join(Admin).where(
        AdminSession.token_hash == digest(token),
        AdminSession.revoked_at.is_(None), AdminSession.expires_at > now,
        AdminSession.last_seen_at > now - timedelta(seconds=IDLE_SECONDS),
        Admin.active.is_(True),
    ).with_for_update(of=AdminSession)).first()


def check_csrf(db, request, now, session_id):
    origins = [x.strip() for x in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",") if x.strip()]
    if request.headers.get("origin") not in origins:
        deny_csrf()
    binding = request.cookies.get(CSRF_COOKIE, "")
    token = request.headers.get("x-csrf-token", "")
    if not binding or not token or max(len(binding), len(token)) > 256:
        deny_csrf()
    context = db.scalar(sa.select(CsrfContext).where(
        CsrfContext.binding_hash == digest(binding), CsrfContext.expires_at > now
    ).with_for_update())
    if (context is None or context.session_id != session_id
            or not secrets.compare_digest(context.token_hash, digest(token))):
        deny_csrf()
    return context


def new_context(db, response, now, session=None):
    binding, token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expiry = min(session.expires_at, now + timedelta(seconds=CSRF_SECONDS)) if session else now + timedelta(seconds=CSRF_SECONDS)
    db.add(CsrfContext(id=uuid4(), binding_hash=digest(binding), token_hash=digest(token),
                       session_id=session.id if session else None, created_at=now, expires_at=expiry))
    set_cookie(response, CSRF_COOKIE, binding, max(1, int((expiry - now).total_seconds())))
    return {"csrf_token": token, "expires_at": expiry}


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100, strict=True)
    password: str = Field(min_length=1, max_length=128, strict=True)


class AdminIdentity(BaseModel):
    id: str
    username: str


class SessionInfo(BaseModel):
    expires_at: datetime
    idle_timeout_seconds: int


class IdentityResponse(BaseModel):
    admin: AdminIdentity
    session: SessionInfo


class CsrfResponse(BaseModel):
    csrf_token: str
    expires_at: datetime


def identity(admin, session):
    return {"admin": {"id": str(admin.id), "username": admin.username},
            "session": {"expires_at": session.expires_at, "idle_timeout_seconds": IDLE_SECONDS}}


def limit_rows(db, username, network, now):
    # Row locks serialize attempts across workers; always lock in key order.
    keys = [(digest("network:" + network), 30),
            (digest("pair:" + username + "\0" + network), 5)]
    result = []
    for key, threshold in sorted(keys):
        db.execute(insert(LoginAttemptWindow).values(
            key_hash=key, window_started_at=now, failed_attempts=0,
            expires_at=now + timedelta(seconds=WINDOW_SECONDS),
        ).on_conflict_do_nothing(index_elements=["key_hash"]))
        row = db.scalar(sa.select(LoginAttemptWindow).where(
            LoginAttemptWindow.key_hash == key).with_for_update())
        if row.expires_at <= now:
            row.window_started_at = now
            row.expires_at = now + timedelta(seconds=WINDOW_SECONDS)
            row.failed_attempts, row.blocked_until = 0, None
        if row.blocked_until and row.blocked_until > now:
            wait = max(1, math.ceil((row.blocked_until - now).total_seconds()))
            raise AuthError(429, "rate_limited", "Trop de tentatives de connexion.", {"Retry-After": str(wait)})
        result.append((row, threshold))
    return result


@router.get("/csrf", response_model=CsrfResponse)
def csrf(request: Request, response: Response, db=Depends(auth_database)):
    now = utcnow()
    found = find_session(db, request, now)
    # Renew only this browser context, never another browser's context.
    binding = request.cookies.get(CSRF_COOKIE, "")
    if binding and len(binding) <= 256:
        db.execute(sa.delete(CsrfContext).where(CsrfContext.binding_hash == digest(binding)))
    result = new_context(db, response, now, found[0] if found else None)
    db.commit()
    return result


@router.post("/login", response_model=IdentityResponse)
def login(payload: LoginInput, request: Request, response: Response,
          origin: str | None = Header(default=None, description="Origine frontend autorisée"),
          x_csrf_token: str | None = Header(default=None, description="Jeton obtenu via GET /admin/csrf"),
          db=Depends(auth_database)):
    now = utcnow()
    existing = find_session(db, request, now)
    context = check_csrf(db, request, now, existing[0].id if existing else None)
    # Do not read user-supplied X-Forwarded-For. Configure trusted proxies separately.
    network = request.client.host if request.client else "unknown"
    limits = limit_rows(db, payload.username, network, now)
    admin = db.scalar(sa.select(Admin).where(Admin.username == payload.username))
    valid = verify_password(admin.password_hash if admin else DUMMY_HASH, payload.password)
    if not valid or admin is None or not admin.active:
        for row, threshold in limits:
            row.failed_attempts += 1
            if row.failed_attempts >= threshold:
                row.blocked_until = row.expires_at
        db.commit()
        raise AuthError(401, "invalid_credentials", "Identifiants incorrects.")
    # Preserve network failures so changing usernames cannot bypass the global limit.
    for row, threshold in limits:
        if threshold == 5:
            row.failed_attempts, row.blocked_until = 0, None
    token = secrets.token_urlsafe(32)
    now = utcnow()
    session = AdminSession(id=uuid4(), admin_id=admin.id, token_hash=digest(token),
                           created_at=now, expires_at=now + timedelta(seconds=ABSOLUTE_SECONDS),
                           last_seen_at=now)
    db.add(session)
    db.flush()
    db.delete(context)
    new_context(db, response, now, session)
    db.commit()
    set_cookie(response, SESSION_COOKIE, token, ABSOLUTE_SECONDS)
    return identity(admin, session)


def session_database(request: Request):
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 256:
        raise AuthError(401, "unauthenticated", "Connectez-vous pour accéder à cet espace.")
    yield from auth_database()


def require_admin(request: Request, db=Depends(session_database)):
    found = find_session(db, request, utcnow())
    if found is None:
        raise AuthError(401, "unauthenticated", "Connectez-vous pour accéder à cet espace.")
    session, admin = found
    session.last_seen_at = utcnow()
    db.commit()
    return admin, session


@router.get("/me", response_model=IdentityResponse)
def me(auth=Depends(require_admin)):
    return identity(*auth)


@router.post("/logout", status_code=204, responses={
    204: {"description": "Session révoquée et cookies supprimés."},
})
def logout(request: Request, response: Response, db=Depends(auth_database)):
    now = utcnow()
    found = find_session(db, request, now)
    if found is not None:
        session, _ = found
        check_csrf(db, request, now, session.id)
        session.revoked_at = now
        db.execute(sa.delete(CsrfContext).where(CsrfContext.session_id == session.id))

    # Also remove this browser's anonymous or expired CSRF context. An expired
    # session is already unusable, so logout remains safe to repeat.
    binding = request.cookies.get(CSRF_COOKIE, "")
    if binding and len(binding) <= 256:
        db.execute(sa.delete(CsrfContext).where(CsrfContext.binding_hash == digest(binding)))
    db.commit()

    set_cookie(response, SESSION_COOKIE, "", 0)
    set_cookie(response, CSRF_COOKIE, "", 0)
    response.status_code = 204
    return response


def register_auth(api):
    @api.exception_handler(AuthError)
    async def auth_error(request, exc):
        return error_response(exc.status, exc.code, exc.message,
                              headers={"Cache-Control": "no-store", **(exc.headers or {})})

    @api.middleware("http")
    async def no_store_admin(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/admin/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    api.include_router(router)
