"""scripts/sync_release_pins.py keeps release pins and documented defaults in step (#457).

The tests run against copies of the repository's real files, so a reworded README or docs page
that a rule no longer matches fails here, not at release time.
"""

import shutil
from pathlib import Path

import pytest

from scripts.sync_release_pins import ROOT, RULES, current_values, sync, tracked_files

# Files that hold an action pin without a rule naming them; the pin rule finds them by scanning.
_SCANNED_PIN_FILES = (
    "demo-page/src/components/Architecture.tsx",
    "docs-site/docs/getting-started/heal-vs-review.mdx",
    "docs-site/docs/getting-started/quickstart-github-action.mdx",
)
_NEW = {"version": "9.8.7", "model": "example/next-default"}


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, list[str]]:
    files = sorted(
        {name for rule in RULES if rule.files for name in rule.files}
        | set(_SCANNED_PIN_FILES)
        | {"CHANGELOG.md"}
    )
    for name in files:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, target)
    return tmp_path, files


def _snapshot(root: Path, files: list[str]) -> dict[str, str]:
    return {name: (root / name).read_text(encoding="utf-8") for name in files}


def test_the_repository_is_in_sync_with_its_version_and_default_model() -> None:
    # The same check CI runs: a release or default change must update every pin.
    report = sync(ROOT, current_values(), tracked_files(), write=False)

    assert report.missing == []
    assert report.changes == []


def test_a_release_rewrites_every_pin_and_default_but_never_the_changelog(
    tree: tuple[Path, list[str]],
) -> None:
    root, files = tree
    old = current_values()
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")

    report = sync(root, _NEW, files, write=True)

    assert report.missing == []
    assert {name for name, *_ in report.changes} == set(files) - {"CHANGELOG.md"}
    after = _snapshot(root, files)
    for name in set(files) - {"CHANGELOG.md"}:
        assert f"@v{old['version']}" not in after[name]
    for lockfile in ("uv.lock", "product/api/uv.lock"):
        assert 'name = "ai-driven-e2e"\nversion = "9.8.7"' in after[lockfile]
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == changelog
    # The free-tier table reports a measurement of the old default; it must keep its name.
    configuration = after["docs-site/docs/reference/configuration.mdx"]
    assert f"NVIDIA `{old['model']}` (the default)" in configuration


def test_a_second_sync_changes_nothing(tree: tuple[Path, list[str]]) -> None:
    root, files = tree
    sync(root, _NEW, files, write=True)
    synced = _snapshot(root, files)

    report = sync(root, _NEW, files, write=True)

    assert report.clean
    assert _snapshot(root, files) == synced


def test_check_mode_reports_stale_values_without_writing(tree: tuple[Path, list[str]]) -> None:
    root, files = tree
    before = _snapshot(root, files)

    report = sync(root, _NEW, files, write=False)

    assert report.changes
    assert _snapshot(root, files) == before


def test_a_reworded_spot_is_reported_missing_instead_of_silently_skipped(
    tree: tuple[Path, list[str]],
) -> None:
    root, files = tree
    readme = root / "README.ja.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace("デフォルトモデルは", "既定モデルは"),
        encoding="utf-8",
    )

    report = sync(root, _NEW, files, write=False)

    assert ("README.ja.md", "intro (ja)") in report.missing
    assert not report.clean


def test_a_pin_in_a_newly_added_doc_is_found_without_a_rule_change(
    tree: tuple[Path, list[str]],
) -> None:
    root, files = tree
    new_doc = root / "docs-site/docs/guides/new-guide.mdx"
    new_doc.parent.mkdir(parents=True, exist_ok=True)
    new_doc.write_text("- uses: Lee-Dongwook/E2E-Self-Heal@v0.1.0\n", encoding="utf-8")

    sync(root, _NEW, [*files, "docs-site/docs/guides/new-guide.mdx"], write=True)

    assert new_doc.read_text(encoding="utf-8") == "- uses: Lee-Dongwook/E2E-Self-Heal@v9.8.7\n"


def test_test_files_keep_their_sample_pins(tree: tuple[Path, list[str]]) -> None:
    # Tests hold deliberately different versions; syncing them would erase what they check.
    root, files = tree
    fixture = root / "tests/test_example.py"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text('PIN = "Lee-Dongwook/E2E-Self-Heal@v0.1.0"\n', encoding="utf-8")

    report = sync(root, _NEW, [*files, "tests/test_example.py"], write=True)

    assert fixture.read_text(encoding="utf-8") == 'PIN = "Lee-Dongwook/E2E-Self-Heal@v0.1.0"\n'
    assert all(name != "tests/test_example.py" for name, *_ in report.changes)
