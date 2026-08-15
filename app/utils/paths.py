"""Secure file-path utilities for project files.

These functions enforce that all file operations stay within approved project
directories and reject path-traversal attempts. They are used by guardrails and
services before any file is read, written, or created.

Security model:
- Every project file must resolve to a path *inside* one of the approved
  root directories (data, assets, output, logs).
- Paths containing ``..`` segments, backslash traversal, NUL bytes, or that
  resolve outside the approved roots are rejected.
- Extensions are validated against allow-lists so an attacker can't smuggle
  an executable disguised as an asset.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from app.config import PROJECT_ROOT, Settings, get_settings
from app.core.exceptions import FileSafetyError
from app.core.logging import get_logger

logger = get_logger("file_safety")

# Characters that are never allowed in a path (NUL, control chars).
_UNSAFE_CHARS = re.compile(r"[\x00-\x1f]")

# Suspicious traversal patterns (checked before resolution for defense-in-depth).
_TRAVERSAL_PATTERNS = re.compile(r"(\.\.[/\\]|[/\\]\.\.|\x00)")


def _approved_roots(settings: Settings | None = None) -> list[Path]:
    """Return the list of approved root directories (resolved, absolute)."""
    settings = settings or get_settings()
    return [
        settings.resolved_path(settings.data_dir),
        settings.resolved_path(settings.assets_dir),
        settings.resolved_path(settings.output_dir),
        settings.resolved_path(settings.logs_dir),
    ]


def is_absolute_path(path: str) -> bool:
    """Return True if ``path`` is an absolute system path."""
    return os.path.isabs(path) or Path(path).is_absolute()


def normalize_path(path: str) -> str:
    """Normalize a path string, stripping redundant separators and ``.``.

    Does NOT resolve symlinks or allow escape — use :func:`is_path_safe` for
    security checks. Raises FileSafetyError if the path is empty or contains
    control characters.
    """
    if not path or not str(path).strip():
        raise FileSafetyError("path must not be empty")
    if _UNSAFE_CHARS.search(path):
        raise FileSafetyError(f"path contains unsafe control characters: {path!r}")
    return os.path.normpath(path)


def contains_traversal(path: str) -> bool:
    """Return True if ``path`` contains path-traversal patterns (``..``, backslashes)."""
    if not path:
        return False
    # Check for explicit ``..`` segments (both / and \ separators).
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    if ".." in parts:
        return True
    if _TRAVERSAL_PATTERNS.search(path):
        return True
    return False


def is_path_safe(path: str, *, allow_absolute: bool = False) -> bool:
    """Return True if ``path`` is safe (no traversal, no control chars).

    When ``allow_absolute`` is False (default), absolute system paths are
    rejected. This is the fast, non-resolving check; use
    :func:`restrict_to_directory` for the authoritative containment check.
    """
    if not path or not str(path).strip():
        return False
    if _UNSAFE_CHARS.search(path):
        return False
    if contains_traversal(path):
        return False
    if not allow_absolute and is_absolute_path(path):
        return False
    return True


def validate_path_safety(path: str, *, allow_absolute: bool = False) -> str:
    """Validate that ``path`` is safe, raising FileSafetyError if not."""
    if not path or not str(path).strip():
        raise FileSafetyError("path must not be empty")
    if _UNSAFE_CHARS.search(path):
        raise FileSafetyError(f"path contains unsafe control characters: {path!r}")
    if contains_traversal(path):
        raise FileSafetyError(f"path traversal detected: {path!r}")
    if not allow_absolute and is_absolute_path(path):
        raise FileSafetyError(
            f"absolute system paths are not allowed: {path!r}; "
            "use a path relative to an approved project directory"
        )
    return path


def restrict_to_directory(
    path: str,
    base_dir: str | Path,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve ``path`` against ``base_dir`` and verify it stays inside.

    This is the authoritative containment check: it resolves the real path and
    confirms the result is within ``base_dir``. Raises FileSafetyError if the
    path escapes the base directory.

    Args:
        path: A relative or absolute path. If relative, it is joined to
            ``base_dir``.
        base_dir: The approved root directory.
        must_exist: If True, the resolved path must exist on disk.
    """
    base = Path(base_dir).resolve()
    validate_path_safety(path, allow_absolute=True)

    candidate = (base / path).resolve() if not is_absolute_path(path) else Path(path).resolve()

    try:
        candidate.relative_to(base)
    except ValueError:
        raise FileSafetyError(
            f"path '{path}' resolves outside approved directory '{base}'",
            details={"resolved": str(candidate), "base_dir": str(base)},
        ) from None

    if must_exist and not candidate.exists():
        raise FileSafetyError(f"file does not exist: {candidate}")

    return candidate


def resolve_project_path(
    path: str,
    settings: Settings | None = None,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve ``path`` against any approved project root.

    Tries each approved root directory in turn; the first that contains the
    resolved path wins. Raises FileSafetyError if the path escapes all
    approved roots.
    """
    settings = settings or get_settings()
    validate_path_safety(path, allow_absolute=True)

    if is_absolute_path(path):
        resolved = Path(path).resolve()
        for root in _approved_roots(settings):
            try:
                resolved.relative_to(root)
                if must_exist and not resolved.exists():
                    raise FileSafetyError(f"file does not exist: {resolved}")
                return resolved
            except ValueError:
                continue
        raise FileSafetyError(
            f"absolute path '{path}' is not inside any approved project directory",
            details={"resolved": str(resolved)},
        )

    # Relative path: try resolving under each approved root.
    for root in _approved_roots(settings):
        candidate = (root / path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if not must_exist or candidate.exists():
            return candidate

    raise FileSafetyError(
        f"path '{path}' does not resolve inside any approved project directory",
        details={"tried_roots": [str(r) for r in _approved_roots(settings)]},
    )


def validate_extension(path: str, allowed: frozenset[str] | set[str]) -> str:
    """Validate that ``path`` has an extension in the ``allowed`` set."""
    ext = Path(path).suffix.lower().lstrip(".")
    if not ext:
        raise FileSafetyError(f"file has no extension: {path!r}")
    if ext not in allowed:
        raise FileSafetyError(
            f"unsupported file extension '.{ext}' for path {path!r}; "
            f"allowed: {sorted(allowed)}",
            details={"extension": ext, "allowed": sorted(allowed)},
        )
    return path


def safe_mkdir(path: str | Path, *, parent: str | Path | None = None) -> Path:
    """Safely create a directory, refusing to overwrite existing files.

    If ``parent`` is given, the path is resolved relative to it and must stay
    inside it. If the path already exists as a file (not a directory), raises
    FileSafetyError to prevent clobbering arbitrary files.
    """
    if parent is not None:
        resolved = restrict_to_directory(str(path), parent)
    else:
        validate_path_safety(str(path), allow_absolute=True)
        resolved = Path(path).resolve()

    if resolved.exists() and not resolved.is_dir():
        raise FileSafetyError(
            f"cannot create directory: path exists and is a file: {resolved}",
            details={"path": str(resolved)},
        )
    resolved.mkdir(parents=True, exist_ok=True)
    logger.debug("created directory: %s", resolved)
    return resolved


def is_within_directory(path: str | Path, base_dir: str | Path) -> bool:
    """Return True if ``path`` resolves inside ``base_dir``."""
    try:
        resolved = Path(path).resolve() if is_absolute_path(str(path)) else (Path(base_dir) / path).resolve()
        resolved.relative_to(Path(base_dir).resolve())
        return True
    except (ValueError, OSError):
        return False


__all__ = [
    "contains_traversal",
    "is_absolute_path",
    "is_path_safe",
    "is_within_directory",
    "normalize_path",
    "resolve_project_path",
    "restrict_to_directory",
    "safe_mkdir",
    "validate_extension",
    "validate_path_safety",
]
