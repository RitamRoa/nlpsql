-- =========================================================
-- Domain: E-Commerce Store & Customer Analytics (Expanded)
-- =========================================================

CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    country TEXT NOT NULL,
    city TEXT NOT NULL,
    loyalty_tier TEXT NOT NULL, -- 'Gold', 'Silver', 'Bronze'
    signup_date TEXT NOT NULL,
    total_spent REAL NOT NULL DEFAULT 0
);

CREATE TABLE categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id INTEGER -- NULL for top-level categories
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    price REAL NOT NULL,
    stock_quantity INTEGER NOT NULL,
    brand TEXT NOT NULL,
    rating REAL, -- 1.0 to 5.0
    FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    order_date TEXT NOT NULL,
    total_amount REAL NOT NULL,
    status TEXT NOT NULL, -- 'Delivered', 'Processing', 'Cancelled', 'Refunded'
    payment_method TEXT NOT NULL, -- 'Credit Card', 'PayPal', 'Bank Transfer'
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    discount REAL NOT NULL DEFAULT 0, -- percentage 0-100
    FOREIGN KEY (order_id) REFERENCES orders(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE reviews (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL,
    customer_id INTEGER NOT NULL,
    rating INTEGER NOT NULL, -- 1-5
    review_text TEXT,
    review_date TEXT NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

-- Seed Data: Customers (10 customers)
INSERT INTO customers VALUES (1, 'Sophia Taylor',   'sophia@example.com',   'USA',       'New York',     'Gold',   '2022-03-15', 1245.95);
INSERT INTO customers VALUES (2, 'Liam Davies',     'liam@example.co.uk',   'UK',        'London',       'Silver', '2022-08-20', 489.50);
INSERT INTO customers VALUES (3, 'Emma Schmidt',    'emma@example.de',      'Germany',   'Berlin',       'Gold',   '2021-11-10', 2100.00);
INSERT INTO customers VALUES (4, 'Noah Tanaka',     'noah@example.jp',      'Japan',     'Tokyo',        'Bronze', '2023-06-05', 94.98);
INSERT INTO customers VALUES (5, 'Olivia Chen',     'olivia@example.sg',    'Singapore', 'Singapore',    'Silver', '2023-01-22', 640.00);
INSERT INTO customers VALUES (6, 'James Wilson',    'james@example.ca',     'Canada',    'Toronto',      'Bronze', '2024-02-01', 34.99);
INSERT INTO customers VALUES (7, 'Amelia Garcia',   'amelia@example.mx',    'Mexico',    'Mexico City',  'Silver', '2023-09-14', 570.48);
INSERT INTO customers VALUES (8, 'William Brown',   'william@example.au',   'Australia', 'Sydney',       'Gold',   '2022-05-30', 1890.00);
INSERT INTO customers VALUES (9, 'Isabella Moore',  'isabella@example.fr',  'France',    'Paris',        'Silver', '2023-11-01', 420.00);
INSERT INTO customers VALUES (10,'Ethan Harris',    'ethan@example.com',    'USA',       'Los Angeles',  'Bronze', '2024-04-18', 179.00);

-- Seed Data: Categories (with hierarchy)
INSERT INTO categories VALUES (1, 'Electronics',  NULL);
INSERT INTO categories VALUES (2, 'Accessories',  NULL);
INSERT INTO categories VALUES (3, 'Home Office',  NULL);
INSERT INTO categories VALUES (4, 'Computers',    1);
INSERT INTO categories VALUES (5, 'Audio',        1);
INSERT INTO categories VALUES (6, 'Peripherals',  2);

-- Seed Data: Products (12 products with brands and ratings)
INSERT INTO products VALUES (101, 'Wireless Noise-Canceling Headphones', 5,  249.99,  45, 'SoundMax',  4.7);
INSERT INTO products VALUES (102, 'Mechanical Gaming Keyboard',          6,  129.50,  60, 'KeyForge',  4.5);
INSERT INTO products VALUES (103, 'Ergonomic Vertical Mouse',            6,   59.99, 120, 'ErgoTech',  4.3);
INSERT INTO products VALUES (104, 'USB-C Dual 4K Docking Station',       4,  179.00,  30, 'HubPro',    4.6);
INSERT INTO products VALUES (105, 'Motorized Standing Desk',             3,  499.00,  15, 'DeskRise',  4.8);
INSERT INTO products VALUES (106, 'Leather Desk Mat',                    3,   34.99,  85, 'DeskStyle', 4.2);
INSERT INTO products VALUES (107, 'Ultra-Wide 34" Monitor',              4,  599.00,  20, 'ViewMax',   4.9);
INSERT INTO products VALUES (108, 'Laptop Stand Adjustable',             3,   49.99,  75, 'StandPro',  4.4);
INSERT INTO products VALUES (109, 'Wireless Earbuds Pro',                5,  199.00,  55, 'SoundMax',  4.6);
INSERT INTO products VALUES (110, 'Webcam 4K HDR',                       6,   89.99,  40, 'ViewMax',   4.5);
INSERT INTO products VALUES (111, 'Smart LED Desk Lamp',                 3,   44.99, 100, 'LightUp',   4.3);
INSERT INTO products VALUES (112, 'Portable SSD 2TB',                    4,  109.99,  65, 'StoreFast', 4.8);

-- Seed Data: Orders (15 orders)
INSERT INTO orders VALUES (1001, 1,  '2024-03-01', 309.98,  'Delivered',  'Credit Card');
INSERT INTO orders VALUES (1002, 2,  '2024-03-05', 129.50,  'Delivered',  'PayPal');
INSERT INTO orders VALUES (1003, 3,  '2024-03-12', 499.00,  'Processing', 'Bank Transfer');
INSERT INTO orders VALUES (1004, 1,  '2024-03-18', 179.00,  'Delivered',  'Credit Card');
INSERT INTO orders VALUES (1005, 4,  '2024-03-22',  94.98,  'Delivered',  'PayPal');
INSERT INTO orders VALUES (1006, 5,  '2024-04-03', 249.99,  'Delivered',  'Credit Card');
INSERT INTO orders VALUES (1007, 8,  '2024-04-10', 599.00,  'Delivered',  'Bank Transfer');
INSERT INTO orders VALUES (1008, 7,  '2024-04-15', 199.00,  'Delivered',  'PayPal');
INSERT INTO orders VALUES (1009, 3,  '2024-05-01', 989.00,  'Delivered',  'Bank Transfer');
INSERT INTO orders VALUES (1010, 6,  '2024-05-08',  34.99,  'Delivered',  'Credit Card');
INSERT INTO orders VALUES (1011, 9,  '2024-05-20', 209.98,  'Delivered',  'PayPal');
INSERT INTO orders VALUES (1012, 8,  '2024-06-02', 709.99,  'Processing', 'Credit Card');
INSERT INTO orders VALUES (1013, 10, '2024-06-15', 179.00,  'Delivered',  'PayPal');
INSERT INTO orders VALUES (1014, 2,  '2024-07-01', 360.00,  'Cancelled',  'Credit Card');
INSERT INTO orders VALUES (1015, 5,  '2024-07-22', 390.01,  'Delivered',  'Credit Card');

-- Seed Data: Order Items
INSERT INTO order_items VALUES (1,  1001, 101, 1, 249.99, 0);
INSERT INTO order_items VALUES (2,  1001, 103, 1,  59.99, 0);
INSERT INTO order_items VALUES (3,  1002, 102, 1, 129.50, 0);
INSERT INTO order_items VALUES (4,  1003, 105, 1, 499.00, 0);
INSERT INTO order_items VALUES (5,  1004, 104, 1, 179.00, 0);
INSERT INTO order_items VALUES (6,  1005, 103, 1,  59.99, 0);
INSERT INTO order_items VALUES (7,  1005, 106, 1,  34.99, 0);
INSERT INTO order_items VALUES (8,  1006, 101, 1, 249.99, 0);
INSERT INTO order_items VALUES (9,  1007, 107, 1, 599.00, 0);
INSERT INTO order_items VALUES (10, 1008, 109, 1, 199.00, 0);
INSERT INTO order_items VALUES (11, 1009, 107, 1, 599.00, 0);
INSERT INTO order_items VALUES (12, 1009, 112, 2, 109.99, 10);
INSERT INTO order_items VALUES (13, 1010, 106, 1,  34.99, 0);
INSERT INTO order_items VALUES (14, 1011, 110, 1,  89.99, 0);
INSERT INTO order_items VALUES (15, 1011, 108, 1,  49.99, 0);
INSERT INTO order_items VALUES (16, 1012, 107, 1, 599.00, 0);
INSERT INTO order_items VALUES (17, 1012, 110, 1,  89.99, 0);
INSERT INTO order_items VALUES (18, 1013, 104, 1, 179.00, 0);
INSERT INTO order_items VALUES (19, 1014, 108, 2,  49.99, 0);
INSERT INTO order_items VALUES (20, 1014, 112, 2, 109.99, 15);
INSERT INTO order_items VALUES (21, 1015, 101, 1, 249.99, 5);
INSERT INTO order_items VALUES (22, 1015, 102, 1, 129.50, 5);
INSERT INTO order_items VALUES (23, 1015, 111, 2,  44.99, 5);

-- Seed Data: Reviews
INSERT INTO reviews VALUES (1, 101, 1, 5, 'Incredible noise cancellation, worth every penny!', '2024-03-10');
INSERT INTO reviews VALUES (2, 105, 3, 5, 'Changed my home office completely. Very sturdy.', '2024-04-01');
INSERT INTO reviews VALUES (3, 102, 2, 4, 'Great tactile feel, a bit loud for an office.', '2024-03-12');
INSERT INTO reviews VALUES (4, 107, 8, 5, 'Stunning display, colors are superb.', '2024-04-20');
INSERT INTO reviews VALUES (5, 109, 7, 4, 'Good sound quality, connection is stable.', '2024-04-22');
INSERT INTO reviews VALUES (6, 103, 4, 3, 'Decent mouse, takes time to get used to vertical design.', '2024-04-02');
INSERT INTO reviews VALUES (7, 112, 3, 5, 'Blazing fast read/write speeds.', '2024-05-15');
