import os
from pathlib import Path

import psycopg


def main() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    migrations_dir = (
        Path(__file__).resolve().parent.parent / "db" / "migrations"
    )
    migration_files = sorted(migrations_dir.glob("*.sql"))

    with psycopg.connect(database_url) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version TEXT PRIMARY KEY,
              applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
            """
        )

        applied = {
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        }

        for migration in migration_files:
            if migration.name in applied:
                continue
            sql = migration.read_text()
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (migration.name,),
                )
            print(f"Applied {migration.name}")


if __name__ == "__main__":
    main()
