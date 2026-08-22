# Phone Harness Monitor Modernization — Orchestration Input

## Goal

Modernize the Phone Harness Monitor into a stable desktop-managed monitoring/control surface while preserving LAN/browser usability.

The final state should provide:

- Electron-managed Monitor Server lifecycle.
- No dependency on VBS as the normal launch path.
- Console-hidden Python server process on Windows.
- Stable LAN access on TCP/17678.
- Phone portrait layout preserved as a vertical layout.
- Tablet landscape layout redesigned as a dedicated layout.
- Desktop layout converted to an IDE-style dockable/resizable panel system.
- Desktop layout persistence and reset.
- Electron-local process controls for Monitor and Phone Harness.
- Electron-local developer/build controls.
- Packaged Windows executable and window-state persistence.
- No tray implementation.

## Canonical target workspace

Work only in the existing Phone Harness worktree/branch used for the current Monitor implementation:

- branch: `feature/phone-harness-mcp-modern`
- expected worktree: `phone-harness-7e2e5c83`

Do not create another nested Workbridge worktree unless the orchestrator explicitly assigns one for an isolated task.

## Non-goals

- Do not add a tray icon or tray-resident mode.
- Do not redesign the game automation logic.
- Do not expose arbitrary shell execution to LAN clients.
- Do not expose build/start/stop/restart OS controls over the existing LAN HTTP API.
- Do not replace the current Python Monitor Server unless a task explicitly proves a replacement is necessary.
- Do not break direct browser access from Android/iOS/another PC on the LAN.

## Safety boundary

The HTTP Monitor is LAN-accessible. Any operation capable of spawning, stopping, restarting, building, or modifying local processes/files MUST remain Electron-local.

Preferred boundary:

`Electron renderer -> narrow preload API -> Electron main -> allowlisted operation`

Never implement:

`LAN HTTP endpoint -> arbitrary process/build command`

## Required implementation loop for every task

Each implementation task runs this loop until completion:

1. Inspect relevant current source and existing tests.
2. Confirm exact ownership boundaries and avoid unrelated changes.
3. Implement the smallest sufficient change.
4. Run focused tests/checks.
5. Perform self-review for correctness, simplicity, security, and regressions.
6. Fix findings.
7. Repeat steps 4–6 until clean, maximum 3 repair loops unless a new root cause is discovered.
8. Run the task's completion gate.
9. Produce a concise handoff containing:
   - files changed
   - tests/checks run
   - verified behavior
   - remaining risks/blockers
   - whether downstream dependencies are now unblocked

Do not declare success from code inspection alone when the task defines a runtime verification.

## Parallel execution waves

### Wave 0 — Baseline

- `01_BASELINE_AND_CONTRACT.md`

Wave 0 must complete before implementation waves begin.

### Wave 1 — Safe parallel foundation work

Run in parallel after Wave 0:

- `02_LAUNCHER_MODERNIZATION.md`
- `03_ELECTRON_LIFECYCLE_HARDENING.md`
- `04_STARTUP_FIREWALL_CONSISTENCY.md`
- `05_TABLET_LANDSCAPE_LAYOUT.md`

Expected file ownership should remain mostly disjoint. If a task needs a file currently owned by another Wave 1 task, stop that edit and report the overlap to the orchestrator instead of racing.

### Wave 2 — Desktop UI architecture

Run after Wave 1 integration:

- `06_DESKTOP_DOCKABLE_LAYOUT.md`

This task owns the primary desktop Monitor layout refactor and therefore is intentionally serialized after tablet layout work.

### Wave 3 — Safe parallel feature work after dock architecture exists

Run in parallel after Wave 2:

- `07_LAYOUT_PERSISTENCE_AND_PANEL_CONTROLS.md`
- `08_MONITOR_CONTROL_PLANE.md`

These may run in parallel only if their concrete edits remain separated between UI-layout persistence code and Electron lifecycle IPC/control code. If both require the same HTML/JS section, serialize them.

### Wave 4 — Process/developer controls

Run after Wave 3:

- `09_PHONE_HARNESS_PROCESS_CONTROL.md`
- `10_DEVELOPER_BUILD_CONTROLS.md`

These may run in parallel if both consume an already-defined Electron-local command registry rather than independently modifying the same dispatcher.

### Wave 5 — Packaging and desktop finish

- `11_WINDOWS_EXE_AND_WINDOW_STATE.md`

### Wave 6 — Final integration/review loop

- `12_FINAL_INTEGRATION_AND_REVIEW.md`

## Merge/integration rule

The orchestrator should integrate completed tasks in wave order. After each wave:

1. re-run the combined focused tests for all tasks in that wave;
2. start the Electron Monitor once;
3. verify TCP/17678 and `HTTP 200`;
4. verify Electron close does not leave an Electron-owned Monitor Server behind;
5. inspect `git diff` for cross-task accidental edits;
6. only then unlock the next wave.

## Definition of done

The modernization program is complete only when all of the following are true:

- Normal development launch does not require VBS.
- Electron starts the Monitor Server with no visible Python console.
- Electron can safely stop/restart its owned Monitor Server.
- Existing external Monitor Server processes are not killed accidentally.
- LAN browser clients can still use the Monitor.
- Phone portrait remains usable as a vertical layout.
- Tablet landscape has a dedicated usable layout.
- Desktop panels can be moved/resized/tabbed/docked.
- Desktop layout survives restart and can be reset.
- Monitor/Phone Harness process controls are Electron-local only.
- Developer build controls are Electron-local only and allowlisted.
- Windows executable packaging works.
- Window size/position/maximized state persists.
- No tray behavior exists.
- Relevant automated tests and runtime smoke checks pass.

