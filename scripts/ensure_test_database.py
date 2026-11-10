"""Create the isolated kenwer_c3_test database if missing; enable pgvector."""

from __future__ import annotations

import sys

import psycopg


ADMIN_URL = "postgresql://kenwer:kenwer@localhost:5433/postgres"
TEST_DB = "kenwer_c3_test"
TEST_URL = f"postgresql://kenwer:kenwer@localhost:5433/{TEST_DB}"


def main() -> int:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (TEST_DB,),
            )
            exists = cur.fetchone() is not None
            if not exists:
                cur.execute(f'CREATE DATABASE "{TEST_DB}"')
                print(f"created database {TEST_DB}")
            else:
                print(f"database {TEST_DB} already exists")

    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            print("pgvector extension ensured on kenwer_c3_test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
