#!/bin/sh
set -e

until python -c "import os, psycopg; psycopg.connect(os.environ['DATABASE_URL']).close()" 2>/dev/null; do
  echo "Waiting for database to be ready..."
  sleep 1
done

echo "Running database migrations..."
python scripts/migrate.py

echo "Seeding sample data (idempotent, safe to re-run)..."
python scripts/seed.py

echo "Starting API server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
