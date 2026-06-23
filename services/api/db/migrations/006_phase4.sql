-- Phase 4: Payment Integration
-- Stores one record per payment attempt; never stores card data.

CREATE TABLE IF NOT EXISTS payments (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id           UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  gateway            VARCHAR(30)   NOT NULL,   -- sslcommerz | bkash | nagad | cod
  gateway_session_id VARCHAR(200),              -- tran_id / paymentID / payment_ref_id
  gateway_ref        VARCHAR(200),              -- confirmed trx ref from gateway
  amount             DECIMAL(12, 2) NOT NULL,
  currency           VARCHAR(10)   DEFAULT 'BDT',
  status             VARCHAR(30)   NOT NULL DEFAULT 'pending',
    -- pending | initiated | paid | failed | cancelled | refunded
  raw_response       JSONB,                     -- full gateway response (audit only)
  idempotency_key    VARCHAR(200)  UNIQUE,       -- prevents duplicate processing
  created_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payments_order_id  ON payments(order_id);
CREATE INDEX IF NOT EXISTS idx_payments_session    ON payments(gateway_session_id);
CREATE INDEX IF NOT EXISTS idx_payments_status     ON payments(status);
CREATE INDEX IF NOT EXISTS idx_payments_gateway    ON payments(gateway);
CREATE INDEX IF NOT EXISTS idx_payments_created    ON payments(created_at);
