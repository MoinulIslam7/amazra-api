# Amazra Platform

Phase 0 foundations for the Amazra monorepo (infra, CI/CD, and base tooling). The system
design reference lives in [SYSTEM.md](./SYSTEM.md).

## Local setup

1. Copy environment variables:
   `cp .env.example .env`
2. Generate local JWT keys (store outside git):
   `openssl genpkey -algorithm RSA -out ./infra/jwt-private.pem -pkeyopt rsa_keygen_bits:2048`
   `openssl rsa -pubout -in ./infra/jwt-private.pem -out ./infra/jwt-public.pem`
   Then set `JWT_PRIVATE_KEY_PATH` and `JWT_PUBLIC_KEY_PATH` in `.env`.
3. Install root tooling dependencies:
   `npm install`
4. Start infrastructure services:
   `docker compose -f docker-compose.yml up --build`
5. Run database migrations:
   `npm run migrate`
6. Seed sample data:
   `npm run seed`
7. Verify service connections:
   `npm run verify:connections`

To process product CSV imports, run the background worker in a separate shell:
`docker compose run --rm api python scripts/import_worker.py`

Image uploads and CSV imports use S3-compatible storage when `S3_BUCKET` is set.
If `S3_BUCKET` is empty, the API stores files under `LOCAL_STORAGE_PATH` and
serves them from `/media`; set `PUBLIC_BASE_URL` to the host that should serve
media (for example `http://localhost:8001` or `http://localhost:8000` via Kong).

Kong runs on `http://localhost:8000` and forwards to the API service. The health endpoint is
available at `http://localhost:8000/api/v1/health`. The Kong Admin API is on
`http://localhost:8002`.

## Categories

- Category trees support unlimited nesting depth.
- Admin CRUD endpoints are available at `/api/v1/admin/categories` and require an admin or
  staff role.

## Project layout

- `apps/` — web, admin, and mobile clients
- `services/` — backend services (API placeholder in `services/api`)
- `packages/` — shared types, utils, and constants
- `infra/` — Docker, Kong, Elasticsearch, and Kubernetes manifests

## Scripts

- `npm run lint` — lint JS/TS files
- `npm run format` — format supported files with Prettier
- `npm run migrate` — apply DB migrations inside the API container
- `npm run seed` — seed base data inside the API container
