"""Sandbox policy for filesystem and subprocess access."""

from fnmatch import fnmatchcase
from pathlib import Path

from app.config import settings

_MODES = {"strict", "relaxed", "off"}
_SHELL_TOKENS = {"&&", "||", ";", "|", ">", ">>", "<", "$(", "`"}
_SHELL_TOKEN_FRAGMENTS = ("$(", "`")
_TEMP_HELPER = ".e2e-healer-verify.mjs"


class SandboxViolation(PermissionError):
    """Raised when a path or command falls outside the configured sandbox."""


def sandbox_mode() -> str:
    """Return the normalized sandbox mode, failing closed on invalid config."""
    mode = settings.sandbox_mode.strip().lower()
    if mode not in _MODES:
        raise SandboxViolation(f"invalid sandbox mode: {settings.sandbox_mode}")
    return mode


def workspace_root() -> Path:
    """Return the resolved workspace root for strict sandbox checks."""
    return Path(settings.workspace_root).expanduser().resolve()


def assert_read_allowed(path: Path) -> None:
    """Allow reads unless strict root checks or deny globs reject the path."""
    if sandbox_mode() == "off":
        return

    resolved = _resolve(path)
    _assert_not_denied(resolved)
    if sandbox_mode() == "strict":
        _assert_inside_workspace(resolved)


def assert_write_allowed(path: Path, reason: str = "write") -> None:
    """Allow writes only inside the configured policy."""
    if sandbox_mode() == "off":
        return

    resolved = _resolve(path)
    _assert_not_denied(resolved)

    mode = sandbox_mode()
    if mode == "strict":
        _assert_inside_workspace(resolved)
    if _is_allowed_temp_helper(resolved):
        return
    if not _matches_any(_write_match_value(resolved), _patterns(settings.write_globs)):
        raise SandboxViolation(f"write denied by sandbox globs: {path}")
    if reason == "selector_verifier_helper" and not _is_allowed_temp_helper(resolved):
        raise SandboxViolation(f"unexpected helper write target: {path}")


def assert_patch_boundary_allowed(path: Path) -> None:
    """Reject generated patches outside the configured architecture boundary."""
    resolved = _resolve(path)
    _assert_not_denied(resolved)
    value = _relative_or_name(resolved)
    if _matches_any(value, _patterns(settings.architecture_deny_globs)):
        raise SandboxViolation(f"patch denied by architecture boundary: {path}")
    if not _matches_any(value, _patterns(settings.architecture_allow_globs)):
        raise SandboxViolation(f"patch not allowed by architecture boundary: {path}")


def assert_command_allowed(argv: list[str], reason: str = "subprocess") -> None:
    """Reject command arguments that look like shell control syntax."""
    if sandbox_mode() == "off":
        return
    if not argv:
        raise SandboxViolation(f"empty command denied: {reason}")
    for arg in argv:
        if (
            arg in _SHELL_TOKENS
            or arg.endswith(";")
            or any(token in arg for token in _SHELL_TOKEN_FRAGMENTS)
        ):
            raise SandboxViolation(f"shell-like command token denied: {arg}")


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _assert_inside_workspace(path: Path) -> None:
    root = workspace_root()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SandboxViolation(f"path outside workspace: {path}") from exc


def _relative_to_workspace(path: Path) -> str:
    return path.relative_to(workspace_root()).as_posix()


def _write_match_value(path: Path) -> str:
    try:
        return _relative_to_workspace(path)
    except ValueError:
        return path.name


def _assert_not_denied(path: Path) -> None:
    rel = _relative_or_name(path)
    if _matches_any(rel, _patterns(settings.deny_globs)):
        raise SandboxViolation(f"path denied by sandbox: {path}")
    denied_dirs = {".git", ".github", ".venv", "node_modules"}
    if denied_dirs.intersection(path.parts):
        raise SandboxViolation(f"path denied by sandbox: {path}")


def _relative_or_name(path: Path) -> str:
    try:
        return path.relative_to(workspace_root()).as_posix()
    except ValueError:
        return path.name


def _patterns(raw: str) -> list[str]:
    return [pattern.strip() for pattern in raw.split(",") if pattern.strip()]


def _matches_any(value: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatchcase(value, pattern):
            return True
        if pattern.startswith("**/") and fnmatchcase(value, pattern[3:]):
            return True
    return False


def _is_allowed_temp_helper(path: Path) -> bool:
    return settings.allow_temp_helper and path.name == _TEMP_HELPER
