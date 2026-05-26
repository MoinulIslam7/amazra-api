import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import get_settings


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode(), salt).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str, role: str) -> tuple[str, datetime, str]:
    settings = get_settings()
    expires_at = _utcnow() + timedelta(minutes=settings.jwt_access_ttl_minutes)
    token_id = str(uuid.uuid4())
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "jti": token_id,
        "iat": int(_utcnow().timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")
    return token, expires_at, token_id


def create_refresh_token(user_id: str) -> tuple[str, datetime, str]:
    settings = get_settings()
    expires_at = _utcnow() + timedelta(days=settings.jwt_refresh_ttl_days)
    token_id = str(uuid.uuid4())
    payload = {
        "sub": user_id,
        "type": "refresh",
        "jti": token_id,
        "iat": int(_utcnow().timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")
    return token, expires_at, token_id


def decode_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_public_key, algorithms=["RS256"])


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
