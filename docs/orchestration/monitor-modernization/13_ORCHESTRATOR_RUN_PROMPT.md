# Orchestrator Run Prompt — Phone Harness Monitor Modernization

Use this prompt as the top-level instruction for an orchestrator that can run multiple implementation agents in parallel and iterate review/fix loops.

---

Execute the Phone Harness Monitor modernization program defined under:

`docs/orchestration/monitor-modernization/`

Start by reading:

1. `00_ORCHESTRATION.md`
2. `01_BASELINE_AND_CONTRACT.md`

Then execute the task files in the Wave order defined by `00_ORCHESTRATION.md`.

## Execution policy

- Treat each task Markdown file as the canonical scope for that task.
- Preserve all pre-existing dirty work unless the task explicitly requires changing it.
- Do not broaden a task because another improvement looks convenient.
- Use parallel execution only where the Wave definition permits it.
- Before parallel agents edit files, assign/confirm file ownership. If two agents need the same file, serialize those edits rather than racing.
- After each implementation, run the task's focused tests and runtime checks.
- Perform a review/fix loop for every task: implement -> test -> review -> fix -> retest, up to 3 repair iterations unless a newly discovered root cause justifies another loop.
- A task is not complete until its explicit Completion Gate passes.
- After every Wave, integrate the Wave results and run the Wave integration checks from `00_ORCHESTRATION.md` before releasing the next Wave.

## Security boundary

The Monitor HTTP server is LAN-accessible. Never expose process control, build control, shell execution, filesystem mutation, or raw Electron/Node APIs through LAN HTTP endpoints.

Privileged operations must remain Electron-local through a narrow, allowlisted preload/IPC boundary.

## UX boundary

- Phone/narrow portrait remains vertical.
- Tablet landscape receives a dedicated touch-friendly layout.
- Desktop receives the dockable IDE-style layout.
- Do not force desktop docking controls onto phone/tablet.

## Process boundary

- Electron may manage processes it explicitly owns.
- Do not kill an external/pre-existing Monitor Server merely because it uses the expected port.
- Avoid broad wildcard process termination when exact ownership/PID is available.
- Normal Windows operation must not show a Python console.

## Excluded scope

- Tray integration is explicitly excluded.
- Do not add speculative named layout presets unless trivial and clearly within Task 07 after all required persistence behavior is complete.
- Do not redesign Phone Harness game automation.

## Required final result

Run `12_FINAL_INTEGRATION_AND_REVIEW.md` after all preceding Waves are integrated.

Do not declare the program complete until that task's final integration matrix passes or a concrete blocker is documented with evidence.

Return a final orchestration report containing:

- task status by ID;
- Wave status;
- files changed by task;
- tests/runtime checks and results;
- repair loops performed;
- unresolved blockers/limitations;
- explicit confirmation that tray functionality was not implemented.

---

