# product/

**English** · [한국어](README.ko.md)

The web product layer for E2E Self-Heal ([#332](https://github.com/Lee-Dongwook/E2E-Self-Heal/discussions/332)).

| Folder | What | Stack |
| --- | --- | --- |
| `api/` | Backend API | FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16 (Python 3.13, uv) |
| `web/` | Web app | React, Vite, Tailwind, TypeScript (pnpm) |

**Boundary:** nothing here diagnoses or patches tests, and nothing here runs Playwright or user
code. That stays in the core (`app/`), which runs in the user's CI. The product only consumes the
core's machine-readable output (`app/schemas.py`). The core does not depend on `product/`.

## Run it locally

Requires Docker, uv, and pnpm 9.

### Option 1: Docker Compose (Database + API)

```bash
# Start both Postgres and the API
docker compose -f product/docker-compose.yml up --build

# Run database migrations in another terminal
docker compose -f product/docker-compose.yml exec api uv run alembic upgrade head
```

### Option 2: Manual (uv)

```bash
# 1. Database
docker compose -f product/docker-compose.yml up -d --wait postgres

# 2. API on http://localhost:8000
cd product/api
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn product_api.main:run --factory --reload

# 3. Web app on http://localhost:5173 (in another terminal)
cd product/web
pnpm install
pnpm dev
```

If a local Postgres already uses port 5432, start the database with `PRODUCT_DB_PORT=55432` and
use that port in `api/.env`.

The page shows the API's health. `curl localhost:8000/healthz` returns
`{"status":"ok","database":true}`, or a 503 with `"database":false` when Postgres is down.

`curl localhost:8000/version` returns `api_version` from the installed `product-api`
package and `schema_version` from the core's JSON contract. This endpoint does not
access Postgres. The API version also appears in `/docs` and `/openapi.json`.

## Checks

```bash
cd product/api && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
cd product/web && pnpm build
```
