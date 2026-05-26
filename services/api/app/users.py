from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from .db import get_connection
from .deps import get_current_user
from .validators import validate_bd_phone

router = APIRouter(prefix="/users", tags=["users"])


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    email: Optional[EmailStr] = None
    phone: Optional[str] = None


class AddressRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    phone: str
    line1: str = Field(..., min_length=3, max_length=300)
    line2: Optional[str] = Field(None, max_length=300)
    district: str = Field(..., min_length=2, max_length=100)
    division: str = Field(..., min_length=2, max_length=100)
    postcode: str = Field(..., min_length=4, max_length=20)
    is_default: bool = False


@router.get("/me")
def get_profile(user=Depends(get_current_user)):
    return user


@router.patch("/me")
def update_profile(
    payload: ProfileUpdateRequest,
    user=Depends(get_current_user),
):
    updates = {}
    if payload.name:
        updates["name"] = payload.name
    if payload.email:
        updates["email"] = payload.email
    if payload.phone:
        validate_bd_phone(payload.phone)
        updates["phone"] = payload.phone

    if not updates:
        return {"status": "no_changes"}

    set_clause = ", ".join(f"{key} = %({key})s" for key in updates.keys())
    updates["user_id"] = user["id"]

    with get_connection() as conn:
        try:
            query = (
                f"UPDATE users SET {set_clause}, updated_at = NOW() "
                "WHERE id = %(user_id)s"
            )
            conn.execute(query, updates)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409, detail="Email or phone already in use"
            ) from exc

    return {"status": "updated"}


@router.post("/me/addresses")
def create_address(payload: AddressRequest, user=Depends(get_current_user)):
    validate_bd_phone(payload.phone)
    with get_connection() as conn:
        if payload.is_default:
            conn.execute(
                "UPDATE addresses SET is_default = FALSE WHERE user_id = %s",
                (user["id"],),
            )
        row = conn.execute(
            """
            INSERT INTO addresses (
              user_id,
              name,
              phone,
              line1,
              line2,
              district,
              division,
              postcode,
              is_default
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                user["id"],
                payload.name,
                payload.phone,
                payload.line1,
                payload.line2,
                payload.district,
                payload.division,
                payload.postcode,
                payload.is_default,
            ),
        ).fetchone()

    return {"id": str(row[0])}


@router.get("/me/addresses")
def list_addresses(user=Depends(get_current_user)):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
              id,
              name,
              phone,
              line1,
              line2,
              district,
              division,
              postcode,
              is_default
            FROM addresses
            WHERE user_id = %s
            ORDER BY is_default DESC, created_at DESC
            """,
            (user["id"],),
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "name": row[1],
            "phone": row[2],
            "line1": row[3],
            "line2": row[4],
            "district": row[5],
            "division": row[6],
            "postcode": row[7],
            "is_default": row[8],
        }
        for row in rows
    ]


@router.put("/me/addresses/{address_id}")
def update_address(
    address_id: str,
    payload: AddressRequest,
    user=Depends(get_current_user),
):
    validate_bd_phone(payload.phone)
    with get_connection() as conn:
        if payload.is_default:
            conn.execute(
                "UPDATE addresses SET is_default = FALSE WHERE user_id = %s",
                (user["id"],),
            )
        updated = conn.execute(
            """
            UPDATE addresses
            SET name = %s, phone = %s, line1 = %s, line2 = %s,
                district = %s, division = %s, postcode = %s, is_default = %s,
                updated_at = NOW()
            WHERE id = %s AND user_id = %s
            """,
            (
                payload.name,
                payload.phone,
                payload.line1,
                payload.line2,
                payload.district,
                payload.division,
                payload.postcode,
                payload.is_default,
                address_id,
                user["id"],
            ),
        )

    if updated.rowcount == 0:
        raise HTTPException(status_code=404, detail="Address not found")

    return {"status": "updated"}


@router.delete(
    "/me/addresses/{address_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_address(address_id: str, user=Depends(get_current_user)):
    with get_connection() as conn:
        result = conn.execute(
            "DELETE FROM addresses WHERE id = %s AND user_id = %s",
            (address_id, user["id"]),
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Address not found")
    return None
