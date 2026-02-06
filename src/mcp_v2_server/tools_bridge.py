from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import ensure
from .path_guard import normalize_relative_path
from .state import AppState
from .tools_manual import manual_read
from .tools_vault import vault_create, vault_write


def _write_to_vault(state: AppState, to_path: str, content: str, mode: str) -> dict[str, Any]:
    normalized = normalize_relative_path(to_path)
    target = state.config.vault_root / normalized
    if target.exists():
        return vault_write(state, path=normalized, content=content, mode=mode)
    if mode == "append":
        # append target can be created if absent to keep operation ergonomic.
        return vault_create(state, path=normalized, content=content)
    return vault_create(state, path=normalized, content=content)


def bridge_copy_section(
    state: AppState,
    from_ref: dict[str, Any],
    to_path: str,
    mode: str,
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure(isinstance(from_ref, dict), "invalid_parameter", "from_ref must be an object")
    ensure(mode in {"overwrite", "append"}, "invalid_parameter", "mode must be overwrite or append")
    from_ref = dict(from_ref)
    suffix = Path(str(from_ref.get("path", ""))).suffix.casefold()
    scope = "file" if suffix == ".json" else "section"
    result = manual_read(state, ref=from_ref, scope=scope, limits=limits or {})
    write_result = _write_to_vault(state, to_path=to_path, content=result["text"], mode=mode)
    return {
        "written_path": write_result["written_path"],
        "written_bytes": write_result["written_bytes"],
        "written_sections": 1,
        "truncated": bool(result["truncated"]),
    }


def bridge_copy_file(
    state: AppState,
    from_path: str,
    manual_id: str,
    to_path: str,
    mode: str,
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure(mode in {"overwrite", "append"}, "invalid_parameter", "mode must be overwrite or append")
    ref = {
        "target": "manual",
        "manual_id": manual_id,
        "path": normalize_relative_path(from_path),
        "start_line": 1,
        "json_path": None,
    }
    scope = "file"
    result = manual_read(state, ref=ref, scope=scope, limits=limits or {})
    write_result = _write_to_vault(state, to_path=to_path, content=result["text"], mode=mode)
    return {
        "written_path": write_result["written_path"],
        "written_bytes": write_result["written_bytes"],
        "truncated": bool(result["truncated"]),
    }
