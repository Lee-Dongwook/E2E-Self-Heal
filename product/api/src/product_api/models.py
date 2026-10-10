"""Database tables owned by the product API."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base for product API ORM models."""


class Project(Base):
    """A GitHub repository that has uploaded one or more CI results."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)


class Run(Base):
    """An immutable core result uploaded by a GitHub Actions workflow."""

    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "workflow_run_id",
            "run_attempt",
            "kind",
            name="uq_runs_workflow_attempt_kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    is_success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    ref: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
