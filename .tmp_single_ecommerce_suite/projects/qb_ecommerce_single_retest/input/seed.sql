INSERT INTO users(id,email,password,role,level,balance,created_at) VALUES
(1,'alice@example.com','123456','customer','gold',120.33,'2026-01-01'),
(2,'admin@example.com','admin','admin','normal',0,'2026-01-01'),
(3,'locked@example.com','123456','customer','normal',-20,'2026-01-01');

INSERT INTO products(id,sku,name,category,price,stock,status,image_url) VALUES
(1,'SKU-APPLE-001','Aster Phone 15','electronics',6999.99,12,'ON_SALE','/images/phone.svg'),
(2,'SKU-BAG-002','Urban Travel Backpack','fashion',399.9,0,'ON_SALE','/images/bag.svg'),
(3,'SKU-COF-003','Colombia Coffee Beans','grocery',86.5,188,'DRAFT','/images/coffee.svg'),
(4,'SKU-TV-004','55 inch Smart TV','electronics',2799,4,'ON_SALE','/images/tv.svg');
