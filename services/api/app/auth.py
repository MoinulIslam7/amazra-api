import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field

from .config import get_settings
from .db import get_connection
from .redis_client import get_redis
from .security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from .validators import validate_bd_phone

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    email: EmailStr
    phone: str
    password: str = Field(..., min_length=8)


class LoginRequest(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password: str


class OtpSendRequest(BaseModel):
    phone: str


class OtpVerifyRequest(BaseModel):
    phone: str
    otp: str = Field(..., min_length=4, max_length=6)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str
    access_token: Optional[str] = None


def _issue_tokens(user_id: str, role: str) -> dict:
    access_token, access_exp, access_jti = create_access_token(user_id, role)
    refresh_token, refresh_exp, refresh_jti = create_refresh_token(user_id)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO refresh_tokens (user_id, token_hash, jti, expires_at)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, hash_token(refresh_token), refresh_jti, refresh_exp),
        )

    return {
        "access_token": access_token,
        "access_expires_at": access_exp,
        "refresh_token": refresh_token,
        "refresh_expires_at": refresh_exp,
        "token_type": "bearer",
        "access_jti": access_jti,
    }


@router.post("/register")
def register(payload: RegisterRequest):
    validate_bd_phone(payload.phone)
    password_hash = hash_password(payload.password)

    with get_connection() as conn:
        role_row = conn.execute(
            "SELECT id FROM roles WHERE name = %s",
            ("customer",),
        ).fetchone()
        if not role_row:
            raise HTTPException(status_code=500, detail="Default role missing")
        try:
            row = conn.execute(
                """
                INSERT INTO users (name, email, phone, password_hash, role_id)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    payload.name,
                    payload.email,
                    payload.phone,
                    password_hash,
                    role_row[0],
                ),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409, detail="Email or phone already registered"
            ) from exc

    return {"id": str(row[0])}


@router.post("/login")
def login(payload: LoginRequest):
    if not payload.email and not payload.phone:
        raise HTTPException(
            status_code=400,
            detail="Email or phone is required",
        )

    if payload.phone:
        validate_bd_phone(payload.phone)

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT users.id, users.password_hash, roles.name, users.is_active
            FROM users
            LEFT JOIN roles ON users.role_id = roles.id
            WHERE (%s IS NOT NULL AND users.email = %s)
               OR (%s IS NOT NULL AND users.phone = %s)
            """,
            (payload.email, payload.email, payload.phone, payload.phone),
        ).fetchone()

    if not row or not row[1] or not row[3]:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(payload.password, row[1]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return _issue_tokens(str(row[0]), row[2] or "customer")


@router.post("/otp/send")
def send_otp(payload: OtpSendRequest):
    validate_bd_phone(payload.phone)
    settings = get_settings()
    redis_client = get_redis()

    rate_key = f"otp_rate:{payload.phone}"
    current = redis_client.incr(rate_key)
    if current == 1:
        redis_client.expire(rate_key, 3600)
    if current > settings.otp_rate_limit_per_hour:
        raise HTTPException(status_code=429, detail="OTP rate limit exceeded")

    otp = f"{secrets.randbelow(1000000):06d}"
    redis_client.setex(f"otp:{payload.phone}", settings.otp_ttl_seconds, otp)
    # In production, send via SMS provider. We log in dev to aid local testing.
    print(f"OTP for {payload.phone}: {otp}")
    return {"status": "sent"}


@router.post("/otp/verify")
def verify_otp(payload: OtpVerifyRequest):
    validate_bd_phone(payload.phone)
    redis_client = get_redis()
    cached = redis_client.get(f"otp:{payload.phone}")
    if not cached or cached != payload.otp:
        raise HTTPException(status_code=401, detail="Invalid OTP")

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT users.id, roles.name, users.is_active
            FROM users
            LEFT JOIN roles ON users.role_id = roles.id
            WHERE users.phone = %s
            """,
            (payload.phone,),
        ).fetchone()

    if not row or not row[2]:
        raise HTTPException(status_code=404, detail="User not found")

    redis_client.delete(f"otp:{payload.phone}")
    return _issue_tokens(str(row[0]), row[1] or "customer")


@router.post("/refresh")
def refresh_token(payload: RefreshRequest):
    try:
        token_payload = decode_token(payload.refresh_token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    if token_payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    token_hash = hash_token(payload.refresh_token)
    now = datetime.now(timezone.utc)
    redis_client = get_redis()
    jti = token_payload.get("jti")
    if jti and redis_client.get(f"refresh_blocklist:{jti}"):
        raise HTTPException(status_code=401, detail="Refresh token revoked")

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              refresh_tokens.id,
              refresh_tokens.user_id,
              refresh_tokens.revoked_at,
              refresh_tokens.expires_at,
              roles.name
            FROM refresh_tokens
            JOIN users ON refresh_tokens.user_id = users.id
            LEFT JOIN roles ON users.role_id = roles.id
            WHERE refresh_tokens.token_hash = %s AND refresh_tokens.jti = %s
            """,
            (token_hash, token_payload.get("jti")),
        ).fetchone()

        if not row or row[2] or row[3] < now:
            raise HTTPException(
                status_code=401,
                detail="Refresh token revoked",
            )

        conn.execute(
            "UPDATE refresh_tokens SET revoked_at = %s WHERE id = %s",
            (now, row[0]),
        )

    return _issue_tokens(str(row[1]), row[4] or "customer")


@router.post("/logout")
def logout(payload: LogoutRequest):
    now = datetime.now(timezone.utc)
    token_hash = hash_token(payload.refresh_token)
    redis_client = get_redis()

    with get_connection() as conn:
        conn.execute(
            "UPDATE refresh_tokens SET revoked_at = %s WHERE token_hash = %s",
            (now, token_hash),
        )

    try:
        refresh_payload = decode_token(payload.refresh_token)
    except Exception:
        refresh_payload = None
    if refresh_payload and refresh_payload.get("jti"):
        exp = refresh_payload.get("exp")
        ttl = (
            max(0, int(exp - datetime.now(timezone.utc).timestamp()))
            if exp
            else 0
        )
        if ttl > 0:
            redis_client.setex(
                f"refresh_blocklist:{refresh_payload['jti']}", ttl, "1"
            )

    if payload.access_token:
        try:
            access_payload = decode_token(payload.access_token)
        except Exception:
            access_payload = None
        if access_payload and access_payload.get("jti"):
            exp = access_payload.get("exp")
            ttl = (
                max(0, int(exp - datetime.now(timezone.utc).timestamp()))
                if exp
                else 0
            )
            if ttl > 0:
                redis_client.setex(
                    f"token_blocklist:{access_payload['jti']}", ttl, "1"
                )

    return {"status": "logged_out"}
