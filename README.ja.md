# Phone Harness

[English](README.md) | [日本語](README.ja.md)

Phone Harness は、エージェントからモバイル端末を観測・操作するための開発用ハーネスです。このforkにはWindows/iOS向けruntime、MCP連携、monitor、視覚認識処理、ドメイン固有workflowなどの拡張が含まれます。

## Public repository 方針

このリポジトリは公開ソースとして、再利用可能な実装・テスト・一般化された技術資料だけを保持します。

次の内容は公開Gitへコミットしません。

- API key、OAuth token、Owner passwordなどのSecret
- UDID、serial、ECID、電話番号、Apple資格情報
- 秘密鍵、証明書、署名用material
- 実PC固有パス・profile・runtime state
- 実環境のSecure MCP Tunnel設定や内部URL
- 実機セッションのhandoff、個別運用記録、ローカル復旧runbook

環境固有のhandoffは `docs/handoffs/` ではなく、Git管理外のローカル／private領域で管理してください。

## 開発

Python側は `pyproject.toml`、MCP serverは `mcp-server/`、desktop monitorは `desktop-monitor/` にあります。環境依存の生成物やWDA署名成果物は `.gitignore` で除外されています。

## MCP

MCP serverの公開仕様は `mcp-server/README.md` と `mcp-server/README.ja.md` を参照してください。認証情報はソースに保存せず、実行時に環境または外部Secret storeから渡します。

## Monitor

`desktop-monitor/` はローカルmonitor UIを提供します。LAN公開や外部接続を利用する場合は、認証・firewall・bind addressを環境ごとに確認してください。

## ドメイン固有処理

`docs/game-operations/` と `src/phone_harness/workflows/` には、Phone Harness上で動作するドメイン固有の学習・操作処理が含まれます。共通runtimeとドメイン固有ロジックの境界を維持してください。

## セキュリティ上の注意

公開branchはすべて外部から閲覧可能です。`main` だけでなくfeature branchにも、Secret・個人情報・ローカル運用情報を含めないでください。

このforkは開発中です。実機操作を伴う変更では、端末状態・認証境界・回帰リスクを確認してから利用してください。
