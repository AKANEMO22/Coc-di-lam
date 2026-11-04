import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

import jwt

from app.config import Settings

STUDENT_ID = re.compile(r"[A-Z]{2}[0-9]{6}")
USERNAME = re.compile(r"[\w.@+\- ]{1,64}")


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Principal:
    student_id: str
    name: str


def normalize_student_id(value: str) -> str:
    return value.strip().upper()


def normalize_username(value: str) -> str:
    """Open sign-in: one account per username, case and extra spaces ignored ("An  Nguyen" = "an nguyen")."""
    return " ".join(value.split()).casefold()


def load_or_create_secret(path: Path) -> str:
    """Signing secret for open sign-in when AUTH_JWT_SECRET is not set: generated once and kept with the
    gateway database (a Docker volume), so tokens survive restarts and redeploys."""
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if len(existing) >= 32:
            return existing
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(48)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(secret)
    return secret


def check_account(settings: Settings, student_id: str, access_code: str) -> str:
    """Validate the typed account (and the class access code in student mode); returns it normalized.
    open: any username; off and student: a student id such as SE100001."""
    if settings.auth_mode == "open":
        student_id = normalize_username(student_id)
        if not student_id or not USERNAME.fullmatch(student_id):
            raise AuthError(422, "Mã sinh viên gồm 1-64 ký tự: chữ, số, khoảng trắng hoặc . _ - @ +")
        return student_id
    student_id = normalize_student_id(student_id)
    if not STUDENT_ID.fullmatch(student_id):
        raise AuthError(422, "Mã số sinh viên không hợp lệ.")
    if settings.student_id_pattern and not re.fullmatch(settings.student_id_pattern, student_id):
        raise AuthError(403, "Mã số sinh viên không được phép đăng nhập.")
    if (settings.auth_mode == "student" and settings.access_code
            and not hmac.compare_digest(access_code.encode(), settings.access_code.encode())):
        raise AuthError(401, "Mã truy cập không đúng.")
    return student_id


def issue_token(settings: Settings, student_id: str, name: str) -> tuple[str, int] | None:
    """Sign in an account check_account already validated. Off mode without a secret has nothing to sign: None."""
    if len(settings.auth_jwt_secret) < 32:
        if not settings.auth_required:
            return None
        raise AuthError(503, "Gateway chưa cấu hình AUTH_JWT_SECRET.")
    now = int(time.time())
    claims = {"iss": settings.auth_issuer, "sub": student_id, "name": name.strip()[:120],
              "iat": now, "exp": now + settings.auth_ttl_sec}
    return jwt.encode(claims, settings.auth_jwt_secret, algorithm="HS256"), settings.auth_ttl_sec


def verify(settings: Settings, token: str | None) -> Principal | None:
    if not token or len(settings.auth_jwt_secret) < 32:
        return None
    try:
        claims = jwt.decode(token, settings.auth_jwt_secret, algorithms=["HS256"], issuer=settings.auth_issuer,
                            options={"require": ["exp", "sub", "iss"]})
    except jwt.PyJWTError:
        return None
    return Principal(student_id=str(claims["sub"]), name=str(claims.get("name") or ""))


def bearer(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    return value.strip() or None if scheme.lower() == "bearer" else None


def origin_allowed(origin: str | None, allowed: list[str]) -> bool:
    if not origin or "*" in allowed:
        return True
    return origin.rstrip("/").lower() in {item.rstrip("/").lower() for item in allowed}
