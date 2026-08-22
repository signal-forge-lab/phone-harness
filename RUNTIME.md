# Phone Runtime Contract

`PhoneRuntime` is the stable automation interface shared by future MCP hosting
and direct local execution. The current contract version is **2**.

## Hosting model

- **Dedicated MCP**: keep one `PhoneRuntime` instance for the lifetime of the MCP
  process and expose its three methods with a thin adapter.
- **Local Codex / local Python**: import `PhoneRuntime` and keep one instance in
  the same Python process for the whole task. No extra daemon is required.
- **Stateless CLI calls**: remain supported, but process-local observation,
  transport metadata and PaddleOCR model caches are intentionally lost between
  invocations.

Example local use:

```python
from phone_harness.runtime import PhoneRuntime

phone = PhoneRuntime()
print(phone.status())
screen = phone.observe()
result = phone.act([
    {"op": "tap_text", "text": "Search", "exact": True},
    {"op": "type_text", "text": "Bluetooth"},
], observation_id=screen["observation_id"])
print(result)
```

## JSON contract v2

Every successful response includes `"contract_version": 2`. Runtime calls also
report `duration_ms`; observation/action calls report the number of Windows
pymobiledevice3 subprocesses started during that call.

### `status()`

Returns connection readiness. Windows additionally returns `transport_mode`,
the selected `active_transport` when ready, and local `tunneld` reachability for
`wifi`/`auto` mode. `tunneld` status intentionally contains only reachability and
device count, not UDIDs or device names.

### `observe(force=False, region=None, mode="semantic", image_profile="full")`

Returns:

```json
{
  "contract_version": 2,
  "observation_id": 7,
  "source": "accessibility",
  "elements": [],
  "cached": false,
  "duration_ms": 12.4,
  "subprocess_count": 1
}
```

Windows prefers WDA accessibility. When the accessibility tree has enough
informative elements, observation stays accessibility-only and avoids OCR.
When it is sparse, local PaddleOCR augments the accessibility result and nearby
duplicate text is suppressed; mixed results report `source=hybrid`. Pure OCR
remains the fallback when WDA accessibility is unavailable. A short-lived
snapshot may be reused inside one long-lived runtime. Fresh observations receive
a monotonically increasing process-local `observation_id`; cache hits retain the
same id.

PaddleOCR inference uses a temporary lossless PNG whose long edge is capped at
1600 px when the original screenshot is larger. OCR coordinates are mapped back
to the full phone-screen coordinate system before being returned, so callers do
not need to know whether inference used a resized image.

An optional absolute screen-pixel region limits accessibility filtering and OCR
work:

```python
runtime.observe(region={"x": 100, "y": 500, "w": 800, "h": 900})
```

Returned element centers stay in the original full-screen coordinate system, so
the same observation can drive `tap`/`drag` without remapping. The normalized
region is included in the observation result and cache scope; a different
rectangle never reuses an incompatible cached observation id. The MCP adapter
uses the same region to crop `include_image=true` image content.

For image-only reasoning, use `mode="visual"`. This skips accessibility and OCR
entirely and returns an image derivative from the shared frame. `image_profile`
selects `full` PNG or progressively coarser JPEG profiles:

- `glance`: 256 px long edge, JPEG quality 28;
- `coarse`: 384 px, quality 35;
- `balanced`: 640 px, quality 45;
- `detail`: 960 px, quality 60.

`glance` is deliberately a candidate-discovery profile, not an exact-object
identity authority. If exact visual identity matters, shortlist candidates on
the compressed frame and confirm them on detailed crops.

To increase image quality without recapturing the phone, pass the current
visual `observation_id` as `reuse_observation_id` and request another
`image_profile`. The retained frame may be reused beyond the normal short cache
TTL, but only for 30 seconds; stale or mismatched ids/regions are rejected with
`STALE_OBSERVATION`.

One `ScreenFrame` is retained per image-backed observation so OCR, region crops
and returned images derive from the same physical capture. Observation timing
includes `capture_ms`, `analysis_ms`, `image_prepare_ms` and total
`duration_ms` where applicable. `image_bytes` records the encoded derivative
size. Status reports the privacy-safe frame source family (`still-png`). On
Windows/iOS 26, when the process already has a ready in-process WDA connection,
the still PNG is obtained from WDA `/screenshot`; this measured roughly
0.34-0.44 s on the accepted device versus roughly 1.9 s through the CoreDevice
screenshot command. A failed or invalid WDA screenshot falls back atomically to
CoreDevice. Visual-only observation does not start WDA merely to gain this
optimization.

The CoreDevice DisplayService RTP/HEVC path is not available on the accepted
iOS 26.6 device: the device rejects media-stream start because remote control
requires iOS 27.0 or later. The BGRA frame-source seam remains for iOS 27+
evaluation, not as an iOS 26 fallback.

`wait_stable()` supports both static and bounded-dynamic screens on Windows.
It first preserves the strict low-noise screen-signature check. If a screen has
a small continuously animated region, three consecutive low and similarly sized
frame deltas are accepted as a stable dynamic state instead of forcing a timeout.
Large or erratic transitions continue waiting.

For region/grid workflows that need post-action evidence, use
`visual_grid.compare_grid_temporal()` instead of one before/after frame when the
screen contains ambient animation. It learns each cell's natural baseline
variation from multiple pre-action frames, then reports only changes that exceed
that observed variation by the configured material margin.

For exact grid/object identity with a compressed overview, use
`visual_grid.analyze_grid_coarse_to_fine()`. The coarse image only selects
nearest-neighbor candidates; all final same-looking pair decisions use detailed
crops. This reduces detailed comparisons without lowering the final evidence
standard.

On iOS 26, physical XCTest authorization can outlive a normal automated
startup window. The managed pymobiledevice3 fork therefore allows the owned
persistent `run-xctrunner` process to use an extended startup timeout, while
PhoneRuntime keeps its own first readiness wait bounded and uses short probes
after entering a pending state. Privacy-safe status includes `wda_state` and
`wda_runner_alive`, allowing an automation host to distinguish transport
readiness from WDA readiness without exposing device identifiers or screen
content.

### `act(actions, observation_id=None)`

Accepts a non-empty JSON array of at most 100 actions. The complete batch is
validated before the first mutation. Supported operations are:

- `tap_text`: `text`, optional `exact`, optional `index`
- `tap_element`: `element_ref`
- `tap`: `x`, `y`
- `drag`: `x1`, `y1`, `x2`, `y2`, optional `duration`
- `type_text`: `text`
- `open_app`: `name`
- `home`
- `swipe`: `direction`, optional `distance`
- `scroll`: optional `amount`
- `press`: `combo`
- `wait_stable`: optional `timeout`, `interval`, `settle`

All `tap_text` targets are resolved against the same pre-action observation and
therefore require the current `observation_id` returned by `observe()`. Other
observe-derived actions should also pass that id; any supplied stale id is
rejected before the first mutation.

This is a runtime-generation guard, not a screenshot hash. It prevents reuse
after this runtime has invalidated an observation, but does not claim to detect
an unrelated human touching the phone between calls. A mandatory screen hash
would add another capture to the hot path, so it is deferred until real-device
latency data justifies it.

If an action changes navigation or makes later semantic targets invalid, split
the workflow at a new `observe()` boundary instead of sending one speculative
batch.

On iOS 26, accessibility `tap_text`/`tap_element`, raw `tap`, `drag`,
`type_text`, `swipe` and `scroll` are normalized into a WDA batch. A following
`wait_stable` no longer forces preceding device actions back to sequential
execution: the WDA segment is flushed first, then stability verification runs.
Before creating the WDA session,
phone-harness asks WDA which application is currently active and attaches the
session to that bundle with app relaunch/termination explicitly disabled. This
preserves the exact screen that produced the `observation_id`; creating a plain
unattached WDA session can otherwise target the wrong application, while a
normal bundle session can relaunch/reset the foreground app. iOS 27+ keeps
native CoreDevice HID as the preferred input backend.

For accessibility-derived taps, WDA batching prefers the center coordinates
already returned by the current observation and maps them into WDA points. This
avoids re-running an element search for every tap while retaining stale-
observation protection; these are observation-derived dynamic coordinates, not
hard-coded app coordinates. Selector lookup remains the fallback when usable
coordinates are absent.

Successful actions report `backend` (`wda_batch`, `native_batch` or
`sequential`), `duration_ms`, `subprocess_count`, and the consumed observation
id when one was supplied.

### Bounded local workflows through `phone_act`

App-specific multi-step policy stays outside the primitive action model, but a
named bounded workflow may execute inside the same long-lived Python runtime to
avoid a ChatGPT round-trip between every small observation/action.

`merge_boss_once` remains as the original conservative diagnostic workflow.
The order-aware production workflow is `merge_boss_turn`, also exposed as one
`run_workflow` action in `phone_act`. Its contract already accepts local budgets
for cycles, merges and producer emissions so one persistent-runtime call can
perform multiple dependency-ordered merge drags, producer bursts and deliveries
without returning to ChatGPT between small actions. A recovery budget allows
low-risk game batches with uncertain/partial outcomes to re-observe and re-plan
locally instead of blindly replaying the same batch.

The generic planning pieces are separate from Merge Boss UI semantics:

- `merge_planner.py`: deterministic pair-merge rounds and chain prediction;
- `order_planner.py`: demand reservation, reverse merge requirements, useful
  order-directed merge planning and completable-order allocation;
- `producer_planner.py`: output selection/burst sizing against order demand and
  reachable merge gain;
- `scroll_strip.py`: generic bounded enumeration of overlapping scroll pages;
- `workflows/merge_boss_strategy.py`: Merge Boss order/producer semantics over
  those generic planners;
- `workflows/merge_boss_control.py`: convert one strategy phase into bounded
  PhoneRuntime action batches and immediately re-plan locally.

Real-device perception for the horizontally-scrollable customer strip and
producer `i` information view is intentionally fail-closed until calibrated.
`merge_boss_turn` therefore currently returns
`real_device_perception_calibration_required` with zero actions. Its MCP schema
is already stable; after calibration only Python perception code needs to change.

Game learning is not stored in generic planner constants. Durable user/device
knowledge belongs under `docs/game-operations/`; Merge Boss additionally keeps
a machine-readable `catalog.json` for learned merge transitions and producer
output catalogs so another chat/runtime can reuse them without embedding them in
the generic runtime.

## Error contract

Runtime failures raise `PhoneRuntimeError`. Local Python/Codex can inspect the
exception directly; a future MCP adapter can serialize `error.to_dict()` rather
than creating a second error model.

The machine-readable fields are:

- `code`: `INVALID_REQUEST`, `STALE_OBSERVATION`, `TARGET_NOT_FOUND`,
  `TARGET_AMBIGUOUS`, `OBSERVE_FAILED`, `STATUS_FAILED`, or `ACTION_FAILED`
- `retryable`: whether refreshing state then retrying may be reasonable
- `phase`: `preflight`, `observe`, `status`, or `execute`
- `action_index`: the failing sequential action when known
- `completed_actions`: how many sequential actions completed when known

`ACTION_FAILED` is not automatically retryable because the lower layer may have
executed part of a batch before reporting failure. Re-observe before deciding
what to do next. Runtime-generated execution/probe failures expose a generic
external message; the original low-level exception remains available as the
Python exception cause for local debugging instead of being serialized by a
future MCP.

## Monitor status surface

Every status/observe/action event atomically refreshes a small JSON status file
under the OS temporary `phone-harness` directory. A future independent monitor
can read this file without sharing memory with the MCP/Codex process.

Monitor schema version **1** exposes only operational metadata:

- runtime id, PID, start/update timestamps and process state
- current operation kind/phase/backend/action count while work is in progress
- counters for status/observe/action/error calls
- last known connection health, transport mode and tunneld reachability
- WDA readiness/lifecycle (`wda_ready`, `wda_state`, `wda_runner_alive`) and
  safe pymobiledevice3 process/timing counters
- last observation id/source/element count/timing
- last action success/backend/count/timing/error code

It intentionally excludes screen text, OCR/accessibility contents, action
payloads, UDID, device name, RSD address, Apple identity, certificate/profile
data, and raw low-level error messages. The monitor should use `pid` plus
`updated_at` to distinguish a live runtime from a stale status file; no fragile
process-exit hook is required.

## Persistent Wi-Fi tunneld

`phone-harness` does not auto-elevate or secretly spawn a privileged tunneld.
Run one standard tunneld in a dedicated elevated terminal and keep that process
alive for the automation session:

```powershell
.\.venv-tunneld\Scripts\python.exe -m pymobiledevice3 remote tunneld
```

Do not disable the RemotePairing/usbmux/mobdev2 monitors merely to force a
Wi-Fi phone-harness session. On the verified Windows/iOS 26.6 path,
`--no-usb --wifi --no-usbmux --no-mobdev2 --protocol tcp` can leave tunneld
running but unable to discover the already-paired Wi-Fi device, producing an
empty `{}` device listing. Keep tunneld discovery broad and enforce Wi-Fi at
the phone-harness layer with `PHONE_HARNESS_TRANSPORT=wifi`.

If RemotePairing advertisements disappear after a long gap or pairing state
changes, reconnect USB once, unlock/trust the phone, and refresh the
RemotePairing bootstrap:

```powershell
.\.venv-tunneld\Scripts\python.exe -m pymobiledevice3 lockdown remotepairing --pair
```

After it succeeds, unplug USB again. A healthy Wi-Fi-only acceptance should
show zero usbmux devices, a RemotePairing Bonjour service, and a non-empty
`http://127.0.0.1:49151/` tunneld listing whose interface is the phone's LAN
address.

On Windows this dedicated `.venv-tunneld` uses Python 3.14, while the main
phone-harness runtime remains on the verified Python 3.12 environment. Install
the same pymobiledevice3 source/revision into both environments so discovery,
RSD, WDA and tunnel behavior stay aligned.

Then run the MCP, Codex process or local Python process with:

```powershell
$env:PHONE_HARNESS_TRANSPORT = "wifi"
```

If an RSD/device operation fails, phone-harness clears its cached device and
transport metadata. After tunneld restores the tunnel, the next runtime call
re-discovers the device; the automation host does not need to be restarted.
