# Task 01 — Baseline and Contract Capture

## Purpose

Capture the current Monitor/Electron state before parallel implementation starts so later agents do not overwrite working behavior or pre-existing dirty changes.

## Scope

Read-only unless a tiny documentation-only correction is required.

Inspect at minimum:

- `src/phone_harness/monitor_web.py`
- `src/phone_harness/monitor_web.html`
- `src/phone_harness/manifest.webmanifest`
- `tests/test_monitor_web.py`
- `desktop-monitor/`
- `tools/start_phone_harness_monitor*.cmd`
- `tools/start_phone_harness_monitor_desktop.vbs`
- `.gitignore`
- project/agent instructions

## Required evidence

Record:

- current branch/worktree identity;
- dirty files relevant to Monitor work;
- current Electron version and Node version;
- current Monitor startup command and Python executable selection;
- current process ownership behavior;
- current LAN bind/port;
- current firewall-related startup assumptions;
- current phone/tablet/desktop CSS breakpoints;
- existing tests/checks that cover Monitor and Electron files.

## Runtime baseline

When safe, verify:

1. Electron Monitor can start.
2. Monitor Server listens on `0.0.0.0:17678`.
3. `http://127.0.0.1:17678/` returns HTTP 200.
4. `http://127.0.0.1:17678/manifest.webmanifest` returns HTTP 200.
5. Closing the Electron-owned Monitor removes its owned listener.

Do not kill unrelated/external Monitor processes merely to obtain this evidence.

## Deliverable

Produce an orchestration handoff summarizing the verified baseline and any contradictions found between source, scripts, runtime, and firewall expectations.

## Completion gate

Wave 1 may start only after the orchestrator has a clear list of protected pre-existing changes and confirmed runtime assumptions.

