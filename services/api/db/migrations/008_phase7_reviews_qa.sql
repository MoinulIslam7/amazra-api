-- Phase 7: Reviews & Ratings, Product Q&A, Admin 2FA

CREATE TABLE IF NOT EXISTS reviews (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id           UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  user_id              UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  rating               SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
  title                VARCHAR(200),
  comment              TEXT,
  is_verified_purchase BOOLEAN DEFAULT FALSE,
  helpful_count        INT DEFAULT 0,
  created_at           TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at           TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE (product_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id);

CREATE TABLE IF NOT EXISTS review_votes (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  review_id  UUID NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE (review_id, user_id)
);

CREATE TABLE IF NOT EXISTS product_questions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  product_id   UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question     TEXT NOT NULL,
  answer       TEXT,
  answered_by  UUID REFERENCES users(id),
  answered_at  TIMESTAMP WITH TIME ZONE,
  created_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_product_questions_product ON product_questions(product_id);

-- Admin/staff TOTP-based 2FA
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS totp_secret  VARCHAR(64),
  ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN DEFAULT FALSE;
