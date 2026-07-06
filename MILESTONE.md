# Star Tech BD — Project Milestones & Development Schedule

---

## Project Timeline Overview

| Phase                                  | Focus                      |
| -------------------------------------- | -------------------------- |
| **Phase 0** — Foundation               | Infra setup, repo, CI/CD   |
| **Phase 1** — Core Backend             | Auth, Products, Categories |
| **Phase 2** — Inventory & Search       | Inventory, Elasticsearch   |
| **Phase 3** — Cart & Orders            | Cart, Checkout, Orders     |
| **Phase 4** — Payments                 | bKash, SSLCOMMERZ, COD     |
| **Phase 5** — Delivery & Notifications | Courier API, SMS/Email     |
| **Phase 6** — PC Builder               | Compatibility engine       |
| **Phase 7** — Frontend Web             | Next.js storefront         |
| **Phase 8** — Admin Dashboard          | Admin panel                |
| **Phase 9** — Mobile App               | React Native app           |
| **Phase 10** — Analytics & Offers      | Reports, Promos            |
| **Phase 11** — QA & Hardening          | Testing, security          |
| **Phase 12** — Launch                  | Production rollout         |

**Total estimated duration: ~34 weeks (~8.5 months)**

---

# PHASE 0 — Foundation & Infrastructure

---

## Milestone 0.1 — Repository & Project Structure Setup

### Goal

Establish a clean monorepo structure that all developers can clone and run locally within 30 minutes.

### Tasks

- [ ] Create GitHub organisation and main repository
- [ ] Set up monorepo structure (apps/web, apps/admin, apps/mobile, services/\*)
- [ ] Add root-level `docker-compose.yml` (python, FastApi, Postgres, Redis, Elasticsearch, RabbitMQ)
- [ ] Add `.env.example` with all required environment variables
- [ ] Create shared `packages/` for common types, utils, and constants
- [ ] Write `CONTRIBUTING.md` with branching strategy (main / develop / feature/\*)
- [ ] Set up ESLint + Prettier + Husky pre-commit hooks
- [ ] Configure TypeScript `tsconfig.json` base config shared across services

### Acceptance Criteria

- [ ] Any developer can run `docker compose up` and get all infra services running
- [ ] Linting and formatting pass on empty project
- [ ] README has local setup instructions

---

## Milestone 0.2 — CI/CD Pipeline

### Goal

Every push triggers automated test + build. Main branch deployments are automated to staging.

### Tasks

- [ ] Set up GitHub Actions workflow: `lint → test → build`
- [ ] Configure Docker image build and push to AWS ECR
- [ ] Set up staging environment on Kubernetes (EKS or local kind cluster)
- [ ] Configure automatic deploy to staging on merge to `develop`
- [ ] Add Slack/Discord webhook for build status notifications
- [ ] Set up branch protection rules on `main` and `develop`

### Acceptance Criteria

- [ ] Push to any branch triggers lint + test
- [ ] Merge to `develop` auto-deploys to staging
- [ ] Failed builds block PRs from merging

---

## Milestone 0.3 — Database Setup & Base Migrations

### Goal

PostgreSQL running with migration tooling in place. Base schema created and seeded.

### Tasks

- [ ] Set up PostgreSQL 16 with connection pooling (PgBouncer)
- [ ] Integrate migration tool (e.g. `db-migrate` or `Prisma migrate`)
- [ ] Create initial migration: `users`, `roles`, `branches` tables
- [ ] Set up Redis 7 cluster (dev: single instance)
- [ ] Set up Elasticsearch 8 index templates
- [ ] Write seed script with 10 sample products, 2 branches, 1 admin user
- [ ] Configure DB read replica (staging only, for pattern validation)

### Acceptance Criteria

- [ ] `npm run migrate` runs all migrations from scratch cleanly
- [ ] `npm run seed` populates sample data
- [ ] All connections (Postgres, Redis, ES) verified from a test script

---

## Milestone 0.4 — API Gateway Setup

### Goal

Kong API Gateway running locally and in staging, routing to placeholder services.

### Tasks

- [ ] Deploy Kong Gateway with declarative config (`kong.yml`)
- [ ] Configure routes: `/api/v1/products/*`, `/api/v1/orders/*`, `/api/v1/users/*`
- [ ] Add JWT validation plugin
- [ ] Add rate limiting plugin (100 req/min per IP default)
- [ ] Add request logging plugin (send to stdout → Loki)
- [ ] Configure CORS allowed origins

### Acceptance Criteria

- [ ] `curl http://localhost:8000/api/v1/health` returns `200 OK`
- [ ] Request without valid JWT to protected route returns `401`
- [ ] Rate limit exceeded returns `429`

---

---

# PHASE 1 — Core Backend Services

---

## Milestone 1.1 — User Service: Registration & Login

### Goal

Users can register, log in via email/phone, and receive JWT tokens. OTP flow via SMS.

### Tasks

- [ ] Create `user-service` NestJS app skeleton
- [ ] `POST /auth/register` — register with name, email, phone, password
- [ ] `POST /auth/login` — login with email/phone + password → returns access + refresh tokens
- [ ] `POST /auth/otp/send` — send OTP to phone number
- [ ] `POST /auth/otp/verify` — verify OTP, return token
- [ ] `POST /auth/refresh` — refresh access token using refresh token
- [ ] `POST /auth/logout` — invalidate refresh token (store in Redis blocklist)
- [ ] Password hashing with `bcrypt` (cost factor 12)
- [ ] JWT signing with RS256 (asymmetric keys)
- [ ] Rate limit OTP endpoint (5 requests/hour per phone)

### Acceptance Criteria

- [ ] Register → Login → get valid JWT works end-to-end
- [ ] Expired access token rejected with `401`
- [ ] OTP expires after 5 minutes
- [ ] Unit tests cover all auth flows (≥ 80% coverage)

---

## Milestone 1.2 — User Service: Profiles & Address Management

### Goal

Authenticated users can manage their profile and saved delivery addresses.

### Tasks

- [ ] `GET /users/me` — get own profile
- [ ] `PATCH /users/me` — update name, email, phone
- [ ] `POST /users/me/addresses` — add delivery address
- [ ] `GET /users/me/addresses` — list saved addresses
- [ ] `PUT /users/me/addresses/:id` — update address
- [ ] `DELETE /users/me/addresses/:id` — delete address
- [ ] Create `addresses` table migration
- [ ] Validate BD phone number format (+8801XXXXXXXXX)

### Acceptance Criteria

- [ ] All CRUD operations work for authenticated users
- [ ] User cannot access/modify another user's addresses
- [ ] Address has required fields: name, phone, line1, district, division, postcode

---

## Milestone 1.3 — Product Service: Category Tree

### Goal

Full category hierarchy (unlimited depth) stored and retrievable. Admin can manage
categories via dedicated admin endpoints.

### Tasks

- [ ] Create `categories` table (self-referencing, `parent_id`)
- [ ] `GET /categories` — return full tree (nested JSON)
- [ ] `GET /categories/:slug` — single category with children
- [ ] `GET /admin/categories` — admin list/category tree
- [ ] `POST /admin/categories` — create category (admin only)
- [ ] `PUT /admin/categories/:id` — update category
- [ ] `DELETE /admin/categories/:id` — soft delete (only if no products)
- [ ] Seed all 20+ top-level Star Tech categories
- [ ] Seed 50+ sub-categories with correct parent linkage
- [ ] Cache category tree in Redis (TTL 1 hour, invalidate on update)

### Acceptance Criteria

- [ ] `GET /categories` returns nested tree in < 100ms (from cache)
- [ ] Creating a sub-category correctly sets `parent_id`
- [ ] Deleting a category with active products returns `409 Conflict`

---

## Milestone 1.4 — Product Service: Brand Management

### Goal

Brands are managed independently. Products can be filtered and grouped by brand.

### Tasks

- [ ] Create `brands` table (id, name, slug, logo_url, is_active)
- [ ] `GET /brands` — list all active brands (paginated)
- [ ] `GET /brands/:slug` — single brand details
- [ ] `POST /brands` — create brand (admin only)
- [ ] `PUT /brands/:id` — update brand
- [ ] Seed 50+ brands (Asus, HP, Lenovo, MSI, TP-Link, Hikvision, etc.)
- [ ] Brand logo upload endpoint → store in S3

### Acceptance Criteria

- [ ] All CRUD endpoints work with proper auth guards
- [ ] Brand list returns logo URL pointing to S3
- [ ] Slug is auto-generated from name if not provided

---

## Milestone 1.5 — Product Service: Product CRUD

### Goal

Full product create/read/update/delete with flexible spec attributes per category.

### Tasks

- [ ] Create `products` table migration (see data model in README)
- [ ] `POST /products` — create product (admin only)
- [ ] `GET /products` — list products (paginated, filterable by category, brand, status)
- [ ] `GET /products/:slug` — get product detail
- [ ] `PUT /products/:id` — update product
- [ ] `DELETE /products/:id` — soft delete (set status = discontinued)
- [ ] `PATCH /products/:id/status` — change status (admin)
- [ ] Flexible `specs` stored as JSONB — validated against category-specific schema
- [ ] Price history: on price change, insert record to `product_price_history` table
- [ ] `GET /products/:id/price-history` — return last 12 price changes

### Acceptance Criteria

- [ ] Create product with specs → retrieve and specs are intact
- [ ] Updating price logs a price history record
- [ ] Public endpoint returns only `status = active` products
- [ ] Admin endpoint can filter by all statuses

---

## Milestone 1.6 — Product Service: Image Upload

### Goal

Products have multiple images. Images uploaded to S3 and served via CDN.

### Tasks

- [ ] Create `product_images` table (id, product_id, url, sort_order, is_primary)
- [ ] `POST /products/:id/images` — upload image (multipart) → resize → save to S3
- [ ] `DELETE /products/:id/images/:imageId` — remove image
- [ ] `PATCH /products/:id/images/:imageId/primary` — set as primary image
- [ ] Image processing: generate 3 sizes (thumbnail 150px, medium 400px, large 800px)
- [ ] Use `sharp` library for server-side image processing
- [ ] Set correct `Cache-Control` headers for CDN (1 year immutable)

### Acceptance Criteria

- [ ] Upload JPEG/PNG → stored in S3 in 3 sizes
- [ ] Primary image returned first in product detail response
- [ ] Images served from CDN URL (not S3 direct)
- [ ] File size limit: 5MB per image

---

## Milestone 1.7 — Product Service: Bulk Import

### Goal

Admin can import hundreds of products via CSV. Import is async with progress tracking.

### Tasks

- [ ] `POST /products/import` — upload CSV file
- [ ] Validate CSV headers and row data
- [ ] Process import as background job (RabbitMQ queue)
- [ ] `GET /products/import/:jobId` — check import job status and progress
- [ ] On completion, return: total rows, success count, error rows with reasons
- [ ] CSV template download endpoint: `GET /products/import/template`
- [ ] Support upsert (create if not exists, update if slug matches)

### Acceptance Criteria

- [ ] 500-row CSV import completes without timeout
- [ ] Rows with invalid data are skipped and reported
- [ ] Import job status shows percentage progress
- [ ] Duplicate slug → update existing product (upsert)

---

---

# PHASE 2 — Inventory & Search

---

## Milestone 2.1 — Inventory Service: Multi-Branch Stock

### Goal

Each product has per-branch stock levels. Stock can be queried and updated atomically.

### Tasks

- [ ] Create `inventory` table (product_id, branch_id composite PK)
- [ ] Create `branches` table (id, name, address, phone, is_active)
- [ ] `GET /inventory/:productId` — stock levels across all branches
- [ ] `GET /inventory/:productId/branch/:branchId` — stock at specific branch
- [ ] `POST /inventory/adjust` — manually adjust stock (admin, with reason)
- [ ] Atomic stock deduction SQL (prevent overselling — see README)
- [ ] `POST /inventory/reserve` — soft-reserve stock for cart (with TTL)
- [ ] `POST /inventory/release` — release reservation (cart abandoned / expired)
- [ ] Background job: release expired reservations every 5 minutes

### Acceptance Criteria

- [ ] Concurrent stock deduction test: 10 concurrent requests for last 1 item → only 1 succeeds
- [ ] Reservation expires after 15 minutes automatically
- [ ] Stock adjustment logs actor and reason in `inventory_audit_log` table

---

## Milestone 2.2 — Inventory Service: Low Stock Alerts & Transfers

### Goal

Admin gets alerted when stock drops below threshold. Stock can be transferred between branches.

### Tasks

- [ ] `GET /inventory/low-stock` — list products below threshold (admin)
- [ ] Trigger low-stock event when quantity crosses threshold → publish to queue
- [ ] Notification Service consumes event → sends email to branch manager
- [ ] `POST /inventory/transfer` — request stock transfer (from_branch → to_branch)
- [ ] Transfer states: pending → approved → in-transit → completed
- [ ] `GET /inventory/transfers` — list transfers (filterable by branch, status)
- [ ] `PATCH /inventory/transfers/:id/status` — update transfer status

### Acceptance Criteria

- [ ] Stock drop below threshold triggers email within 2 minutes
- [ ] Transfer request creates records in both source and destination branches
- [ ] Transfer only completes when both branches confirm

---

## Milestone 2.3 — Search Service: Elasticsearch Indexing

### Goal

Product data is indexed in Elasticsearch. Index stays in sync with database changes.

### Tasks

- [ ] Define Elasticsearch index mapping for products (name, brand, category, specs, price, stock)
- [ ] `POST /search/index/all` — full re-index of all products (admin, background job)
- [ ] Event listener: on product create/update → upsert Elasticsearch document
- [ ] Event listener: on product delete → remove from index
- [ ] Event listener: on inventory update → update `in_stock` field in ES
- [ ] Configure `edge_ngram` tokeniser for autocomplete
- [ ] Set up index aliases for zero-downtime re-indexing

### Acceptance Criteria

- [ ] Full re-index of 10,000 products completes in < 5 minutes
- [ ] Product update reflects in search within 30 seconds
- [ ] Index alias swap causes zero search downtime

---

## Milestone 2.4 — Search Service: Search API

### Goal

Fully featured search API with filters, sorting, facets, and fuzzy matching.

### Tasks

- [ ] `GET /search?q=&brand=&category=&min_price=&max_price=&in_stock=&sort=` — main search endpoint
- [ ] Multi-field match: name (boost 3×), brand (boost 2×), category, specs
- [ ] Fuzzy matching (`fuzziness: AUTO`) for typo tolerance
- [ ] Filter by: brand (multi-select), price range, category, in-stock only, specs
- [ ] Sort options: relevance, price-asc, price-desc, newest, popularity
- [ ] Facet aggregations: brand count, price histogram, spec values
- [ ] Pagination: `page` + `per_page` (default 24)
- [ ] `GET /search/suggest?q=` — autocomplete from Redis cache (< 50ms target)
- [ ] Log search queries to analytics (what users search for)

### Acceptance Criteria

- [ ] Search "lenovo laptop" returns relevant products < 300ms
- [ ] Searching "laaptop" (typo) still returns laptop results
- [ ] Facets reflect count of products matching each filter option
- [ ] Autocomplete returns results in < 50ms

---

## Milestone 2.5 — Laptop Finder Wizard

### Goal

Step-by-step guided wizard recommends laptops based on user answers.

### Tasks

- [ ] Define wizard question schema (JSON config): use-case, budget, OS preference, display size, priority (battery/performance/portability)
- [ ] `GET /search/finder/questions` — return wizard questions
- [ ] `POST /search/finder/results` — submit answers → return matched laptops
- [ ] Map answers to Elasticsearch filters and boost rules
- [ ] Results ranked by match score (how many criteria satisfied)
- [ ] Return top 8 recommendations with match explanation

### Acceptance Criteria

- [ ] Budget filter is strictly applied (no results above max budget)
- [ ] "Gaming" use-case boosts GPU-heavy laptops
- [ ] Results returned in < 500ms

---

---

# PHASE 3 — Cart & Orders

---

## Milestone 3.1 — Cart Service

### Goal

Users and guests can manage a shopping cart. Cart persists across sessions for logged-in users.

### Tasks

- [ ] Guest cart stored in Redis with `cart:{sessionId}` key (TTL 7 days)
- [ ] Logged-in cart stored in DB (`carts` table), synced to Redis
- [ ] `GET /cart` — get current cart (merges guest + user cart on login)
- [ ] `POST /cart/items` — add item (product_id, quantity)
- [ ] `PATCH /cart/items/:productId` — update quantity
- [ ] `DELETE /cart/items/:productId` — remove item
- [ ] `DELETE /cart` — clear cart
- [ ] On add-to-cart: check real-time stock from Inventory Service
- [ ] On add-to-cart: soft-reserve stock (15-minute hold)
- [ ] Cart total recalculated on every mutation (apply active price, not cached)

### Acceptance Criteria

- [ ] Guest adds items → logs in → guest cart merges with account cart
- [ ] Adding out-of-stock item returns `409 Out of stock`
- [ ] Cart item price reflects current product price (not stale)

---

## Milestone 3.2 — Coupon / Promo Service

### Goal

Admin can create promo codes. Users can apply them at checkout with validation.

### Tasks

- [ ] Create `coupons` table (code, type, value, min_order, max_uses, expires_at, is_active)
- [ ] Coupon types: `percentage` (20% off), `fixed` (BDT 200 off), `free_shipping`
- [ ] `POST /coupons` — create coupon (admin)
- [ ] `GET /coupons` — list all coupons with usage stats (admin)
- [ ] `POST /cart/coupon` — apply coupon code to cart
- [ ] `DELETE /cart/coupon` — remove applied coupon
- [ ] Validate: not expired, usage limit not exceeded, minimum order met, not already used by user
- [ ] Track coupon usage per user (one-time-use coupons)

### Acceptance Criteria

- [ ] Expired coupon returns `410 Coupon expired`
- [ ] Max-uses reached returns `409 Coupon exhausted`
- [ ] One-time coupon: second use by same user rejected
- [ ] Discount correctly deducted from cart total

---

## Milestone 3.3 — Order Service: Place Order

### Goal

User can place an order from their cart. Order record created with correct state.

### Tasks

- [ ] `POST /orders` — place order (requires valid cart + payment intent)
- [ ] Validate: all cart items still in stock
- [ ] Deduct inventory atomically on order placement
- [ ] Create order + order_items records in a single DB transaction
- [ ] Generate order reference number (e.g. `ST-2026-00001`)
- [ ] Publish `order.placed` event to RabbitMQ
- [ ] `GET /orders` — list user's own orders (paginated)
- [ ] `GET /orders/:id` — get order detail with items and status history
- [ ] `GET /orders/:id/invoice` — generate PDF invoice

### Acceptance Criteria

- [ ] Order placement is atomic: all items deducted or none
- [ ] Order reference is unique and sequential
- [ ] `order.placed` event published and confirmed in queue
- [ ] Invoice PDF generates with correct line items and totals

---

## Milestone 3.4 — Order Service: Order Lifecycle Management

### Goal

Admin can manage the order lifecycle from confirmed through to delivered or cancelled.

### Tasks

- [ ] `PATCH /orders/:id/status` — update status (admin/staff)
- [ ] Allowed transitions: placed→confirmed→packed→shipped→delivered, placed→cancelled
- [ ] Each transition logs entry to `order_status_history` table (status, changed_by, changed_at, note)
- [ ] `POST /orders/:id/cancel` — customer cancel (only if status = placed)
- [ ] On cancel: release inventory reservation, trigger refund if paid
- [ ] `GET /admin/orders` — admin list with filters (status, branch, date range, search by ref)
- [ ] `GET /admin/orders/export` — export filtered orders to CSV

### Acceptance Criteria

- [ ] Invalid status transition (e.g. delivered → packed) returns `422`
- [ ] Cancellation releases stock within 5 seconds
- [ ] Status history shows full audit trail with timestamps
- [ ] Admin CSV export works for 1,000+ orders

---

## Milestone 3.5 — Returns & Warranty Handling

### Goal

Customer can raise a return or warranty claim. Admin can process it.

### Tasks

- [ ] Create `return_requests` table (order_id, items, reason, status, created_at)
- [ ] `POST /orders/:id/return` — raise return request (within 7 days of delivery)
- [ ] `GET /orders/returns` — customer's return history
- [ ] `GET /admin/returns` — admin list of all returns
- [ ] `PATCH /admin/returns/:id/status` — approve / reject / complete return
- [ ] On return approval: trigger refund to original payment method
- [ ] Create `warranty_claims` table (product_id, order_id, issue_desc, status)
- [ ] `POST /warranty-claims` — submit claim with photos
- [ ] `GET /admin/warranty-claims` — admin warranty queue

### Acceptance Criteria

- [ ] Return request after 7 days returns `422 Return window closed`
- [ ] Approved return triggers refund event to Payment Service
- [ ] Warranty claim requires order_id proving purchase

---

---

# PHASE 4 — Payment Integration

---

## Milestone 4.1 — SSLCOMMERZ Integration (Cards)

### Goal

Users can pay with credit/debit card via SSLCOMMERZ. Successful payment confirms order.

### Tasks

- [x] Integrate SSLCOMMERZ SDK / API
- [x] `POST /payments/sslcommerz/initiate` — create payment session, return redirect URL
- [x] Handle success callback: `POST /payments/sslcommerz/success`
- [x] Handle failure callback: `POST /payments/sslcommerz/fail`
- [x] Handle cancel callback: `POST /payments/sslcommerz/cancel`
- [x] Verify IPN (Instant Payment Notification) with HMAC signature
- [x] Idempotency: second callback for same transaction is ignored
- [x] Store payment record in `payments` table (never store card data)
- [x] Publish `payment.confirmed` or `payment.failed` event

### Acceptance Criteria

- [x] Successful payment → order status changes to confirmed
- [x] Failed payment → order remains in placed state with `payment_status = failed`
- [x] IPN signature mismatch → request rejected with `400`
- [x] Duplicate IPN callback → idempotently ignored

---

## Milestone 4.2 — bKash Integration

### Goal

Users can pay with bKash mobile wallet. Supports bKash payment agreement + execute flow.

### Tasks

- [x] Integrate bKash Payment Gateway API (create, execute, query)
- [x] `POST /payments/bkash/create` — create bKash payment
- [x] `POST /payments/bkash/execute` — execute after user approval
- [x] `GET /payments/bkash/query/:paymentId` — query payment status
- [x] `POST /payments/bkash/refund` — initiate refund
- [x] Handle bKash token expiry and refresh (Redis-cached access token)
- [x] Test with bKash sandbox environment
- [x] Graceful error handling: bKash timeout, user cancellation, insufficient balance messages

### Acceptance Criteria

- [x] End-to-end bKash payment flow works in sandbox
- [x] bKash refund initiated within 24 hours of return approval
- [x] Timeout handled gracefully (user redirected with clear message)

---

## Milestone 4.3 — Nagad Integration

### Goal

Users can pay with Nagad mobile wallet.

### Tasks

- [x] Integrate Nagad Payment API (create order, verify payment)
- [x] `POST /payments/nagad/initiate` — RSA-signed initialize + complete flow
- [x] `POST /payments/nagad/callback` — handle callback
- [x] Query payment status via reconciliation report
- [ ] Nagad refund endpoint (pending Nagad refund API availability)

### Acceptance Criteria

- [x] Same acceptance criteria as bKash milestone
- [x] Nagad sandbox tests pass

---

## Milestone 4.4 — Cash on Delivery & Payment Reconciliation

### Goal

COD orders are handled without a gateway. Admin can reconcile all payments.

### Tasks

- [x] COD orders bypass payment gateway; `payment_status = pending_cod`
- [x] COD limit: max order BDT 50,000 (configurable via `COD_MAX_ORDER_AMOUNT`)
- [x] On delivery confirmation → staff marks payment received → `payment_status = paid`
- [x] `GET /admin/payments` — list all payments with filters
- [x] `GET /admin/payments/reconciliation` — daily reconciliation report (gateway vs DB)
- [ ] EMI option flag on checkout (shows "EMI available" badge, directs to bank partner page)
- [x] Failed payment retry: `POST /payments/:orderId/retry`

### Acceptance Criteria

- [x] COD order above limit returns `422`
- [x] Reconciliation report shows matched/unmatched transactions
- [x] Payment retry generates a new payment session for the same order

---

---

# PHASE 5 — Delivery & Notifications

---

## Milestone 5.1 — Delivery Service: Courier Integration ✅ Done

### Goal

Orders can be dispatched via Pathao or Steadfast. Tracking numbers linked to orders.

### Tasks

- [x] Integrate Pathao Courier API (create parcel, track) — `delivery.py`
- [x] Integrate Steadfast Courier API (create parcel, track) — `delivery.py`
- [x] `POST /admin/delivery/dispatch/:orderId` — dispatch order (admin, select courier)
- [x] Store tracking number in `courier_dispatches` table + `orders.tracking_number`
- [x] `GET /delivery/track/:orderId` — fetch live tracking status from courier
- [x] Webhook endpoint: `POST /delivery/webhook/pathao` — receive delivery updates
- [x] Webhook endpoint: `POST /delivery/webhook/steadfast`
- [x] Auto-update order status on delivery webhook (shipped, delivered, returned)
- [x] Shipping zone config: `GET /delivery/zones`, `POST /admin/delivery/zones` (admin)
- [x] `GET /delivery/zones/calculate` — shipping cost by district + weight

### Acceptance Criteria

- [x] Parcel created in Pathao → tracking number returned and saved
- [x] Delivery webhook → order status updated within 30 seconds
- [x] Shipping cost correctly calculated by delivery zone

---

## Milestone 5.2 — Click & Collect (In-Store Pickup) ✅ Done

### Goal

Customer can choose to pick up order from a Star Tech branch instead of home delivery.

### Tasks

- [x] Add `fulfilment_type`, `pickup_branch_id`, `pickup_ready_at`, `pickup_confirmed_at` to orders
- [x] `GET /branches` — public endpoint: list active branches with address and opening hours
- [x] On checkout: `fulfilment_type=pickup` → `pickup_branch_id` required, `address_id` optional
- [x] `GET /admin/delivery/pickup/queue` — branch pickup queue (filterable by branch)
- [x] `PATCH /admin/delivery/pickup/:id/ready` — notify customer order is ready
- [x] `PATCH /admin/delivery/pickup/:id/confirm` — staff confirms customer collected

### Acceptance Criteria

- [x] Pickup order skips courier creation (422 if dispatch attempted)
- [x] Customer receives SMS when order is ready for pickup
- [x] Staff can filter pickup-only orders per branch

---

## Milestone 5.3 — Notification Service: SMS & Email ✅ Done

### Goal

Customers receive SMS and email notifications for all key order events.

### Tasks

- [x] `scripts/notification_worker.py` — RabbitMQ consumer for `notification_events` queue
- [x] SMS templates: order_confirmed, order_shipped, order_delivered, pickup_ready, return_approved, price_alert, restock_alert
- [x] Email templates: same events via SendGrid HTML email
- [x] Integrate Twilio SMS provider (`SMS_PROVIDER=twilio`)
- [x] Integrate generic HTTP SMS provider for BD local providers (`SMS_PROVIDER=generic_http`)
- [x] Integrate SendGrid for transactional email
- [x] `GET /notifications/preferences` — user can manage opt-in/out per channel
- [x] `PATCH /notifications/preferences` — update preferences
- [x] Failed notification: retry 3× with exponential backoff (2s → 4s → 8s), then dead-letter
- [x] All sends logged in `notification_log` table

### Acceptance Criteria

- [x] Order placed (COD) SMS arrives within 60 seconds
- [x] User who opts out of email does not receive email
- [x] Failed SMS retried 3 times before logging as `dead_lettered`

---

## Milestone 5.4 — Notification Service: Price Alerts & Restock ✅ Done

### Goal

Users can subscribe to price drop alerts and back-in-stock notifications.

### Tasks

- [x] `POST /notifications/price-alert` — subscribe to product price alert at target price
- [x] `DELETE /notifications/price-alert/:productId` — cancel price alert
- [x] `POST /notifications/restock-alert` — subscribe to product restock notification
- [x] `DELETE /notifications/restock-alert/:productId` — cancel restock alert
- [x] `scripts/alert_worker.py` — polls every 5 min; fires price + restock alerts via `notification_events` queue
- [x] `GET /notifications/my-alerts` — list user's active alerts (price + restock)
- [x] `DELETE /notifications/alerts/:id` — cancel any alert by ID

### Acceptance Criteria

- [x] Price drops to/below target → subscriber notified within 5 minutes (poll interval)
- [x] Out-of-stock product restocked → subscriber notified within 5 minutes
- [x] User can have up to 20 active alerts (enforced in app)

---

---

# PHASE 6 — PC Builder Tool

---

## Milestone 6.1 — Compatibility Engine

### Goal

Core compatibility rules validated in real-time as user selects PC components.

### Tasks

- [ ] Define compatibility rule schema (JSON config per rule type)
- [ ] Rules to implement:
  - CPU socket ↔ Motherboard socket (e.g. AM5, LGA1700)
  - RAM type ↔ Motherboard support (DDR4 / DDR5)
  - RAM speed within motherboard max supported speed
  - PSU wattage ≥ sum of component TDP (CPU + GPU + 20% headroom)
  - Case form factor ↔ Motherboard size (ATX, mATX, mITX)
  - GPU length ≤ case max GPU clearance (mm)
  - CPU cooler height ≤ case max cooler clearance (mm)
- [ ] `POST /pc-builder/validate` — submit component set → return compatibility result
- [ ] Return: `{ compatible: bool, warnings: [], errors: [] }`
- [ ] Errors block adding to cart; warnings inform but allow proceed

### Acceptance Criteria

- [ ] AM5 CPU + LGA1700 motherboard → error returned
- [ ] DDR5 RAM + DDR4 motherboard → error returned
- [ ] 850W GPU in 600W PSU build → warning with wattage breakdown
- [ ] All rules unit-tested

---

## Milestone 6.2 — PC Builder API & Build Management

### Goal

Users can build, save, share, and load PC builds. Builds can be added to cart.

### Tasks

- [ ] `GET /pc-builder/components/:slot` — get compatible components for a slot given existing selections
- [ ] `POST /pc-builder/builds` — save a build (auth required for permanent, guest gets temp link)
- [ ] `GET /pc-builder/builds/:slug` — load a saved build (public)
- [ ] `GET /pc-builder/builds` — list authenticated user's builds
- [ ] `DELETE /pc-builder/builds/:id` — delete build
- [ ] `POST /pc-builder/builds/:id/cart` — add all components to cart
- [ ] `GET /pc-builder/presets` — load Intel/Ryzen starter presets (admin-managed)
- [ ] `POST /pc-builder/presets` — create preset (admin)
- [ ] Share URL: `startech.com.bd/pc-builder/share/{slug}`

### Acceptance Criteria

- [ ] Saved build loads with all components correctly
- [ ] Add-all-to-cart adds each component as a separate line item
- [ ] Guest build link works without login
- [ ] Share URL is < 100 characters

---

---

# PHASE 7 — Frontend Web Application

---

## Milestone 7.1 — Next.js App Shell & Navigation

### Tasks

- [x] Set up Next.js 14 with App Router, TypeScript, Tailwind CSS
- [x] Global layout: header (logo, search bar, cart icon, account menu)
- [x] Mega menu navigation (matches Star Tech's 20+ category structure)
- [x] Mobile responsive hamburger menu
- [x] Footer (links, branch locations, social links)
- [x] Breadcrumb component
- [x] Loading states and skeleton screens for all pages
- [x] Light/dark mode toggle
- [x] Google Analytics 4 + Facebook Pixel integration (env-gated, wired via `Analytics.tsx`)

### Acceptance Criteria

- [ ] Lighthouse performance score ≥ 85 on mobile
- [ ] Navigation renders correctly on all screen sizes (320px → 1440px)
- [ ] Core Web Vitals: LCP < 2.5s, CLS < 0.1

---

## Milestone 7.2 — Homepage & Product Listing Pages

### Tasks

- [x] Homepage: hero banner, featured categories, deal of the day, featured products, brands
- [x] Category page: product grid with sidebar filters
- [ ] Filters: brand (checkbox), price range (slider), in-stock toggle, spec filters (dynamic per category) — brand/price/in-stock done, dynamic spec filters not implemented
- [x] Sort dropdown: relevance, price, newest
- [x] Product card component: image, name, price, rating, add-to-cart button
- [x] Pagination / infinite scroll
- [x] "On sale" badge overlay on discounted products
- [x] "Out of stock" badge on unavailable products

### Acceptance Criteria

- [ ] Homepage loads in < 2s (SSG)
- [ ] Filters update results without full page reload
- [ ] URL reflects filter state (shareable filtered URLs)

---

## Milestone 7.3 — Product Detail Page

### Tasks

- [x] Product image gallery (prev/next + thumbnails; no pinch-zoom)
- [x] Product name, brand, price, availability badge
- [x] Specs table (rendered from JSONB specs)
- [x] Add to cart / wishlist buttons
- [x] Quantity selector
- [x] Product comparison checkbox (up to 3 products)
- [x] Reviews section (display + submit review form) — new backend `reviews` table/endpoints
- [x] Q&A section — new backend `product_questions` table/endpoints
- [x] Related products carousel
- [x] "Also bought with" section (implemented as brand-based "You May Also Like"; no purchase-affinity data exists yet)
- [x] Price history chart (last 6 months) — backend endpoint made public (was admin-only)
- [x] SEO: dynamic `<title>`, `<meta description>`, JSON-LD schema

### Acceptance Criteria

- [x] Page is SSR for SEO (not client-only) — converted to server component + `generateMetadata`
- [x] JSON-LD Product schema renders correctly (not run through Google's Rich Results test tool)
- [ ] Image gallery works on touch (swipe on mobile) — click arrows only, no swipe gesture

---

## Milestone 7.4 — Search & Finder Pages

### Tasks

- [x] Search results page with facet sidebar
- [x] Real-time autocomplete dropdown (debounced, 300ms)
- [x] Empty state: "No results for X"
- [x] Laptop Finder wizard UI (step-by-step questions, progress bar) — `/laptop-finder`
- [x] Product comparison page (side-by-side spec table) — `/compare`
- [x] Search analytics: track what users search for (GA4 custom events)

### Acceptance Criteria

- [ ] Autocomplete appears in < 200ms after typing — not benchmarked
- [x] Laptop Finder completes in 5 steps or fewer (backend defines 5 questions)
- [x] Comparison page works for 2–3 products side by side

---

## Milestone 7.5 — Cart, Checkout & Order Tracking

### Tasks

- [x] Cart drawer/page: items, quantities, subtotal, remove item
- [x] Coupon code input and feedback
- [x] Checkout page: address selection/entry, delivery method, payment method
- [x] bKash / Nagad flow: redirect to mobile banking gateway
- [x] SSLCOMMERZ card payment form
- [x] COD option
- [x] Order confirmation page with order reference
- [x] Order history page (account)
- [x] Order detail + status timeline
- [x] Real-time tracking status (polling every 30 sec when order in transit)

### Acceptance Criteria

- [x] Guest checkout works without account
- [x] bKash redirect and callback handled correctly
- [ ] Order confirmation email received within 2 minutes of placement — not measured
- [x] Mobile checkout form usable with one hand (thumb-friendly)

---

---

# PHASE 8 — Admin Dashboard

---

## Milestone 8.1 — Admin Auth & Layout

### Tasks

- [x] Admin login page (email + password, 2FA via TOTP) — new backend `/auth/2fa/*` endpoints + `users.totp_enabled/totp_secret`
- [ ] Role-based sidebar navigation (super_admin, branch_admin, staff) — backend only has `admin`/`staff` roles today, no `super_admin`/`branch_admin` distinction
- [x] Dashboard home: today's orders, revenue, low-stock alerts (new users count omitted — no backend endpoint exists for it)
- [x] Responsive layout (tablet + desktop; no mobile for admin)

---

## Milestone 8.2 — Product & Category Management UI

### Tasks

- [x] Product list with search, filter, and bulk actions (activate, set draft, discontinue)
- [x] Product create/edit form with image upload, spec editor (key/value; not schema-driven per category)
- [ ] Category tree UI with drag-to-reorder — reorder implemented via up/down controls, not drag-and-drop
- [x] Brand management list + form
- [x] Bulk CSV import UI with progress and error report download

---

## Milestone 8.3 — Order & Inventory Management UI

### Tasks

- [x] Order list with filters (status, date, branch, payment method — search by reference, not payment method, since admin list doesn't filter on it)
- [x] Order detail page: items, timeline, change status, print invoice — new `GET/PATCH /admin/orders/{id}` + `GET /admin/orders/{id}/invoice` endpoints (admin order detail/invoice didn't exist before, only customer-scoped versions did)
- [x] Return & warranty claim management queue — new `PATCH /admin/warranty-claims/{id}/status` endpoint (previously missing entirely)
- [x] Inventory dashboard: per-branch stock levels, low-stock list — low-stock list + per-product stock lookup (search product → per-branch levels); no all-products-at-once grid
- [x] Stock transfer request form and approval UI
- [x] Manual stock adjustment form

---

## Milestone 8.4 — Analytics & Reports UI

### Tasks

- [x] Sales dashboard with date picker: revenue chart, order count, AOV — new `GET /admin/analytics/sales`
- [x] Revenue by category (bar chart) — new `GET /admin/analytics/by-category`
- [x] Revenue by branch (branch comparison) — new `GET /admin/analytics/by-branch`
- [x] Top 20 products (table with units sold, revenue) — new `GET /admin/analytics/top-products`
- [x] Customer acquisition chart (new vs returning) — new `GET /admin/analytics/customers` (first-order-date based)
- [x] Export any report to CSV / Excel — client-side CSV export per section (no Excel/.xlsx)
- [x] Coupon usage report (used existing `GET /coupons` usage_count, already returned)

---

---

# PHASE 9 — Mobile Application

---

## Milestone 9.1 — Mobile App Foundation

### Tasks

- [ ] React Native (Expo) project setup
- [ ] Navigation: Tab bar (Home, Search, Cart, Account) + Stack navigator
- [ ] Auth screens: login, register, OTP verification
- [ ] Reusable component library matching web design
- [ ] API client with JWT token management and auto-refresh
- [ ] Push notification setup (Firebase FCM)

---

## Milestone 9.2 — Home, Browse & Search

### Tasks

- [ ] Home screen: banner, featured categories, deals
- [ ] Category browse screen
- [ ] Search screen with autocomplete
- [ ] Product listing with infinite scroll
- [ ] Filters bottom sheet
- [ ] Product detail screen with image gallery

---

## Milestone 9.3 — Cart, Checkout & Orders

### Tasks

- [ ] Cart screen
- [ ] Checkout: address, delivery, payment selection
- [ ] bKash in-app web view payment
- [ ] Order confirmation screen
- [ ] Order history and detail screen with status timeline
- [ ] Push notification on order status change

---

## Milestone 9.4 — Account, Wishlist & PC Builder Mobile

### Tasks

- [ ] Account screen: profile, addresses, loyalty points
- [ ] Wishlist screen
- [ ] Price alert subscriptions
- [ ] PC Builder mobile flow (simplified, step-by-step)
- [ ] App store assets: screenshots, icons, descriptions
- [ ] Submit to Google Play (beta) and App Store (TestFlight)

---

---

# PHASE 10 — Analytics, Offers & Loyalty

---

## Milestone 10.1 — Offer & Promo Engine

### Tasks

- [ ] Flash sale scheduler: set start/end time, discounted price activates automatically
- [ ] Bundle deals: buy X + Y together → fixed/percentage discount
- [ ] `GET /offers` — current live offers page
- [ ] `GET /offers/eid-deals` — campaign-specific deals page
- [ ] Gift card: create, redeem, balance check
- [ ] Loyalty points: define earn rate (e.g. 1 point per BDT 100 spent)
- [ ] `POST /loyalty/redeem` — redeem points at checkout (e.g. 100 points = BDT 10 off)
- [ ] Points expiry after 12 months of inactivity

---

## Milestone 10.2 — Analytics Pipeline

### Tasks

- [ ] Event tracking: page_view, product_view, add_to_cart, purchase, search
- [ ] Ingest events to ClickHouse via Kafka consumer
- [ ] Build analytics API: `GET /analytics/sales`, `GET /analytics/products`, `GET /analytics/users`
- [ ] Funnel analysis: browse → cart → checkout → purchase conversion rates
- [ ] Scheduled report email: daily sales summary to admin at 9 AM
- [ ] "Most viewed products in last 24h" for homepage sorting

---

---

# PHASE 11 — QA, Performance & Security Hardening

---

## Milestone 11.1 — Testing

### Tasks

- [ ] Unit test coverage ≥ 80% for all backend services
- [ ] Integration tests for all critical flows: order placement, payment, inventory deduction
- [ ] E2E tests with Playwright: browse → add to cart → checkout → order confirmation
- [ ] Load test: simulate 1,000 concurrent users with k6
- [ ] Load test Elasticsearch with 50 concurrent search requests
- [ ] Test concurrent stock deduction (oversell protection)
- [ ] Cross-browser test: Chrome, Firefox, Safari, Samsung Internet
- [ ] Mobile device test: Android (Samsung, Xiaomi), iOS (iPhone 12+)

### Acceptance Criteria

- [ ] All E2E tests pass on staging
- [ ] Load test: P95 response < 2s under 1,000 concurrent users
- [ ] Zero oversell in concurrent inventory test (100 concurrent attempts for 10 units)

---

## Milestone 11.2 — Security Hardening

### Tasks

- [ ] Penetration test: SQL injection, XSS, CSRF, IDOR checks
- [ ] OWASP Top 10 review and fixes
- [ ] Configure Content Security Policy headers
- [ ] Enable HSTS + secure cookies
- [ ] Secrets management: move all secrets to AWS Secrets Manager / Vault
- [ ] Enable CloudTrail / access logs on all S3 buckets
- [ ] Database: enable row-level security (RLS) where applicable
- [ ] Review all admin endpoints have `admin` role guard
- [ ] PCI DSS self-assessment checklist

---

## Milestone 11.3 — Performance Optimisation

### Tasks

- [ ] Achieve Lighthouse score ≥ 85 on all key pages (home, PDP, search)
- [ ] Core Web Vitals: LCP < 2.5s, FID < 100ms, CLS < 0.1
- [ ] Image optimisation: WebP conversion, `srcset` for all product images
- [ ] Implement HTTP/2 push for critical CSS
- [ ] Redis cache warming on deployment (pre-cache category tree, top products)
- [ ] DB query analysis: add missing indexes based on slow query log
- [ ] Enable Postgres connection pooling in production (PgBouncer)
- [ ] Review and reduce bundle size (code splitting, tree shaking)

---

---

# PHASE 12 — Production Launch

---

## Milestone 12.1 — Pre-Launch Checklist

### Tasks

- [ ] DNS cutover plan documented (zero-downtime strategy)
- [ ] SSL certificate issued and auto-renewal configured
- [ ] Production environment variables set and verified
- [ ] Backups verified: daily DB snapshot, S3 versioning enabled
- [ ] Runbook documented: how to roll back each service
- [ ] Monitoring dashboards reviewed: alert thresholds tuned
- [ ] On-call rota defined for launch week
- [ ] Data migration: import existing product catalogue from old system
- [ ] Smoke test production with real bKash sandbox payment

---

## Milestone 12.2 — Staged Rollout

### Tasks

- [ ] Deploy to production with 5% traffic canary
- [ ] Monitor error rate, latency, and conversion for 2 hours
- [ ] Increase to 25% traffic → 50% → 100% over 24 hours
- [ ] DNS cutover from old platform to new (keep old platform as standby for 48h)
- [ ] Announce launch on social media / email newsletter
- [ ] Monitor peak traffic handling (first 48 hours)
- [ ] Post-launch retrospective scheduled for 1 week after launch

### Go/No-Go Criteria

- [ ] Error rate < 0.5% on canary traffic
- [ ] P95 response time < 2s on canary traffic
- [ ] Payment flow tested with real transaction in production
- [ ] No P0/P1 bugs open

---

---

## Summary Tracker

| Milestone                        | Phase              | Status  |
| -------------------------------- | ------------------ | ------- |
| 0.1 Repo & project structure     | Foundation         | ⬜ Todo |
| 0.2 CI/CD pipeline               | Foundation         | ⬜ Todo |
| 0.3 Database setup               | Foundation         | ⬜ Todo |
| 0.4 API Gateway                  | Foundation         | ⬜ Todo |
| 1.1 User registration & login    | Core Backend       | ⬜ Todo |
| 1.2 User profiles & addresses    | Core Backend       | ⬜ Todo |
| 1.3 Category tree                | Core Backend       | ⬜ Todo |
| 1.4 Brand management             | Core Backend       | ⬜ Todo |
| 1.5 Product CRUD                 | Core Backend       | ⬜ Todo |
| 1.6 Image upload                 | Core Backend       | ⬜ Todo |
| 1.7 Bulk import                  | Core Backend       | ⬜ Todo |
| 2.1 Multi-branch inventory       | Inventory & Search | ⬜ Todo |
| 2.2 Low stock alerts & transfers | Inventory & Search | ⬜ Todo |
| 2.3 Elasticsearch indexing       | Inventory & Search | ⬜ Todo |
| 2.4 Search API                   | Inventory & Search | ⬜ Todo |
| 2.5 Laptop Finder wizard         | Inventory & Search | ⬜ Todo |
| 3.1 Cart service                 | Cart & Orders      | ⬜ Todo |
| 3.2 Coupon service               | Cart & Orders      | ⬜ Todo |
| 3.3 Place order                  | Cart & Orders      | ⬜ Todo |
| 3.4 Order lifecycle              | Cart & Orders      | ⬜ Todo |
| 3.5 Returns & warranty           | Cart & Orders      | ⬜ Todo |
| 4.1 SSLCOMMERZ (cards)           | Payments           | ✅ Done |
| 4.2 bKash integration            | Payments           | ✅ Done |
| 4.3 Nagad integration            | Payments           | ✅ Done |
| 4.4 COD & reconciliation         | Payments           | ✅ Done |
| 5.1 Courier integration          | Delivery           | ✅ Done |
| 5.2 Click & Collect              | Delivery           | ✅ Done |
| 5.3 SMS & email notifications    | Notifications      | ✅ Done |
| 5.4 Price & restock alerts       | Notifications      | ✅ Done |
| 6.1 Compatibility engine         | PC Builder         | ⬜ Todo |
| 6.2 Build management API         | PC Builder         | ⬜ Todo |
| 7.1 Next.js app shell            | Frontend Web       | ✅ Done |
| 7.2 Homepage & listings          | Frontend Web       | ✅ Done |
| 7.3 Product detail page          | Frontend Web       | ✅ Done |
| 7.4 Search & Finder              | Frontend Web       | ✅ Done |
| 7.5 Cart, checkout & tracking    | Frontend Web       | ✅ Done |
| 8.1 Admin auth & layout          | Admin Dashboard    | ✅ Done |
| 8.2 Product management UI        | Admin Dashboard    | ✅ Done |
| 8.3 Order & inventory UI         | Admin Dashboard    | ✅ Done |
| 8.4 Analytics UI                 | Admin Dashboard    | ✅ Done |
| 9.1 Mobile app foundation        | Mobile App         | ⬜ Todo |
| 9.2 Home, browse & search        | Mobile App         | ⬜ Todo |
| 9.3 Cart, checkout & orders      | Mobile App         | ⬜ Todo |
| 9.4 Account & PC Builder         | Mobile App         | ⬜ Todo |
| 10.1 Offer & promo engine        | Analytics & Offers | ⬜ Todo |
| 10.2 Analytics pipeline          | Analytics & Offers | ⬜ Todo |
| 11.1 Testing                     | QA & Hardening     | ⬜ Todo |
| 11.2 Security hardening          | QA & Hardening     | ⬜ Todo |
| 11.3 Performance optimisation    | QA & Hardening     | ⬜ Todo |
| 12.1 Pre-launch checklist        | Launch             | ⬜ Todo |
| 12.2 Staged rollout              | Launch             | ⬜ Todo |

---

## Status Legend

| Symbol         | Meaning                        |
| -------------- | ------------------------------ |
| ⬜ Todo        | Not started                    |
| 🔵 In Progress | Actively being worked on       |
| ✅ Done        | Complete and accepted          |
| 🔴 Blocked     | Blocked by dependency or issue |
| ⏸️ Paused      | Deprioritised or on hold       |

---

_Update this file at every sprint sync. Each milestone = one PR review checkpoint._
