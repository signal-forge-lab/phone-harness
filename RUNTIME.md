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

### `observe(force=False)`

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

Windows prefers WDA accessibility and falls back to PaddleOCR. A short-lived
snapshot may be reused inside one long-lived runtime. Fresh observations receive
a monotonically increasing process-local `observation_id`; cache hits retain the
same id.

### `act(actions, observation_id=None)`

Accepts a non-empty JSON array of at most 100 actions. The complete batch is
validated before the first mutation. Supported operations are:

- `tap_text`: `text`, optional `exact`, optional `index`
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

On iOS 26, batches made only of accessibility `tap_text`, raw `tap`, `drag`,
`type_text`, `swipe` and `scroll` are normalized and sent through one
pymobiledevice3 process and one WDA session. Before creating that session,
phone-harness asks WDA which application is currently active and attaches the
session to that bundle with app relaunch/termination explicitly disabled. This
preserves the exact screen that produced the `observation_id`; creating a plain
unattached WDA session can otherwise target the wrong application, while a
normal bundle session can relaunch/reset the foreground app. iOS 27+ keeps
native CoreDevice HID as the preferred input backend.

Successful actions report `backend` (`wda_batch`, `native_batch` or
`sequential`), `duration_ms`, `subprocess_count`, and the consumed observation
id when one was supplied.

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
- WDA readiness and safe pymobiledevice3 process/timing counters
- last observation id/source/element count/timing
- last action success/backend/count/timing/error code

It intentionally excludes screen text, OCR/accessibility contents, action
payloads, UDID, device name, RSD address, Apple identity, certificate/profile
data, and raw low-level error messages. The monitor should use `pid` plus
`updated_at` to distinguish a live runtime from a stale status file; no fragile
process-exit hook is required.

## Persistent Wi-Fi tunneld

`phone-harness` does not auto-elevate or secretly spawn a privileged tunneld.
Run one Wi-Fi-only tunneld in a dedicated elevated terminal and keep that
process alive for the automation session:

```powershell
.\.venv\Scripts\python.exe -m pymobiledevice3 remote tunneld `
  --no-usb --no-usbmux --no-mobdev2
```

Then run the MCP, Codex process or local Python process with:

```powershell
$env:PHONE_HARNESS_TRANSPORT = "wifi"
```

If an RSD/device operation fails, phone-harness clears its cached device and
transport metadata. After tunneld restores the tunnel, the next runtime call
re-discovers the device; the automation host does not need to be restarted.
