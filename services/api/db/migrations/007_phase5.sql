-- Phase 5: Delivery & Notifications

-- Extend branches with pickup support and opening hours
ALTER TABLE branches
  ADD COLUMN IF NOT EXISTS opening_hours      JSONB,
  ADD COLUMN IF NOT EXISTS is_pickup_available BOOLEAN DEFAULT TRUE;

-- Extend orders with Click & Collect (pickup) support
ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS fulfilment_type      VARCHAR(20) DEFAULT 'delivery',
  ADD COLUMN IF NOT EXISTS pickup_branch_id     UUID REFERENCES branches(id),
  ADD COLUMN IF NOT EXISTS pickup_ready_at      TIMESTAMP WITH TIME ZONE,
  ADD COLUMN IF NOT EXISTS pickup_confirmed_at  TIMESTAMP WITH TIME ZONE;

-- Configurable shipping zones with per-district mapping
CREATE TABLE IF NOT EXISTS delivery_zones (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        VARCHAR(100)  NOT NULL,
  districts   TEXT[]        NOT NULL DEFAULT '{}',  -- BD district names covered
  base_rate   DECIMAL(10, 2) NOT NULL DEFAULT 0,
  rate_per_kg DECIMAL(10, 2) NOT NULL DEFAULT 0,
  is_active   BOOLEAN DEFAULT TRUE,
  created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- One record per courier dispatch (parcel creation call)
CREATE TABLE IF NOT EXISTS courier_dispatches (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id       UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  courier        VARCHAR(30) NOT NULL,     -- pathao | steadfast
  consignment_id VARCHAR(200),             -- courier-internal parcel/consignment ID
  tracking_id    VARCHAR(200),             -- public tracking code shown to customer
  status         VARCHAR(50) DEFAULT 'created',
  raw_response   JSONB,
  created_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Per-user notification channel preferences
CREATE TABLE IF NOT EXISTS notification_preferences (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  sms_order_updates   BOOLEAN DEFAULT TRUE,
  email_order_updates BOOLEAN DEFAULT TRUE,
  sms_marketing       BOOLEAN DEFAULT FALSE,
  email_marketing     BOOLEAN DEFAULT FALSE,
  updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Audit log of every notification attempt
CREATE TABLE IF NOT EXISTS notification_log (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
  channel    VARCHAR(20)  NOT NULL,    -- sms | email
  event_type VARCHAR(50)  NOT NULL,
  recipient  VARCHAR(200) NOT NULL,    -- phone number or email address
  status     VARCHAR(20)  DEFAULT 'pending',
    -- pending | sent | failed | dead_lettered
  attempts   INT          DEFAULT 0,
  last_error TEXT,
  sent_at    TIMESTAMP WITH TIME ZONE,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Price drop alert subscriptions (max 20 per user, enforced in app)
CREATE TABLE IF NOT EXISTS price_alerts (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  product_id   UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  target_price DECIMAL(12, 2) NOT NULL,
  is_active    BOOLEAN DEFAULT TRUE,
  triggered_at TIMESTAMP WITH TIME ZONE,
  created_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE (user_id, product_id)
);

-- Back-in-stock notification subscriptions
CREATE TABLE IF NOT EXISTS restock_alerts (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  product_id   UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  is_active    BOOLEAN DEFAULT TRUE,
  triggered_at TIMESTAMP WITH TIME ZONE,
  created_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE (user_id, product_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_courier_dispatches_order
  ON courier_dispatches(order_id);
CREATE INDEX IF NOT EXISTS idx_courier_dispatches_tracking
  ON courier_dispatches(tracking_id);
CREATE INDEX IF NOT EXISTS idx_notification_log_user
  ON notification_log(user_id);
CREATE INDEX IF NOT EXISTS idx_notification_log_status
  ON notification_log(status);
CREATE INDEX IF NOT EXISTS idx_notification_log_created
  ON notification_log(created_at);
CREATE INDEX IF NOT EXISTS idx_price_alerts_user
  ON price_alerts(user_id);
CREATE INDEX IF NOT EXISTS idx_price_alerts_product_active
  ON price_alerts(product_id) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_restock_alerts_product_active
  ON restock_alerts(product_id) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_orders_fulfilment
  ON orders(fulfilment_type);
