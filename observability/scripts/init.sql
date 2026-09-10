-- ─────────────────────────────────────────────────────────────
-- Retail Order Platform — Lab Database Init
-- ─────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Products catalog
CREATE TABLE IF NOT EXISTS products (
    id          VARCHAR(50) PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    price       NUMERIC(10,2) NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Inventory (stock)
CREATE TABLE IF NOT EXISTS inventory (
    id          SERIAL PRIMARY KEY,
    product_id  VARCHAR(50) REFERENCES products(id),
    quantity    INT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(product_id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_product ON inventory(product_id);

-- Orders
CREATE TABLE IF NOT EXISTS orders (
    id             VARCHAR(50) PRIMARY KEY DEFAULT ('ord_' || substr(md5(random()::text), 1, 8)),
    user_id        VARCHAR(50) NOT NULL,
    product_id     VARCHAR(50) REFERENCES products(id),
    quantity       INT NOT NULL DEFAULT 1,
    total_amount   NUMERIC(10,2) NOT NULL DEFAULT 0,
    payment_method VARCHAR(50) NOT NULL DEFAULT 'mock_card',
    status         VARCHAR(50) NOT NULL DEFAULT 'created',
    request_id     VARCHAR(200),
    trace_id       VARCHAR(200),
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_product ON orders(product_id);

-- Payments
CREATE TABLE IF NOT EXISTS payments (
    id             VARCHAR(50) PRIMARY KEY DEFAULT ('pay_' || substr(md5(random()::text), 1, 8)),
    order_id       VARCHAR(50) REFERENCES orders(id),
    amount         NUMERIC(10,2) NOT NULL,
    method         VARCHAR(50) NOT NULL,
    status         VARCHAR(50) NOT NULL DEFAULT 'pending',
    request_id     VARCHAR(200),
    trace_id       VARCHAR(200),
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);

-- Notifications log
CREATE TABLE IF NOT EXISTS notifications (
    id          SERIAL PRIMARY KEY,
    order_id    VARCHAR(50) REFERENCES orders(id),
    user_id     VARCHAR(50),
    event_type  VARCHAR(100),
    status      VARCHAR(50) DEFAULT 'pending',
    payload     JSONB,
    processed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_order ON notifications(order_id);
CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status);

-- ─── Seed Data ───────────────────────────────────────────────
INSERT INTO products (id, name, description, price) VALUES
  ('p001', 'Laptop Pro 15"',   'High-performance laptop for developers', 1299.99),
  ('p002', 'Mechanical Keyboard', 'RGB mechanical keyboard with Cherry MX switches', 149.99),
  ('p003', 'USB-C Hub',        '7-in-1 USB-C hub with 4K HDMI', 49.99),
  ('p004', 'Monitor 27"',      '4K UHD IPS monitor with HDR', 599.99),
  ('p005', 'Webcam 1080p',     'Full HD webcam with built-in microphone', 79.99)
ON CONFLICT DO NOTHING;

INSERT INTO inventory (product_id, quantity) VALUES
  ('p001', 100),
  ('p002', 500),
  ('p003', 300),
  ('p004', 50),
  ('p005', 200)
ON CONFLICT (product_id) DO UPDATE SET quantity = EXCLUDED.quantity;
