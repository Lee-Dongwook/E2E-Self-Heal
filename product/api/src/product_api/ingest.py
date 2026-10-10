"""Validation and persistence for CI result uploads."""

from datetime import UTC, datetime
from typing import Annotated

from app.schemas import SCHEMA_VERSION, HealResult, ReviewReport, SuiteSummary
from fastapi import HTTPException, status
from pydantic import BaseModel, Field, TypeAdapter, field_validator
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert

from product_api.models import Project, Run

RunPayload = Annotated[HealResult | SuiteSummary | ReviewReport, Field(discriminator="kind")]
run_payload_adapter = TypeAdapter(RunPayload)
SUPPORTED_SCHEMA_VERSIONS = (SCHEMA_VERSION,)


class Repository(BaseModel):
    github_repo_id: int = Field(gt=0)
    full_name: str = Field(min_length=1, max_length=255)


class Workflow(BaseModel):
    run_id: int = Field(gt=0)
    run_attempt: int = Field(default=1, ge=1)


class IngestRunRequest(BaseModel):
    repository: Repository
    workflow: Workflow
    commit_sha: str = Field(min_length=1, max_length=64)
    ref: str = Field(min_length=1, max_length=255)
    payload: RunPayload

    @field_validator("payload", mode="before")
    @classmethod
    def _known_schema_version(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            supported = ", ".join(SUPPORTED_SCHEMA_VERSIONS)
            raise ValueError(f"unsupported schema_version; supported versions: {supported}")
        return value


class IngestRunResponse(BaseModel):
    id: int
    created: bool


def payload_is_success(payload: RunPayload) -> bool:
    """Normalize each core result's completion signal for dashboard filtering."""
    if isinstance(payload, ReviewReport):
        return payload.is_complete
    return payload.is_success


def store_run(engine: Engine, request: IngestRunRequest) -> IngestRunResponse:
    """Store a run once; retries with the same workflow attempt return the original row."""
    payload = request.payload.model_dump(mode="json")
    with engine.begin() as connection:
        project_id = connection.execute(
            insert(Project)
            .values(
                github_repo_id=request.repository.github_repo_id,
                full_name=request.repository.full_name,
            )
            .on_conflict_do_update(
                index_elements=[Project.github_repo_id],
                set_={"full_name": request.repository.full_name},
            )
            .returning(Project.id)
        ).scalar_one()

        created_id = connection.execute(
            insert(Run)
            .values(
                project_id=project_id,
                kind=request.payload.kind,
                schema_version=request.payload.schema_version,
                is_success=payload_is_success(request.payload),
                commit_sha=request.commit_sha,
                ref=request.ref,
                workflow_run_id=request.workflow.run_id,
                run_attempt=request.workflow.run_attempt,
                received_at=datetime.now(UTC),
                payload=payload,
            )
            .on_conflict_do_nothing(constraint="uq_runs_workflow_attempt_kind")
            .returning(Run.id)
        ).scalar_one_or_none()
        if created_id is not None:
            return IngestRunResponse(id=created_id, created=True)

        existing_id = connection.execute(
            select(Run.id).where(
                Run.project_id == project_id,
                Run.workflow_run_id == request.workflow.run_id,
                Run.run_attempt == request.workflow.run_attempt,
                Run.kind == request.payload.kind,
            )
        ).scalar_one()
        return IngestRunResponse(id=existing_id, created=False)


def require_upload_auth() -> None:
    """Deny all uploads until the OIDC authentication endpoint is implemented."""
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="upload authentication is not configured"
    )
