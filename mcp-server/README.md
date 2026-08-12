# phone-harness MCP

Dedicated remote MCP server for `phone-harness`. It exposes the existing
`PhoneRuntime` through three tools and keeps one Python runtime process alive
for the lifetime of the MCP process.

## MCP contract

This server is intentionally **Modern-only** and uses MCP protocol
`2026-07-28`. Legacy `initialize` sessions are rejected. The tool surface is:

- `phone_status` — read-only runtime/transport health.
- `phone_observe` — read-only accessibility/OCR observation. Screenshots are
  returned only when `include_image=true` is explicitly requested.
- `phone_act` — one bounded write/action batch. Pass the `observation_id` from
  `phone_observe` for semantic or observation-derived actions.

The Node process owns one JSONL Python child. The child owns one `PhoneRuntime`,
so OCR, WDA readiness, transport metadata and observation caches remain warm.
There is no additional phone-harness daemon.

## Install

```powershell
cd .\mcp-server
npm ci --ignore-scripts
npm run build
```

Set runtime configuration in the same terminal before starting. Do not commit
the owner password.

```powershell
$env:PHONE_HARNESS_MCP_PUBLIC_BASE_URL = "https://YOUR-TAILSCALE-NAME:8443/"
$env:PHONE_HARNESS_MCP_OWNER_TOKEN = "A-LONG-RANDOM-LOCAL-SECRET"
$env:PHONE_HARNESS_PYTHON = "C:\path\to\phone-harness\.venv\Scripts\python.exe"
npm start
```

The server always binds `127.0.0.1`; the local port defaults to `7677` and can
be changed with `PHONE_HARNESS_MCP_PORT`.

OAuth state is stored under `%USERPROFILE%\.phone-harness-mcp` by default.
Only registered-client metadata and token hashes are persisted. The default
remote OAuth redirect allowlist is `chatgpt.com`; loopback redirects are also
accepted for local MCP clients.

## Tailscale Funnel

Workbridge can remain on its existing Funnel. Expose this MCP on a separate
Funnel HTTPS port, for example `8443`, while proxying to local port `7677`:

```powershell
tailscale funnel --bg --https=8443 http://127.0.0.1:7677
```

Then the MCP URL is:

```text
https://YOUR-TAILSCALE-NAME:8443/mcp
```

Check the existing configuration before changing it:

```powershell
tailscale funnel status
```

Do not expose the MCP before OAuth tests pass and the owner password is set to
a strong random value.

## Verification without an iPhone

```powershell
npm test
npm run typecheck
npm run build
npm audit --omit=dev --audit-level=moderate
```

These checks cover Modern discovery, legacy rejection, tool registration,
JSONL request multiplexing, OAuth redirect policy, persisted token hashing,
protected-resource metadata, Origin rejection and a complete DCR + PKCE +
Bearer-authenticated Modern discovery flow. Real phone actions remain a
separate hardware acceptance step.

With a trusted, unlocked iPhone connected and Calculator deliberately left in
the foreground, the two real-device smoke checks are:

```powershell
$env:PHONE_HARNESS_PYTHON = "C:\path\to\.venv\Scripts\python.exe"
npm run smoke:device
npm run smoke:device:http
```

Both scripts stop before mutation unless Calculator-specific accessibility keys
are present. The first verifies the Modern handler and long-lived Python bridge;
the second adds local HTTP, dynamic client registration, PKCE and Bearer auth.
Neither requests a screenshot or logs general screen contents.
