from __future__ import annotations

import json

import mcp_v2_server.app as app_module
from mcp_v2_server.app import create_app
from mcp_v2_server.errors import ToolError
from mcp_v2_server.logging_jsonl import JsonlLogger


def test_app_create_smoke(state) -> None:
    app = create_app(state)
    assert app is not None


def test_execute_logs_success_and_marks_discovery(state, monkeypatch) -> None:
    rows: list[dict[str, object]] = []

    def fake_emit(**kwargs):  # type: ignore[no-untyped-def]
        rows.append(dict(kwargs))

    monkeypatch.setattr(state.logger, "emit", fake_emit)
    out = app_module._execute(state, "manual_ls", lambda: {"items": []})

    assert out == {"items": []}
    assert state.manual_ls_seen is True
    assert rows == []


def test_execute_logs_tool_error(state, monkeypatch) -> None:
    rows: list[dict[str, object]] = []

    def fake_emit(**kwargs):  # type: ignore[no-untyped-def]
        rows.append(dict(kwargs))

    monkeypatch.setattr(state.logger, "emit", fake_emit)
    out = app_module._execute(
        state,
        "manual_ls",
        lambda: (_ for _ in ()).throw(ToolError("invalid_parameter", "bad")),
    )

    assert out["code"] == "invalid_parameter"
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert rows[0]["level"] == "error"
    assert rows[0]["code"] == "invalid_parameter"


def test_jsonl_logger_emits_error_only(capsys) -> None:
    logger = JsonlLogger()
    logger.emit(tool="manual_ls", ok=True, elapsed_ms=1, level="info")
    logger.emit(tool="manual_ls", ok=False, elapsed_ms=2, level="error", code="invalid_parameter")
    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]

    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["level"] == "error"
    assert payload["code"] == "invalid_parameter"
