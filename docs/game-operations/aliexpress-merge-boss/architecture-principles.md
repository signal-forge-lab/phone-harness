# Knowledge, Navigation, and Learning Architecture Principles

Status: working architecture constraints for the evidence-gathering phase.

This document intentionally freezes boundaries more strongly than algorithms.
The concrete schemas, thresholds, route scoring, frame intervals, and learning
promotion rules remain provisional until more real-device play has produced
representative evidence.

## Stable boundaries

- Phone Harness core remains domain-neutral. Merge Boss, AliExpress, 7x9 board,
  producers, customer orders, energy, bubbles, and game-specific screen meaning
  belong to task/domain knowledge rather than generic core semantics.
- A future caller should identify work logically (`task`, `app`, `domain`,
  `goal`) and receive the relevant knowledge bundle without knowing repository
  paths or relying on a particular AI model's chat memory.
- Runtime state, durable knowledge, and supporting evidence are separate.
- Human Teaching, real-device observations, successful actions, and failed or
  non-progressing actions can all feed learning.
- OCR is one observation signal, not a mandatory authority. Visual, structural,
  accessibility, temporal, and prior-transition evidence may also contribute.
- Unknown screens/states must be represented explicitly rather than guessed into
  a known state.
- Safety enforcement can be generic, while the meaning/risk of specific actions
  is supplied by the task/domain.
- Durable action identity is semantic/structural rather than an absolute pixel
  coordinate.  Device pixels are a runtime binding produced from the current
  frame geometry.  Reference calibration may contain pixels for one known
  device, but it must be scaled/resolved against the live screen before use.

## Runtime geometry binding

Phone Harness must assume that screen resolution, aspect ratio, safe areas and
app layout can differ across devices.  Prefer, in order, a currently recognized
semantic/accessibility element, a visual region/object, task-local geometry
such as board row/column, normalized/reference calibration geometry, and only
then the final device pixel used by `tap`/`drag`.

Merge Boss calibration is therefore a **reference-device profile**, not a
universal coordinate map.  Current implementation scales board/order regions,
hint slots and scroll actions from the reference `screen_size` to the captured
frame size before recognition/action binding.  More advanced anchor/safe-area
alignment remains an evidence-driven future improvement if simple frame scaling
proves insufficient on a materially different aspect ratio.

## Revised navigation/execution principle

`one consequential action -> one fresh observation` is **not** a universal
gameplay invariant. It is a conservative option for unknown screens,
high-uncertainty transitions, bridge reconciliation, or high-risk actions.

Normal gameplay should optimize for useful throughput. When actions are
predictable, local, and cheaply recoverable, bounded predictive action bursts are
preferred, followed by observation at a useful checkpoint. The appropriate
burst size is itself learnable and context-dependent.

Observation is also adaptive. Candidate methods include:

- one lightweight visual frame;
- two or more temporally separated lightweight frames;
- ROI-only temporal sampling;
- full-resolution visual inspection;
- OCR/semantic analysis;
- item/info-panel confirmation.

The system should learn which option gives the best accuracy/latency balance for
each recurring context. Fixed global delays should give way to learned
transition/animation settle intervals where evidence supports that change.

## Architecture is not absolute truth

Current architecture is a hypothesis constrained by known evidence, not a goal
in itself. Real play must be allowed to reveal that a boundary, abstraction,
state model, or learning representation is too conservative, too expensive, or
unnecessarily specialized.

When an alternative appears:

1. keep the current design/rule identifiable;
2. record the alternative as a candidate rather than silently replacing it;
3. record the context and evidence that motivated the candidate;
4. compare speed, correctness, observation cost, error/recovery cost, and
   robustness;
5. promote/generalize only when evidence supports doing so;
6. keep domain-specific discoveries out of generic core unless they genuinely
   generalize to other tasks/apps.

This applies equally to gameplay heuristics and architecture proposals.

## Evidence to preserve during play

For design-relevant events, preserve enough information to answer:

- What state/context were we in?
- What goal were we pursuing?
- Which strategy/observation method was selected?
- What alternative methods were plausible?
- What was expected to happen?
- What actually happened?
- How long/costly was the observation or action path?
- Was the miss cheap and recoverable, or expensive?
- Is the result a game fact, an operating heuristic, a negative example, or an
  architecture hypothesis?

The exact event/schema format is deliberately not frozen yet.

## End-to-end timing is part of the evidence

Performance evidence must not stop at tool-internal duration. Preserve the
elapsed time between meaningful phone actions as an end-to-end cadence metric.
Also preserve the measurable gap between one host/tool request completing and
the next request beginning. That outside-tool gap can include AI/planner
deliberation, orchestration, UI/transport idle time, and scheduling; it is an
observable latency measure, not an attempt to expose private chain-of-thought.

Large, obvious latency sources discovered during live play are eligible for the
same short fix -> play -> measure loop as correctness defects. Small/local
micro-optimizations should wait until the dominant bottlenecks are clear.

## Human questions are durable and asynchronous

AI-originated Human Teaching questions are not ephemeral modal prompts. A
question remains answerable after the workflow that created it has continued or
ended, and an operator may answer it later from conversation history. The
question-time frame is part of that durable question context: persist a bounded
preview copy when the question is posted and keep it viewable from history so a
late human answer can be grounded in the exact screen that caused the question.

Normal low-risk play must not block waiting for an answer. The runtime checks
for late answers at safe workflow boundaries while continuing useful work. When
an answer arrives, it can be promoted into the normal Human Teaching/learning
path as a nonblocking learning input; promotion itself must not pause gameplay.
Only a truly high-risk decision that cannot safely proceed without human
authority may justify waiting rather than deferring.

## Performance triage during the play/learn loop

Timing evidence from the existing trace/timeline is part of the learning loop,
not merely post-hoc profiling.  Do not stop normal play for micro-optimization,
but when a repeated phase is clearly dominating wall-clock time, treat it as a
high-priority improvement candidate alongside correctness failures.

The loop should therefore distinguish:

- **clear bottleneck:** repeatedly large latency or a large share of bounded-run
  wall time; eligible for a minimal in-loop improvement;
- **small/local cost:** record it, but defer detailed tuning until the behavior
  and schemas are better understood;
- **accuracy/latency tradeoff:** preserve competing methods and compare them in
  real play rather than assuming either the fastest or the most conservative
  method is universally correct.

Current trace evidence (2026-08-22) shows that local strategy selection is
negligible compared with board perception and phone action transport.  This
supports prioritizing fewer redundant full-board analyses, better bounded action
batching, lightweight urgent-object checks, and transport latency work before
micro-optimizing the planner itself.

## Human Teaching question policy

Human Teaching is part of the active learning loop, not only a passive inbox for
operator-initiated comments.  When the system encounters an ambiguity that is
material, reusable, and cheap for a present human to resolve, it should ask a
focused question rather than either guessing silently or merely logging an
opaque failure.

Do not ask for every low-confidence detail.  Prefer autonomous continuation when
confidence is already high and an error is cheap to recover.  Good question
triggers include:

- a repeated observation/action mismatch whose cause changes future policy;
- an unknown screen/object semantics that blocks or distorts planning;
- a conflict between existing learned knowledge and fresh live evidence;
- a state where a short human explanation can eliminate repeated expensive
  inspection or repeated mistakes.

Answers to learning-oriented questions should be promoted into the durable Human
Teaching inbox so the external AI can compare existing knowledge, persist the
appropriate reusable fact/artifact, verify it, and only then mark it handled.
This keeps `answered` distinct from `learned`.

## Design items intentionally left open

- manifest/bundle schema;
- state/transition/learning-record schemas;
- confidence and promotion thresholds;
- route/path scoring;
- negative-evidence decay and retry rules;
- unknown-state clustering;
- exact safety enums;
- visual-state representation;
- temporal sample count and intervals;
- strategy selection and experimentation policy.

These should be decided after additional GPT real-device play shows which data
is repeatedly useful.
