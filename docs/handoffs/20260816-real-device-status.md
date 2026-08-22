# Phone Harness Windows real-device handoff — 2026-08-16

This is the current restart/recovery handoff for the Windows iPhone path. Read
this before changing transport, WDA, MCP or Secure MCP Tunnel settings.

## Goal

The target path is:

```text
ChatGPT
  -> Secure MCP Tunnel
  -> phone-harness MCP on 127.0.0.1:17677
  -> long-lived PhoneRuntime
  -> pymobiledevice3 tunneld/RSD over Wi-Fi
  -> WDA accessibility/input on iOS 26.6
  -> real iPhone
```

The MCP tool surface is `phone_status`, `phone_observe`, `phone_act`.

## Verified environment

- Windows 11.
- Main phone-harness venv: Python 3.12.10.
- Dedicated tunneld venv: Python 3.14.4.
- iPhone: iOS 26.6.
- Main runtime transport: `PHONE_HARNESS_TRANSPORT=wifi`.
- Local tunneld HTTP status: `http://127.0.0.1:49151/`.
- Local MCP: `http://127.0.0.1:17677/mcp`.
- OAuth issuer is published through Tailscale Funnel at `/phone-auth/`.
- Secure MCP Tunnel profile: `phone-harness-mcp`.

Do not put UDID, serial, ECID, phone number, Apple credentials, OAuth owner
password or API keys into this document.

## Acceptance already completed

The following were verified with USB physically disconnected unless stated
otherwise:

1. usbmux reports no USB device.
2. Bonjour RemotePairing advertisement is visible after bootstrap.
3. Bonjour mobdev2 and Wi-Fi lockdownd are reachable.
4. Standard privileged `pymobiledevice3 remote tunneld` creates an RSD tunnel
   whose interface is the phone LAN address.
5. `PhoneRuntime.status()` returns `ready`, `transport_mode=wifi`,
   `active_transport=wifi` and tunneld device count 1.
6. WDA/XCTest starts over the Wi-Fi RSD tunnel after the physical XCTest
   passcode confirmation.
7. `PhoneRuntime.observe(force=True)` returns WDA accessibility elements.
8. Modern MCP 2026-07-28 real-device smoke passes for `phone_status`,
   `phone_observe` and `phone_act` with WDA batching.
9. HTTP OAuth smoke passes DCR + PKCE + Bearer + MCP tool call.
10. Calculator was opened without fixed coordinates and executed
    `12,345 x 1.08 / 3`; the observed result was `4,444.2`.
11. ChatGPT itself called `phone_status` through the recreated
    `phone-harness-mcp` App and Secure MCP Tunnel; result was
    `connection_state=ready`, `active_transport=wifi`.

## Correct Wi-Fi recovery sequence

Treat each layer independently. Do not call all failures simply "Wi-Fi is
broken".

### A. RemotePairing bootstrap

If RemotePairing discovery is missing, reconnect USB once, unlock/trust the
phone, then run from the Python 3.14 tunneld environment:

```powershell
& "C:\path\to\.venv-tunneld\Scripts\python.exe" `
  -m pymobiledevice3 lockdown remotepairing --pair
```

After success, unplug USB again. Confirm USB is absent and RemotePairing is
advertised:

```powershell
... -m pymobiledevice3 usbmux list
... -m pymobiledevice3 bonjour remotepairing
... -m pymobiledevice3 bonjour mobdev2
```

### B. tunneld / RSD

Run one elevated standard tunneld and leave it alive:

```powershell
& "C:\path\to\.venv-tunneld\Scripts\python.exe" `
  -m pymobiledevice3 remote tunneld
```

Then inspect:

```powershell
Invoke-RestMethod http://127.0.0.1:49151/
```

Expected: one device/tunnel and an interface matching the phone's LAN/Wi-Fi
address.

Do **not** use the previous command:

```text
remote tunneld --no-usb --wifi --no-usbmux --no-mobdev2 --protocol tcp
```

It was proven to start a server while suppressing discovery needed by this
Windows RemotePairing path, leaving the device listing empty.

### C. PhoneRuntime

Set the final runtime mode explicitly:

```powershell
$env:PHONE_HARNESS_TRANSPORT = "wifi"
```

`auto` is useful for diagnosis but is not the final acceptance configuration.
The final acceptance requires USB physically disconnected and
`active_transport=wifi`.

### D. WDA / XCTest

iOS 26.6 uses the signed phone-harness WDA runner. Starting/restarting XCTest
can display an iPhone passcode prompt labelled "Enable UI Automation". This is
a physical user step; never guess or enter the passcode from automation.

For direct diagnosis over an existing tunneld device, `pymobiledevice3
developer wda status --xctrunner <signed runner> --tunnel <device>` can start
the runner and should end with `ready: true`. Do not use the USB-default form
after USB has been unplugged.

### E. Local MCP + OAuth

The verified local MCP environment is conceptually:

```powershell
$env:PHONE_HARNESS_MCP_PUBLIC_BASE_URL = "http://127.0.0.1:17677/"
$env:PHONE_HARNESS_MCP_OAUTH_ISSUER_URL = "https://<tailscale-name>/phone-auth/"
$env:PHONE_HARNESS_MCP_OAUTH_RESOURCE_URL = "https://tunnel-service.gateway.unified-0.internal.api.openai.org/v1/mcp/<tunnel-id>"
$env:PHONE_HARNESS_MCP_PORT = "17677"
$env:PHONE_HARNESS_PYTHON = "C:\path\to\phone-harness\.venv\Scripts\python.exe"
$env:PHONE_HARNESS_TRANSPORT = "wifi"
$env:PHONE_HARNESS_MCP_OWNER_TOKEN = "<local owner password>"
```

Health and local protected-resource metadata must both work:

```powershell
Invoke-RestMethod http://127.0.0.1:17677/healthz
Invoke-RestMethod http://127.0.0.1:17677/.well-known/oauth-protected-resource/mcp
```

The second route must return JSON with local resource
`http://127.0.0.1:17677/mcp` and the public OAuth authorization server. Do not
replace this local PRM route with the internal Secure Tunnel PRM path.

### F. Secure MCP Tunnel

The local tunnel-client profile maps `main` to `http://127.0.0.1:17677/mcp`.
Its Harpoon section allows the public Tailscale OAuth issuer and plaintext HTTP
to the local MCP target.

`CONTROL_PLANE_API_KEY` is intentionally session-only. After opening a new
PowerShell, set it again before:

```powershell
tunnel-client doctor --profile-file <phone-harness-mcp.yaml> --explain
tunnel-client run    --profile-file <phone-harness-mcp.yaml>
```

Expected doctor checks:

```text
control_plane_api_key PASS
mcp_target            PASS
mcp_server_reachable  PASS (HTTP 401 is expected without Bearer auth)
oauth_metadata        PASS
RESULT ok
```

Keep `tunnel-client run` alive. In ChatGPT create/select the App with
Connection=`Tunnel`, tunnel=`phone-harness-mcp`, Authentication=`OAuth`.
Do not paste the internal tunnel service URL into the ordinary server-URL field.

## What was wrong or missing in the previous handoff

1. It treated the over-constrained `--no-*` tunneld command as the intended
   Wi-Fi command. That command was disproven by real-device recovery.
2. It recorded a historical `TUNNEL_PASS` without clearly distinguishing a
   RemotePairing TCP tunnel from the mobdev2/CoreDeviceTunnelProxy route. This
   led investigation toward the wrong transport layer.
3. It did not make the one-time `lockdown remotepairing --pair` USB bootstrap a
   first-class recovery step.
4. It did not separate the connection into RemotePairing, tunneld/RSD,
   PhoneRuntime, WDA/XCTest, local MCP/OAuth and Secure MCP Tunnel layers, so a
   failure at one layer was repeatedly mistaken for failure at another.
5. It did not state that `CONTROL_PLANE_API_KEY` was session-only and must be
   restored in a new PowerShell before `tunnel-client doctor/run`.
6. It did not state that local PRM
   `/.well-known/oauth-protected-resource/mcp` must stay available for
   `tunnel-client doctor`, even when the accepted OAuth audience is the Secure
   MCP Tunnel resource.
7. It retained/encouraged legacy `:8443/mcp` thinking even though the final
   Secure MCP Tunnel path uses Connection=`Tunnel`, not a directly published
   whole-MCP Funnel URL.
8. It did not document the physical XCTest passcode prompt as a normal WDA
   startup boundary that automation must stop for.
9. It did not record that `pymobiledevice3 developer wda run-xctrunner` had a
   hard-coded 30-second WDA readiness timeout. When physical XCTest approval
   took longer, the host task was cancelled even though the iPhone passcode
   sheet remained visible, making each later observation look like a new WDA
   startup failure.

## Current real-device state and remaining work

Do not repeat Calculator unless a regression requires it. Merge Boss baseline
acceptance has now been executed on the real device.

Read `docs/game-operations/aliexpress-merge-boss/rules.md` before acting. The
confirmed rules are intentionally app knowledge, not PhoneRuntime logic:

- board is 7 columns x 9 rows;
- upper-right electricity badge means producer, not merge target;
- producer tap emits merge-target items;
- if no merge-target items exist, tap a producer;
- item tap shows level/description above the board;
- merge identity means visually identical, not merely similar.

Real-device Merge Boss acceptance completed on 2026-08-16:

- Exact navigation rediscovered and verified:
  `AliExpress -> Account/MyAccount -> マージボス`.
- The board reached the 7x9 merge screen over disconnected-USB Wi-Fi transport.
- Current 1206x2622 layout calibration used
  `x=35, y=910, w=1135, h=1485` for the board rectangle.
- `visual_grid` at `inset=0.20`, `exact_threshold=0.95` identified exactly five
  same-looking groups: one normal fish pair plus four electricity-badged
  producer pairs.
- A fish tap showed `フグ / Lvl.4` and its next merge result.
- A real WDA drag merged the two visually identical Lv.4 fish into one
  `タコ / Lvl.5`; the next-result description updated to `ウミガメ <Lvl.6>`.
- Cell-level before/after comparison strongly detected the two manipulated top
  cells. It also detected unchanged producer cells because their glow animation
  changed between frames; this is a confirmed generic false-positive mode for
  single-frame visual differencing.
- Accessibility alone exposed only the app root plus one stray label on the
  game canvas, proving that a non-empty accessibility tree is not necessarily
  sufficient screen coverage.
- Later live-play acceptance exercised the no-pair producer rule. The selected
  `プロフェッショナルフィッシングギアボックス Lv.8` emitted `エビ Lv.1`
  twice on two energy-consuming taps (`159 -> 158 -> 157`). The two Shrimp were
  semantically confirmed as identical and successfully merged into `カニ Lv.2`.
  Durable producer/output mappings and merge progression are now maintained in
  `docs/game-operations/aliexpress-merge-boss/knowledge.mbk`; a new chat must read
  that file rather than relearn the mapping.

Current game state has advanced further. During the later live performance demo,
the tested Lv.8 fishing producer was selected, then tapped twice to emit two
shrimp, consuming energy from 159 to 157. The two shrimp were independently
confirmed as `エビ / Lvl.1` and were merged successfully into `カニ / Lvl.2`.
The resulting crab was at the former right-hand shrimp destination when last
observed. A new chat must observe the current board before planning another
merge; do not assume the old fish/octopus-only state.

### 2026-08-17 Merge Boss calibration/performance update

The current 1206x2622 layout is now live-calibrated (`calibration.json` has
`calibrated=true`). The app-specific perception path can read the complete
three-position customer strip and the 7x9 board without full-screen OCR.

Current customer scan is five logical customers. The final right-edge page is a
partial overlap page, so one Book Lv10 customer appears on two adjacent views;
scroll-position/customer-slot geometry de-duplicates that customer. Do **not**
de-duplicate by requested item because the user confirmed that two different
customers may request the exact same item simultaneously.

The current known order targets observed during calibration were Tennis Lv10,
Viewing Equipment Lv9, Fish Plush Lv8 + Viewing Equipment Lv4, Book Lv10, and
Fishing Supplies Lv6. Treat these as current-session state, not permanent game
rules; rescan after delivery or later human play.

The board producer badge detector now uses a 16-template animation-phase bank
with threshold 0.90. Across six saved real frames it detected exactly the eight
true producer cells with no false producer cells; validation minimum true score
was about 0.936 and non-producer maximum about 0.839.

Real local control executed:

- seven strict visual merge drags in one WDA batch; six materially freed cells;
- a later three-cycle turn: six Fishing Gear Box taps, then three merges, then
  one further merge; zero recoveries;
- after the latest session/capture/cache optimizations, two consecutive
  same-runtime 2-cycle workflow calls completed two merges each. The first call
  cost about 13.94 s including the initial customer scan; the second reused the
  order cache and cost about 5.36 s.

The game board has therefore changed substantially again. Always observe the
current board before the next action.

Performance facts that should not be re-investigated from scratch:

- warm in-process WDA screenshot is about 0.37-0.43 s;
- fresh visual-only process now promotes an already-running WDA automatically;
- WDA active-app session is reused across batches and dropped without replay on
  failure;
- cached-order board snapshots are about 1.5 s; the initial full customer scan
  remains the largest one-time cost at roughly 10.5 s;
- CoreDevice DisplayService HEVC/BGRA streaming is unavailable on this iOS 26.6
  device: the device returns CoreDevice error 9021, "Remote control requires
  iOS 27.0 or later on this device." Keep the still-PNG/WDA fallback and retry
  the stream path only on iOS 27+.

Remaining Merge Boss work is now primarily robustness rather than basic
feasibility: automatic board-bound detection, generic producer-badge evidence,
animation-tolerant action verification, and broader progression/special-item
coverage. Do not tap a producer merely for testing while normal merge targets
are available; the confirmed game rule says producer tapping is for obtaining
merge items when needed.

## Generic improvement backlog discovered during acceptance

These are cross-app/runtime improvements, not Merge Boss-specific shortcuts:

1. **Implemented 2026-08-16:** expose privacy-safe WDA lifecycle state through
   `wda_state` and `wda_runner_alive` instead of relying only on `wda_ready`.
2. **Implemented 2026-08-16:** avoid killing/recreating the XCUITest runner when
   physical confirmation exceeds the normal startup window. The managed
   pymobiledevice3 fork now supports configurable
   `developer wda run-xctrunner --startup-timeout` while retaining its
   30-second default. phone-harness starts its owned persistent runner with a
   600-second startup lifetime, keeps its own first readiness wait bounded at
   35 seconds, then uses a short readiness probe during the pending backoff.
3. **Implemented 2026-08-16:** sparse-accessibility hybrid observation. Windows
   continues using fast accessibility-only observation when at least three
   informative accessibility elements exist. If the tree is sparse, local OCR
   augments it and nearby duplicate text is suppressed. Mixed observations are
   reported as `source=hybrid`. This was real-device verified on Merge Boss:
   the hybrid result exposed `タコ`, `Lvl.5`, the next merge description and
   `販売`, none of which were available from the sparse accessibility tree.
4. **Implemented 2026-08-16:** generic OCR inference downscaling. Screenshots
   whose long edge exceeds 1600 px are resized to a temporary lossless PNG for
   PaddleOCR inference, then OCR boxes are mapped back through the existing
   screen-coordinate transform. On the Merge Boss frame, isolated warm OCR
   inference improved from 3.16 s at 2622 px to 1.97 s at 1600 px while keeping
   the key Japanese text. End-to-end warm hybrid observe improved from about
   4.62 s before this change to about 3.89 s after it. OCR cold start remains
   about 14-15 s because model initialization dominates the first call.
5. Expose safe layered diagnostics without identifiers: RemotePairing/tunneld
   readiness, RSD cached, WDA lifecycle, last backend and bounded timing.
6. **Implemented 2026-08-16:** generic region/crop observation. `PhoneRuntime`
   and MCP `phone_observe` accept optional absolute screen bounds `{x,y,w,h}`.
   Accessibility is filtered to that rectangle; sparse-tree OCR and optional
   image content are cropped to it; returned element coordinates remain in the
   full-screen coordinate system. Region is part of the observation cache scope,
   so changing the rectangle produces a fresh observation id.
   The source/build/tests are complete, but the currently connected ChatGPT App
   still has its previously scanned tool schema. To expose the new `region`
   field to ChatGPT, restart the rebuilt local MCP and rescan/reconnect the App.
7. Add generic grid/region coordinate helpers above the transport layer. App
   rules may supply a detected rectangle and row/column count, while runtime
   only performs coordinate transforms and stale-observation checks.
8. Keep visual object identity separate from OCR text similarity. A reusable
   image-region fingerprint/embedding candidate stage can support games,
   icon-heavy apps and photo/catalog UIs, but must verify state after actions.
9. **Implemented 2026-08-16:** animation-tolerant verification.
   - Windows `wait_stable` keeps the original strict near-identical signature
     check, then additionally accepts a bounded dynamic steady state when three
     consecutive frame deltas remain small and consistent. On the real Merge
     Boss screen, ambient full-screen signature deltas measured about
     `0.028-0.039`; the new bounded-motion path returned `true` instead of
     repeatedly exhausting the timeout.
   - `visual_grid.compare_grid_temporal()` now accepts at least two pre-action
     baseline frames plus one or more post-action frames. Each cell learns its
     own preexisting ambient-variation floor; a post-action cell is material
     only when even its best match to the baseline falls below that floor by a
     configurable margin. In a real-device five-frame no-action Merge Boss
     probe, all 63 cells were classified ambient-only and the animated producer
     cells were no longer false material changes.
   - This remains visual evidence, not app/game success semantics. Callers must
     still decide whether a material cell change is the expected outcome.
10. Add one documented layered recovery/check command or diagnostic report so a
   new chat can identify the failing layer before changing code or pairing
   records.

### Visual-performance work and second live acceptance

The visual pipeline was extended while a real device was not available, then
re-tested on the real Wi-Fi-connected iPhone. New MCP schema fields still
require a connector rescan/reconnect before ChatGPT can invoke them directly;
the underlying Python runtime has been exercised live.

- `ScreenFrame` / `FrameBroker` share one physical capture across OCR, region
  crops and returned images; full PNG passthrough avoids a no-op re-encode.
- The frame source is now pluggable. `BgraCaptureSource` can consume a future
  persistent decoded DisplayService frame without first generating a full PNG.
- `phone_observe`/`PhoneRuntime.observe` add `mode=visual` to skip text analysis
  entirely, plus `image_profile=full|glance|coarse|balanced|detail`.
- A visual observation can later be refined from the exact same retained frame
  using `reuse_observation_id`; the explicit reuse window is capped at 30
  seconds and does not pay another device capture.
- Observation timings now separate capture, semantic analysis and image
  preparation, and expose encoded image bytes.
- `tools/benchmark_visual_profiles.py` and `visual_quality.py` provide an
  offline quality/size/structure benchmark and grid candidate-recall benchmark.
- `analyze_grid_coarse_to_fine()` shortlists pairs on a compressed image and
  makes final identity decisions only on detailed crops.

Saved real Merge Boss frame evidence:

- 2.45 MB lossless board crop -> about 5.99 KB at 256 px JPEG q28;
- low-frequency similarity about 0.9947, edge similarity about 0.9807;
- top-1 coarse nearest-neighbor shortlist retained all five detailed exact
  reference pairs;
- production coarse-to-fine matching reproduced all five exact groups while
  reducing detailed comparisons from 1,953 to 55 (2.82%).
- even 128 px JPEG q10 (~1 KB) retained 5/5 top-1 reference pairs on this one
  board, but that is not enough evidence to make it the default.

Do **not** apply the exact-item threshold directly to compressed coarse images:
the saved frame proved that JPEG/resizing can add/remove exact groups at the
same threshold. Coarse images shortlist; detailed crops confirm.

Second live acceptance findings:

- Wi-Fi transport/tunneld/RSD were ready and WDA reached the ready state.
- Calculator was operated using current accessibility refs, not fixed
  coordinates. `12,345 x 1.08 / 3` produced and re-observed `4,444.2`.
- Mixing `wait_stable` into a WDA-action batch previously forced the whole
  request to sequential mode: 14 taps plus wait took about 46.6 s. Runtime now
  flushes WDA-batchable actions before `wait_stable`, keeping
  `backend=wda_batch`.
- WDA accessibility batch taps now prefer coordinates derived from the current
  observation. Five Calculator taps improved from ~3.71 s to ~3.16 s.
- A one-request W3C `/actions` multi-tap probe was rejected because the real
  device dropped an early tap despite only modest latency improvement.
- Merge Boss was reached via `MyAccount -> マージボス`, `プレイ` was entered,
  a producer was exercised, and a real merge was completed during the later
  benchmark.
- Pre-optimization Merge Boss board `glance` capture paid ~1.86-1.93 s for the
  CoreDevice PNG. Same-frame refinement itself was only ~0.1 s.
- The iOS 26.6 device explicitly rejects the CoreDevice DisplayService media
  stream because remote control requires iOS 27.0 or later. The HEVC/BGRA
  stream path is therefore not an iOS 26 solution.
- The already-running WDA `/screenshot` endpoint measured ~439/359/342 ms in
  three direct samples. Windows capture now uses that in-process endpoint when
  WDA is already ready, validates the PNG, and falls back to CoreDevice on any
  failure. It does not cold-start WDA just to take a visual screenshot.
- With that capture source active, the Merge Boss board `glance` measured about
  423 ms capture + 59 ms image preparation = 483 ms total, ~7.7 KB, zero
  subprocesses.
- Warm Merge Boss hybrid observe improved from ~7.45 s to ~2.11 s.
- `wait_stable` alone measured ~1.33 s with the warm WDA screenshot path.
- OCR should remain capped at a 1600 px long edge. Current-frame warm tests were
  ~1.52 s at 1600 and retained small labels such as `ATM` and `プレイ`; 1200,
  1000 and 800 px only saved a few hundred milliseconds while losing or
  corrupting those labels. Board-only cropping also produced almost no warm OCR
  speed gain (~2.31 s full vs ~2.26 s region).
- Live play-board visual glance measured ~0.46 s total at ~5.9 KB; capture was
  ~0.38-0.41 s and JPEG preparation ~0.04-0.06 s.
- Local 63-cell direct full matching was ~0.16-0.20 s, while coarse-to-fine was
  ~0.56-0.62 s despite reducing detailed comparisons to roughly 3%. Use the
  direct matcher when detail pixels are already local and cheap.
- The emitted shrimp pair proved a false-negative mode in the current exact
  matcher: color similarity ~0.961, edge similarity ~0.899. Both items were
  nevertheless confirmed as the same `エビ / Lvl.1` and merged successfully.
  The next generic visual priority is animation-tolerant same-item identity so
  OCR confirmation is not required for routine animated sprites.
- Producer tap timings were ~0.70-0.77 s; real drag merge ~1.50 s; post-merge
  hybrid verification ~2.31 s; live-board dynamic `wait_stable` ~2.63 s.

See `docs/performance/visual-pipeline-roadmap.md` for the current performance
architecture and live validation order.

### Merge Boss durable knowledge and fast-loop handoff

Before operating Merge Boss in a new chat, read both
`docs/game-operations/aliexpress-merge-boss/rules.md` and
`docs/game-operations/aliexpress-merge-boss/knowledge.mbk`. The latter is the
accumulating cross-chat record for user-confirmed rules, device-confirmed
producer/output mappings and merge progressions.

Current device-confirmed example: `プロフェッショナルフィッシングギアボックス
Lv.8` emitted `エビ Lv.1` twice; those Shrimp merged into `カニ Lv.2`. Do not
claim Shrimp is its exclusive output until additional observations support that.

To remove ChatGPT-per-tap latency, the bounded
workflow executes inside the persistent Python bridge. The old
`merge_boss_once` one-merge workflow remains diagnostic; the intended contract
for the next real-device session is:

```json
{
  "actions": [{
    "op": "run_workflow",
    "name": "merge_boss_turn",
    "max_cycles": 10,
    "max_merges": 24,
    "max_emissions": 20,
    "uncertain_burst_size": 6,
    "max_recoveries": 3
  }]
}
```

The generic planners can already reserve requested inventory, reverse known
merge chains, plan multiple dependency rounds, select/order producer bursts and
avoid double-spending inventory across deliveries. The local controller can
flatten predicted chain rounds into one WDA drag batch, execute producer bursts,
re-perceive locally and deliver completed orders without returning to ChatGPT.

The remaining blocker is only real-device perception calibration. Before
enabling `merge_boss_turn`, the next session must:

1. enumerate all ~5-6 customers in the horizontally-scrollable top order strip;
2. bind each customer's one/two requested item identity+level and any `完成`
   button to a stable local order id;
3. select each visible producer, open the upper-left `i` control in the
   description area, and verify whether its full possible-output list is
   machine-readable;
4. update both `knowledge.mbk` and `catalog.json`, marking
   `outputs_complete=true` only when the `i` view confirms the complete list;
5. select a normal board item, open the description panel's upper-left `i`, and
   verify that it exposes that item type/family's full `Lvl.1` through `Lvl.10`
   list with `Lvl.10` as max;
6. open an order item's upper-left `i` in the customer strip and verify it
   exposes the same item-family hint;
7. for every newly encountered family, extract all ten `level -> identity`
   entries and persist them via `catalog.json` / `record_item_family_hint()`.
   If an order target's type or level is initially unclear, resolve it from one
   of these hints before planning production instead of guessing from appearance;
8. calibrate energy and selected-producer state; and
9. verify board item identity+level bindings to 7x9 cells without paying
   full-screen OCR on every hot-loop iteration.

Offline perception work completed after this handoff section was first written:

- `MergeBossLivePerception` now enumerates overlapping order pages, deduplicates
  customers by stable order id, and reads the board last so its observation id
  remains authoritative after horizontal order scrolling;
- off-screen `完成` bindings retain the page where each customer was seen, so
  control can scroll back from the final scan page and attempt multiple
  deliveries inside one local batch;
- unknown item families use `ensure_item_family()`: known complete families are
  returned from `catalog.json` without reopening `i`; unknown families invoke
  the calibrated hint reader once, atomically persist the full level-1..10
  chain, then become cache hits on later turns;
- producer knowledge uses the same pattern through
  `ensure_producer_catalog()`. A producer `i` hint is merged with observed
  emissions; if a supposedly complete hint conflicts with a previously
  observed output, `outputs_complete` stays false rather than discarding
  evidence;
- generic `semantic_slots.py` parses calibrated OCR/accessibility slots and can
  use the slot's expected level when tiny level text is unreadable. Recognized
  level conflicts fail closed;
- `calibration.json` now persists known screen geometry and future order/hint
  slot calibration. The existing 1206x2622 board bounds are recorded, but the
  file remains `calibrated=false` until the missing live values are verified;
- merely setting `calibrated=true` is insufficient: live readiness also
  requires screen/board/order geometry, forward/backward order scrolling, board
  and order readers, and both item-family and producer `i` readers.

The generic horizontal-strip enumerator is already implemented in
`phone_harness.scroll_strip`; it handles overlapping pages, duplicate cards,
stagnation and a hard page bound. The next session only needs to calibrate the
Merge Boss-specific card key and scroll geometry.

Until these checks pass, `merge_boss_turn` deliberately returns
`real_device_perception_calibration_required` and performs zero phone actions.

The WDA pending-state implementation spans both managed worktrees. The main
Python 3.12 phone-harness environment currently imports `pymobiledevice3` from
the managed `pymobiledevice3-3df0881c` worktree, so the real-device path uses
the new timeout option directly. Regression checks after the change:

```text
phone-harness Python tests: 224/224 PASS
pymobiledevice3 WDA tests:   10/10 PASS
MCP tests:                   29/29 PASS
MCP typecheck/build:         PASS
```

## Repository/worktree state rule

Continue development in the dedicated phone-harness worktree. Do not push or
publish unless explicitly requested. Keep device identifiers, pair records,
credentials and OAuth secrets out of Git.
