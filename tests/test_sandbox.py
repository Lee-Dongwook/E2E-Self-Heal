from pathlib import Path

import pytest

from app.config import settings
from app.sandbox import (
    SandboxViolation,
    assert_auto_discovered_target,
    assert_command_allowed,
    assert_read_allowed,
    assert_write_allowed,
    assert_patch_boundary_allowed,
)
from app.utils.files import atomic_write


def _strict(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    monkeypatch.setattr(settings, "workspace_root", str(root))
    monkeypatch.setattr(
        settings,
        "write_globs",
        "*.spec.js,*.spec.jsx,*.spec.ts,*.spec.tsx,"
        "*.test.js,*.test.jsx,*.test.ts,*.test.tsx,"
        "**/*.spec.js,**/*.spec.jsx,**/*.spec.ts,**/*.spec.tsx",
    )
    monkeypatch.setattr(
        settings,
        "deny_globs",
        ".env,.env.*,**/.env,**/.env.*,.git/**,.github/**,node_modules/**,.venv/**,uv.lock",
    )


def test_strict_allows_target_spec_write(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    target = root / "tests" / "login.spec.ts"
    target.parent.mkdir(parents=True)
    target.write_text("old")
    _strict(monkeypatch, root)

    assert_write_allowed(target, reason="repair_target")
    atomic_write(target, "new")

    assert target.read_text() == "new"


def test_strict_rejects_write_outside_workspace(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    outside = tmp_path / "outside.spec.ts"
    root.mkdir()
    outside.write_text("x")
    _strict(monkeypatch, root)

    with pytest.raises(SandboxViolation):
        assert_write_allowed(outside, reason="repair_target")


def test_strict_rejects_symlink_escape(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    outside = tmp_path / "outside.spec.ts"
    link = root / "tests" / "linked.spec.ts"
    link.parent.mkdir(parents=True)
    outside.write_text("x")
    import app.sandbox as sandbox_module

    original_resolve = sandbox_module._resolve

    def mock_resolve(path):
        if Path(path).resolve() == link.resolve():
            return outside.resolve()
        return original_resolve(path)

    monkeypatch.setattr(sandbox_module, "_resolve", mock_resolve)
    _strict(monkeypatch, root)

    with pytest.raises(SandboxViolation):
        assert_write_allowed(link, reason="repair_target")


def test_relaxed_still_rejects_secret_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "sandbox_mode", "relaxed")
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "write_globs", "*.spec.ts,**/*.spec.ts")
    monkeypatch.setattr(settings, "deny_globs", ".env,.env.*,**/.env,**/.env.*")

    with pytest.raises(SandboxViolation):
        assert_read_allowed(tmp_path / ".env")
    with pytest.raises(SandboxViolation):
        assert_write_allowed(tmp_path / ".env", reason="repair_target")


def test_relaxed_rejects_non_test_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "sandbox_mode", "relaxed")
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "write_globs", "*.spec.ts,**/*.spec.ts")

    with pytest.raises(SandboxViolation):
        assert_write_allowed(tmp_path / "src" / "app.ts", reason="repair_target")


def test_strict_allows_selector_verifier_temp_helper(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _strict(monkeypatch, root)
    monkeypatch.setattr(settings, "allow_temp_helper", True)

    assert_write_allowed(root / ".e2e-healer-verify.mjs", reason="selector_verifier_helper")


@pytest.mark.parametrize("suffix", [".mjs", ".cjs"])
def test_strict_allows_shadow_replay_temp_helper(monkeypatch, tmp_path, suffix):
    root = tmp_path / "repo"
    root.mkdir()
    _strict(monkeypatch, root)
    monkeypatch.setattr(settings, "allow_temp_helper", True)

    assert_write_allowed(
        root / f".e2e-healer-shadow-unique{suffix}",
        reason="shadow_replay_helper",
    )


def test_shadow_replay_helper_reason_rejects_unexpected_name(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _strict(monkeypatch, root)
    monkeypatch.setattr(settings, "write_globs", "**/*")

    with pytest.raises(SandboxViolation, match="unexpected Shadow helper"):
        assert_write_allowed(root / "arbitrary.mjs", reason="shadow_replay_helper")


def _relaxed(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(settings, "sandbox_mode", "relaxed")
    monkeypatch.setattr(settings, "workspace_root", str(root))
    monkeypatch.setattr(settings, "write_globs", "*.spec.ts,**/*.spec.ts")


def test_auto_discovered_allows_in_workspace_target(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    target = root / "tests" / "login.spec.ts"
    target.parent.mkdir(parents=True)
    target.write_text("x")
    _relaxed(monkeypatch, root)

    assert_auto_discovered_target(target)


def test_auto_discovered_rejects_absolute_external_in_relaxed(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "victim.spec.ts"
    outside.write_text("x")
    _relaxed(monkeypatch, root)

    with pytest.raises(SandboxViolation):
        assert_auto_discovered_target(outside)


def test_auto_discovered_rejects_relative_external_in_relaxed(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _relaxed(monkeypatch, root)

    # ../victim.spec.ts resolves above the workspace root -> rejected.
    with pytest.raises(SandboxViolation):
        assert_auto_discovered_target(root / ".." / "victim.spec.ts")


def test_auto_discovered_rejects_symlink_escape(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    outside = tmp_path / "victim.spec.ts"
    link = root / "tests" / "linked.spec.ts"
    link.parent.mkdir(parents=True)
    outside.write_text("x")
    import app.sandbox as sandbox_module

    original_resolve = sandbox_module._resolve

    def mock_resolve(path):
        if Path(path).resolve() == link.resolve():
            return outside.resolve()
        return original_resolve(path)

    monkeypatch.setattr(sandbox_module, "_resolve", mock_resolve)
    _relaxed(monkeypatch, root)

    with pytest.raises(SandboxViolation):
        assert_auto_discovered_target(link)


def test_auto_discovered_off_mode_bypasses(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "victim.spec.ts"
    outside.write_text("x")
    monkeypatch.setattr(settings, "sandbox_mode", "off")
    monkeypatch.setattr(settings, "workspace_root", str(root))

    assert_auto_discovered_target(outside)


def test_command_guard_rejects_shell_chaining(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "relaxed")

    assert_command_allowed(["npx", "playwright", "test"], reason="playwright")
    with pytest.raises(SandboxViolation):
        assert_command_allowed(["npx", "playwright", "test", "&&", "rm"], reason="playwright")


def test_architecture_boundary_allows_configured_layer(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "architecture_allow_globs", "tests/**")
    monkeypatch.setattr(settings, "architecture_deny_globs", "src/**")
    assert_patch_boundary_allowed(tmp_path / "tests" / "login.spec.ts")


def test_architecture_boundary_rejects_forbidden_layer(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "architecture_allow_globs", "**/*")
    monkeypatch.setattr(settings, "architecture_deny_globs", "src/**")
    with pytest.raises(SandboxViolation, match="architecture boundary"):
        assert_patch_boundary_allowed(tmp_path / "src" / "app.ts")
