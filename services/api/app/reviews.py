from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .db import get_connection
from .deps import get_current_user, require_admin

router = APIRouter(prefix="/products", tags=["reviews"])
actions_router = APIRouter(tags=["reviews"])


class ReviewRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    title: Optional[str] = Field(None, max_length=200)
    comment: Optional[str] = None


class QuestionRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000)


class AnswerRequest(BaseModel):
    answer: str = Field(..., min_length=1, max_length=2000)


@router.get("/{product_id}/reviews")
def list_reviews(product_id: str, page: int = 1, per_page: int = 10):
    offset = max(page - 1, 0) * per_page
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT reviews.id, reviews.rating, reviews.title, reviews.comment,
                   reviews.is_verified_purchase, reviews.helpful_count,
                   reviews.created_at, users.name
            FROM reviews
            JOIN users ON users.id = reviews.user_id
            WHERE reviews.product_id = %s
            ORDER BY reviews.helpful_count DESC, reviews.created_at DESC
            LIMIT %s OFFSET %s
            """,
            (product_id, per_page, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM reviews WHERE product_id = %s",
            (product_id,),
        ).fetchone()

    return {
        "items": [
            {
                "id": str(row[0]),
                "rating": row[1],
                "title": row[2],
                "comment": row[3],
                "is_verified_purchase": row[4],
                "helpful_count": row[5],
                "created_at": row[6],
                "user_name": row[7],
            }
            for row in rows
        ],
        "total": total[0],
    }


@router.post("/{product_id}/reviews")
def create_review(
    product_id: str,
    payload: ReviewRequest,
    user=Depends(get_current_user),
):
    with get_connection() as conn:
        product = conn.execute(
            "SELECT id FROM products WHERE id = %s", (product_id,)
        ).fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        verified = conn.execute(
            """
            SELECT 1
            FROM order_items
            JOIN orders ON orders.id = order_items.order_id
            WHERE orders.user_id = %s
              AND order_items.product_id = %s
              AND orders.status = 'delivered'
            LIMIT 1
            """,
            (user["id"], product_id),
        ).fetchone()

        try:
            row = conn.execute(
                """
                INSERT INTO reviews (
                  product_id, user_id, rating, title, comment, is_verified_purchase
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    product_id,
                    user["id"],
                    payload.rating,
                    payload.title,
                    payload.comment,
                    bool(verified),
                ),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409,
                detail="You have already reviewed this product",
            ) from exc

    return {"id": str(row[0]), "is_verified_purchase": bool(verified)}


@actions_router.post("/reviews/{review_id}/helpful")
def mark_review_helpful(review_id: str, user=Depends(get_current_user)):
    with get_connection() as conn:
        review = conn.execute(
            "SELECT id FROM reviews WHERE id = %s", (review_id,)
        ).fetchone()
        if not review:
            raise HTTPException(status_code=404, detail="Review not found")

        try:
            conn.execute(
                "INSERT INTO review_votes (review_id, user_id) VALUES (%s, %s)",
                (review_id, user["id"]),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=409,
                detail="You already marked this review as helpful",
            ) from exc

        conn.execute(
            "UPDATE reviews SET helpful_count = helpful_count + 1 WHERE id = %s",
            (review_id,),
        )

    return {"status": "updated"}


@router.get("/{product_id}/questions")
def list_questions(product_id: str, page: int = 1, per_page: int = 10):
    offset = max(page - 1, 0) * per_page
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT product_questions.id, product_questions.question,
                   product_questions.answer, product_questions.answered_at,
                   product_questions.created_at, askers.name
            FROM product_questions
            JOIN users AS askers ON askers.id = product_questions.user_id
            WHERE product_questions.product_id = %s
            ORDER BY product_questions.created_at DESC
            LIMIT %s OFFSET %s
            """,
            (product_id, per_page, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM product_questions WHERE product_id = %s",
            (product_id,),
        ).fetchone()

    return {
        "items": [
            {
                "id": str(row[0]),
                "question": row[1],
                "answer": row[2],
                "answered_at": row[3],
                "created_at": row[4],
                "user_name": row[5],
            }
            for row in rows
        ],
        "total": total[0],
    }


@router.post("/{product_id}/questions")
def ask_question(
    product_id: str,
    payload: QuestionRequest,
    user=Depends(get_current_user),
):
    with get_connection() as conn:
        product = conn.execute(
            "SELECT id FROM products WHERE id = %s", (product_id,)
        ).fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        row = conn.execute(
            """
            INSERT INTO product_questions (product_id, user_id, question)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (product_id, user["id"], payload.question),
        ).fetchone()

    return {"id": str(row[0])}


@actions_router.post("/questions/{question_id}/answer")
def answer_question(
    question_id: str,
    payload: AnswerRequest,
    user=Depends(require_admin),
):
    with get_connection() as conn:
        updated = conn.execute(
            """
            UPDATE product_questions
            SET answer = %s, answered_by = %s, answered_at = NOW()
            WHERE id = %s
            """,
            (payload.answer, user["id"], question_id),
        )

    if updated.rowcount == 0:
        raise HTTPException(status_code=404, detail="Question not found")

    return {"status": "answered"}
