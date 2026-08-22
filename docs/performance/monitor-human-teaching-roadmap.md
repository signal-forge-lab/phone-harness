# Phone Harness monitor + human teaching backlog

Status: user-requested follow-up after the current Merge Boss calibration/workflow work.

## Monitor goal

Provide a Windows GUI that makes the active phone-automation pipeline observable
without reading raw logs.

Show, when available:

- current operation / workflow / phase;
- whether a capture is running, capture source, region/profile, latency and bytes;
- a safe preview of the captured region/frame;
- local analysis type, inputs, outputs, candidate counts, confidence and timing;
- tool-side processing and selected/fallback backend;
- host request received by the tool;
- host-visible decision summary, confidence, uncertainty, chosen action(s) and
  structured rationale supplied explicitly by the host;
- action batch sent to the device and per-action/result timing;
- re-observation / recovery / re-plan events;
- durable-knowledge writes caused by user teaching or device confirmation.

Do not depend on or attempt to expose a model's private hidden chain-of-thought.
If reasoning visibility is desired, use an explicit concise `decision_summary`
or equivalent structured rationale field generated for observability.

## Human teaching interface

Provide a Windows-side interaction surface for uncertain automation decisions:

- AI/tool can post a concrete human-readable question;
- question states exactly what the human should inspect/decide;
- GUI shows current relevant screenshot/region when useful;
- answer text field + Send action;
- optional quick-choice buttons for simple alternatives;
- submitted answer returns to the waiting local workflow/host;
- confirmed reusable teaching is persisted to the appropriate durable knowledge
  source and tagged by evidence class;
- local workflow resumes by re-observing/re-planning rather than blindly replaying
  a stale action batch.

## Architecture rule

Keep generic monitoring/event transport/question-answer plumbing independent of
Merge Boss. App/game-specific renderers and knowledge adapters consume the same
generic event/question interfaces.

## Implemented monitor surfaces

The monitor now has two local front ends over the same generic trace/operator
transport:

- `phone-harness-monitor`: Windows Tkinter desktop monitor.
- `phone-harness-monitor-web`: responsive browser monitor intended as the
  primary operational UI.

The web monitor binds to `0.0.0.0:17678` by default and is intended for access
from a separate phone/tablet/PC on the same LAN. The HTTP handler rejects
non-loopback/non-private/non-link-local source addresses and does not expose any
phone-action endpoint. Browser-side writes are limited to submitting an answer
to the currently pending operator question.

Responsive layout contract:

- desktop: timeline / frame+analysis / decision+teaching in three columns;
- tablet: two columns with decision/teaching below;
- mobile: one-column stacked layout, touch-sized teaching controls and a
  viewport-safe frame preview.

The initial `/api/state` response includes recent history; later polling sends
only events newer than the browser's last `monotonic_ns`, so a mobile browser
does not repeatedly download the full trace history.

Runtime state is written to `%TEMP%/phone-harness/monitor/server.json`. It
includes a `preferred_url`; on the current real Windows host the detected
preferred LAN URL is `http://<LAN_IP>:17678/`.

Windows Firewall is intentionally narrow. The helper
`tools/enable_phone_harness_monitor_lan_firewall.ps1` creates only:

`Private profile + TCP 17678 + RemoteAddress LocalSubnet + phone-harness pythonw.exe`.

The inverse helper removes that exact rule. Do not add a Public-profile or
Internet-wide rule merely to make testing easier.

## Activation state on 2026-08-17

The responsive web monitor is implemented and currently runs on the Windows
host at the detected preferred URL `http://<LAN_IP>:17678/`. Localhost and
the host LAN address both returned HTTP 200 during acceptance.

The Python runtime already emits `host.request`, operation, observation,
Merge-Boss analysis, structured decision, action, error, operator question /
answer and knowledge-write events. Human Teaching file IPC and the web
`POST /api/respond` round trip are proven.

The currently loaded ChatGPT App tool schema predates the new optional
`phone_act.decision_context` and `ask_operator` action. To make those two host
features callable from ChatGPT, restart only the rebuilt local MCP Node process
and rescan/reconnect the ChatGPT App. Do not restart tunneld merely for this
schema refresh.

The current Workbridge process is not elevated, so the narrow Windows Firewall
rule could not be installed automatically. The helper script is implemented
and PowerShell-parser verified; run it once from the interactive Windows
desktop and accept its UAC prompt when LAN clients need access.

## P1 live update on 2026-08-20

- Human Teaching now supports both AI-initiated questions and an operator-initiated durable Inbox.
- Inbox messages may contain text plus one PNG/JPEG/WebP image; messages and replies survive Monitor restart.
- Human presence is a durable HERE/AWAY switch. HERE preserves the normal Human Teaching wait path. AWAY skips/cancels AI questions so local workflows continue autonomously instead of blocking.
- Monitor has a dedicated Failure Judgment surface for expected / actual / reason / next action instead of relying on generic error events.
- Monitor aggregates Capture, Recognition, Decision, Action and Learning timings as count / latest / average / p95 / total. These categories can overlap and MUST NOT be summed as turn wall-clock time.
- The desktop layout no longer puts all operational information in one right rail. Preview spans the main workspace; Decision/Failure, Performance/Runtime and Human Teaching/Inbox are paired rows.
- Analysis / Event Detail shows a bounded semantic/OCR summary from the already-redacted public observation elements.
- Host Activity supports explicit WORKING / WAITING / COMPLETED / PAUSED markers plus stale/interrupted inference. Explicit markers take precedence over individual tool-completion events.
- A live Merge Boss regression after these changes completed `produce x2 -> merge x2` with zero recovery over Wi-Fi while USB was disconnected.
- Merge Boss intentionally permits bounded arbitrary production when no order-relevant producer is selected. Generated items are future inventory, and incomplete producer knowledge should be learned by observing outputs rather than treated as a planning failure.
