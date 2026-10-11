"""FastAPI application for the product layer.

The product never runs Playwright or repair logic; it only consumes structured output that
the user's CI sends after running the core (see discussion #332).
"""

from collections.abc import Awaitable, Callable
from importlib.metadata import version
from typing import Annotated, Literal

from app.logging import configure_logging
from app.schemas import SCHEMA_VERSION
from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from product_api.config import get_settings
from product_api.db import database_reachable, get_engine
from product_api.ingest import (
    IngestRunRequest,
    IngestRunResponse,
    store_run,
)
from product_api.oidc import UploadIdentity, require_upload_auth


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool


class VersionInfo(BaseModel):
    api_version: str
    schema_version: str


def create_app() -> FastAPI:
    api_version = version("product-api")
    api = FastAPI(title="E2E Self-Heal API", version=api_version)

    @api.middleware("http")
    async def reject_oversized_uploads(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method == "POST" and request.url.path == "/v1/runs":
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    is_too_large = int(content_length) > get_settings().max_upload_bytes
                except ValueError:
                    return JSONResponse(
                        status_code=400, content={"detail": "invalid content-length"}
                    )
                if is_too_large:
                    return JSONResponse(
                        status_code=413, content={"detail": "request body is too large"}
                    )
        return await call_next(request)

    @api.get("/version")
    def version_info() -> VersionInfo:
        return VersionInfo(api_version=api_version, schema_version=SCHEMA_VERSION)

    @api.get("/healthz")
    def healthz(response: Response, engine: Annotated[Engine, Depends(get_engine)]) -> Health:
        if not database_reachable(engine):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return Health(status="degraded", database=False)
        return Health(status="ok", database=True)

    @api.post("/v1/runs", response_model=IngestRunResponse, status_code=status.HTTP_201_CREATED)
    def ingest_run(
        request: IngestRunRequest,
        engine: Annotated[Engine, Depends(get_engine)],
        identity: Annotated[UploadIdentity, Depends(require_upload_auth)],
    ) -> IngestRunResponse:
        # GitHub's signed claims, rather than caller-controlled JSON, define storage identity.
        request.repository.github_repo_id = identity.repository_id
        request.repository.full_name = identity.full_name
        request.workflow.run_id = identity.run_id
        request.workflow.run_attempt = identity.run_attempt
        request.commit_sha = identity.sha
        request.ref = identity.ref
        return store_run(engine, request)

    return api


def run() -> FastAPI:
    """Uvicorn factory: configure logging from settings, then build the app."""
    configure_logging(get_settings().log_level)
    return create_app()
