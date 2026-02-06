from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .errors import ToolError, ensure

DAILY_FILE_RE = re.compile(r"^daily/\d{4}-\d{2}-\d{2}\.md$", re.IGNORECASE)


def normalize_relative_path(path: str) -> str:
    ensure(bool(path and path.strip()), "invalid_path", "path is required")
    canonical = path.replace("\\", "/").strip()
    ensure(not canonical.startswith("/"), "invalid_path", "absolute path is not allowed")

    parts = []
    for part in PurePosixPath(canonical).parts:
        if part in {"", "."}:
            continue
        ensure(part != "..", "invalid_path", "parent traversal is not allowed")
        parts.append(part)
    ensure(bool(parts), "invalid_path", "path is empty after normalization")
    return "/".join(parts)


def _is_subpath_casefold(path: Path, root: Path) -> bool:
    p = str(path).casefold()
    r = str(root).casefold()
    if p == r:
        return True
    return p.startswith(r + "/")


def _reject_symlink_parts(root: Path, relative: str) -> None:
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ToolError("forbidden", "symlink access is not allowed", {"path": str(current)})


def resolve_inside_root(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    normalized = normalize_relative_path(relative)
    root_real = root.resolve()
    candidate = root / normalized
    _reject_symlink_parts(root, normalized)

    if must_exist:
        if not candidate.exists():
            raise ToolError("not_found", "target not found", {"path": normalized})
        resolved = candidate.resolve()
    else:
        # Keep non-existing leaf untouched while still resolving parent boundaries.
        parent_resolved = candidate.parent.resolve()
        resolved = parent_resolved / candidate.name

    if not _is_subpath_casefold(resolved, root_real):
        raise ToolError("out_of_scope", "path is out of scope", {"path": normalized})
    return resolved


def is_system_path(relative: str) -> bool:
    normalized = normalize_relative_path(relative)
    lower = normalized.casefold()
    return lower == ".system" or lower.startswith(".system/")


def is_daily_path(relative: str) -> bool:
    normalized = normalize_relative_path(relative)
    return normalized.casefold().startswith("daily/")


def is_daily_path_under_root(vault_root: Path, relative: str) -> bool:
    normalized = normalize_relative_path(relative)
    daily_root = (vault_root / "daily").resolve()
    target = resolve_inside_root(vault_root, normalized, must_exist=False)
    p = str(target).casefold()
    d = str(daily_root).casefold()
    return p == d or p.startswith(d + "/")


def is_system_path_under_root(vault_root: Path, relative: str) -> bool:
    normalized = normalize_relative_path(relative)
    system_root = (vault_root / ".system").resolve()
    target = resolve_inside_root(vault_root, normalized, must_exist=False)
    p = str(target).casefold()
    s = str(system_root).casefold()
    return p == s or p.startswith(s + "/")


def validate_daily_filename(relative: str) -> None:
    normalized = normalize_relative_path(relative)
    ensure(
        bool(DAILY_FILE_RE.match(normalized)),
        "forbidden",
        "daily path must be daily/YYYY-MM-DD.md",
        {"path": normalized},
    )
