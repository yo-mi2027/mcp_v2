from __future__ import annotations

import argparse
import time
from typing import Any, Callable

from .errors import ToolError
from .state import AppState, create_state
from .tools_bridge import bridge_copy_file as bridge_copy_file_impl
from .tools_bridge import bridge_copy_section as bridge_copy_section_impl
from .tools_manual import manual_excepts as manual_excepts_impl
from .tools_manual import manual_find as manual_find_impl
from .tools_manual import manual_hits as manual_hits_impl
from .tools_manual import manual_list as manual_list_impl
from .tools_manual import manual_ls as manual_ls_impl
from .tools_manual import manual_read as manual_read_impl
from .tools_manual import manual_toc as manual_toc_impl
from .tools_tooling import get_tooling_guide as get_tooling_guide_impl
from .tools_vault import (
    artifact_audit as artifact_audit_impl,
    vault_coverage as vault_coverage_impl,
    vault_create as vault_create_impl,
    vault_find as vault_find_impl,
    vault_ls as vault_ls_impl,
    vault_read as vault_read_impl,
    vault_replace as vault_replace_impl,
    vault_scan as vault_scan_impl,
    vault_search as vault_search_impl,
    vault_write as vault_write_impl,
)

try:
    from fastmcp import FastMCP
except Exception:
    class FastMCP:  # type: ignore[override]
        def __init__(self, name: str) -> None:
            self.name = name
            self._tools: dict[str, Callable[..., Any]] = {}

        def tool(self, *args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                self._tools[func.__name__] = func
                return func

            return decorator

        def run(self, *args: Any, **kwargs: Any) -> None:
            return None


def _execute(state: AppState, tool: str, fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    started = time.monotonic()
    try:
        out = fn(*args, **kwargs)
        fields: dict[str, Any] = {}
        if isinstance(out, dict):
            if tool in {"manual_find", "vault_find"}:
                fields["trace_id"] = out.get("trace_id")
                summary = out.get("summary") or {}
                for key in (
                    "candidates",
                    "warnings",
                    "max_stage_applied",
                    "scope_expanded",
                    "cutoff_reason",
                    "escalation_reasons",
                    "unscanned_sections_count",
                    "integrated_candidates",
                    "file_bias_ratio",
                    "gap_ranges_count",
                    "sufficiency_score",
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
            elif tool == "vault_coverage":
                fields["path"] = out.get("path")
                fields["coverage_ratio"] = out.get("coverage_ratio")
                fields["covered_ranges"] = out.get("covered_ranges")
                fields["uncovered_ranges"] = out.get("uncovered_ranges")
            elif tool == "artifact_audit":
                for key in ("artifact_path", "source_path", "rootless_nodes", "orphan_branches", "one_way_refs"):
                    if key in out:
                        fields[key] = out[key]
            elif tool in {"vault_create", "vault_write"}:
                fields["path"] = out.get("written_path")
                fields["written_bytes"] = out.get("written_bytes")
                if tool == "vault_write":
                    fields["mode"] = kwargs.get("mode")
            elif tool == "vault_replace":
                fields["path"] = out.get("written_path")
                fields["replacements"] = out.get("replacements")
            elif tool in {"bridge_copy_section", "bridge_copy_file"}:
                fields["written_path"] = out.get("written_path")
                fields["mode"] = kwargs.get("mode")
                fields["written_bytes"] = out.get("written_bytes")
                fields["truncated"] = out.get("truncated")
                if tool == "bridge_copy_section":
                    fields["written_sections"] = out.get("written_sections")
            elif tool == "get_tooling_guide":
                fields["first_tool"] = out.get("first_tool")
                fields["intent"] = kwargs.get("intent")
                fields["target"] = kwargs.get("target")
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
    def manual_list() -> dict[str, Any]:
        return _execute(app_state, "manual_list", lambda: manual_list_impl(app_state))

    @mcp.tool()
    def manual_ls(manual_id: str | None = None) -> dict[str, Any]:
        return _execute(app_state, "manual_ls", lambda: manual_ls_impl(app_state, manual_id=manual_id))

    @mcp.tool()
    def manual_toc(manual_id: str) -> dict[str, Any]:
        return _execute(app_state, "manual_toc", lambda: manual_toc_impl(app_state, manual_id=manual_id))

    @mcp.tool()
    def manual_find(
        query: str,
        manual_id: str | None = None,
        intent: str | None = None,
        max_stage: int | None = None,
        only_unscanned_from_trace_id: str | None = None,
        budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "manual_find",
            lambda: manual_find_impl(
                app_state,
                query=query,
                manual_id=manual_id,
                intent=intent,
                max_stage=max_stage,
                only_unscanned_from_trace_id=only_unscanned_from_trace_id,
                budget=budget,
            ),
        )

    @mcp.tool()
    def manual_read(
        ref: dict[str, Any],
        scope: str | None = None,
        limits: dict[str, Any] | None = None,
        expand: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "manual_read",
            lambda: manual_read_impl(app_state, ref=ref, scope=scope, limits=limits, expand=expand),
        )

    @mcp.tool()
    def manual_excepts(manual_id: str, node_id: str | None = None) -> dict[str, Any]:
        return _execute(app_state, "manual_excepts", lambda: manual_excepts_impl(app_state, manual_id=manual_id, node_id=node_id))

    @mcp.tool()
    def manual_hits(trace_id: str, kind: str | None = None, offset: int | None = None, limit: int | None = None) -> dict[str, Any]:
        return _execute(
            app_state,
            "manual_hits",
            lambda: manual_hits_impl(app_state, trace_id=trace_id, kind=kind, offset=offset, limit=limit),
        )

    @mcp.tool()
    def vault_ls(relative_dir: str | None = None) -> dict[str, Any]:
        return _execute(app_state, "vault_ls", lambda: vault_ls_impl(app_state, relative_dir=relative_dir))

    @mcp.tool()
    def vault_read(
        path: str,
        full: bool | None = None,
        range: dict[str, Any] | None = None,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "vault_read",
            lambda: vault_read_impl(app_state, path=path, full=full, range=range, limits=limits),
        )

    @mcp.tool()
    def vault_find(query: str, scope: dict[str, Any] | None = None, budget: dict[str, Any] | None = None) -> dict[str, Any]:
        return _execute(app_state, "vault_find", lambda: vault_find_impl(app_state, query=query, scope=scope, budget=budget))

    @mcp.tool()
    def vault_scan(
        path: str,
        cursor: dict[str, Any] | None = None,
        chunk_lines: int | None = None,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "vault_scan",
            lambda: vault_scan_impl(app_state, path=path, cursor=cursor, chunk_lines=chunk_lines, limits=limits),
        )

    @mcp.tool()
    def vault_coverage(path: str, cited_ranges: list[dict[str, int]]) -> dict[str, Any]:
        return _execute(
            app_state,
            "vault_coverage",
            lambda: vault_coverage_impl(app_state, path=path, cited_ranges=cited_ranges),
        )

    @mcp.tool()
    def artifact_audit(
        artifact_path: str,
        source_path: str,
        cited_ranges: list[dict[str, int]] | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "artifact_audit",
            lambda: artifact_audit_impl(
                app_state,
                artifact_path=artifact_path,
                source_path=source_path,
                cited_ranges=cited_ranges,
            ),
        )

    @mcp.tool()
    def vault_create(path: str, content: str) -> dict[str, Any]:
        return _execute(app_state, "vault_create", lambda: vault_create_impl(app_state, path=path, content=content))

    @mcp.tool()
    def vault_write(path: str, content: str, mode: str) -> dict[str, Any]:
        return _execute(
            app_state,
            "vault_write",
            lambda: vault_write_impl(app_state, path=path, content=content, mode=mode),
        )

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

    @mcp.tool()
    def vault_search(
        query: str,
        mode: str | None = None,
        glob: str | None = None,
        relative_dir: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "vault_search",
            lambda: vault_search_impl(
                app_state,
                query=query,
                mode=mode,
                glob=glob,
                relative_dir=relative_dir,
                limit=limit,
            ),
        )

    @mcp.tool()
    def bridge_copy_section(
        from_ref: dict[str, Any],
        to_path: str,
        mode: str,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "bridge_copy_section",
            bridge_copy_section_impl,
            app_state,
            from_ref=from_ref,
            to_path=to_path,
            mode=mode,
            limits=limits,
        )

    @mcp.tool()
    def bridge_copy_file(
        from_path: str,
        manual_id: str,
        to_path: str,
        mode: str,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _execute(
            app_state,
            "bridge_copy_file",
            bridge_copy_file_impl,
            app_state,
            from_path=from_path,
            manual_id=manual_id,
            to_path=to_path,
            mode=mode,
            limits=limits,
        )

    @mcp.tool()
    def get_tooling_guide(intent: str | None = None, target: str | None = None) -> dict[str, Any]:
        return _execute(app_state, "get_tooling_guide", get_tooling_guide_impl, intent=intent, target=target)

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
