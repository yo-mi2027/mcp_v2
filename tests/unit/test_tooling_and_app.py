from __future__ import annotations

from mcp_v2_server.app import create_app


def test_app_create_smoke(state) -> None:
    app = create_app(state)
    assert app is not None
