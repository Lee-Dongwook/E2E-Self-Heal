"""Keep the JSON output reference honest about the code it documents.

The reference page (docs-site/docs/reference/json-output.mdx) documents every field of the
machine-readable contract and shows payloads captured from real CLI runs, stored as files in
docs-site/src/data/json-contract/. These tests fail when the models and the page drift apart:
a field added, removed or renamed in ``app/schemas.py``, or a captured payload that no longer
round-trips through the current models.
"""

import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter

from app.schemas import (
    SCHEMA_VERSION,
    AstLockRejection,
    CandidateEvidence,
    EvidenceBundle,
    EvidenceDomDiff,
    HealResult,
    LoopEvidence,
    LoopEvidenceDetails,
    PatchInstruction,
    ProviderProvenance,
    RefusalReason,
    RefusalReport,
    RepairSummary,
    ReviewFinding,
    ReviewReport,
    SelectorHintEvidence,
    SnapshotReference,
    SuiteSummary,
)

PAGE = Path("docs-site/docs/reference/json-output.mdx")
PAYLOADS = Path("docs-site/src/data/json-contract")

DOCUMENTED_MODELS: list[type[BaseModel]] = [
    RepairSummary,
    RefusalReport,
    SuiteSummary,
    ReviewReport,
    PatchInstruction,
    ReviewFinding,
    EvidenceBundle,
    EvidenceDomDiff,
    SelectorHintEvidence,
    SnapshotReference,
    CandidateEvidence,
    AstLockRejection,
    LoopEvidence,
    LoopEvidenceDetails,
    ProviderProvenance,
]

# Reasons the page documents but cannot show a captured payload for, and why.
NOT_CAPTURED = {
    # Needs a live app URL and a candidate selector that matches several elements.
    RefusalReason.AMBIGUOUS_TARGET,
}

_ADAPTERS: dict[str, TypeAdapter] = {
    "repair": TypeAdapter(HealResult),
    "refusal": TypeAdapter(HealResult),
    "suite": TypeAdapter(SuiteSummary),
    "review": TypeAdapter(ReviewReport),
}


def _documented_fields(page: str) -> dict[str, set[str]]:
    """Map each ``### `Model``` section to the field names in its table's first column."""
    sections: dict[str, set[str]] = {}
    current: str | None = None
    for line in page.splitlines():
        heading = re.match(r"^#{2,4} `(\w+)`\s*$", line)
        if heading:
            name = heading.group(1)
            sections[name] = set()
            current = name
            continue
        if line.startswith("#"):
            current = None
            continue
        row = re.match(r"^\| `(\w+)` \|", line)
        if current is not None and row:
            sections[current].add(row.group(1))
    return sections


def _payload_paths() -> list[Path]:
    return sorted(PAYLOADS.glob("*.json"))


@pytest.mark.parametrize("model", DOCUMENTED_MODELS, ids=lambda model: model.__name__)
def test_every_field_of_every_model_is_documented(model: type[BaseModel]) -> None:
    sections = _documented_fields(PAGE.read_text(encoding="utf-8"))

    assert model.__name__ in sections, f"the page has no `{model.__name__}` section"
    # Both directions: an undocumented field, or a documented field that no longer exists.
    assert sections[model.__name__] == set(model.model_fields)


@pytest.mark.parametrize("path", _payload_paths(), ids=lambda path: path.stem)
def test_each_captured_payload_matches_the_current_models_exactly(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    adapter = _ADAPTERS[raw["kind"]]

    parsed = adapter.validate_python(raw)

    assert raw["schema_version"] == SCHEMA_VERSION
    # Validation alone ignores unknown keys, so also require an exact round trip: a field the
    # models dropped, or one they gained, means the captured example is stale.
    assert adapter.dump_python(parsed, mode="json") == raw


def test_every_refusal_reason_is_documented_and_captured_unless_explained() -> None:
    page = PAGE.read_text(encoding="utf-8")
    captured: set[RefusalReason] = set()
    for path in _payload_paths():
        raw = json.loads(path.read_text(encoding="utf-8"))
        documents = raw["results"] if raw["kind"] == "suite" else [raw]
        captured |= {RefusalReason(d["reason"]) for d in documents if d["kind"] == "refusal"}

    for reason in RefusalReason:
        assert f"`{reason.value}`" in page, f"{reason.value} is not documented"
    assert captured == set(RefusalReason) - NOT_CAPTURED
