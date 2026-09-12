# Phone Harness MCP Server

[English](README.md) | [日本語](README.ja.md)

このディレクトリは Phone Harness runtime をMCP経由で利用するためのサーバー実装です。Node/TypeScript側のMCP・HTTP/OAuth境界と、Python runtimeへのbridgeを提供します。

## 構成

- `src/server.ts` — MCP tool surface
- `src/http-server.ts` — HTTP transport
- `src/oauth.ts` — OAuth関連処理
- `src/runtime-bridge.ts` — Python runtime bridge
- `python_bridge.py` — Python側bridge
- `scripts/` — smoke・補助script

## セキュリティ境界

認証情報はソースコード、設定example、test fixtureへ実値で保存しません。Owner token、API key、OAuth credential、tunnel credential、端末識別情報は実行環境または外部Secret storeから渡してください。

公開exampleでは次の形式だけを使用します。

```text
<owner-token>
https://<public-host>/...
C:\path\to\...
```

実環境の内部URL、実PC固有path、端末ID、秘密鍵・証明書は公開Gitへ置きません。

## 開発

```powershell
npm install
npm test
```

実機smokeを行う場合は、使用する端末・署名material・network profileをローカル環境側で用意してください。生成物とローカルprofileはGit管理外です。

## 運用

MCP serverはPhone Harness runtimeの境界として扱い、認証・transport・tool contractを変更する場合はHTTP/MCP双方の回帰テストを確認してください。
