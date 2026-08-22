# Task 02 — Launcher Modernization / Remove VBS from Normal Flow

## Goal

Make the normal development launch path match Workbridge-style operation: console-driven during development, packaged executable later.

## Required outcome

- VBS is not the documented/default launch method.
- Provide one obvious development launch command from the Electron project, preferably an npm script.
- Keep Python child console hidden through Electron itself.
- Do not depend on `pythonw.exe` merely to hide the console.
- Preserve direct/manual Python Monitor startup tools where they remain useful for debugging.

## Implementation guidance

Prefer the smallest path such as:

- `npm run start` or `npm run monitor`
- optional repository-root convenience command only if an existing project convention supports it

Do not add a new launcher framework.

## VBS handling

`tools/start_phone_harness_monitor_desktop.vbs` should either be removed or clearly marked legacy/non-default if another active workflow still references it.

Before deleting it, search for references.

## Files likely owned

- `desktop-monitor/package.json`
- `desktop-monitor/package-lock.json` only if dependencies/scripts materially change
- launch-related docs/tools
- VBS file only after reference check

Avoid editing Electron lifecycle implementation unless necessary; Task 03 owns lifecycle hardening.

## Tests/checks

- `npm test`
- `npm run check`
- launch Electron using the new documented command
- confirm no extra console window is created for Python

## Completion gate

A developer can start the desktop Monitor without VBS and without manually starting the Python Monitor Server.

