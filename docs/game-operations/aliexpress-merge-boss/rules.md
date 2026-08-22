# AliExpress Merge Boss

Durable learned facts are stored in `knowledge.mbk` (MBK1 compact JSONL).
`rules.md` keeps readable operating rules; `knowledge.mbk` is the AI-only
cross-chat knowledge authority.

## Confirmed rules

User-confirmed on 2026-08-16:

1. The board is 7 columns by 9 rows.
2. Items with an electricity-like icon at the upper-right are producers, not merge targets.
3. Tapping a producer emits merge-target items onto the board.
4. If there are no merge-target items on the board, tap a producer.
5. Tapping an item shows its level and description above the board.
6. Merge-compatible items must be visually identical. Merely similar-looking items are not the same item.
7. When a producer is selected, the `i` icon at the upper-left of the board's
   description area can be used to inspect which items that producer can emit.
8. Producer emission is not a one-per-loop operation. If board space and energy
   remain, multiple items may be emitted in one local planning loop.
9. Multiple merge candidates may be executed in one local loop. Planning should
   account for the level/result produced by earlier merges so chain merges can
   be anticipated rather than rediscovered by ChatGPT one action at a time.
10. The purpose of production/merging is customer-order fulfillment. Customer
    cards near the top request one or two items each; when the matching items
    are available, `完成` appears and should be tapped to deliver them.
11. The customer area scrolls horizontally and has roughly five to six
    customers, so one local planning loop should inspect the whole order strip,
    not only the initially visible cards.
12. Each item type/family has levels 1 through 10, and level 10 is the maximum.
13. Selecting a board item and opening the upper-left `i` in the description
    panel shows that item's type/family level-1 through level-10 list.
14. The upper-left `i` on a customer order item exposes the same family/level
    hint. If a requested item's type or level is not yet known, inspect this
    hint, learn the family chain, persist it, then plan production from that
    learned chain.
15. Merge-compatible items should be merged even when they are not requested
    by any current customer. Board capacity is valuable; do not keep duplicate
    lower-level items merely because a future order might ask for them. If a
    later order needs a lower level than the current inventory, rebuild that
    chain from newly produced level-1/level-2 items.
16. Each customer card has a green refresh/change button at its lower-left.
    When the visible order set is dominated by very distant requirements such
    as level-9/level-10 targets that would require an extreme number of steps,
    changing that customer is allowed. If the replacement order is still poor,
    the customer may be changed again. Where the usefulness threshold or the
    number of repeated refreshes is not clear from current evidence, ask the
    operator rather than hard-coding a universal rule.
17. Bubble items are time-limited and take priority over ordinary board work.
    Tap a bubble promptly, complete the reward/ad flow, and return to Merge Boss.
    If the timer expires before the ad is watched, the bubble degrades into a
    round lightning icon and loses value.
    When the bubble interaction uses the selected-item action, the first tap
    selects it/shows the top hint and the second tap performs the action.
18. A round lightning icon can be collected by selecting it and tapping again;
    the live game currently grants +1 Merge Boss energy for that collection.
    Because the icon has no further upgrade path or other use, collect it before
    normal merge/production work whenever it is visible. This should normally
    be rare because bubble items are supposed to be handled before they expire.
19. The operator has moved two currently unnecessary producers into storage.
    Therefore a visible producer count lower than the historical eight must not
    automatically be classified as recognition degradation. Storage mechanics
    themselves can be learned later.
20. Green cash-stack icons are special board items rather than ordinary
    customer-order inventory. Prioritize safe merges of these stacks to reclaim
    board capacity. Their remaining collect/convert behavior should be learned
    from the in-game help shown after selecting one, rather than guessed from
    appearance alone.
    Live help for `紙幣の大きな束 Lv.5` says a double-tap collects 32 bills,
    while merging two matching Lv.5 stacks produces `袋一杯の紙幣 Lv.6`.
    Prefer the merge while an exact same-level pair exists because it both
    compresses the board and preserves progression; cash-out remains available
    when merging is no longer useful.

Real-device confirmation on 2026-08-16:

- Navigation path: `AliExpress -> Account/MyAccount -> マージボス`.
- The live game board was confirmed as a 7x9 regular grid.
- Tapping a fish item showed `フグ`, `Lvl. 4` and the instruction to merge it into `タコ <Lvl.5>`.
- Dragging one visually identical `フグ Lv.4` onto the other successfully produced one `タコ Lv.5`.
- The post-merge item panel updated to `タコ`, `Lvl.5` and the instruction to merge into `ウミガメ <Lvl.6>`.
- Producer cells in the bottom two rows visibly carried the electricity badge and must be excluded from normal merge candidates even when two producer sprites are visually identical.
- In the later live demo, `プロフェッショナルフィッシングギアボックス Lv.8`
  selected on the first tap, then emitted `エビ Lv.1` on each of the next two
  taps while energy decreased `159 -> 158 -> 157`.
- The two emitted `エビ Lv.1` items were merged successfully into `カニ Lv.2`.
- A single-frame exact edge matcher did not recognize those animated shrimp as
  identical even though both item panels reported the same name and level. See
  `knowledge.mbk` for the evidence and identity fallback rule.
- On the currently tested Lv.8 fishing-gear producer, the first tap selected the
  producer and showed its information panel; the second tap consumed one energy
  and emitted an item. This two-step interaction is confirmed for that live
  state and must not be generalized blindly to every producer type without
  observing the result.
- Two emitted shrimp were confirmed independently as `エビ / Lvl.1 / マージして
  カニ<Lvl.2>を取得`. Dragging one shrimp onto the other produced `カニ /
  Lvl.2`, and the panel updated to the next result `チョウザメ <Lvl.3>`.

## Current live calibration

This is evidence for the currently tested 1206x2622 screenshot/layout, not a
universal hard-coded board rectangle:

- Board bounds used successfully: `x=35, y=910, w=1135, h=1485`.
- Cell centers are derived from those bounds and the 7x9 shape; coordinates are
  not stored per cell.
- `visual_grid` with `inset=0.20` and `exact_threshold=0.95` produced exactly
  five strict same-looking groups in the pre-merge frame:
  - one normal-item pair (`r0c3`, `r0c4`) corresponding to the two fish;
  - four producer pairs in the bottom two rows.
- The same-looking groups were separated from the closest non-match by a wide
  margin on this frame. The weakest confirmed same-looking pair had minimum
  color/edge similarity about `0.967`; the strongest non-match was about
  `0.866`.
- The above threshold is a calibrated candidate threshold for this visual
  condition, not a semantic rule or a guarantee across other devices/themes.

## Operating implications

- Treat producer cells and merge-target cells as different roles.
- Do not propose a merge solely from semantic similarity or approximate appearance.
- Use the 7x9 grid to address cell centers rather than estimating free-form coordinates.
- A visual similarity score is only candidate evidence; the agent must not describe a merge as successful until the post-action board confirms the expected state transition.
- When identity is uncertain, tapping an item to inspect its displayed level/description is a valid disambiguation step.
- Before merging, remove producer cells from the candidate set even if visual
  comparison groups them as exact pairs.
- Animated producer glows can change pixels between screenshots without any
  user action. Cell-frame comparison must distinguish material target changes
  from ambient animation instead of treating every pixel delta as action impact.
- Animated normal items can also defeat a strict single-frame edge threshold.
  In the live shrimp pair, color similarity was about `0.961` but edge
  similarity only about `0.899`, so the current `0.95` color+edge exact test
  rejected a pair that was semantically and operationally proven identical.
  Treat strict single-frame matching as candidate evidence, not the final
  authority, when item sprites animate.
- The generic temporal-grid comparison has now been real-device verified on
  this board: a five-frame no-action sample classified all 63 cells as
  ambient-only, including the continuously glowing producer cells. Prefer this
  temporal evidence over a single before/after frame for action verification.
- Optimize for speed rather than per-action supervision. Merge mistakes are
  acceptable game errors: prefer bounded local multi-action batches, observe
  once after the batch/burst, then re-plan from the resulting board.
- Do not generalize `one action -> one fresh observation` into a universal
  gameplay rule. Use predictive play when confidence is high and the mistake is
  cheap/recoverable. Tighten the observe/action loop only for unknown screens,
  uncertain transitions, high-risk/irreversible actions, bridge recovery, or
  after a prediction has failed.
- Observation strategy is adaptive rather than fixed. A stable board normally
  needs one lightweight fresh frame; animation/transition ambiguity may justify
  two or more temporally separated frames. Learn the shortest useful settle or
  sample interval per animation/transition instead of hard-coding a global
  delay.
- Multi-frame sampling must be cost-aware. Prefer lightweight visual frames for
  temporal comparison, and do not duplicate expensive OCR/semantic analysis by
  default. Escalate to OCR, high-resolution crops, extra frames, or info panels
  only when their expected accuracy gain justifies the latency.
- When several viable action/observation methods exist, compare alternatives
  using observed speed, correctness, recoverability, and failure history rather
  than adopting the first workable method as a permanent rule. Preserve useful
  mistakes as negative evidence so later play becomes both faster and safer.
- Treat every rule, threshold, timing assumption, and architecture idea in this
  document as a revisable working hypothesis unless it is an explicit safety
  boundary or directly confirmed game invariant. Live play should challenge the
  current rules rather than merely execute them.
- When play suggests a better rule or design, preserve both the incumbent and
  the alternative long enough to compare them. Record the context, evidence,
  latency/accuracy tradeoff, recovery cost, and the condition under which each
  approach works or fails. Prefer contextual policies over replacing one global
  rule with another global rule prematurely.
- Use low-risk gameplay as an experiment surface. It is acceptable to try a
  plausible alternative execution/observation strategy, measure the outcome,
  and keep comparative evidence. Do not experiment with paid, irreversible, or
  account/security actions.
- Distinguish three kinds of conclusions: confirmed game facts, current best
  operating heuristics, and architecture/design hypotheses. Store evidence so a
  later AI can revisit heuristics/design without losing the observations that
  led to the current choice.
- When no merge is available, producer planning should use the learned producer
  output catalog, free-cell count, energy when readable, current item
  identities/levels, and predicted chain-merge opportunities to decide both
  which producer and how many emissions to request.
- Do not optimize for maximum merge count alone. Preserve/produce items needed
  by current orders, execute all immediately completable deliveries, then use
  remaining board capacity for merges/production that move inventory toward the
  outstanding order targets.
- However, confirmed duplicate items should still be compacted even when they
  are unrelated to current orders. The default is to reclaim board cells now
  and rebuild low-level chains later if a future customer asks for them.
- Treat customer refresh as a strategic escape hatch for extremely distant
  order sets, not as an automatic fixed-threshold action. Repeated refreshes are
  allowed, but ask the operator when the tradeoff is genuinely ambiguous.
- Bubble handling has higher urgency than normal merge/production planning
  because its reward can expire. If a bubble is visible, handle it before
  continuing the ordinary bounded play loop.
- Bubble priority is preemptive, not merely the next normal priority. If a
  bubble is visible while inspecting orders, learning an item family, or doing
  another reversible low-risk subtask, dismiss only what is necessary to
  reach the board and handle the bubble immediately; then resume the interrupted
  work. Do not let knowledge-gathering or order analysis consume the bubble's
  remaining lifetime.
- Observed failure case: a bubble was left visible while order/family-learning
  work continued and expired into a round lightning item. Treat this as negative
  evidence against doing OCR, semantic inspection, catalog learning, or other
  non-urgent analysis before bubble handling.
- A successful merge may also randomly create an additional green cash/banknote
  item. That extra object can consume the cell the merge would normally free, so
  unchanged `free_cells` is not by itself a merge failure or proof of a bubble.
  Compare before/after occupied cells and preserve newly-created objects as
  transition evidence.
- Human questions are asynchronous learning requests. Keep unanswered questions
  durable and replyable after the originating workflow continues or ends. Do not
  stop ordinary low-risk play just to wait for an answer; poll late answers at
  safe boundaries and promote reusable answers into Human Teaching.
- Do not treat a reduced producer count as a regression without checking whether
  the operator intentionally placed producers in storage.
- Prefer compacting green cash-stack special items when safe duplicate merges
  are available; they do not need to be preserved for ordinary customer orders.
- At zero energy, tap the plus icon next to the energy counter to open the
  `エネルギーを獲得` menu. The menu has passive regeneration, paid x50
  purchases, an immediate daily claim (observed as x25), and external task
  entries marked `移動`. Do not automatically spend cash/diamonds or place
  orders merely to obtain energy.
- Do not end the autonomous play loop merely because energy reaches zero.
  Continue whenever a safe/free recovery method remains. The actual stop
  condition is `energy == 0` and no usable recovery method remains. Prefer
  simple ad-watch recovery before external shopping/order tasks.
- Avoid repeatedly opening the info panel for already learned items. Use
  temporal cell continuity, learned item visuals, and known merge successors;
  temporarily raise whole-board resolution only when confidence or board-space
  pressure justifies it.
- The zero-energy recovery window scrolls. Before declaring recovery exhausted,
  scan the full scroll range. Browse/view tasks share a simple navigate -> wait
  required duration -> completion/return flow.
- When game energy reaches zero, do not end the learning session immediately.
  Tap the plus control beside the Merge Boss energy value and inspect every
  available recovery option. The common user-taught flow is option -> reward/ad
  transition -> wait the shown countdown -> tap the resulting check mark ->
  return to Merge Boss, but exceptions must be learned from the live UI rather
  than assumed.

## Still unknown

- A robust automatic board-rectangle detector for other devices/layouts.
- Exact producer badge detection rule.
- A general temporal/animation filter for producer glows and other animated cells.
- A generic animation-tolerant same-item verifier for moving item sprites; OCR
  name/level confirmation is currently a correct but expensive fallback.
- Complete merge progression and any special-item rules.
- Whether every visible producer's `i` information view exposes a stable,
  machine-readable list of possible outputs and whether the list includes
  probabilities/rarities or only item identities.
- Exact customer-card/order-strip bounds, scroll step/overlap strategy, and the
  most reliable machine-readable representation of requested item identity and
  level.
