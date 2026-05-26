from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import get_connection
from .redis_client import get_redis
from .security import decode_token

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except Exception as exc:  # noqa: BLE001 - surface auth failures explicitly
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from exc

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    token_id = payload.get("jti")
    redis_client = get_redis()
    if token_id and redis_client.get(f"token_blocklist:{token_id}"):
        raise HTTPException(status_code=401, detail="Token revoked")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              users.id,
              users.name,
              users.email,
              users.phone,
              users.is_active,
              roles.name AS role_name
            FROM users
            LEFT JOIN roles ON users.role_id = roles.id
            WHERE users.id = %s
            """,
            (user_id,),
        ).fetchone()

    if not row or not row[4]:
        raise HTTPException(status_code=401, detail="User inactive")

    return {
        "id": str(row[0]),
        "name": row[1],
        "email": row[2],
        "phone": row[3],
        "role": row[5] or "customer",
    }


def require_admin(user=Depends(get_current_user)):
    if user["role"] not in {"admin", "staff"}:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
