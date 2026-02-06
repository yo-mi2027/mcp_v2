from __future__ import annotations

from pathlib import Path

import pytest

from mcp_v2_server.config import Config
from mcp_v2_server.state import AppState, create_state


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppState:
    workspace = tmp_path / "ws"
    manuals = workspace / "manuals"
    vault = workspace / "vault"
    manuals.mkdir(parents=True, exist_ok=True)
    vault.mkdir(parents=True, exist_ok=True)

    _write(
        manuals / "m1" / "rules.md",
        "# 総則\n対象です。\n## 例外\nこの場合は対象外です。\n### 参照\n別表を参照。\n",
    )
    _write(manuals / "m1" / "policy.json", '{"title":"支払い","except":"不適用あり"}')
    _write(manuals / "m2" / "appendix.md", "# 手順\nフローを実施する。\n")

    _write(vault / "source.md", "line1\nline2\nline3\nline4\nline5\n")
    _write(vault / "notes.md", "対象外 条件\n")
    _write(vault / "artifact.md", "node A\nsource_lines: 1-2\n根拠なし要素\n")

    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("MANUALS_ROOT", str(manuals))
    monkeypatch.setenv("VAULT_ROOT", str(vault))
    monkeypatch.setenv("TRACE_TTL_SEC", "1")
    monkeypatch.setenv("TRACE_MAX_KEEP", "5")
    monkeypatch.setenv("ALLOW_FILE_SCOPE", "false")

    cfg = Config.from_env()
    return create_state(cfg)
