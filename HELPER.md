# Amazra — Helper Guide

Practical reference for running the project, logging in, using every feature, and
poking at the database directly. For the original architecture write-up see
[SYSTEM.md](./SYSTEM.md); for build/feature status per phase see
[MILESTONE.md](./MILESTONE.md).

---

## 1. Running everything

One command, from the `amazra-api` repo root:

```bash
docker compose -f docker-compose.yml up --build
```

This builds the API image and starts Postgres, PgBouncer, Redis, Elasticsearch,
RabbitMQ, Kong, and the API itself. The API container automatically waits for
Postgres, runs migrations, seeds sample data (safe to re-run — inserts are
`ON CONFLICT DO NOTHING`), then starts the server. Nothing else to run by hand.

Then, in a second terminal, start the web app:

```bash
cd amazra-web
npm run dev
```

### Service directory

| Service                   | URL                                 | Notes                                        |
| ------------------------- | ----------------------------------- | -------------------------------------------- |
| Storefront (Next.js)      | http://localhost:3000               | customer-facing site                         |
| Admin dashboard           | http://localhost:3000/admin/login   | same Next.js app, `/admin/*` routes          |
| API (via Kong gateway)    | http://localhost:8000/api/v1        | what the web app actually talks to           |
| API (direct, bypass Kong) | http://localhost:8001/api/v1        | useful for debugging Kong-routing issues     |
| API health check          | http://localhost:8000/api/v1/health | should return `{"status":"ok"}`              |
| Kong Admin API            | http://localhost:8002               | inspect Kong's own config                    |
| RabbitMQ management UI    | http://localhost:15672              | login `guest` / `guest`                      |
| Postgres                  | `localhost:5433`                    | see [§3 Database access](#3-database-access) |
| Redis                     | `localhost:6380`                    | cache, sessions, autocomplete, OTP storage   |
| Elasticsearch             | http://localhost:9201               | product search index                         |

---

## 2. Logging in

### Customer account

No seeded customer account exists — register one yourself:

- Storefront: http://localhost:3000/register (needs name, a BD phone number in the
  form `01XXXXXXXXX`, optional email, password)
- Or via API: `POST /api/v1/auth/register` with `{name, email, phone, password}`

Customer login accepts **either** phone+password or email+password at
http://localhost:3000/login.

### Admin account (seeded)

```
Email:    admin@amazra.com
Password: admin12345
```

Log in at **http://localhost:3000/admin/login**. This account has the `admin`
role, which unlocks the entire `/admin/*` dashboard. Override these defaults by
setting `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD` in `.env` before first seed.

2FA is **off** by default for this account, so the plain email+password form logs
you straight in. To turn on 2FA:

1. `POST /api/v1/auth/2fa/setup` (authenticated) → returns a `secret` and an
   `otpauth_uri` — scan the URI with an authenticator app (Google Authenticator,
   Authy, etc.), or paste the raw `secret` into one manually.
2. `POST /api/v1/auth/2fa/enable` with `{"code": "<6-digit code>"}` to confirm.
3. From then on, `/admin/login` will prompt for a 6-digit code after your
   password, since the login form detects `{"requires_2fa": true}` from the API
   and shows the code step automatically.
4. To turn it back off: `POST /api/v1/auth/2fa/disable` with a valid code.

There's no UI for step 1/2 yet (no self-service 2FA enrollment screen was built)
— use `curl` or a REST client with your bearer token.

### Staff accounts

The `staff` role has identical dashboard access to `admin` today (the backend
doesn't yet distinguish `super_admin` / `branch_admin` — see MILESTONE.md 8.1).
Create one by registering normally, then in the database:

```sql
UPDATE users SET role_id = (SELECT id FROM roles WHERE name = 'staff')
WHERE email = 'someone@example.com';
```

---

## 3. Database access

Connection details (from `.env`):

```
Host:     localhost
Port:     5433
Database: amazra
User:     postgres
Password: postgres
```

**Via `psql` on the host** (if installed):

```bash
psql postgresql://postgres:postgres@localhost:5433/amazra
```

**Via `psql` inside the container** (no local install needed):

```bash
docker exec -it amazra-postgres psql -U postgres -d amazra
```

**Via a GUI client** (TablePlus, DBeaver, pgAdmin, Postico, etc.) — use the same
host/port/user/password/database above. `localhost:5433` is reachable from the
host because docker-compose publishes that port.

**Useful things to poke at:**

```sql
\dt                              -- list all tables
SELECT * FROM users;             -- see registered users + roles
SELECT * FROM orders ORDER BY created_at DESC LIMIT 10;
SELECT * FROM products LIMIT 10;
SELECT * FROM schema_migrations; -- which migrations have been applied
```

There's also a connection-pooled endpoint via PgBouncer on `localhost:6432` (same
credentials) if you want to test through the pooler specifically — the app
itself talks to Postgres directly, not through PgBouncer, so this is optional.

---

## 4. Storefront features (customer-facing)

All at http://localhost:3000 unless noted.

| Feature                  | URL                                                                                                                              | Notes                                                                              |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Homepage                 | `/`                                                                                                                              | hero, featured categories, deals, brands                                           |
| Category browsing        | `/category/laptops` (or `desktops`, `components`, `monitors`, `accessories`, `storage`, `networking`, `cameras`, `gaming`, etc.) | product grid + filters                                                             |
| All products             | `/products`                                                                                                                      | filter by brand/price/stock                                                        |
| Product detail           | `/products/amazra-laptop-pro-14`                                                                                                 | gallery, specs, reviews, Q&A, price history, related/also-liked, compare, wishlist |
| Search                   | `/search?q=laptop`                                                                                                               | autocomplete dropdown appears while typing in the header search bar                |
| Laptop Finder wizard     | `/laptop-finder`                                                                                                                 | 5-question guided wizard → ranked recommendations                                  |
| Product comparison       | `/compare`                                                                                                                       | add up to 3 products via the ⚖ icon on any product card or detail page             |
| Cart                     | `/cart` (or the cart drawer icon in the header)                                                                                  | coupon code input included                                                         |
| Checkout                 | `/checkout`                                                                                                                      | address, delivery method (delivery/pickup), payment method                         |
| Order confirmation       | `/checkout/confirmation/<orderId>`                                                                                               | shown right after placing an order                                                 |
| Order history            | `/account/orders`                                                                                                                | requires login                                                                     |
| Order tracking           | `/account/orders/<orderId>`                                                                                                      | live status timeline, polls every 30s while in transit                             |
| Wishlist                 | `/account/wishlist`                                                                                                              | requires login                                                                     |
| Notification preferences | `/account/notifications`                                                                                                         | opt in/out of SMS/email channels                                                   |
| Dark mode                | toggle icon (☀/🌙) in the header                                                                                                 | persists via `next-themes`                                                         |

**Reviews & Q&A**: on any product page, the "Reviews" and "Q&A" tabs let a
logged-in customer submit a star rating + comment, or ask a question. Reviews
are marked "Verified Purchase" automatically if the reviewer has a `delivered`
order containing that product.

**Payments**: bKash/Nagad/SSLCOMMERZ only work if their sandbox credentials are
set in `.env` (they're blank by default, so at checkout only **Cash on Delivery**
will actually succeed end-to-end unless you configure gateway credentials).

---

## 5. Admin dashboard features

All under http://localhost:3000/admin (login required — see §2).

| Feature             | URL                                           | What it does                                                                                                                                                                            |
| ------------------- | --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dashboard home      | `/admin`                                      | today's orders, today's revenue, low-stock count, recent orders list                                                                                                                    |
| Orders              | `/admin/orders`                               | filter by status/branch/date/reference, CSV export                                                                                                                                      |
| Order detail        | `/admin/orders/<id>`                          | items, customer info, change status (follows allowed transitions), print PDF invoice, status timeline                                                                                   |
| Returns & warranty  | `/admin/returns`                              | two tabs — approve/reject/complete return requests and warranty claims                                                                                                                  |
| Inventory           | `/admin/inventory`                            | 3 tabs: **Low Stock** list, **Stock Lookup** (search a product → per-branch quantities → manual adjustment), **Transfers** (request + advance pending→approved→in_transit→completed)    |
| Products            | `/admin/products`                             | search/filter/bulk activate-draft-discontinue                                                                                                                                           |
| Product create/edit | `/admin/products/new`, `/admin/products/<id>` | full form incl. spec key/value editor and image upload/primary/delete                                                                                                                   |
| Categories          | `/admin/categories`                           | tree view, create/edit, up/down reorder, soft delete                                                                                                                                    |
| Brands              | `/admin/brands`                               | list, create/edit, logo upload                                                                                                                                                          |
| Bulk import         | `/admin/import`                               | upload CSV → live progress polling → error report download                                                                                                                              |
| Analytics & reports | `/admin/analytics`                            | date-range picker; revenue trend chart, revenue by category, revenue by branch, top 20 products, new-vs-returning customers chart, coupon usage — every section has a CSV export button |

CSV template for bulk import: click "Download CSV Template" on the import page,
or `GET /api/v1/products/import/template` directly.

---

## 6. API reference (grouped by feature)

Base URL: `http://localhost:8000/api/v1` (via Kong) — all routes below are
relative to that. Admin-only routes require a bearer token from a user with
`admin` or `staff` role.

| Area               | Key routes                                                                                                                                                                                                     |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Auth               | `POST /auth/register`, `/auth/login`, `/auth/otp/send`, `/auth/otp/verify`, `/auth/refresh`, `/auth/logout`, `/auth/2fa/setup`, `/auth/2fa/enable`, `/auth/2fa/disable`                                        |
| Users              | `GET/PATCH /users/me`, `/users/me/addresses`                                                                                                                                                                   |
| Categories         | `GET /categories`, admin CRUD at `/admin/categories`                                                                                                                                                           |
| Brands             | `GET /brands`, admin CRUD + `/brands/{id}/logo`                                                                                                                                                                |
| Products           | `GET /products`, `/products/{slug}`, `/products/{id}/price-history` (public); admin CRUD, image upload, `/admin/products/{id}`                                                                                 |
| Bulk import        | `POST /products/import`, `/products/import/{jobId}`, `/products/import/template`                                                                                                                               |
| Reviews / Q&A      | `/products/{id}/reviews`, `/reviews/{id}/helpful`, `/products/{id}/questions`, `/questions/{id}/answer` (admin)                                                                                                |
| Search             | `/search`, `/search/autocomplete`, `/search/finder/questions`, `/search/finder/results`                                                                                                                        |
| Cart / Coupons     | `/cart`, `/cart/items`, `/cart/coupon`, admin `/coupons`                                                                                                                                                       |
| Orders             | `/orders`, `/orders/{id}`, `/orders/{id}/invoice`, `/orders/{id}/cancel`; admin `/admin/orders`, `/admin/orders/export`, `/admin/orders/{id}`, `/admin/orders/{id}/invoice`, `PATCH /admin/orders/{id}/status` |
| Returns / Warranty | `/orders/{id}/return`, `/orders/returns`, `/warranty-claims`; admin `/admin/returns`, `/admin/warranty-claims`                                                                                                 |
| Inventory          | `/inventory/{productId}`, `/inventory/adjust`, `/inventory/low-stock`, `/inventory/transfer(s)`                                                                                                                |
| Payments           | `/payments/*` (bkash/nagad/sslcommerz/cod flows), admin `/admin/payments/reconciliation`                                                                                                                       |
| Delivery           | `/delivery/*`, `/branches`, admin `/admin/delivery/*`                                                                                                                                                          |
| Notifications      | `/notifications`, `/notifications/preferences`                                                                                                                                                                 |
| Analytics (admin)  | `/admin/analytics/sales`, `/by-category`, `/by-branch`, `/top-products`, `/customers`                                                                                                                          |

Interactive docs aren't enabled (FastAPI's `/docs` isn't exposed in `main.py`) —
this table plus reading the relevant `app/*.py` file is the fastest way to check
a request/response shape.

---

## 7. Everyday operations

```bash
# Tail API logs
docker logs -f amazra-api

# Rebuild just the API after a code/dependency change
docker compose -f docker-compose.yml up -d --build api

# Run migrations / seed manually (already automatic on container start)
npm run migrate
npm run seed

# Stop everything (keeps data)
docker compose -f docker-compose.yml down

# Stop everything AND wipe the database/search index
docker compose -f docker-compose.yml down -v
```
