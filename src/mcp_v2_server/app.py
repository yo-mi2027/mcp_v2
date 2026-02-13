from __future__ import annotations

import argparse
import time
from typing import Any, Callable

from .errors import ToolError
from .state import AppState, create_state
from .tools_manual import manual_find as manual_find_impl
from .tools_manual import manual_hits as manual_hits_impl
from .tools_manual import manual_ls as manual_ls_impl
from .tools_manual import manual_read as manual_read_impl
from .tools_manual import manual_scan as manual_scan_impl
from .tools_manual import manual_toc as manual_toc_impl
from .tools_vault import (
    vault_create as vault_create_impl,
    vault_read as vault_read_impl,
    vault_replace as vault_replace_impl,
    vault_scan as vault_scan_impl,
)

try:
    from fastmcp import FastMCP
except Exception as e:  # pragma: no cover - import guard for runtime setup
    raise RuntimeError(
        "fastmcp is required to run mcp_v2_server. Install dependencies with: pip install -r requirements.txt"
    ) from e


def _execute(state: AppState, tool: str, fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    started = time.monotonic()
    try:
        out = fn(*args, **kwargs)
        fields: dict[str, Any] = {}
        if isinstance(out, dict):
            if tool == "manual_find":
                fields["trace_id"] = out.get("trace_id")
                summary = out.get("summary") or {}
                for key in (
                    "scanned_files",
                    "scanned_nodes",
                    "candidates",
                    "file_bias_ratio",
                    "conflict_count",
                    "gap_count",
                    "integration_status",
                ):
                    if key in summary:
                        fields[key] = summary[key]
                fields["next_actions"] = out.get("next_actions")
            elif tool == "vault_read":
                fields["path"] = kwargs.get("path")
                fields["truncated"] = out.get("truncated")
            elif tool == "vault_scan":
                fields["path"] = kwargs.get("path")
                fields["applied_range"] = out.get("applied_range")
                fields["eof"] = out.get("eof")
                fields["truncated_reason"] = out.get("truncated_reason")
            elif tool == "manual_scan":
                fields["manual_id"] = kwargs.get("manual_id")
                fields["path"] = kwargs.get("path")
                fields["applied_range"] = out.get("applied_range")
                fields["eof"] = out.get("eof")
                fields["truncated_reason"] = out.get("truncated_reason")
            elif tool == "vault_create":
                fields["path"] = out.get("written_path")
                fields["written_bytes"] = out.get("written_bytes")
            elif tool == "vault_replace":
                fields["path"] = out.get("written_path")
                fields["replacements"] = out.get("replacements")
        state.logger.emit(tool=tool, ok=True, elapsed_ms=int((time.monotonic() - started) * 1000), **fields)
        return out
    except ToolError as e:
        state.logger.emit(tool=tool, ok=False, level="error", elapsed_ms=int((time.monotonic() - started) * 1000), code=e.code)
        return e.to_dict()
    except Exception as e:  # pragma: no cover - defensive guard
        state.logger.emit(tool=tool, ok=False, level="error", elapsed_ms=int((time.monotonic() - started) * 1000), code="conflict")
        return ToolError(code="conflict", message=str(e)).to_dict()


def create_app(state: AppState | None = None) -> FastMCP:
    app_state = state or create_state()
    mcp = FastMCP("mcp_v2_server")

    @mcp.tool()
    def manual_ls(id: str | None = None) -> dict[str, Any]:
        return _execute(app_state, "manual_ls", lambda: manual_ls_impl(app_state, id=id))

    @mcp.tool()
    def manual_toc(manual_id: str) -> dict[str, Any]:
        return _execute(app_state, "manual_toc", lambda: manual_toc_impl(app_state, manual_id=manual_id))

    @mcp.tool()
    def manual_find(
        query: str,
        manual_id: str | None = None,
        expand_scope: bool | None = None,
        only_unscanned_from_trace_id: str | None = None,
        budget: dict[str, Any] | None = None,
        include_claim_graph: bool | None = None,
        use_cache: bool | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "manual_find",
            lambda: manual_find_impl(
                app_state,
                query=query,
                manual_id=manual_id,
                expand_scope=expand_scope,
                only_unscanned_from_trace_id=only_unscanned_from_trace_id,
                budget=budget,
                include_claim_graph=include_claim_graph,
                use_cache=use_cache,
            ),
        )

    @mcp.tool()
    def manual_read(
        ref: dict[str, Any],
        scope: str | None = None,
        allow_file: bool | None = None,
        expand: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "manual_read",
            lambda: manual_read_impl(app_state, ref=ref, scope=scope, allow_file=allow_file, expand=expand),
        )

    @mcp.tool()
    def manual_scan(
        manual_id: str,
        path: str,
        start_line: int | None = None,
        cursor: dict[str, Any] | int | str | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "manual_scan",
            lambda: manual_scan_impl(
                app_state,
                manual_id=manual_id,
                path=path,
                start_line=start_line,
                cursor=cursor,
            ),
        )

    @mcp.tool()
    def manual_hits(trace_id: str, kind: str | None = None, offset: int | None = None, limit: int | None = None) -> dict[str, Any]:
        return _execute(
            app_state,
            "manual_hits",
            lambda: manual_hits_impl(app_state, trace_id=trace_id, kind=kind, offset=offset, limit=limit),
        )

    @mcp.tool()
    def vault_read(
        path: str,
        full: bool | None = None,
        range: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "vault_read",
            lambda: vault_read_impl(app_state, path=path, full=full, range=range),
        )

    @mcp.tool()
    def vault_scan(
        path: str,
        start_line: int | None = None,
        cursor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "vault_scan",
            lambda: vault_scan_impl(
                app_state,
                path=path,
                start_line=start_line,
                cursor=cursor,
            ),
        )

    @mcp.tool()
    def vault_create(path: str, content: str) -> dict[str, Any]:
        return _execute(app_state, "vault_create", lambda: vault_create_impl(app_state, path=path, content=content))

    @mcp.tool()
    def vault_replace(path: str, find: str, replace: str, max_replacements: int | None = None) -> dict[str, Any]:
        return _execute(
            app_state,
            "vault_replace",
            lambda: vault_replace_impl(
                app_state,
                path=path,
                find=find,
                replace=replace,
                max_replacements=max_replacements,
            ),
        )

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true", default=False)
    args = parser.parse_args()
    mcp = create_app()
    if args.stdio:
        try:
            mcp.run(transport="stdio")
        except TypeError:
            mcp.run()
    else:
        mcp.run()


if __name__ == "__main__":
    main()
