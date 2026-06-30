CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  email VARCHAR(64) NOT NULL,
  password VARCHAR(64) NOT NULL,
  role VARCHAR(16) DEFAULT 'customer',
  level VARCHAR(16),
  balance DECIMAL(8,2),
  created_at TEXT
);

CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  sku VARCHAR(32),
  name VARCHAR(128),
  category VARCHAR(32),
  price DECIMAL(8,2),
  stock INTEGER,
  status VARCHAR(16),
  image_url TEXT
);

CREATE TABLE carts (
  id INTEGER PRIMARY KEY,
  user_id INTEGER,
  product_id INTEGER,
  qty INTEGER DEFAULT 1,
  coupon_code VARCHAR(64),
  updated_at TEXT
);

CREATE TABLE orders (
  id VARCHAR(64) PRIMARY KEY,
  user_id INTEGER,
  status VARCHAR(16),
  payable DECIMAL(8,2),
  address TEXT,
  created_at TEXT,
  paid_at TEXT
);

CREATE TABLE order_items (
  id INTEGER PRIMARY KEY,
  order_id VARCHAR(64),
  product_id INTEGER,
  sku VARCHAR(32),
  price DECIMAL(8,2),
  qty INTEGER
);

CREATE TABLE payments (
  id VARCHAR(64) PRIMARY KEY,
  order_id VARCHAR(64),
  amount DECIMAL(8,2),
  channel VARCHAR(16),
  status VARCHAR(16),
  callback_payload TEXT
);

CREATE TABLE refunds (
  id VARCHAR(64) PRIMARY KEY,
  order_id VARCHAR(64),
  user_id INTEGER,
  amount DECIMAL(8,2),
  reason TEXT,
  status VARCHAR(16)
);

CREATE INDEX idx_products_name ON products(name);
CREATE INDEX idx_orders_user_id ON orders(status);
