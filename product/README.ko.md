# product/

[English](README.md) · **한국어**

E2E Self-Heal의 웹 제품 레이어입니다 ([#332](https://github.com/Lee-Dongwook/E2E-Self-Heal/discussions/332)).

| 폴더 | 역할 | 스택 |
| --- | --- | --- |
| `api/` | Backend API | FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16 (Python 3.13, uv) |
| `web/` | Web app | React, Vite, Tailwind, TypeScript (pnpm) |

**경계:** 여기에서는 테스트를 진단하거나 패치하지 않으며, Playwright나 사용자 코드를 실행하지도 않습니다. 그 역할은 사용자의 CI에서 실행되는 core(`app/`)에 남아 있습니다. product는 core의 machine-readable output(`app/schemas.py`)만 소비합니다. core는 `product/`에 의존하지 않습니다.

## 로컬 실행

Docker, uv, pnpm 9가 필요합니다.

### 옵션 1: Docker Compose (Database + API)

```bash
# Start both Postgres and the API
docker compose -f product/docker-compose.yml up --build

# Run database migrations in another terminal
docker compose -f product/docker-compose.yml exec api uv run alembic upgrade head
```

### 옵션 2: 수동 실행 (uv)

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

로컬 Postgres가 이미 5432 포트를 사용 중이라면, `PRODUCT_DB_PORT=55432`로 database를 시작하고 `api/.env`에서도 같은 포트를 사용하세요.

페이지에 API의 health 상태가 표시됩니다. `curl localhost:8000/healthz`는
`{"status":"ok","database":true}`를 반환하고, Postgres가 내려가 있으면 503과 함께 `"database":false`를 반환합니다.

`curl localhost:8000/version`은 설치된 `product-api` package의 `api_version`과 core의 JSON contract에 정의된 `schema_version`을 반환합니다. 이 endpoint는 Postgres에 접근하지 않습니다. API version은 `/docs`와 `/openapi.json`에도 표시됩니다.

## 점검

```bash
cd product/api && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
cd product/web && pnpm build
```
