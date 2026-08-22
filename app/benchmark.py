"""Offline token benchmark for the checked-in repair examples."""

from functools import lru_cache
from pathlib import Path
from typing import Self

import tiktoken
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import settings

from app.preprocess.diff_ast_analyzer import analyze_diff
from app.preprocess.jsx_chunker import CodeChunk, chunk_for_line
from app.prompts.diagnoser import SYSTEM_PROMPT, build_user_prompt

_TOKENIZER_NAME = "cl100k_base"
_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class BenchmarkScenario(BaseModel):
    """A stored example with the selector expected to fail after applying its diff."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    test_path: Path
    diff_path: Path
    failing_selector: str = Field(min_length=1)

    @field_validator("test_path", "diff_path")
    @classmethod
    def validate_scenario_file(cls, path: Path) -> Path:
        """Require each stored scenario input to be a readable regular file."""
        if not path.is_file():
            raise ValueError(f"benchmark scenario file does not exist: {path}")
        return path


class BenchmarkResult(BaseModel):
    """Full-file and semantic-context token totals for one example."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    context_strategy: str = Field(min_length=1)
    full_prompt_tokens: int = Field(ge=0)
    chunked_prompt_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_chunked_total(self) -> Self:
        """Prevent invalid benchmark results whose chunk is larger than the baseline."""
        if self.chunked_prompt_tokens > self.full_prompt_tokens:
            raise ValueError("chunked_prompt_tokens must not exceed full_prompt_tokens")
        return self

    chunked_prompt_tokens: int

    @property
    def tokens_saved(self) -> int:
        return self.full_prompt_tokens - self.chunked_prompt_tokens

    @property
    def savings_percent(self) -> float:
        if self.full_prompt_tokens == 0:
            return 0.0
        return self.tokens_saved / self.full_prompt_tokens * 100


def example_scenarios(repository_root: Path = _REPOSITORY_ROOT) -> tuple[BenchmarkScenario, ...]:
    """Return the breakage scenarios maintained with the runnable example project."""
    examples = repository_root / "examples"
    return (
        BenchmarkScenario(
            name="id-rename",
            test_path=examples / "scenarios/id-rename/spec.ts",
            diff_path=examples / "scenarios/id-rename/change.patch",
            failing_selector="#submit-btn",
        ),
        BenchmarkScenario(
            name="jsx-context",
            test_path=examples / "scenarios/jsx-context/benchmark-context.tsx",
            diff_path=examples / "scenarios/jsx-context/change.patch",
            failing_selector="legacy-submit",
        ),
    )


def run_example_benchmark(
    repository_root: Path = _REPOSITORY_ROOT,
) -> tuple[BenchmarkResult, ...]:
    """Measure Diagnoser prompt content for every checked-in breakage scenario."""
    return tuple(_benchmark_scenario(scenario) for scenario in example_scenarios(repository_root))


def _benchmark_scenario(scenario: BenchmarkScenario) -> BenchmarkResult:
    source = scenario.test_path.read_text()
    failing_line = _line_containing(source, scenario.failing_selector)
    error_log = (
        f"Error: waiting for locator({scenario.failing_selector!r}) timed out\n"
        f"  at {scenario.test_path}:{failing_line}"
    )
    dom_diff_context = [
        change.model_dump() for change in analyze_diff(scenario.diff_path.read_text())
    ]
    full_context = CodeChunk(
        source=source, start_line=1, end_line=len(source.splitlines()), is_fallback=True
    )
    semantic_context = chunk_for_line(source, failing_line, margin=settings.jsx_chunk_margin_lines)
    full_prompt = build_user_prompt(error_log, dom_diff_context, "", full_context)
    chunked_prompt = build_user_prompt(error_log, dom_diff_context, "", semantic_context)
    return BenchmarkResult(
        name=scenario.name,
        context_strategy="whole-file fallback"
        if semantic_context.is_fallback
        else f"semantic JSX chunk ({semantic_context.start_line}-{semantic_context.end_line})",
        full_prompt_tokens=_count_prompt_tokens(full_prompt),
        chunked_prompt_tokens=_count_prompt_tokens(chunked_prompt),
    )


def _line_containing(source: str, text: str) -> int:
    """Return the 1-indexed line containing a scenario's failing selector."""
    for line_number, line in enumerate(source.splitlines(), start=1):
        if text in line:
            return line_number
    raise ValueError(f"selector {text!r} is not present in benchmark scenario source")


def _count_prompt_tokens(user_prompt: str) -> int:
    """Count system and user prompt content with a stable tokenizer estimate."""
    return len(_tokenizer().encode(SYSTEM_PROMPT)) + len(_tokenizer().encode(user_prompt))


@lru_cache(maxsize=1)
def _tokenizer() -> tiktoken.Encoding:
    """Load the fixed tokenizer once per process."""
    return tiktoken.get_encoding(_TOKENIZER_NAME)
