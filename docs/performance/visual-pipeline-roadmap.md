# Visual pipeline performance roadmap

The target is not maximum image quality. The target is the minimum visual
information needed to make the next correct decision, with escalation only when
a coarse representation is insufficient.

The intended analogy is human vision without glasses: large layout, motion,
object grouping and obviously-similar icons should remain usable while small
text and fine identity details may require a closer look.

## Core principles

1. Do not capture an image when accessibility already answers the question.
2. Do not run OCR when the task is visual rather than textual.
3. Capture one physical frame per observation and share it between OCR, image
   output, region crops, visual signatures and grid analysis.
4. Use a heavily resized/compressed image for candidate discovery.
5. Never let the compressed candidate image become the final authority for
   exact object identity. Confirm shortlisted candidates against the detailed
   source frame.
6. Increase resolution/quality only when evidence says the coarse stage cannot
   preserve candidate recall.
7. Measure each stage separately: `capture_ms`, `analysis_ms`,
   `image_prepare_ms`, image bytes and total observation duration.
8. Record performance-improvement candidates when real-device evidence exposes
   them instead of relying on later recollection.
9. When a reproducible defect blocks or materially degrades the active
   play/learning loop, fix and verify it in place before continuing rather than
   deferring every defect to a cleanup phase.
10. Preserve the fast normal path; expensive analysis belongs at explicit
    escalation points such as a full-board jam, not on every turn.

## Implemented architecture

`ScreenFrame` / `FrameBroker` represent one physical phone frame. Derived
region PNGs, OCR images and coarse JPEGs are cached per frame. If the capture
backend already produced a full PNG and no transform is requested, the original
file is reused instead of being decoded/re-encoded.

The frame source is pluggable. On Windows/iOS 26 the preferred warm capture is
the already-running in-process WDA `/screenshot` endpoint. If WDA is not already
ready, is unavailable through the in-process Wi-Fi path, or the returned PNG
cannot be validated, capture falls back to the original CoreDevice still-PNG
command. Visual-only observation deliberately does not start WDA just to gain a
faster screenshot because WDA cold-start latency would outweigh the benefit.

`BgraCaptureSource` can still consume raw decoded BGRA pixels without generating
a full-resolution PNG, preserving a future iOS 27+ persistent-stream seam.

`PhoneRuntime.observe(mode="visual", image_profile=...)` skips accessibility
and OCR. MCP `phone_observe` exposes the same mode. Candidate profiles:

| profile | long edge | format | quality |
|---|---:|---|---:|
| `glance` | 256 px | JPEG | 28 |
| `coarse` | 384 px | JPEG | 35 |
| `balanced` | 640 px | JPEG | 45 |
| `detail` | 960 px | JPEG | 60 |
| `full` | original | PNG | lossless |

`glance` is deliberately conservative until more diverse real screens are
benchmarked. Lower 128-192 px profiles are benchmark candidates, not runtime
defaults.

Visual refinement can reuse the exact same retained physical frame. Pass the
current visual `observation_id` as `reuse_observation_id` with a higher
`image_profile`; no new phone capture is performed. This explicit reuse may
outlive the short normal observation cache but is capped at 30 seconds to avoid
quietly refining a very old frame after possible human interaction.

`analyze_grid_coarse_to_fine()` uses the coarse image only to shortlist nearest
cell candidates. Final exact-pair decisions use normalized crops from the
detailed frame. JPEG artifacts therefore cannot become final identity evidence.

## Offline real-frame evidence (2026-08-16)

Saved Merge Boss real-device frame:

- full screen: 1206x2622, about 3.12 MB PNG;
- board crop lossless PNG: about 2.45 MB;
- board calibration: x=35, y=910, w=1135, h=1485, grid 9x7.

| encoding | bytes | lossless ratio | low-frequency sim | edge sim |
|---|---:|---:|---:|---:|
| 256 px JPEG q28 | ~5.99 KB | 0.24% | 0.9947 | 0.9807 |
| 384 px JPEG q35 | ~13.4 KB | 0.55% | 0.9967 | 0.9875 |
| 640 px JPEG q45 | ~35.0 KB | 1.43% | 0.9980 | 0.9919 |
| 960 px JPEG q60 | ~77.1 KB | 3.15% | 0.9987 | 0.9941 |

Applying the final `exact_threshold=0.95` directly to compressed images did
not preserve the correct exact groups. Compressed images are therefore not an
exact-identity authority.

Nearest-neighbor candidate recall was strong. On the same board, 256 px JPEG
q28 with each cell's top-1 nearest candidate preserved all five full-detail
reference pairs (5/5 recall); the candidate set was about 3.8% of the complete
pair space.

The production coarse-to-fine matcher reproduced all five full-detail groups
while reducing detailed comparisons from 1,953 possible pairs to 55 (2.82%).

Exploratory lower-quality runs also retained all five top-1 reference pairs:

- 128 px JPEG q10: ~1.0 KB;
- 160 px JPEG q15: ~1.8 KB;
- 192 px JPEG q20: ~3.0 KB.

That evidence comes from one board/layout only. Do not make those profiles the
runtime default until multiple apps and screen types confirm recall.

## Live-device evidence after reconnect (2026-08-16)

Real-device Wi-Fi acceptance was repeated after the offline work.

- transport: Wi-Fi, tunneld/RSD ready;
- Calculator semantic warm observation: about 0.43-0.48 s once WDA was warm;
- Calculator `12,345 x 1.08 / 3` was executed from accessibility-derived
  element refs and verified as `4,444.2`;
- a batch containing 14 taps plus `wait_stable` initially fell back to
  sequential execution and took about 46.6 s. Runtime batching now keeps WDA
  actions batched and flushes them before `wait_stable` instead;
- accessibility WDA batches now prefer coordinates from the current observation
  rather than re-finding each element by selector. These are dynamic observed
  coordinates, not app-specific hard-coded coordinates;
- five Calculator taps improved from about 3.71 s to about 3.16 s after that
  coordinate change;
- five taps plus `wait_stable` remained `backend=wda_batch` and measured about
  7.18 s before the faster screenshot source below was enabled;
- a W3C `/actions` one-request multi-tap experiment was rejected: it was only
  modestly faster and dropped an early tap on the real Calculator screen.

Merge Boss was reached through the documented path
`AliExpress -> MyAccount -> マージボス` and a full live play loop was exercised.

Before the WDA screenshot optimization, the known board region
`x=35,y=910,w=1135,h=1485` measured roughly:

- 7.7 KB `glance` image;
- 1.86-1.93 s physical CoreDevice still capture;
- about 95 ms `glance` preparation;
- about 3.0 s total visual observation;
- about 0.1 s same-frame `detail` refinement with no recapture.

After enabling the already-ready in-process WDA screenshot source, the same
real Merge Boss board measured:

- screenshot capture: about 423 ms;
- `glance` preparation: about 59 ms;
- `glance` bytes: about 7.7 KB;
- total visual observation: about 483 ms;
- subprocesses: 0.

Direct WDA `/screenshot` samples were approximately 439 ms, 359 ms and 342 ms
at native 1206x2622 resolution. The PNGs were larger than the CoreDevice ones
(about 4.8 MB in these samples), but the much lower capture latency dominates
because FrameBroker immediately derives the tiny consumer image.

Warm Merge Boss hybrid observation improved from about 7.45 s before this
capture change to about 2.11 s afterward. `wait_stable` alone measured about
1.33 s with the warm WDA screenshot path.

### Live Merge Boss play-loop benchmark

The actual `プレイ` flow was executed, including producer use and a real merge.

- overview `プレイ` OCR-derived tap: ~2.15 s (`sequential`, one subprocess);
- first board hybrid observe after entering play: ~2.24 s;
- board-only visual glance on the live play board: ~0.46 s total, ~5.9 KB;
- direct live board WDA capture within the local visual pipeline: ~0.38-0.41 s;
- `glance` JPEG preparation: ~0.04-0.06 s;
- one producer-grid tap: ~0.70-0.77 s (`wda_batch`, zero subprocesses);
- first tap on the tested Lv.8 fishing producer selected it; the following tap
  consumed one energy and emitted an item;
- two emission taps reduced energy from 159 to 157 and emitted two shrimp;
- tapping each shrimp for semantic disambiguation took ~0.66-0.70 s per tap;
- each hybrid semantic confirmation was ~2.39 s and proved both items were
  `エビ / Lvl.1` with the same next merge result;
- the real drag merge took ~1.50 s;
- post-merge hybrid verification took ~2.31 s and confirmed `カニ / Lvl.2`;
- live-board `wait_stable(timeout=6, interval=.3, settle=3)` took ~2.63 s.

The first coarse-to-fine implementation decoded/prepared two complete grids and
was slower than direct local comparison. It was replaced by an in-memory 8x8
coarse shortlist over the already-prepared 32x32 cell crops. On six saved real
Merge Boss frames, `candidate_neighbors=4` reproduced **100%** of the strict
`color>=0.95 && edge>=0.95` pairs while reducing detail comparisons to roughly
10-15% of the active-cell pair space. Measured local pair-ranking time improved
from about 149-227 ms to about 101-114 ms. Final merge authority still uses the
32x32 detail comparison; 8x8 is candidate generation only.

The live shrimp pair also exposed the next major correctness/performance issue.
The two items were operationally identical, but single-frame comparison measured
about color `0.961` / edge `0.899`, so the current `0.95` requirement rejected
them. Semantic tap+OCR confirmation worked but costs several seconds. The next
high-value generic improvement is an animation-tolerant same-object verifier
(multi-frame/phase-invariant descriptor or equivalent) so routine merge identity
does not need two OCR confirmations.

OCR remains a separate cost. On the current Merge Boss frame with a warm
PP-OCRv6 model:

| OCR long edge | inference | observed fidelity |
|---:|---:|---|
| 1600 px | ~1.52 s | kept `Lv.330`, description, `ATM`, `プレイ` |
| 1200 px | ~1.43 s | `ATM` degraded and `プレイ` disappeared |
| 1000 px | ~1.21 s | more small-text degradation |
| 800 px | ~1.14 s | additional small-text loss |

Keep the OCR default at 1600 px. Extremely low resolution is appropriate for
visual candidate discovery, not for small-text OCR. Cropping to the board alone
also did not materially reduce warm PaddleOCR time in the same-process test
(~2.31 s full screen vs ~2.26 s board region).

## Windows/iOS 26 capture path

The preferred warm Windows/iOS 26 path is now:

```text
already-ready WDA -> in-process /screenshot -> native PNG -> FrameBroker derivatives
```

Fallback remains:

```text
pymobiledevice3 developer core-device screen-capture screenshot
    -> device ScreenCaptureService
    -> full-resolution PNG
    -> FrameBroker derivatives
```

CoreDevice `ScreenCaptureService.capture_screenshot()` currently documents only
`requested_format="png"`; there is no device-side JPEG/quality/size control in
that still-image API. Resize/compression therefore occurs after the full PNG has
already crossed the device/host boundary.

## Persistent-stream boundary

pymobiledevice3 already contains a general-device DisplayService path:

```text
DisplayService video stream -> RTP/HEVC -> PyAV/libav -> BGRA
```

Windows already has PyAV 18.0.0 and the HEVC decoder can be created. The
existing `VncStreamServer` can retain latest decoded BGRA frames. However, a
real-device probe on the current iOS 26.6 phone failed at `startmediastream`
with the device error that remote control requires iOS 27.0 or later.

Therefore this path is not an iOS 26 performance option. Do not keep retrying
or refactor VNC around it on this device. Retain `BgraCaptureSource` for iOS
27+ evaluation later.

## Next live-device performance stages

1. Add a generic animation-tolerant same-object verifier and re-run the proven
   shrimp case without semantic OCR fallback.
2. Route OCR-derived observed coordinates through the in-process WDA coordinate
   batch path; the live `プレイ` tap still paid a sequential subprocess path.
3. Prefer direct local full-grid matching for small resident grids; reserve
   coarse-to-fine for expensive remote/AI/detail comparisons.
4. Verify `reuse_observation_id` escalation from `glance` to `detail`/`full`
   and confirm that `capture_ms` is not paid twice.
5. Verify coarse candidate recall on more than Merge Boss before
   lowering `glance` below 256 px/q28.
6. **Implemented 2026-08-17:** reuse the cached WDA active-app session for
   subsequent batches. A failed cached-session batch is never replayed; the
   session cache is dropped and the controller re-observes/re-plans from truth.
   Warm order-strip drag improved from about 1.80-1.95 s to about 1.52-1.56 s.
7. Keep CoreDevice still-PNG fallback and evaluate DisplayService/PyAV only on
   iOS 27+ where the OS permits that stream.

The final objective is human-like pacing, not one arbitrary millisecond target.
Simple visual actions should become capture/decision limited rather than
OCR/full-PNG limited, while uncertain cases deliberately pay for a higher
quality re-check.

## Merge Boss local fast-loop architecture

The live demo proved that per-step ChatGPT orchestration is itself a dominant
latency source. The evidence-oriented debug path repeatedly crossed
ChatGPT/Workbridge/MCP boundaries between tiny actions; that is not the intended
steady-state runtime.

The original `MergeBossFastWorkflow` proved that local execution can remove
ChatGPT-per-tap latency, but its one-merge limit is now a diagnostic legacy path.
The production architecture is an order-aware multi-turn controller:

1. enumerate/allocate already completable customer orders;
2. reserve inventory that already satisfies current demand;
3. reverse merge chains from outstanding requested items to lower-level inputs;
4. batch all dependency-safe drags and flatten predicted later rounds into the
   same sequential WDA batch when their target cells are deterministic;
5. if production is required, select the producer whose learned output catalog
   best reduces order deficit and emit a locally-bounded burst;
6. re-perceive locally and repeat without a remote-agent round trip;
7. deliver every newly-completable order before merging its requested inventory
   upward.

The reusable pieces (`merge_planner`, `order_planner`, `producer_planner`) know
nothing about AliExpress. Merge Boss-specific order/producer semantics live in
`merge_boss_strategy`, action batching/replanning in `merge_boss_control`, and
screen interpretation in `merge_boss_perception`.

The standalone `tools/merge_boss_fast_demo.py` is diagnostic only. A fresh
Python/WDA process measured about 5.4 s even when foreground preflight rejected
a non-AliExpress screen, so process-per-cycle execution is not the production
path.

The production path remains inside the existing three MCP tools. The new stable
workflow contract is:

```json
{
  "op": "run_workflow",
  "name": "merge_boss_turn",
  "max_cycles": 10,
  "max_merges": 24,
  "max_emissions": 20,
  "uncertain_burst_size": 6,
  "max_recoveries": 3,
  "order_rescan_every": 6
}
```

The Node MCP boundary forwards this to the already-running Python bridge as a
`workflow` request. The same `PhoneRuntime`, WDA runner, FrameBroker and OCR
model remain alive during the loop, removing ChatGPT micro-step round-trips and
Python process startup from the hot path.

Live perception is now calibrated for the current 1206x2622 Merge Boss layout.
`calibration.json` is active, the 3-position order strip is read without OCR,
the 7x9 board uses a multi-phase producer-badge template bank, and strict visual
merge candidates are produced locally. Unknown semantic identities remain
fail-closed/escalatable rather than being guessed.

The perception side is now fixture-driven rather than a single placeholder.
`calibration.json` stores app-specific geometry while generic semantic-slot
parsing and order-strip enumeration remain reusable. Unknown item/producers
open their `i` hints only until a complete catalog entry has been learned; hot
loops should therefore converge toward visual board capture + local planning
without repeated OCR/hint navigation.

`scroll_strip.enumerate_scroll_strip` is the generic bounded enumerator prepared
for the customer row. It deduplicates overlapping pages and stops after repeated
no-new-item pages or a hard page cap; Merge Boss perception only needs to supply
the live strip bounds, one scroll action, and a stable customer-card key.

Learned game facts are intentionally not hard-coded into the workflow. Durable
producer/output mappings and merge progressions remain in
`docs/game-operations/aliexpress-merge-boss/knowledge.mbk`; planner inputs are
mirrored in the adjacent `catalog.json`.

## 2026-08-17 local-loop performance update

The dominant costs shifted after the first live demo:

- fresh visual-only local process previously used the CoreDevice still-PNG path
  at about 2.0-2.2 s per capture because `_WDA_READY` had not yet been populated;
- `windows.capture()` now probes an already-running WDA by attempting the first
  screenshot directly (1 s bound, 20 s failure backoff) without starting WDA;
- fresh-process first capture is about 1.44 s because RSD/WDA connection setup
  remains, while subsequent captures in the same runtime are about 0.37-0.43 s;
- WDA active-app sessions are now reused across input batches. Warm customer-row
  drag improved from about 1.80-1.95 s to about 1.52-1.56 s; batch failure only
  invalidates the cached session and is not replayed automatically;
- forward customer-row drag is verified at 0.25 s gesture duration; backward
  requires the existing 0.45 s to reliably snap back to the previous page;
- the 3-page customer scan currently yields five logical customers. The final
  partial page overlaps the previous page by one customer; de-duplication uses
  scroll geometry/customer slots, **not order content**, because separate
  customers may legitimately request the same item;
- `MergeBossLivePerception` caches the order scan and re-reads the board every
  cycle. First full snapshot measured about 10.54 s; cached-order board-only
  snapshots measured about 1.57 s and 1.49 s;
- the long-lived `PhoneRuntime` now reuses the same Merge Boss perception object
  across separate `merge_boss_turn` workflow calls, so the order cache survives
  normal MCP calls until invalidated by delivery or an explicit rescan;
- same-runtime two-call real-device benchmark: first 2-cycle call (initial order
  scan included) took about 13.94 s; the second 2-cycle call reused order state
  and took about 5.36 s. Both completed two real strict merges with zero
  recovery, about 1.14-1.23 s per merge action.

Earlier in the same session a real three-cycle local turn executed producer
generation followed by 3 merges and then 1 additional merge without a remote
agent round-trip: 6 producer taps, 4 merges, zero recovery, 22.48 s before the
latest session/capture/cache optimizations. This demonstrates the intended
`generate -> multi-merge -> re-observe -> re-merge` local control flow.
