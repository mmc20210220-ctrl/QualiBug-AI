# Enterprise Shop Demo PRD

## Product scope
Enterprise Shop is a small e-commerce platform used for AI automated testing demos. It includes authentication, product catalog, cart, coupon, checkout, order management, and admin inventory operations.

## User roles
- Guest: can view product catalog.
- User: can login, manage cart, checkout, view own orders.
- Admin: can view all orders, create products, update inventory.

## Authentication rules
1. Active users can login with correct username and password.
2. Wrong password increments failed login count.
3. After 5 failed attempts, account is locked.
4. Locked users cannot login.
5. Missing or invalid token returns UNAUTHORIZED.

## Catalog rules
1. Product list supports keyword, category, price range, and price sorting.
2. Inactive products must not be visible to users.
3. Product detail returns PRODUCT_NOT_FOUND for missing product.

## Cart rules
1. User can add product to cart.
2. Quantity must be between 1 and 20.
3. Quantity cannot exceed current stock.
4. Cart subtotal equals sum of price * quantity.

## Coupon and checkout rules
1. WELCOME10 gives 10% off when subtotal >= 10.
2. VIP20 gives 20% off only for VIP users when subtotal >= 100.
3. Invalid coupon returns INVALID_COUPON.
4. Empty cart cannot checkout.
5. Total = subtotal - discount + shipping + tax.
6. Checkout creates paid order for mock_card and clears cart.
7. Inventory must be reduced after order creation.

## Permission rules
1. Normal users cannot access admin APIs.
2. Normal users can only read their own orders.
3. Admin can view all orders and update inventory.

## Key risks for AI testing
- Broken access control on admin APIs.
- Overselling inventory during checkout.
- Coupon misuse or double discount.
- Order ownership bypass.
- Account lockout not enforced.
- Invalid quantity accepted.
- Data consistency mismatch between cart, order, and inventory.
