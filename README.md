# Amazra Platform

Phase 0 foundations for the Amazra monorepo (infra, CI/CD, and base tooling). The system
design reference lives in [SYSTEM.md](./SYSTEM.md).

## Local setup

1. Copy environment variables:
   `cp .env.example .env`
2. Install root tooling dependencies:
   `npm install`
3. Start infrastructure services:
   `docker compose -f docker-compose.yml up --build`
4. Run database migrations:
   `npm run migrate`
5. Seed sample data:
   `npm run seed`
6. Verify service connections:
   `npm run verify:connections`

Kong runs on `http://localhost:8000` and forwards to the API service. The health endpoint is
available at `http://localhost:8000/api/v1/health`. The Kong Admin API is on
`http://localhost:8002`.

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
