CREATE TABLE `categories` (
  `categories_id` INT PRIMARY KEY AUTO_INCREMENT,
  `name` VARCHAR(100) UNIQUE NOT NULL,
  `description` TEXT
);

CREATE TABLE `customers` (
  `customers_id` INT PRIMARY KEY AUTO_INCREMENT,
  `first_name` VARCHAR(100) NOT NULL,
  `last_name` VARCHAR(100) NOT NULL,
  `email` VARCHAR(255) UNIQUE NOT NULL,
  `phone` VARCHAR(30),
  `country` VARCHAR(100) NOT NULL,
  `city` VARCHAR(100),
  `acquisition_channel` ENUM ('organic', 'paid_ads', 'social_media', 'email', 'referral', 'affiliate') DEFAULT 'organic',
  `created_at` TIMESTAMP DEFAULT (now())
);

CREATE TABLE `products` (
  `product_id` INT PRIMARY KEY AUTO_INCREMENT,
  `category_id` INT NOT NULL,
  `name` VARCHAR(200) NOT NULL,
  `description` TEXT,
  `price` NUMERIC(10,2) NOT NULL CHECK (price >= 0),
  `cost` NUMERIC(10,2) NOT NULL CHECK (cost >= 0),
  `stock` INT CHECK (stock >= 0) DEFAULT 0,
  `is_active` BOOLEAN DEFAULT true
);

CREATE TABLE `orders` (
  `orders_id` INT PRIMARY KEY AUTO_INCREMENT,
  `customer_id` INT NOT NULL,
  `status` ENUM ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled', 'returned') DEFAULT 'pending',
  `shipping_address` VARCHAR(255),
  `shipping_city` VARCHAR(100),
  `shipping_country` VARCHAR(100),
  `order_date` TIMESTAMP NOT NULL DEFAULT (now()),
  `shipped_date` TIMESTAMP,
  `delivered_date` TIMESTAMP
);

CREATE TABLE `order_items` (
  `order_item_id` INT NOT NULL,
  `product_id` INT NOT NULL,
  `quantity` INT NOT NULL CHECK (quantity > 0) DEFAULT 1,
  `unit_price` NUMERIC(10,2) NOT NULL CHECK (unit_price >= 0),
  `discount_pct` NUMERIC(5,2) CHECK (discount_pct >= 0 AND discount_pct <= 100) DEFAULT 0,
  PRIMARY KEY (`order_id`, `product_id`)
);

CREATE TABLE `payments` (
  `payments_id` INT PRIMARY KEY AUTO_INCREMENT,
  `order_id` INT UNIQUE NOT NULL,
  `method` ENUM ('credit_card', 'debit_card', 'paypal', 'bank_transfer', 'apple_pay', 'google_pay') NOT NULL,
  `status` ENUM ('completed', 'refunded', 'pending', 'failed') DEFAULT 'pending',
  `amount` NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
  `currency` VARCHAR(3) DEFAULT 'EUR',
  `paid_at` TIMESTAMP
);

CREATE TABLE `reviews` (
  `review_id` INT PRIMARY KEY AUTO_INCREMENT,
  `order_id` INT NOT NULL,
  `product_id` INT NOT NULL,
  `rating` INT NOT NULL CHECK (rating >= 1 AND rating <= 5),
  `comment` TEXT,
  `created_at` TIMESTAMP DEFAULT (now())
);

CREATE INDEX `idx_customers_geo` ON `customers` (`country`, `city`);

CREATE INDEX `idx_customers_channel` ON `customers` (`acquisition_channel`);

CREATE INDEX `idx_products_category` ON `products` (`category_id`);

CREATE INDEX `idx_orders_customer` ON `orders` (`customer_id`);

CREATE INDEX `idx_orders_status` ON `orders` (`status`);

CREATE INDEX `idx_orders_date` ON `orders` (`order_date`);

CREATE INDEX `idx_items_product` ON `order_items` (`product_id`);

CREATE INDEX `idx_payments_status` ON `payments` (`status`);

CREATE UNIQUE INDEX `idx_reviews_item` ON `reviews` (`order_id`, `product_id`);

ALTER TABLE `categories` COMMENT = 'Clasificación jerárquica de productos (Smartphones, Laptops, Audio, etc.)';

ALTER TABLE `customers` COMMENT = 'Clientes del e-commerce. country/city directamente aquí (no tabla separada) para evitar sobre-normalización con <10 valores.';

ALTER TABLE `products` COMMENT = 'Catálogo de productos. price = PVP actual. cost = coste de adquisición (para margen). El precio histórico se guarda en order_items.unit_price.';

ALTER TABLE `orders` COMMENT = 'Cabecera de pedido. No almacena customer_name (violaría 3NF: dependencia transitiva orders → customer_id → name).';

ALTER TABLE `order_items` COMMENT = 'Tabla intermedia N:M (orders ↔ products). unit_price = precio en el momento de la compra (puede diferir de products.price actual). Clave compuesta (order_id, product_id).';

ALTER TABLE `payments` COMMENT = 'Relación 1:1 con orders (unique en order_id). Registra método, estado e importe del pago.';

ALTER TABLE `reviews` COMMENT = 'Valoración sobre una línea de pedido concreta (order_id + product_id), no sobre el pedido completo. FK compuesta → order_items.';

ALTER TABLE `products` ADD FOREIGN KEY (`category_id`) REFERENCES `categories` (`id`);

ALTER TABLE `orders` ADD FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`);

ALTER TABLE `order_items` ADD FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`);

ALTER TABLE `order_items` ADD FOREIGN KEY (`product_id`) REFERENCES `products` (`id`);

ALTER TABLE `payments` ADD FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`);

ALTER TABLE `order_items` ADD FOREIGN KEY (`order_id`, `product_id`) REFERENCES `reviews` (`order_id`, `product_id`);
