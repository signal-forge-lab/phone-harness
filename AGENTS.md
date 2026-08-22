# phone-harness working rules

## Human teaching during live operation

- During phone automation, compare another probe/inference with asking the human operator.
- Default to acting on a high-confidence inference instead of interrupting the operator for the remaining small uncertainty. For low-risk app/game operation, roughly 90% confidence is enough to continue and verify from the result.
- Do not stop work merely to close a generic/common-sense 10% uncertainty gap. Prefer a reversible probe, action, re-observation, and correction loop.
- Ask immediately, even mid-task, when the ambiguity can materially change the data model/control policy, when competing interpretations are similarly plausible, when a wrong action has meaningful cost/risk, or when human teaching is clearly faster than testing.
- Repeat whenever needed; do not force an uncertain inference just to avoid asking.
- Phrase questions for a human operator in terms of exactly what they should look at or decide; do not ask them to reason about internal data-model terminology when a simple visual/gameplay question will do.
- Persist confirmed teaching during the same session so later chats do not ask the same question again.
- Evidence classes: `U` = user-confirmed, `D` = device-confirmed, `H` = hypothesis.

## Architecture separation

- Generic modules may model observations, visual descriptors, identity matching, grid cells, state transitions, demand planning, batching, retries, scrolling, and human-question seams.
- Generic modules must not know AliExpress, Merge Boss, family names, producer-family rules, orders, energy, or Complete buttons.
- Merge Boss-specific interpretation, policy, catalog knowledge, screen geometry, family/level semantics, producer behavior, orders, energy, and objectives belong under `src/phone_harness/workflows/merge_boss*` and `docs/game-operations/aliexpress-merge-boss/`.
- Device/MCP transport layers must not contain game rules.

## Deferred generic UX work

- Before closing the current phone-harness workstream, revisit
  `docs/performance/monitor-human-teaching-roadmap.md` with the user.
- When the `phone_host_activity` MCP tool is available, mark `working` when
  beginning/resuming a phone-harness task, `paused` when intentionally stopping
  before completion, and `completed` immediately before the final completion
  reply. This explicit marker is monitor observability metadata only; never use
  it to expose hidden chain-of-thought.

## Merge Boss durable knowledge

- Read `docs/game-operations/aliexpress-merge-boss/knowledge.mbk`, `catalog.json`, `visual.mbv`, and `calibration.json` before resuming Merge Boss work.
- `knowledge.mbk` is the durable AI-only knowledge source. It uses MBK1: compact UTF-8 JSON Lines, one fact per line.
- `visual.mbv` is the compact MBV1 visual-template cache; keep image fingerprints out of prose knowledge.
- Update durable knowledge whenever the user teaches a new rule or live-device verification establishes a reusable fact.
- Prefer learned durable knowledge over re-probing the same question in a later chat.
- Producer selection is opportunistic rather than exclusive to the current
  order. Prefer a directly useful producer when known, but arbitrary bounded
  production is valid because produced items remain useful for future orders.
  When output knowledge is incomplete, use produce -> observe -> learn ->
  re-plan instead of idling merely because the current order route is unknown.
