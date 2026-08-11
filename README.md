# Phone Harness 📱

Connect an LLM directly to your real iPhone with a thin, editable harness.
No jailbreak and no WebDriverAgent.

The harness now has two host transports:

- **macOS** — iPhone Mirroring + Apple Vision OCR + CGEvents.
- **Windows** — `pymobiledevice3` CoreDevice + local PaddleOCR PP-OCRv6 +
  Universal HID.

> **Windows development status:** the backend, packaging, local OCR and
> device-free tests are implemented. Real-device acceptance is still required
> before the Windows path should be treated as fully verified.

On macOS the iPhone Mirroring window is the transport. On Windows the harness
talks to the real phone over Apple's USB/CoreDevice services through
`pymobiledevice3`. In both cases the agent works from screenshots, local OCR and
tap-ready coordinates; screenshots only need a vision model when OCR cannot
describe an icon or ambiguous visual state.

```
  ● agent: wants to open Weather
  │
  ● ocr() → "Weather" at (400, 468)
  │
  ● tap(400, 468) → wait_stable() → ocr() confirms the forecast
  ✓ done
```

**Your phone, driven by an agent.**

## Setup

### Windows

Use Python 3.12 and install the Windows prerequisites described in
[`install.md`](install.md). The minimal verified environment is:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip setuptools wheel
.venv\Scripts\python.exe -m pip install pymobiledevice3==10.7.1
.venv\Scripts\python.exe -m pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
.venv\Scripts\python.exe -m pip install paddleocr==3.7.0
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\phone-harness.exe --doctor
```

The Windows backend is verified against the `pymobiledevice3` **v10.7.1** tag.
It deliberately keeps that GPL-3.0-or-later project as an external dependency
instead of copying or vendoring its source into this MIT repository.
That process boundary is an engineering boundary, not a legal conclusion;
review the final GPL distribution obligations before publishing a bundled
installer, binary distribution, or commercial package.

### macOS

Paste into Claude Code or Codex:

```text
Set up phone-harness for me. Clone https://github.com/ShawnPana/phone-harness
into ~/.phone-harness (its canonical home) and read `install.md` first to install
it and connect it to my real iPhone
through the macOS iPhone Mirroring app — install it so `phone-harness` is a
command on my PATH, and register it as an agent skill named phone-harness using
`phone-harness skill` as the body, so you reach for it automatically. Then read
`SKILL.md` for normal usage, and always read `src/phone_harness/helpers.py`
because that is where the functions are. Whenever you capture or verify the
screen, activate the iPhone Mirroring window so I can see what you're doing on
the phone.

Setup needs two things only I can do: pairing iPhone Mirroring with my phone
once, and granting the terminal Accessibility + Screen Recording in System
Settings — walk me through those and wait for me. Verify with
`./phone-harness --doctor`.

After it's installed, as a quick demo that interaction works, go to my Home
Screen and — if the phone is connected and unlocked — ask me whether you should
open the Weather app as a harmless test; only open it if I say yes. If the
session is paused or the phone is locked, just tell me the doctor status instead.
```

The agent will walk you through the two things only you can do: **pairing**
iPhone Mirroring with your phone once (the pairing prompts need the physical
phone), and granting the terminal **Accessibility** (taps & keystrokes) and
**Screen Recording** (seeing the phone) in System Settings → Privacy &
Security. Screen Recording takes effect after the terminal restarts;
Accessibility is immediate. Then `./phone-harness --doctor` verifies the whole
chain.

These are the permissions currently known to be required. A fresh machine may
prompt for more the first time an action runs — if `--doctor` passes but taps or
capture silently do nothing, watch for a macOS permission prompt. See
[install.md](install.md) for details.

## Why this works

On **macOS**, iPhone Mirroring (Sequoia+) renders the phone as a Mac window and
forwards real mouse and keyboard input as touches.

On **Windows**, `pymobiledevice3` exposes CoreDevice screen capture, display
information and named HID buttons. iOS 27+ also supports CoreDevice Universal
HID remote input. On iOS 26, phone-harness automatically uses a provisioned
WebDriverAgent (WDA) fallback for touch/drag/type and keeps that XCTest runner
alive instead of restarting it for every action.

That gives an agent the same three primitives on either host:

- **See** — capture the phone and OCR locally. macOS uses Apple Vision; Windows
  uses PaddleOCR PP-OCRv6 medium. Every visible string has a tap-ready center.
- **Act** — macOS uses CGEvents. Windows uses CoreDevice Universal HID on iOS
  27+ and the provisioned WDA fallback on iOS 26. Screenshot pixels are
  converted to the backend's coordinate system at the transport boundary.
- **Verify** — screenshot again. No DOM means the capture is the ground truth.

macOS-specific things that do NOT work, learned the hard way: AppleScript `click at` (silently
ignored — the window is a video stream with no accessibility tree), unicode key
payloads (mirroring forwards raw HID keycodes, so typing must use keycodes), a
slow touch-drag (barely moves an iOS list — use wheel scroll for lists, a fast
flick for pages), and input while the window isn't frontmost (swallowed).

## Usage

Cross-platform one-liners:

```bash
phone-harness -c "print(screen_info())"
phone-harness -c "open_app('Notes'); print([o['text'] for o in ocr()][:10])"
```

On Unix shells, heredocs remain convenient for multi-line scripts:

```bash
./phone-harness <<'PY'
open_app("Notes")
tap_text("New Note")
type_text("hello from the harness")
print([o["text"] for o in ocr()][:10])
PY
```

Day-to-day workflow lives in [SKILL.md](SKILL.md), which [install.md](install.md)
registers as an agent skill (`phone-harness skill` prints the body) so the agent
reaches for it on its own.

## Architecture

- `SKILL.md` — day-to-day usage (the agent-facing product surface)
- `install.md` — permissions bootstrap and troubleshooting
- `src/phone_harness/` — protected core:
  - `mirror.py` — window discovery, focus, capture, CGEvent input
  - `ocr.py` — Vision-framework text recognition → screen-point boxes
  - `windows.py` — Windows CoreDevice screenshot/input transport
  - `paddle_ocr.py` — PP-OCRv6 local OCR → screenshot-pixel boxes
  - `helpers.py` — the primitives pre-imported into scripts
  - `admin.py` — `--doctor`
  - `run.py` — the CLI (`exec` stdin with helpers in scope)
- `agent-workspace/agent_helpers.py` — helper code the agent edits; auto-loaded
  into every script's namespace

There is no phone-harness daemon. macOS window bounds/captures are re-queried
per call. Windows keeps only the selected USB device and latest capture size in
the current process; a new `phone-harness` invocation starts fresh.

## Development

From a checkout, use `./phone-harness` to run the working tree directly:

```bash
./phone-harness <<'PY'
print(screen_info())
PY
```

## Limits

- One phone, one active session.
- Windows capture/OCR targets iOS 17.4+ over USB; earlier iOS 17 releases can
  require a privileged `tunneld` path.
- Native Windows CoreDevice touchscreen and virtual-keyboard input requires iOS
  27.0+ on the tested/current service. On iOS 26.6 the harness intentionally
  rejects `tap`/`drag`/scroll/`type_text` instead of reporting a false success.
- When native CoreDevice typing is available, it currently supports printable
  ASCII only.
- `app_switcher()` is not part of the Windows MVP; `home()` and `open_app()` are.
- On macOS, unlocking the physical phone pauses mirroring.
- No multi-touch (no pinch), no camera/Face ID flows, DRM video renders black.
- OCR sees text, not semantics — unlabeled icons need a screenshot + a
  vision-capable model.
