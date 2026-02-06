# mcp_v2

統合版MCPサーバ（v2）の設計・要件定義を置くフォルダです。

前提ディレクトリ構造（採用）:

- `manuals/<manual_id>/`
- `vault/`

## Docs

- `requirements.md`: 要件定義（目的、スコープ、機能/非機能、評価指標）
- `spec_v2.md`: 共通基盤仕様（Config、安全要件、共通ログ/出力、`next_actions`/`ref` モデル）
- `spec_manuals.md`: `manual_*` 仕様（探索Stage、I/Oスキーマ）
- `spec_vault.md`: `vault_*` / `artifact_audit` 仕様（行レンジ走査、カバレッジ監査、I/Oスキーマ）
- `spec_bridge.md`: `bridge_*` 仕様（manual->vaultコピー、I/Oスキーマ）
- `rag_design_v2.md`: RAG設計（探索ワークフロー、検索実装仕様、見落とし検知）
