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

`phone_act` can also run one named bounded workflow without adding a fourth MCP
tool. The legacy conservative workflow remains available:

```json
{
  "op": "run_workflow",
  "name": "merge_boss_once",
  "max_producer_taps": 4,
  "max_relaxed_checks": 3
}
```

The order-aware multi-action contract is already exposed as:

```json
{
  "op": "run_workflow",
  "name": "merge_boss_turn",
  "max_cycles": 10,
  "max_merges": 24,
  "max_emissions": 20,
  "uncertain_burst_size": 6,
  "max_recoveries": 3
}
```

`run_workflow` must be the only action in that `phone_act` request. It is
forwarded to the already-running Python bridge, so the workflow reuses the same
`PhoneRuntime`, WDA state, FrameBroker and OCR model instead of starting a new
Python process or returning to ChatGPT between each small step.

`merge_boss_turn` is designed to batch independent merge drags, predict higher-
level chain merges, produce several items inside one local turn, and deliver
currently complete customer orders. Its live perception layer is deliberately
disabled until the next real-device session calibrates the full horizontal
customer strip and each producer's upper-left `i` information view. Until then,
calling it returns a structured calibration-required result and performs no
phone action.

`phone_observe` also accepts an optional absolute-pixel `region` object
`{x,y,w,h}`. Region observation limits accessibility/OCR work and optional image
content to that rectangle while returned element coordinates remain full-screen
coordinates for follow-up actions. Changing region creates a fresh observation
cache scope. After changing this tool schema, rebuild/restart the local MCP and
rescan/reconnect the ChatGPT App so the host refreshes its cached tool schema.

The Node process owns one JSONL Python child. The child owns one `PhoneRuntime`,
so OCR, WDA readiness, transport metadata and observation caches remain warm.
Before each runtime call the Node bridge checks `python_bridge.py` and
`src/phone_harness/**/*.py`; when those Python sources change, it rotates the
Python child only after all in-flight calls finish. OAuth, the MCP listener and
the Secure MCP Tunnel stay up while the Python runtime reloads. Rotation first
closes the Python bridge input so the runtime can stop any WDA runner it owns;
forced process termination is only a bounded fallback. There is no additional
phone-harness daemon.

Additional Python source roots can be included in the revision check with
`PHONE_HARNESS_MCP_RUNTIME_WATCH_PATHS`, using the platform path delimiter
(`;` on Windows, `:` on macOS/Linux). This is normally unnecessary because
`pymobiledevice3` is currently invoked as a fresh subprocess for device calls.

`phone_observe` also supports a visual-only path for image/layout/icon work:

```text
mode=visual
image_profile=glance|coarse|balanced|detail|full
region={x,y,w,h}  # optional absolute phone-screen rectangle
reuse_observation_id=<current visual observation id>  # optional refinement
```

Visual mode skips accessibility/OCR and returns image content. Coarse profiles
are JPEG derivatives from one shared physical frame; `full` is lossless PNG.
The default remains semantic observation for backward compatibility.
`reuse_observation_id` lets a later call ask for a clearer derivative of that
same captured frame rather than taking another screenshot; retained-frame reuse
is bounded to 30 seconds.

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

## Secure MCP Tunnel

For ChatGPT Secure MCP Tunnel, keep the MCP transport on loopback and publish
only the OAuth authorization server on HTTPS. The Tunnel resource URL is an
exact allowlisted OAuth audience; do not use a broad hostname allowlist.

```powershell
$env:PHONE_HARNESS_MCP_PUBLIC_BASE_URL = "http://127.0.0.1:17677/"
$env:PHONE_HARNESS_MCP_OAUTH_ISSUER_URL = "https://YOUR-TAILSCALE-NAME/phone-auth/"
$env:PHONE_HARNESS_MCP_OAUTH_RESOURCE_URL = "https://tunnel-service.gateway.unified-0.internal.api.openai.org/v1/mcp/YOUR-TUNNEL-ID"
```

Proxy `/phone-auth` to the loopback MCP server. For a path-scoped OAuth issuer,
also publish RFC 8414 authorization-server metadata at
`/.well-known/oauth-authorization-server/phone-auth/`. The MCP endpoint itself
should remain reachable only through the Secure MCP Tunnel.

The local MCP must keep its own loopback MCP URL as the RFC 9728 protected
resource and use the local metadata URL in its unauthenticated
`WWW-Authenticate` challenge:

```text
http://127.0.0.1:17677/.well-known/oauth-protected-resource/mcp
```

`PHONE_HARNESS_MCP_OAUTH_RESOURCE_URL` remains an additional exact OAuth
audience accepted by the provider, but the local MCP does not publish that
internal Tunnel URL as its own protected-resource metadata. `tunnel-client`
discovers the loopback metadata and rewrites the protected-resource identity and
`resource_metadata` URL to the OpenAI Tunnel identity for the remote product.

Do not hand-enter the MCP environment on every restart. On Windows, configure
the machine-local values once and then use the startup wrapper:

```powershell
.\tools\configure_phone_harness_mcp.ps1
.\tools\start_phone_harness_mcp.ps1
```

The configuration wrapper stores non-secret settings under
`%LOCALAPPDATA%\phone-harness-mcp\config.json` and stores the owner token in a
separate Windows-DPAPI-encrypted file. It stores only the control-plane API-key
**environment variable name**, not that API key value. Use
`-PersistControlPlaneApiKey` once to copy the current process value into the
Windows User environment so it survives a PC restart. Windows User environment
variables are stored by Windows and are readable by processes running as that
user, so use this only for the explicitly requested convenience tradeoff.

The startup wrapper loads those values, builds the TypeScript server, replaces
only the Node listener previously started by the wrapper, verifies local
protected-resource metadata and the 401 OAuth challenge, runs `tunnel-client
doctor`, starts/reuses the configured Secure MCP Tunnel process, then waits for
the tunnel-client `/readyz` endpoint to return `200` before reporting `READY`.
If the configured port is still owned by an older manually started process,
stop that process once; the wrapper deliberately refuses to kill an unmanaged
listener.
Use `configure_phone_harness_mcp.ps1 -OAuthResourceUrl <new-url>` when a new
ChatGPT Secure MCP Tunnel resource URL must be adopted; no source edit is
required.

The verified Windows tunnel-client profile is stored outside this repository at
`tools/openai-tunnel-client/profiles/phone-harness-mcp.yaml`. It maps the `main`
channel to `http://127.0.0.1:17677/mcp`, enables Harpoon for the public OAuth
issuer host, and references the configured environment variable instead of
containing the API key value. For reboot-safe startup, configure once with:

```powershell
$env:CONTROL_PLANE_API_KEY = "<runtime API key>"
.\tools\configure_phone_harness_mcp.ps1 -PersistControlPlaneApiKey
```

After that, `start_phone_harness_mcp.ps1` owns the normal `doctor`/`run` flow.
No API key value is written to the repository or MCP JSON configuration.

For the complete local phone-harness lifecycle, including the administrator-
privileged pymobiledevice3 tunneld, use:

```powershell
.\tools\start_phone_harness.ps1
.\tools\stop_phone_harness.ps1
```

The top-level start command brings up/adopts tunneld first and then delegates to
`start_phone_harness_mcp.ps1`. The top-level stop command stops the managed
Secure MCP Tunnel and Node MCP first, then elevates only the tunneld stop.

## Tailscale Funnel

A legacy whole-MCP Funnel can still be used for direct remote MCP access, but
it is not required when Secure MCP Tunnel is used. Check the existing
configuration before changing it:

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
