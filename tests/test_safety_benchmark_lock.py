"""One safety benchmark per checkout (#452).

A second run in the same checkout would patch, or copy, the demo app the first one has broken
and score the wrong app without saying so (#407). The lock is held by real processes here: the
guarantee is about separate runs, and about the OS dropping the lock when a run dies.
"""

import os
import signal
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.safety_benchmark import BenchmarkLockedError, benchmark_lock_path, checkout_lock

_HOLD = textwrap.dedent(
    """
    import os, signal, sys
    from pathlib import Path
    from app.safety_benchmark import checkout_lock

    with checkout_lock(Path(sys.argv[1])):
        print("locked", flush=True)
        if sys.argv[2] == "die":
            os.kill(os.getpid(), signal.SIGKILL)
        sys.stdin.read()
    """
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scenarios").mkdir(parents=True)
    (repo / "scenarios" / ".keep").write_text("")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def other_run(checkout: Path) -> Iterator[subprocess.Popen[str]]:
    """A second process holding the checkout's lock until the test ends."""
    holder = _hold(checkout / "scenarios", "wait")
    yield holder
    holder.communicate("")


def _hold(scenario_root: Path, mode: str) -> subprocess.Popen[str]:
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD, str(scenario_root), mode],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    # structlog's default output shares stdout, so skip to the marker.
    assert any(line.strip() == "locked" for line in iter(holder.stdout.readline, ""))
    return holder


def test_a_second_run_in_the_same_checkout_is_refused_with_who_holds_it(
    checkout: Path, other_run: subprocess.Popen[str]
) -> None:
    result = CliRunner().invoke(
        app, ["safety-benchmark", "--scenario-root", str(checkout / "scenarios")]
    )

    assert result.exit_code == 2
    output = " ".join(result.output.split())
    assert "another safety benchmark is running in this checkout" in output
    assert f"pid {other_run.pid}" in output
    assert "git worktree add" in output


def test_a_lock_left_by_a_killed_run_does_not_block_the_next(checkout: Path) -> None:
    killed = _hold(checkout / "scenarios", "die")
    assert killed.wait() == -signal.SIGKILL
    assert f'"pid": {killed.pid}' in benchmark_lock_path(checkout / "scenarios").read_text()

    with checkout_lock(checkout / "scenarios") as path:
        assert f'"pid": {os.getpid()}' in path.read_text()


def test_separate_worktrees_of_one_repository_can_run_at_once(
    checkout: Path, other_run: subprocess.Popen[str]
) -> None:
    worktree = checkout.parent / "worktree"
    _git(checkout, "worktree", "add", "-q", "--detach", str(worktree))

    with checkout_lock(worktree / "scenarios"):
        pass
    with pytest.raises(BenchmarkLockedError):
        with checkout_lock(checkout / "scenarios"):
            pass


def test_the_lock_never_shows_in_git_status(checkout: Path) -> None:
    with checkout_lock(checkout / "scenarios"):
        assert _git(checkout, "status", "--porcelain", "--untracked-files=all") == ""
