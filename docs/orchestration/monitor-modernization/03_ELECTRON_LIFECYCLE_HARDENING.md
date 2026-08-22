# Task 03 — Electron Monitor Lifecycle Hardening

## Goal

Complete and harden Electron ownership of the Monitor Server process.

## Required behavior

- Single Electron application instance.
- Electron-owned Monitor Server starts automatically.
- Python process uses `windowsHide: true` on Windows.
- Electron waits for HTTP readiness before presenting a healthy Monitor state.
- Electron close stops only the Monitor Server it owns.
- External pre-existing Monitor Server is detected and not killed.
- Server crash is distinguished from intentional stop/restart.
- Intentional restart does not surface a false crash dialog.
- Child process tree does not remain orphaned after normal Electron exit.
- Startup failure gives a useful bounded error and retry path.

## Constraints

- Do not add automatic infinite restart loops.
- Do not kill processes by broad command-line wildcard when ownership PID/process handle is available.
- Do not expose lifecycle controls to the LAN HTTP API.

## Files likely owned

- `desktop-monitor/main.cjs`
- `desktop-monitor/server-process.cjs`
- focused Electron/process tests

Avoid launch documentation changes unless strictly necessary; Task 02 owns launcher modernization.

## Required tests

Add/maintain automated coverage for at least:

- Python executable resolution;
- environment/PYTHONPATH construction;
- intentional stop does not call unexpected-exit handler;
- intentional restart does not call unexpected-exit handler;
- external-server mode is non-owning;
- single-instance behavior where testable without brittle GUI automation.

## Runtime verification

1. Start desktop Monitor.
2. Verify listener `0.0.0.0:17678`.
3. Verify HTTP 200.
4. Record owned Python PID.
5. Close Electron normally.
6. Verify the owned PID/listener is gone.

## Completion gate

Lifecycle behavior is deterministic and process ownership is explicit.

