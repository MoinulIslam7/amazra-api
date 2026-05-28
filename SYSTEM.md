# Star Tech BD — End-to-End System Design

> **Star Tech** is Bangladesh's largest tech-focused e-commerce and retail platform, selling computers, laptops, phones, components, networking gear, cameras, office equipment, security systems, software, and 100+ other categories from 500+ brands across multiple physical branches and online.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [User Personas](#2-user-personas)
3. [Feature List](#3-feature-list)
4. [System Architecture](#4-system-architecture)
5. [Microservices Breakdown](#5-microservices-breakdown)
6. [Data Model](#6-data-model)
7. [Key System Flows](#7-key-system-flows)
8. [Tech Stack](#8-tech-stack)
9. [External Integrations](#9-external-integrations)
10. [Non-Functional Requirements](#10-non-functional-requirements)
11. [Bangladesh-Specific Considerations](#11-bangladesh-specific-considerations)
12. [Infrastructure & DevOps](#12-infrastructure--devops)
13. [Security](#13-security)

---

## 1. Project Overview

Star Tech BD (`startech.com.bd`) operates as both an **online e-commerce platform** and a **multi-branch physical retail chain** in Bangladesh. The system must seamlessly handle:

- Product discovery across 500+ brands and 20+ top-level categories
- A unique **PC Builder tool** with real-time compatibility checking
- Order management across online and offline (POS) channels
- Multi-branch inventory with stock transfers
- Local payment gateways (bKash, Nagad, SSLCOMMERZ)
- B2C (consumers) and B2B (corporate bulk buyers) customer types
- Delivery partner integrations and click-and-collect

### Scale at a Glance

| Metric                 | Value      |
| ---------------------- | ---------- |
| Brands                 | 500+       |
| Top-level categories   | 20+        |
| Sub-categories         | 200+       |
| Physical branches (BD) | 10+        |
| Customer types         | B2C + B2B  |
| Primary market         | Bangladesh |

---

## 2. User Personas

### End Consumer (B2C)

- Buys laptops, phones, accessories, and PC components
- Uses the PC Builder tool to configure custom rigs
- Compares specs between products and reads reviews
- Pays via bKash, Nagad, credit/debit card, or Cash on Delivery
- Tracks orders via SMS and the website

### Corporate / B2B Buyer

- Places bulk orders for servers, switches, printers, and office equipment
- Requires tax invoices, credit terms, and formal quotations
- May request installation or after-sales service
- Often has a dedicated sales rep point-of-contact

### Admin / Staff

- Manages products, inventory levels, and orders from the admin dashboard
- Has branch-level or HQ-level access based on role
- Handles warranty claims, returns, and refunds
- Generates sales reports and inventory snapshots

### Vendor / Supplier

- Updates stock feeds and product pricing
- Manages purchase order acknowledgements
- May access a restricted supplier portal

---

## 3. Feature List

### 3.1 Customer-Facing Features

#### Discovery & Search

- Full-text product search with autocomplete/suggestions
- Filter by brand, price range, specs, and availability
- Category and deep sub-category browsing
- **Laptop Finder** wizard (guided questions → recommendations)
- Product comparison (side-by-side spec table)

#### PC Builder Tool

- Select components slot by slot (CPU, Motherboard, RAM, GPU, Storage, PSU, Case, Cooler)
- Real-time **compatibility engine** (socket match, RAM type, PSU wattage, case clearance)
- Live price total and stock availability check
- Save builds with a unique shareable URL/slug
- Add entire build to cart in one click
- Intel and Ryzen preset starting configurations

#### Cart & Checkout

- Guest cart (Redis-backed) and logged-in persistent cart
- Coupon and promo code application
- Multiple saved delivery addresses
- Delivery date/slot selection
- Order summary review before payment

#### Payments

- **bKash** (mobile banking)
- **Nagad** (mobile banking)
- **Rocket** (mobile banking)
- Credit / Debit card via **SSLCOMMERZ**
- **Cash on Delivery (COD)**
- Bank transfer (for B2B)
- **EMI** options via partner banks

#### User Account

- Registration and login (email/phone + OTP)
- Order history and real-time order tracking
- Wishlist management
- Saved delivery addresses
- Loyalty / reward points balance

#### Reviews & Ratings

- Verified-purchase product reviews
- Star ratings (1–5)
- Product Q&A section
- Helpfulness votes on reviews

#### Notifications

- Order status updates via SMS and email
- Price drop alerts for wishlisted items
- Back-in-stock notifications
- Promotional offer and deal alerts

#### Offers & Deals

- Flash sales (e.g., Eid Deals, special offers)
- Bundle discounts
- Gift cards
- Loyalty points redemption

---

### 3.2 Admin / Back-Office Features

#### Product Management

- Rich product editor (name, specs, images, description, SEO fields)
- Variant management (e.g., colour, storage size)
- Bulk import/export via CSV
- Price history tracking
- Product status: active / draft / discontinued

#### Inventory Management

- Per-branch stock levels
- Low-stock threshold alerts
- Stock transfer requests between branches
- Reorder point triggers (auto-purchase order suggestion)
- Serial number / IMEI tracking for high-value items

#### Order Management

- Full order lifecycle: placed → confirmed → packed → shipped → delivered → returned
- Manual order creation (for phone/walk-in orders)
- Return and refund workflows
- Warranty claim tracking
- Invoice and packing slip generation

#### Analytics & Reporting

- Sales dashboard (daily, weekly, monthly)
- Revenue breakdown by branch, category, and brand
- Top-selling products report
- Customer behaviour funnel (browse → cart → purchase)
- Export reports to Excel / PDF

#### Logistics Management

- Delivery partner API integration
- Zone-based shipping rate configuration
- Tracking number linking per order
- **Click & Collect** (in-store pickup at chosen branch)

#### Branch Management

- Per-branch stock and order views
- Branch-specific pricing overrides
- Staff account and role management per branch
- POS terminal integration for walk-in sales

---

## 4. System Architecture

The platform follows a **microservices architecture** with clear separation of concerns across layers.

```
┌─────────────────────────────────────────────────────────────┐
│                        CLIENT LAYER                         │
│   Web App (Next.js)  │  Mobile App (RN)  │  Admin Dashboard │
└───────────────────────────────┬─────────────────────────────┘
                                │ HTTPS
┌───────────────────────────────▼─────────────────────────────┐
│                      CDN / EDGE LAYER                       │
│          Cloudflare CDN · Image CDN · WAF · DDoS            │
└───────────────────────────────┬─────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────┐
│                    API GATEWAY / BFF                        │
│     Kong · Rate Limiting · JWT Auth · Request Routing       │
└───────┬────────┬────────┬────────┬────────┬────────┬────────┘
        │        │        │        │        │        │
┌───────▼──┐ ┌───▼───┐ ┌──▼───┐ ┌─▼────┐ ┌─▼────┐ ┌─▼──────┐
│  Order   │ │Product│ │Inven-│ │ User │ │Pay-  │ │Search  │
│ Service  │ │Service│ │tory  │ │Serv- │ │ment  │ │Service │
│          │ │       │ │Serv. │ │ ice  │ │Serv. │ │(ES)    │
└──────────┘ └───────┘ └──────┘ └──────┘ └──────┘ └────────┘
        │        │        │        │        │        │
┌───────▼────────▼────────▼────────▼────────▼────────▼───────┐
│                 MESSAGE QUEUE / EVENT BUS                   │
│              RabbitMQ / Kafka · Event Sourcing              │
└───────────────────────────────┬─────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────┐
│                        DATA LAYER                           │
│  PostgreSQL · Redis · Elasticsearch · S3 · ClickHouse       │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Microservices Breakdown

| Service                   | Responsibility                                                   |
| ------------------------- | ---------------------------------------------------------------- |
| **Order Service**         | Order lifecycle, returns, refunds, invoice generation            |
| **Product Service**       | Product CRUD, categories, brands, SEO metadata                   |
| **Inventory Service**     | Multi-branch stock levels, reservations, transfers               |
| **User Service**          | Registration, authentication, profiles, loyalty points           |
| **Payment Service**       | Gateway integration, payment sessions, callbacks, reconciliation |
| **Delivery Service**      | Courier API integration, tracking, shipping zones                |
| **Notification Service**  | SMS, email, push notification dispatch                           |
| **Search Service**        | Elasticsearch indexing, full-text search, facets, autocomplete   |
| **Offer / Promo Service** | Coupon codes, flash sales, bundle rules, gift cards              |
| **Analytics Service**     | Event ingestion, reporting queries, dashboard aggregations       |
| **PC Builder Service**    | Compatibility matrix, build saving/sharing, build-to-cart        |
| **Branch Service**        | Branch configuration, POS integration, staff management          |

### Service Communication

- **Synchronous**: REST over HTTP for real-time queries (product detail, search, cart)
- **Asynchronous**: RabbitMQ/Kafka events for decoupled operations (order placed → inventory deduction → notification → analytics)

---

## 6. Data Model

### Users Table

```sql
CREATE TABLE users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            VARCHAR(200) NOT NULL,
  email           VARCHAR(200) UNIQUE,
  phone           VARCHAR(20) NOT NULL,        -- BD phone number, OTP target
  password_hash   VARCHAR(255),
  type            VARCHAR(20) DEFAULT 'b2c',   -- b2c | b2b | admin | staff
  loyalty_points  INT DEFAULT 0,
  is_active       BOOLEAN DEFAULT TRUE,
  created_at      TIMESTAMP DEFAULT NOW(),
  updated_at      TIMESTAMP DEFAULT NOW()
);
```

### Products Table

```sql
CREATE TABLE products (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            VARCHAR(500) NOT NULL,
  slug            VARCHAR(500) UNIQUE NOT NULL, -- URL-friendly, indexed
  brand_id        UUID REFERENCES brands(id),
  category_id     UUID REFERENCES categories(id),
  price           DECIMAL(12,2) NOT NULL,       -- BDT
  original_price  DECIMAL(12,2),               -- for showing discount
  specs           JSONB,                        -- flexible per-category attributes
  status          VARCHAR(20) DEFAULT 'active', -- active | draft | discontinued
  is_featured     BOOLEAN DEFAULT FALSE,
  meta_title      VARCHAR(300),
  meta_description TEXT,
  created_at      TIMESTAMP DEFAULT NOW(),
  updated_at      TIMESTAMP DEFAULT NOW()
);
```

### Orders Table

```sql
CREATE TABLE orders (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID REFERENCES users(id),
  branch_id        UUID REFERENCES branches(id),  -- fulfilment branch
  status           VARCHAR(30) DEFAULT 'placed',
    -- placed | confirmed | packed | shipped | delivered | cancelled | returned
  total_amount     DECIMAL(12,2) NOT NULL,         -- BDT
  discount_amount  DECIMAL(12,2) DEFAULT 0,
  shipping_amount  DECIMAL(12,2) DEFAULT 0,
  payment_status   VARCHAR(20) DEFAULT 'pending',
    -- pending | paid | failed | refunded
  payment_method   VARCHAR(30),                    -- bkash | nagad | card | cod
  payment_ref      VARCHAR(200),                   -- gateway transaction ID
  delivery_address JSONB NOT NULL,                 -- snapshot at order time
  tracking_number  VARCHAR(200),                   -- courier tracking ID
  notes            TEXT,
  created_at       TIMESTAMP DEFAULT NOW(),
  updated_at       TIMESTAMP DEFAULT NOW()
);
```

### Order Items Table

```sql
CREATE TABLE order_items (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id    UUID REFERENCES orders(id) ON DELETE CASCADE,
  product_id  UUID REFERENCES products(id),
  quantity    INT NOT NULL,
  unit_price  DECIMAL(12,2) NOT NULL,   -- price snapshot at purchase time
  total_price DECIMAL(12,2) NOT NULL
);
```

### Inventory Table (Multi-Branch)

```sql
CREATE TABLE inventory (
  product_id          UUID REFERENCES products(id),
  branch_id           UUID REFERENCES branches(id),
  quantity            INT NOT NULL DEFAULT 0,
  reserved_qty        INT NOT NULL DEFAULT 0,  -- held for pending orders
  low_stock_threshold INT DEFAULT 5,           -- alert trigger
  updated_at          TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (product_id, branch_id)
);
```

### Categories Table (Self-Referencing Tree)

```sql
CREATE TABLE categories (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        VARCHAR(200) NOT NULL,
  slug        VARCHAR(200) UNIQUE NOT NULL,
  parent_id   UUID REFERENCES categories(id), -- NULL = top-level
  sort_order  INT DEFAULT 0,
  is_active   BOOLEAN DEFAULT TRUE
);
```

Categories are stored as a self-referencing tree with unlimited depth. Admin CRUD is
exposed via `/api/v1/admin/categories` and guarded by admin/staff roles.

### PC Builds Table

```sql
CREATE TABLE pc_builds (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID REFERENCES users(id),     -- NULL for guest builds
  slug        VARCHAR(200) UNIQUE NOT NULL,  -- shareable URL key
  name        VARCHAR(200),
  components  JSONB NOT NULL,                -- {cpu_id, mb_id, ram_id, ...}
  total_price DECIMAL(12,2),
  is_public   BOOLEAN DEFAULT TRUE,
  created_at  TIMESTAMP DEFAULT NOW()
);
```

---

## 7. Key System Flows

### 7.1 Order Placement Flow

```
User adds to cart
      │
      ▼
Redis soft-reserves stock (15 min TTL)
      │
      ▼
User initiates checkout
  → selects address, delivery slot, applies coupon
      │
      ▼
Payment gateway call (SSLCOMMERZ / bKash API)
  → payment session created, redirect to gateway
      │
      ▼
Payment callback received (gateway POST)
  → Order Service verifies signature
  → Creates order record in PostgreSQL
  → Emits OrderPlaced event to message queue
      │
      ▼ (async, parallel)
  ┌───┴────────────────────────────┐
  │                                │
  ▼                                ▼
Inventory Service             Notification Service
Deducts stock                 Sends SMS + email confirmation
  │                                │
  ▼                                ▼
Analytics Service             Loyalty Service
Captures purchase event       Credits points if applicable
      │
      ▼
Branch staff picks & packs order
      │
      ▼
Delivery partner API called
  → Tracking number generated and stored
  → Customer notified with tracking link
      │
      ▼
Delivery confirmed (courier webhook)
  → Order status → Delivered
  → Loyalty points finalised
```

### 7.2 PC Builder Flow

```
User opens PC Builder
      │
      ▼
Choose base preset (Intel / Ryzen) or start blank
      │
      ▼
Select component slot-by-slot
      │
      ▼ (real-time)
Compatibility Engine checks:
  - CPU socket ↔ Motherboard socket
  - RAM type (DDR4/DDR5) compatibility
  - PSU wattage ≥ total component TDP
  - Case form factor ↔ Motherboard ATX/mATX
  - CPU cooler clearance
      │
      ▼
Inventory Service: live stock check per component
Price total updated live
      │
      ▼
User saves build → unique slug generated
  → Shareable URL: /pc-builder/share/{slug}
      │
      ▼
"Add all to cart" → all components as line items
→ Standard checkout flow begins
```

### 7.3 Product Search Flow

```
User types query
      │
      ▼
Autocomplete → Redis cache (instant, <50ms)
      │
      ▼
Full search request
      │
      ▼
Elasticsearch query
  - Multi-field match (name, brand, category, specs)
  - Fuzzy matching for typos
  - Filter by: price range, brand, in-stock status, specs
  - Boost: featured products, high-stock items
      │
      ▼
Results enriched with:
  - Real-time stock status from Inventory Service
  - Current prices from Product Service
  - Aggregated facets (brand counts, price histograms)
      │
      ▼
Response to client:
  - Paginated product list
  - Filter facets for sidebar UI
  - Total result count
```

### 7.4 Inventory Stock Deduction (Concurrency-Safe)

```sql
-- Atomic stock deduction to prevent overselling
UPDATE inventory
SET
  quantity     = quantity     - :ordered_qty,
  reserved_qty = reserved_qty - :ordered_qty
WHERE
  product_id = :product_id
  AND branch_id = :branch_id
  AND (quantity - reserved_qty) >= :ordered_qty;

-- If 0 rows updated → out of stock → rollback order
```

---

## 8. Tech Stack

### Frontend

| Layer            | Technology                   | Reason                           |
| ---------------- | ---------------------------- | -------------------------------- |
| Web App          | **Next.js 14** (App Router)  | SSR/SSG for SEO, fast page loads |
| Mobile App       | **React Native** (Expo)      | iOS + Android from one codebase  |
| Admin Dashboard  | **React + Vite**             | SPA, fast iteration              |
| State Management | **Zustand + React Query**    | Client state + server cache sync |
| Styling          | **Tailwind CSS + shadcn/ui** | Rapid UI, consistent design      |

### Backend

| Layer                    | Technology                        | Reason                                     |
| ------------------------ | --------------------------------- | ------------------------------------------ |
| Core API Services        | **NestJS (Node.js + TypeScript)** | Modular, DI, well-suited for microservices |
| High-throughput services | **Go**                            | Inventory Service, Search aggregation      |
| API Gateway              | **Kong**                          | Rate limiting, auth, routing, plugins      |
| Authentication           | **JWT + OTP (SMS)**               | Stateless, refresh tokens, role-based      |

### Data & Storage

| Store               | Technology             | Used For                                             |
| ------------------- | ---------------------- | ---------------------------------------------------- |
| Primary database    | **PostgreSQL 16**      | Users, orders, products, inventory                   |
| Cache               | **Redis 7**            | Cart, sessions, price cache, autocomplete            |
| Search              | **Elasticsearch 8**    | Full-text search, facets, filters                    |
| Analytics           | **ClickHouse**         | Fast OLAP, sales reports, funnel analysis            |
| Object storage      | **AWS S3 / DO Spaces** | Product images, invoices, export files               |
| Flexible attributes | **MongoDB**            | Per-category spec schemas (camera specs ≠ RAM specs) |

### Infrastructure

| Component        | Technology                                         |
| ---------------- | -------------------------------------------------- |
| Containerisation | Docker + Kubernetes (EKS/GKE)                      |
| CI/CD            | GitHub Actions                                     |
| Message queue    | RabbitMQ / Apache Kafka                            |
| Monitoring       | Prometheus + Grafana                               |
| Error tracking   | Sentry                                             |
| CDN / WAF        | Cloudflare                                         |
| Cloud region     | AWS `ap-south-1` (Mumbai) — lowest latency from BD |

---

## 9. External Integrations

### Payment Gateways

| Gateway        | Type          | Notes                              |
| -------------- | ------------- | ---------------------------------- |
| **SSLCOMMERZ** | Aggregator    | Cards, net banking, mobile banking |
| **bKash**      | Mobile wallet | Most popular in Bangladesh         |
| **Nagad**      | Mobile wallet | Government-backed, growing fast    |
| **Rocket**     | Mobile wallet | DBBL mobile banking                |

### Delivery Partners

| Partner           | Type                    |
| ----------------- | ----------------------- |
| **Pathao**        | Last-mile delivery API  |
| **Steadfast**     | Courier API             |
| **RedX**          | Courier API             |
| **Self-delivery** | Branch-managed delivery |

### Communication

| Service                            | Used For                  |
| ---------------------------------- | ------------------------- |
| **Twilio / local BD SMS provider** | OTP, order status SMS     |
| **SendGrid / SES**                 | Transactional email       |
| **Firebase FCM**                   | Mobile push notifications |

### Analytics & Marketing

| Tool               | Purpose                         |
| ------------------ | ------------------------------- |
| Google Analytics 4 | Traffic and conversion tracking |
| Facebook Pixel     | Ad retargeting                  |
| Hotjar             | Heatmaps, session recording     |

---

## 10. Non-Functional Requirements

### Performance Targets

| Metric                    | Target      |
| ------------------------- | ----------- |
| Web page load (P95)       | < 2 seconds |
| Search API response       | < 300 ms    |
| Checkout API latency      | < 500 ms    |
| Payment callback handling | < 1 second  |
| Inventory deduction       | < 100 ms    |
| CDN cache hit ratio       | > 85%       |

### Availability & Scalability

| Requirement        | Target                                               |
| ------------------ | ---------------------------------------------------- |
| Platform uptime    | 99.9% (≤ 8.7 hours downtime/year)                    |
| Peak load handling | 10× normal traffic (Eid sales, flash deals)          |
| Horizontal scaling | All services are stateless, scale via Kubernetes HPA |
| Database reads     | Read replicas for product catalogue queries          |
| Cart / session     | Redis cluster with replication                       |
| Message queue      | Kafka with consumer group auto-scaling               |

---

## 11. Bangladesh-Specific Considerations

| Consideration                | Implementation                                              |
| ---------------------------- | ----------------------------------------------------------- |
| **Mobile-first traffic**     | ~70% of visits from mobile; responsive UI, touch-optimised  |
| **Low bandwidth**            | WebP images, lazy loading, < 200KB initial JS bundle        |
| **Local payment methods**    | bKash, Nagad, Rocket, COD — not just cards                  |
| **Bangla language support**  | i18n framework; Bangla product names, UI strings            |
| **Service worker / offline** | Cache product pages for intermittent 3G connectivity        |
| **Low hosting latency**      | AWS ap-south-1 (Mumbai) — closest region to Dhaka           |
| **COD fraud mitigation**     | COD order limit cap, phone OTP verification before dispatch |
| **National holidays**        | Special deal campaigns for Eid, Puja, Independence Day      |
| **B2B invoicing**            | VAT-compliant invoices per NBR (Bangladesh Revenue) rules   |

---

## 12. Infrastructure & DevOps

### Kubernetes Deployment (High-Level)

```yaml
# Each microservice runs as a separate Deployment
# Example: Order Service
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
spec:
  replicas: 3
  selector:
    matchLabels:
      app: order-service
  template:
    spec:
      containers:
        - name: order-service
          image: startech/order-service:latest
          resources:
            requests: { cpu: '100m', memory: '256Mi' }
            limits: { cpu: '500m', memory: '512Mi' }
```

### CI/CD Pipeline (GitHub Actions)

```
Push to main branch
      │
      ▼
Run unit + integration tests
      │
      ▼
Build Docker image → push to ECR
      │
      ▼
Deploy to staging (K8s rolling update)
      │
      ▼
Smoke tests pass?
  Yes → Deploy to production (canary → 100%)
  No  → Auto-rollback
```

### Monitoring Stack

- **Prometheus** — metrics scraping from all services
- **Grafana** — dashboards for latency, error rate, throughput
- **Sentry** — real-time error tracking with stack traces
- **PagerDuty** — on-call alerting for critical failures
- **CloudWatch / Datadog** — infrastructure-level metrics (RDS, Redis, EC2)

---

## 13. Security

### Authentication & Authorisation

- **JWT access tokens** (15-minute expiry) + **refresh tokens** (30-day expiry, rotated)
- **OTP via SMS** for login, password reset, and high-value order confirmation
- **Role-based access control (RBAC)**: `customer`, `staff`, `branch_admin`, `super_admin`
- API Gateway enforces JWT validation before any service call

### Data Security

- **TLS 1.3** for all data in transit
- **AES-256** encryption for sensitive data at rest (payment references, PII)
- **Card data never stored** — tokenised by SSLCOMMERZ / gateway
- **PII data minimisation** — only store what is necessary

### Application Security

- **Cloudflare WAF** — protects against SQLi, XSS, CSRF, bot attacks
- **Rate limiting** — per-IP and per-user at API Gateway level
- **CORS** — strict allowlist of trusted origins
- **Input validation** — all API inputs validated with class-validator / Zod schemas
- **Audit logs** — every admin action logged with actor ID, timestamp, and diff

### Payment Security

- PCI DSS compliant payment flow — all card processing delegated to certified gateways
- Payment callback verification via HMAC signature check
- Idempotency keys on all payment API calls to prevent duplicate charges

---

## Getting Started (Development)

```bash
# Clone the repository
git clone https://github.com/your-org/startech-bd.git
cd startech-bd

# Start all services with Docker Compose
docker compose up -d

# Run database migrations
npm run migrate

# Seed sample data
npm run seed

# Start the web app
cd apps/web && npm run dev
# → http://localhost:3000

# Start the admin dashboard
cd apps/admin && npm run dev
# → http://localhost:3001
```

### Environment Variables

```env
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/startech
REDIS_URL=redis://localhost:6379

# Auth
JWT_SECRET=your-secret-key
JWT_REFRESH_SECRET=your-refresh-secret

# Payment Gateways
SSLCOMMERZ_STORE_ID=your-store-id
SSLCOMMERZ_STORE_PASS=your-store-pass
BKASH_APP_KEY=your-bkash-app-key
BKASH_APP_SECRET=your-bkash-app-secret

# Services
ELASTICSEARCH_URL=http://localhost:9200
S3_BUCKET=startech-media
S3_REGION=ap-south-1

# Notifications
SMS_API_KEY=your-sms-api-key
SENDGRID_API_KEY=your-sendgrid-key
```

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m 'feat: add your feature'`
4. Push to the branch: `git push origin feature/your-feature`
5. Open a Pull Request against `main`

Please follow the [Conventional Commits](https://www.conventionalcommits.org/) specification for commit messages.

---

## License

This system design document is for educational and planning purposes, based on publicly available information from [startech.com.bd](https://www.startech.com.bd/).

---

_Last updated: May 2026_
