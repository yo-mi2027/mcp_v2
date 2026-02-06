from __future__ import annotations

import json

import pytest

from mcp_v2_server.app import _execute
from mcp_v2_server.app import create_app
from mcp_v2_server.errors import ToolError
from mcp_v2_server.tools_tooling import get_tooling_guide


def test_tooling_first_tool_rules() -> None:
    assert get_tooling_guide(intent="explore", target="vault")["first_tool"] == "vault_find"
    assert get_tooling_guide(intent="explore", target="manual")["first_tool"] == "manual_find"
    assert get_tooling_guide(intent="produce", target="manual")["first_tool"] == "vault_create"
    assert get_tooling_guide(intent="revise", target="vault")["first_tool"] == "vault_search"
    assert get_tooling_guide(intent="audit", target="manual")["first_tool"] == "vault_coverage"
    assert get_tooling_guide(intent="unknown", target="vault")["first_tool"] == "manual_find"
    assert get_tooling_guide(intent=None, target=None)["first_tool"] == "manual_find"


def test_app_create_smoke(state) -> None:
    app = create_app(state)
    assert app is not None


def test_tooling_invalid_intent_and_target() -> None:
    with pytest.raises(ToolError) as e:
        get_tooling_guide(intent="bad", target=None)
    assert e.value.code == "invalid_parameter"
    with pytest.raises(ToolError) as e:
        get_tooling_guide(intent=None, target="bad")
    assert e.value.code == "invalid_parameter"


def test_tooling_catalog_shape_constraints() -> None:
    out = get_tooling_guide(intent="explore", target="vault")
    assert isinstance(out["tools"], list)
    for tool in out["tools"]:
        assert isinstance(tool["required_inputs"], list)
        assert isinstance(tool["safe_defaults"], dict)
        assert len(tool["common_errors"]) <= 2


def test_execute_logs_tooling_extension_fields(state, capsys) -> None:
    out = _execute(
        state,
        "get_tooling_guide",
        lambda *, intent, target: {"first_tool": "vault_find", "tools": []},
        intent="explore",
        target="vault",
    )
    assert out["first_tool"] == "vault_find"
    line = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["tool"] == "get_tooling_guide"
    assert payload["first_tool"] == "vault_find"
    assert payload["intent"] == "explore"
    assert payload["target"] == "vault"
