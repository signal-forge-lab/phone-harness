# Task 12 — Final Integration, Regression Review, and Repair Loop

## Goal

Integrate all completed modernization tasks and run a final multi-axis review/repair loop before declaring the Monitor modernization complete.

## Required review axes

### Correctness

- Electron ownership semantics are correct.
- Stop/restart/build operations affect only intended processes.
- LAN Monitor still works.
- phone/tablet/desktop layouts select the intended mode.
- desktop layout persistence is reliable.

### Security

- No process/build OS controls are exposed through LAN HTTP endpoints.
- Renderer does not receive raw Node/Electron privileged APIs.
- IPC/preload APIs are narrow and allowlisted.
- No arbitrary shell command input is accepted from renderer content.

### Simplicity

- No duplicated launch paths remain without a reason.
- Legacy VBS is removed or explicitly non-default.
- No speculative tray/profile/framework work was added.
- Avoid separate near-duplicate process managers for Monitor and Phone Harness if one small shared primitive is sufficient.

### UX

- Phone portrait remains usable.
- Tablet landscape is intentionally designed, not just a compressed desktop grid.
- Desktop docking is usable and recoverable via Reset Layout.
- Privileged desktop-only controls are clearly separated from normal Monitor content.

### Runtime/process hygiene

- Electron close leaves no owned Monitor Server orphan.
- Double-launch focuses/uses one Electron instance.
- External server is not killed.
- Restart/build transitions do not create duplicate servers.

## Required test matrix

Run all relevant repository tests plus focused Monitor/Electron tests.

At minimum perform runtime smoke checks for:

1. development Electron launch;
2. packaged executable launch if Task 11 completed packaging;
3. root HTTP 200;
4. manifest HTTP 200;
5. LAN browser access where available;
6. phone portrait layout;
7. tablet landscape layout;
8. desktop dock move/resize/tab;
9. layout persistence/restart;
10. Reset Layout;
11. Monitor Stop/Restart;
12. Phone Harness status/control where implemented;
13. Build/Build & Restart where implemented;
14. application close process cleanup.

## Repair loop

For every blocking finding:

1. identify root cause;
2. apply the smallest fix;
3. rerun the focused failed check;
4. rerun the relevant integration checks;
5. re-review affected security/process boundaries.

Repeat until no blocking findings remain or a genuine HUMAN_REQUIRED decision is identified.

## Final handoff

Provide:

- final feature checklist;
- changed files grouped by subsystem;
- tests/checks and results;
- known non-blocking limitations;
- any intentionally deferred items;
- confirmation that tray was not implemented;
- recommended next action, if any.

## Completion gate

All required modernization goals in `00_ORCHESTRATION.md` are satisfied or explicitly documented as blocked with evidence.

