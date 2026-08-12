# Phone Runtime Contract

`PhoneRuntime` is the stable automation interface shared by future MCP hosting
and direct local execution. The current contract version is **1**.

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
])
print(result)
```

## JSON contract v1

Every successful response includes `"contract_version": 1`.

### `status()`

Returns connection readiness. Windows additionally returns `transport_mode`,
the selected `active_transport` when ready, and local `tunneld` reachability for
`wifi`/`auto` mode. `tunneld` status intentionally contains only reachability and
device count, not UDIDs or device names.

### `observe(force=False)`

Returns:

```json
{
  "contract_version": 1,
  "source": "accessibility",
  "elements": [],
  "cached": false
}
```

Windows prefers WDA accessibility and falls back to PaddleOCR. A short-lived
snapshot may be reused inside one long-lived runtime.

### `act(actions)`

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

All `tap_text` targets are resolved against the same pre-action observation.
If an action changes navigation or makes later semantic targets invalid, split
the workflow at a new `observe()` boundary instead of sending one speculative
batch.

On iOS 26, batches made only of accessibility `tap_text`, `type_text` and
`swipe` are sent through one pymobiledevice3 process and one WDA session. iOS
27+ keeps native CoreDevice HID as the preferred input backend.

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
