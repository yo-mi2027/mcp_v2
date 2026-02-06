from __future__ import annotations

from typing import Any

from .errors import ToolError

INTENTS = {"explore", "produce", "revise", "audit", "unknown", None}
TARGETS = {"manual", "vault", "unknown", None}


def _first_tool(intent: str | None, target: str | None) -> str:
    if intent == "explore":
        return "vault_find" if target == "vault" else "manual_find"
    if intent == "produce":
        return "vault_create"
    if intent == "revise":
        return "vault_search"
    if intent == "audit":
        return "vault_coverage"
    return "manual_find"


def get_tooling_guide(intent: str | None = None, target: str | None = None) -> dict[str, Any]:
    if intent not in INTENTS:
        raise ToolError("invalid_parameter", "invalid intent")
    if target not in TARGETS:
        raise ToolError("invalid_parameter", "invalid target")
    tools = [
        {
            "tool_name": "manual_list",
            "when_to_use": "manual ID 一覧の確認",
            "required_inputs": [],
            "safe_defaults": {},
            "common_errors": [{"code": "not_found", "fix": "manuals ルートを確認する"}],
        },
        {
            "tool_name": "manual_ls",
            "when_to_use": "manual配下の対象ファイルを列挙",
            "required_inputs": [],
            "safe_defaults": {"manual_id": None},
            "common_errors": [{"code": "not_found", "fix": "manual_id を確認する"}],
        },
        {
            "tool_name": "manual_toc",
            "when_to_use": "見出し/JSONノードの構造取得",
            "required_inputs": ["manual_id"],
            "safe_defaults": {},
            "common_errors": [{"code": "not_found", "fix": "manual_id を確認する"}],
        },
        {
            "tool_name": "manual_find",
            "when_to_use": "manual群の探索を開始するとき",
            "required_inputs": ["query"],
            "safe_defaults": {"max_stage": 4, "budget": {"time_ms": 60000, "max_candidates": 200}},
            "common_errors": [
                {"code": "invalid_parameter", "fix": "queryを空にしない"},
                {"code": "not_found", "fix": "manual_idまたはtrace_idを確認する"},
            ],
        },
        {
            "tool_name": "manual_hits",
            "when_to_use": "manual_find の候補詳細をページング取得",
            "required_inputs": ["trace_id"],
            "safe_defaults": {"kind": "candidates", "offset": 0, "limit": 50},
            "common_errors": [{"code": "not_found", "fix": "trace_id の期限切れを確認する"}],
        },
        {
            "tool_name": "manual_read",
            "when_to_use": "候補本文を段階的に取得",
            "required_inputs": ["ref"],
            "safe_defaults": {"scope": None, "limits": {"max_chars": 8000}},
            "common_errors": [
                {"code": "invalid_scope", "fix": "jsonにはfile scopeのみ使う"},
                {"code": "forbidden", "fix": "md file scopeはALLOW_FILE_SCOPEとallow_fileを有効化する"},
            ],
        },
        {
            "tool_name": "manual_excepts",
            "when_to_use": "例外語彙を抽出",
            "required_inputs": ["manual_id"],
            "safe_defaults": {"node_id": None},
            "common_errors": [{"code": "not_found", "fix": "manual_id を確認する"}],
        },
        {
            "tool_name": "vault_find",
            "when_to_use": "vault成果物を探索するとき",
            "required_inputs": ["query"],
            "safe_defaults": {"budget": {"time_ms": 60000, "max_candidates": 200}},
            "common_errors": [
                {"code": "invalid_parameter", "fix": "queryを指定する"},
                {"code": "invalid_path", "fix": "scope.relative_dirの形式を確認する"},
            ],
        },
        {
            "tool_name": "vault_scan",
            "when_to_use": "行レンジ単位の逐次走査",
            "required_inputs": ["path"],
            "safe_defaults": {"cursor": {"start_line": 1}, "chunk_lines": 80},
            "common_errors": [{"code": "invalid_parameter", "fix": "chunk_lines/cursorを許容範囲にする"}],
        },
        {
            "tool_name": "vault_coverage",
            "when_to_use": "根拠行のカバレッジ監査",
            "required_inputs": ["path", "cited_ranges"],
            "safe_defaults": {"cited_ranges": []},
            "common_errors": [
                {"code": "not_found", "fix": "pathを確認する"},
                {"code": "invalid_parameter", "fix": "cited_rangesの範囲形式を確認する"},
            ],
        },
        {
            "tool_name": "vault_audit",
            "when_to_use": "成果物と根拠の整合監査",
            "required_inputs": ["report_path", "source_path"],
            "safe_defaults": {"cited_ranges": None},
            "common_errors": [{"code": "not_found", "fix": "report/source pathを確認する"}],
        },
        {
            "tool_name": "vault_create",
            "when_to_use": "成果物の新規作成",
            "required_inputs": ["path", "content"],
            "safe_defaults": {"path": "project-a/result.md"},
            "common_errors": [
                {"code": "conflict", "fix": "既存ファイルならvault_writeを使う"},
                {"code": "forbidden", "fix": ".system配下は予約領域のため使用しない"},
            ],
        },
        {
            "tool_name": "vault_write",
            "when_to_use": "既存ファイルへの上書き/追記",
            "required_inputs": ["path", "content", "mode"],
            "safe_defaults": {"mode": "append"},
            "common_errors": [
                {"code": "conflict", "fix": "対象ファイルを先に作成する"},
                {"code": "forbidden", "fix": "daily配下はappendのみ、.system配下は書き込み不可"},
            ],
        },
        {
            "tool_name": "vault_replace",
            "when_to_use": "既存テキストの置換更新",
            "required_inputs": ["path", "find", "replace"],
            "safe_defaults": {"max_replacements": 1},
            "common_errors": [{"code": "forbidden", "fix": "daily/.system配下ではreplaceを使わない"}],
        },
        {
            "tool_name": "vault_search",
            "when_to_use": "既存成果物の修正対象を探す",
            "required_inputs": ["query"],
            "safe_defaults": {"mode": "regex", "limit": 50},
            "common_errors": [
                {"code": "invalid_parameter", "fix": "query/limitを見直す"},
                {"code": "invalid_path", "fix": "relative_dirを相対パスで指定する"},
            ],
        },
        {
            "tool_name": "vault_ls",
            "when_to_use": "vaultディレクトリ一覧取得",
            "required_inputs": [],
            "safe_defaults": {"relative_dir": None},
            "common_errors": [{"code": "invalid_path", "fix": "relative_dir を相対パスで指定する"}],
        },
        {
            "tool_name": "vault_read",
            "when_to_use": "vaultファイルの範囲/全文読取",
            "required_inputs": ["path"],
            "safe_defaults": {"full": False, "range": {"start_line": 1, "end_line": 80}},
            "common_errors": [{"code": "invalid_parameter", "fix": "full=falseのときrangeを指定する"}],
        },
        {
            "tool_name": "bridge_copy_section",
            "when_to_use": "manualのsection/snippetをvaultへ転記",
            "required_inputs": ["from_ref", "to_path", "mode"],
            "safe_defaults": {"mode": "append"},
            "common_errors": [{"code": "forbidden", "fix": "md file copy時はallow_file条件を満たす"}],
        },
        {
            "tool_name": "bridge_copy_file",
            "when_to_use": "manualファイル単位でvaultへ転記",
            "required_inputs": ["from_path", "manual_id", "to_path", "mode"],
            "safe_defaults": {"mode": "append"},
            "common_errors": [{"code": "forbidden", "fix": "md全文転記の許可条件を満たす"}],
        },
        {
            "tool_name": "get_tooling_guide",
            "when_to_use": "初手ツール選択のガイド取得",
            "required_inputs": [],
            "safe_defaults": {"intent": None, "target": None},
            "common_errors": [{"code": "invalid_parameter", "fix": "intent/targetを許容語彙で指定する"}],
        },
    ]
    return {"first_tool": _first_tool(intent, target), "tools": tools}
