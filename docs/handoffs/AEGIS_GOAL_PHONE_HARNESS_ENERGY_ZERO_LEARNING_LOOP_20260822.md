# Aegis Gate Goal — Phone Harness Merge Boss Energy-Zero Learning Loop

FINAL_GOAL_ID: goal-phone-harness-merge-boss-energy-zero-learning-loop-20260822
GOAL_REVISION: 3

## Goal Statement

Continue the existing Phone Harness / AliExpress Merge Boss real-device play and learning loop from the current configured Phone Harness executor workspace.

The immediate primary checkpoint is to **play until Merge Boss game energy is freshly observed at 0 at least once** while preserving accumulated runtime knowledge, Human Teaching, question history, visual evidence, monitor state, and all pre-existing baseline changes.

During play, do not treat current rules, heuristics, recognition methods, or architecture as absolute truth. Use real-device evidence to identify clearly material correctness or latency problems. When a problem is obviously large enough to distort play/learning or dominate wall-clock time, apply the smallest justified code fix, test it, and resume play. Record useful counterexamples, alternative methods, negative evidence, timing evidence, and design candidates as durable knowledge.

Revision 2 additionally incorporates the newest live-play evidence from the direct GPT continuation after Revision 1 was written: a repeatedly selected visual merge pair was semantically confirmed to be two different General Generator Card fragment levels, repeated non-progressing visual pairs now have short-lived suppression/cooldown support, and the User explicitly taught that General Generator Card fragments are low priority for the current objective and should normally be ignored.

Revision 3 incorporates the latest recovery work immediately before handing control back to Aegis Gate. The prior `OBSERVE_FAILED` / WDA-pending condition was traced to a stale long-lived in-process RSD/WDA client even though the separately running WDA endpoint itself was healthy. A minimal RSD/WDA-client refresh-after-backoff fix is now implemented and verified. After the managed MCP restart, fresh visual observation succeeded in about 1.5 seconds and fresh semantic observation also succeeded. The latest authoritative phone screen at handoff is the iPhone Home Screen, not Merge Boss, so current game energy is unknown and must be freshly observed after navigating back to the game.

## Authoritative workspace and baseline

- Managed worktree:
  `%USERPROFILE%\Documents\Intelligence Works\.workbridge\worktrees\phone-harness-7e2e5c83`
- Branch:
  `feature/phone-harness-mcp-modern`
- Runtime Python authority: use the configured `PHONE_HARNESS_PYTHON` / `%LOCALAPPDATA%\phone-harness-mcp\config.json` `pythonPath`; do **not** assume the managed worktree contains its own `.venv`.
- Latest confirmed configured runtime Python resolves to the product checkout venv under `~\Documents\Intelligence Works\products\phone-harness-windows\.venv\Scripts\python.exe`.
- For tests against this managed worktree, bind imports to the managed source (`PYTHONPATH=%CD%\src`) and use the already-working project test invocation rather than silently falling back to an unrelated system Python.
- Phone Harness MCP: `127.0.0.1:17677`
- Monitor Web: `0.0.0.0:17678`
- Device transport: Wi-Fi when available.

The worktree is intentionally heavily dirty. **Do not reset, clean, discard, rewrite, or remove pre-existing baseline changes.** Preserve unrelated baseline content unless this Goal explicitly requires modification.

Do not push, publish, deploy, or perform remote repository operations unless the User explicitly requests them.

## Aegis execution authority

This Revision 3 file is the canonical Goal for the new Aegis Gate run. Evidence and artifacts from the older `goal-phone-harness-merge-boss-builder-only-20260820` / `mission-phe3-001-exhaust-safe-energy-order-loop` run remain useful history, but their runtime state, terminal status, cached observations, and energy values are **not** authority for the current live device state.

Do not reset or replace Phone Harness knowledge/worktree state merely to make Aegis orchestration clean. If stale Aegis runtime/queue/checkpoint state must be cleared before launch, clear only the Aegis orchestration state required for the new Goal and preserve Phone Harness source, knowledge, visual catalog, Human Teaching history, Monitor state, and trace evidence.

Reviewer/Steward terminal decisions must require the fresh evidence defined by this Goal rather than inferring completion from an older Mission result.

## Immediate start procedure

1. Reuse the configured Phone Harness runtime and Monitor; start/restart them only if required. Do not restart a healthy WDA/MCP merely because an older run once entered `pending`.
2. Freshly read `phone_status` and verify transport/runtime health. The last confirmed handoff state was `connection_state=ready`, Wi-Fi transport, Human Teaching pending `0`, and WDA ready after the first successful observation.
3. Freshly observe the phone before making any claim about the current Merge Boss screen or game energy. The last confirmed screen was the iPhone Home Screen.
4. If the phone is still on Home Screen, navigate back to AliExpress / Merge Boss from the **fresh semantic observation** using current element identity/ref when available. Do not reuse stale absolute coordinates from a prior frame.
5. Process any genuinely operator-initiated pending Human Teaching promptly according to the durable-learning contract. Re-check the inbox even though the last confirmed pending count was `0`.
6. Once Merge Boss is visibly active, freshly read the in-game energy and board state, then resume the bounded play/learning loop from that actual state.

## Primary completion checkpoint

Reach a state where the **Merge Boss in-game lightning energy counter is freshly observed as exactly 0**.

Do not confuse the iPhone battery indicator with Merge Boss game energy.

Do not claim energy 0 from prediction, stale screenshots, arithmetic alone, or a previous run. It must be freshly observed on the live game UI.

The **first** fresh observation of energy 0 is a required evidence checkpoint. Preserve that screen/trace before doing anything that could obscure it. It is not, by itself, sufficient for final `DONE` when free/safe/immediate energy recovery is still available.

After the first 0 checkpoint is recorded, inspect the known free/safe/immediate recovery surfaces across their relevant scroll range. If such recovery is available, consume it, return to Merge Boss, spend the recovered energy with the normal order/board-compaction policy, and re-check recovery again. Final completion requires a fresh energy-0 state with no remaining free/safe/immediate recovery that the current Goal permits. Paid purchases, paid currency use, and unsafe account-changing actions remain excluded.

## Play policy

Optimize for useful gameplay throughput, not one-action/one-observation conservatism.

- Predictive bounded action bursts are allowed when actions are low-risk, local, and cheaply recoverable.
- Adapt observation frequency to uncertainty, animation, urgency, and recovery cost.
- Learn action-specific animation/settle intervals instead of imposing one global fixed wait.
- Compare multiple plausible methods when useful and preserve evidence about their speed/accuracy tradeoff.
- A full board is a jam-recovery trigger, not a terminal state.
- Merge safe duplicates for compaction even when they are not immediately order-relevant.
- Producer output may be generated repeatedly while useful free cells and game energy remain.
- A temporary energy-0 screen is a checkpoint, not an automatic terminal state when free/safe/immediate recovery remains.
- After recording the first energy-0 checkpoint, exhaust permitted free/safe/immediate recovery before the final terminal claim, and re-spend any recovered energy in Merge Boss.
- General Generator Card fragments are currently low priority. Do not spend merge/analysis effort actively pursuing them unless required for board safety, unavoidable compaction, or a later explicit objective.
- Avoid paid purchase/payment/unsafe account-changing actions.

When a visually similar pair repeatedly fails to merge, do not keep retrying it simply because it remains a strong visual match. Prefer semantic identity/level evidence when available, suppress the suspect pair for a bounded period, and continue useful play.

## Bubble / transient-object policy

Bubble items are time-sensitive and can degrade into round-lightning items.

- Bubble handling is a high-priority interrupt over ordinary order analysis, OCR, family learning, and normal merge planning.
- Bubble interaction requiring item activation uses the established two-tap semantics where applicable: first tap selects/shows the top hint, second tap executes the selected-item action.
- Do **not** assume every merge-capacity anomaly is a bubble.
- A successful merge can also randomly generate a green cash-stack item, so missing expected free-cell gain may be explained by bubble spawn, green cash spawn, another added object, no-op/failed merge, or perception error.
- When a probable bubble or other uncertain transient item is about to be acted on, preserve a durable screenshot/preview with the candidate cell/evidence so a human can review it later.
- Bubble detection rules are hypotheses. False positives and false negatives must be retained as learning evidence.

## Human Teaching and AI Question policy

Human Teaching is part of the active learning loop.

- AI questions should be used for ambiguities that are material, reusable, and cheap for a human to explain.
- Normal low-risk gameplay must **not** block while waiting for an answer.
- Questions remain durable and answerable later from Monitor conversation history.
- The runtime should poll for late answers at safe workflow boundaries while continuing useful work.
- Question answers promoted into Human Teaching are non-blocking learning inputs; they must not pause gameplay merely because they entered the Teaching inbox.
- Question records must preserve the relevant screenshot at question time when visual review can matter.
- Direct operator-initiated Teaching remains higher priority and should be processed promptly.
- `answered`, `handled`, and `learned` are distinct states. `learned=true` should mean durable knowledge/artifact persistence was actually completed and verified.

If a pending Teaching is already represented by equivalent durable knowledge, do not create a duplicate fact merely to clear the inbox. Verify the existing knowledge, then handle the Teaching with a concise note that the equivalent rule is already persisted.

## Monitor/UI baseline to preserve

The current Monitor baseline includes:

- left / center / right desktop information layout;
- draggable major splitters for left/center/right widths and Timeline height;
- persisted splitter layout in local browser storage;
- phone/preview image display modes `Fit` and `100%`;
- `100%` mode is scrollable so clipped portions can be inspected;
- durable question-time screenshots accessible from question/conversation history;
- Accessibility/OCR details collapsed by default;
- Timing view includes tool-stage timings plus end-to-end action cadence and measurable outside-tool host gap.

Do not regress these behaviors while pursuing gameplay fixes.

## Geometry invariant

Do not use one device's absolute pixel coordinates as durable semantic knowledge or object identity.

Preferred resolution order:

1. semantic/accessibility element identity when available;
2. current-frame visual recognition;
3. board/card-local logical geometry such as row/column or local region;
4. calibrated relative/runtime-scaled geometry;
5. absolute pixel coordinates only as the final live-device execution binding.

Reference-device calibration may be scaled/bound to the current frame at runtime. Device resolution, aspect ratio, safe-area, UI scale, and layout differences must not silently turn a reference pixel into universal knowledge.

## Learning / architecture policy

Current rules and architecture are hypotheses constrained by evidence, not immutable truth.

During play preserve evidence about:

- state/context and active goal;
- selected action/strategy;
- plausible alternative strategies;
- expected vs actual result;
- latency and observation cost;
- error/recovery cost;
- false positives and false negatives;
- reusable domain facts;
- runtime-only facts;
- negative transition evidence;
- candidate architecture/rule changes.

When evidence suggests a better rule or architecture:

1. keep the current rule/design identifiable;
2. record the alternative as a candidate;
3. preserve motivating evidence and context;
4. compare correctness, speed, observation cost, and recovery cost;
5. promote/generalize only when evidence supports it.

Do not prematurely finalize Manifest / State / Transition / Learning Record schemas during this Goal unless real-device evidence has clearly saturated enough cases to justify it.

## Performance policy

Timing is an explicit part of the Goal.

Observe both:

- tool-internal timings such as capture, recognition, action transport, and learning writes;
- end-to-end elapsed time between meaningful phone actions, including the measurable outside-tool host/request gap.

The outside-tool gap is an observable latency metric and may include model/planner deliberation, orchestration, transport/UI idle time, and scheduling. Do not attempt to expose private chain-of-thought; only use observable timestamps/gaps.

If a repeated phase is **clearly** dominating wall-clock time or causing obvious throughput loss, it is eligible for the same minimal fix -> test -> resume-play loop as a correctness defect. Record smaller optimization opportunities but defer detailed micro-optimization until dominant bottlenecks are clear.

## Current known implementation/evidence baseline

Preserve and build on the following already-implemented or already-learned behavior:

- generic Phone Harness core / Merge Boss domain separation;
- runtime geometry scaling from reference calibration;
- occupancy/perception work that distinguishes deep cell occupancy from producer glow contamination;
- bounded predictive merge/producer bursts;
- Human Teaching async question/history support;
- durable question previews;
- timing summary with action cadence and host gap;
- merge anomaly questions that consider bubble, green cash, other spawn, no-op, and perception error;
- probable bubble/transient candidate trace events preserve a durable preview for later human review;
- a repeated single-pair merge anomaly without independently observed spawn can accumulate short-lived runtime evidence; repeated non-progressing pairs are temporarily suppressed for a bounded number of snapshots rather than dominating the same burst indefinitely;
- when both cells of a visual merge candidate already have confident semantic catalog matches and their identity/level differ, that pair can be rejected before another drag attempt;
- live panel verification confirmed one recurring confusing pair: one General Generator Card fragment was Lvl.1 and the other was Lvl.2. Treat this as a concrete false-positive example, not as evidence that all similarly colored fragments are equivalent;
- User Teaching for the current objective: General Generator Card fragments are low priority and should normally be ignored;
- Merge Boss semantic knowledge in `docs/game-operations/aliexpress-merge-boss/knowledge.mbk`;
- architecture principles in `docs/game-operations/aliexpress-merge-boss/architecture-principles.md`;
- long-lived Wi-Fi RSD/WDA recovery: if an existing owned WDA runner is alive, startup backoff has elapsed, and the in-process ready probe fails, the runtime now refreshes only the cached in-process RSD/WDA client and retries the ready probe instead of treating a healthy WDA endpoint as permanently pending;
- the WDA/RSD fix has real-device evidence: direct WDA status/screenshot were healthy during diagnosis, then after the managed MCP restart a fresh Phone Harness visual observation succeeded in about 1.5 seconds and semantic observation succeeded afterward.

The last in-game energy value observed before the WDA/observe interruption was 70, but that value is now **historical only**. The latest authoritative live phone screen before Revision 3 is the iPhone Home Screen. Current Merge Boss energy is unknown due to elapsed time/natural regeneration and must be freshly observed after returning to the game.

The live semantic confirmation that exposed the recurring false pair used the in-game item information panel and read:

- one fragment: `ゼネラルジェネレータカードのいくつかの断片` / Lvl.1;
- the other fragment: `ゼネラルジェネレータカードの断片` / Lvl.2.

This is also a performance lesson: repeated blind drag retries are more expensive than one targeted semantic/level confirmation once a pair has already shown non-progressing behavior. Do not make OCR a universal pre-merge requirement; use it as an escalation path for recurring confusing pairs.

## In-loop code-fix eligibility

Modify code during play only when at least one of the following is clearly true:

- a defect is causing repeated wrong phone actions;
- a defect is preventing the system from reaching/operating the board;
- a defect is suppressing or corrupting durable learning/Human Teaching;
- a perception error is materially distorting planner state;
- a repeated latency source is clearly dominant and has a small, evidence-backed fix;
- a safety/geometry issue could cause device-dependent or irreversible mis-operation.

For eligible fixes:

1. make the smallest justified change;
2. add/update focused regression tests;
3. run the relevant focused tests;
4. run the full Python suite when practical after a bounded change set;
5. run the TypeScript MCP build when MCP/runtime bridge code is affected;
6. restart only the required runtime component;
7. return to real play promptly.

Do not turn the Goal into an open-ended refactor or speculative framework build.

## Verification baseline at Goal creation

Revision 1 started from:

- full Python test suite: **342 / 342 PASS**
- MCP TypeScript build: **PASS**
- inline Monitor JavaScript syntax (`node --check`): **PASS**
- Phone Harness status after restart: **ready / Wi-Fi**

Revision 2 then added the semantic-mismatch / merge-anomaly changes with focused regression groups green and MCP TypeScript build green.

The latest Revision 3 handoff supersedes the old verification gap:

- Windows/WDA-focused regression suite after the RSD/WDA recovery fix: **86 / 86 PASS**
- current full Python suite after the latest bounded changes: **350 / 350 PASS**
- MCP TypeScript build during the managed restart: **PASS**
- managed MCP restart: **PASS**
- fresh `phone_status`: **ready / Wi-Fi / Human Teaching pending 0**
- first post-restart fresh visual observe: **PASS**, about **1.5 s**, WDA promoted to `ready`
- subsequent fresh semantic observe: **PASS**
- latest confirmed live screen: **iPhone Home Screen**

Do not rerun the full suite merely to satisfy the obsolete Revision 2 note. Re-run focused/full verification only when new code changes or fresh failure evidence justify it.

## Done criteria

This Goal revision is DONE when all of the following are true:

1. Merge Boss game energy has been freshly observed at exactly 0 and that first 0-energy checkpoint has been preserved as live evidence.
2. After that checkpoint, the permitted free/safe/immediate recovery surfaces have been checked across the relevant available scroll range.
3. Any permitted recovered energy has been consumed and spent back in Merge Boss, and the recovery check has been repeated until no such immediate recovery remains.
4. The final live Merge Boss state is freshly observed at energy exactly 0 with no remaining free/safe/immediate recovery allowed by this Goal.
5. Human Teaching has no unresolved genuinely operator-initiated blocking item; late non-blocking question answers have been handled according to the durable-learning contract.
6. No unresolved implementation defect discovered during the run is still materially preventing safe continuation from the final checkpoint.
7. Material rule/design/performance discoveries from the run have been persisted as durable knowledge or explicit candidate evidence rather than remaining only in model chat memory.
8. Any code changes made during the run have appropriate regression tests and the relevant verification is green.
9. Pre-existing dirty-worktree content has been preserved.

## Completion report

Return a bounded report containing:

```text
Fresh final game-energy observation
Real-device play summary
Human Teaching / question summary
Knowledge learned or revised
Correctness problems found and fixes applied
Performance bottlenecks observed and fixes/deferred candidates
Changed files
Tests run
Test results
Current remaining risks / next candidates
Commit hash or no-commit reason
```

