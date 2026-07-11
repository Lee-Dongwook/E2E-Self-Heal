from typing import cast

import app.nodes.reviewer as reviewer_module
from app.nodes.reviewer import reviewer
from app.schemas import ReviewFinding, ReviewOutput
from app.state import AgentState

BASE_STATE = cast(
    AgentState,
    {
        "analysis_report": "The #cta selector broke because className changed.",
        "dom_diff_context": [
            {"file": "components/CTAButton.tsx", "line": 12, "current": {"tag": "button"}}
        ],
        "current_code": "await page.click('#cta')",
    },
)


def test_reviewer_returns_findings(monkeypatch):
    finding = ReviewFinding(
        file="components/CTAButton.tsx",
        line=12,
        broken_selector="#cta",
        root_cause="className renamed from 'cta' to 'cta-btn'",
        suggestion="add a stable data-testid",
        recommended_selector="getByRole('button', { name: 'Submit' })",
    )
    monkeypatch.setattr(
        reviewer_module, "generate_review", lambda *a, **k: ReviewOutput(findings=[finding])
    )

    result = reviewer(BASE_STATE)

    findings = result["review_report"]["findings"]
    assert len(findings) == 1
    assert findings[0]["file"] == "components/CTAButton.tsx"
    assert findings[0]["line"] == 12
    assert findings[0]["recommended_selector"].startswith("getByRole")


def test_reviewer_returns_empty_report_on_llm_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("llm exploded")

    monkeypatch.setattr(reviewer_module, "generate_review", boom)

    result = reviewer(BASE_STATE)

    assert result == {"review_report": {"findings": []}}
