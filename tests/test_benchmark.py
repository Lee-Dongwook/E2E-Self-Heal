from pathlib import Path

import pytest

from app.benchmark import (
    BenchmarkScenario,
    BenchmarkResult,
    _count_prompt_tokens,
    _benchmark_scenario,
    _line_containing,
    _tokenizer,
    run_example_benchmark,
)
from app.config import settings
from app.preprocess.jsx_chunker import CodeChunk
from app.prompts.diagnoser import build_user_prompt


def test_prompt_builder_uses_chunk_metadata_and_optional_snapshot() -> None:
    prompt = build_user_prompt(
        "Error: timeout",
        [{"attribute": "id", "before": "old", "after": "new"}],
        "- role: button\n  name: Submit",
        CodeChunk(source="<button>Submit</button>\n", start_line=4, end_line=4),
    )

    assert "ARIA page snapshot (at failure)" in prompt
    assert "semantic JSX chunk, lines 4-4" in prompt
    assert "<button>Submit</button>" in prompt


def test_semantic_chunk_prompt_has_fewer_tokens_than_full_file_prompt() -> None:
    full_source = "\n".join(f"const unused{index} = {index};" for index in range(50))
    full_prompt = build_user_prompt(
        "Error: timeout",
        [],
        "",
        CodeChunk(source=full_source, start_line=1, end_line=50, is_fallback=True),
    )
    semantic_prompt = build_user_prompt(
        "Error: timeout",
        [],
        "",
        CodeChunk(source="<button>Submit</button>", start_line=25, end_line=25),
    )

    assert _count_prompt_tokens(semantic_prompt) < _count_prompt_tokens(full_prompt)


def test_benchmark_reports_all_checked_in_examples() -> None:
    results = run_example_benchmark()
    assert [result.name for result in results] == ["id-rename", "jsx-context"]
    assert results[0].context_strategy == "whole-file fallback"
    assert results[0].tokens_saved == 0
    assert results[1].context_strategy.startswith("semantic JSX chunk")
    assert results[1].chunked_prompt_tokens < results[1].full_prompt_tokens
    assert results[1].tokens_saved > 0


def test_benchmark_uses_configured_semantic_jsx_margin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_path = tmp_path / "button.spec.tsx"
    test_path.write_text(
        "\n".join(
            [
                *(f"const unused{index} = {index};" for index in range(50)),
                "export function ButtonTest() {",
                "  return (",
                "    <section>",
                '      <button id="old-button">Submit</button>',
                "    </section>",
                "  );",
                "}",
            ]
        )
    )
    diff_path = tmp_path / "button.diff"
    diff_path.write_text(
        """diff --git a/button.tsx b/button.tsx
index 1111111..2222222 100644
--- a/button.tsx
+++ b/button.tsx
@@ -1,5 +1,5 @@
 export function Button() {
   return (
-    <button id="old-button">Submit</button>
+    <button id="new-button">Submit</button>
   );
 }
"""
    )

    monkeypatch.setattr(settings, "jsx_chunk_margin_lines", 0)
    result = _benchmark_scenario(
        BenchmarkScenario(
            name="jsx-example",
            test_path=test_path,
            diff_path=diff_path,
            failing_selector="old-button",
        )
    )

    assert result.context_strategy == "semantic JSX chunk (54-54)"
    assert result.chunked_prompt_tokens < result.full_prompt_tokens
    assert result.tokens_saved > 0


def test_line_containing_rejects_missing_selector() -> None:
    with pytest.raises(ValueError, match="not present"):
        _line_containing("await page.click('#present')", "#missing")


def test_result_calculates_token_savings() -> None:
    result = BenchmarkResult(
        name="example",
        context_strategy="semantic JSX chunk (1-1)",
        full_prompt_tokens=100,
        chunked_prompt_tokens=20,
    )

    assert result.tokens_saved == 80
    assert result.savings_percent == 80.0


def test_tokenizer_is_loaded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeTokenizer:
        def encode(self, text: str) -> list[str]:
            return list(text)

    def fake_get_encoding(name: str) -> FakeTokenizer:
        calls.append(name)
        return FakeTokenizer()

    _tokenizer.cache_clear()
    monkeypatch.setattr("app.benchmark.tiktoken.get_encoding", fake_get_encoding)
    try:
        _count_prompt_tokens("first prompt")
        _count_prompt_tokens("second prompt")
    finally:
        _tokenizer.cache_clear()

    assert calls == ["cl100k_base"]
