---
name: phone-harness
description: "Control the user's real iPhone from macOS or Windows: read the screen with local OCR, open apps, tap, type, swipe, and verify results."
---

# phone-harness

Direct iPhone control with a screenshot/OCR/action/verification loop. macOS uses
iPhone Mirroring + Vision OCR + CGEvents. Windows uses `pymobiledevice3`
CoreDevice + PaddleOCR PP-OCRv6 + Universal HID. For task-specific edits, use
`agent-workspace/agent_helpers.py`. For setup or transport problems, read
`install.md`.

The Windows backend is currently **implementation-complete but pending
real-device acceptance**. Do not represent Windows tap/scroll/type/open-app
behavior as verified until the physical-device acceptance suite has passed.

## When Not to Use

If the task is doable on the Mac or the web — a website, an API, an app with a
web equivalent — do it there and leave the phone alone. Use phone-harness only
when the task genuinely needs the phone: iOS-only apps, things tied to the
user's phone number or 2FA, testing how something looks on the phone.

## Usage

Prefer `-c` for short cross-platform actions:

```bash
phone-harness -c "print(screen_info())"
phone-harness -c "open_app('Notes'); print([o['text'] for o in ocr()][:10])"
```

On Unix shells, heredocs are also available for multi-line commands:

```bash
phone-harness <<'PY'
print(screen_info())
PY
```

- Invoke as `phone-harness`. Use `-c` for portable one-liners; use stdin or a
  Unix heredoc when a task needs multiple lines.
- Helpers are pre-imported. macOS coordinates are global screen points;
  Windows coordinates are screenshot pixels. Prefer OCR-derived coordinates so
  this distinction stays internal.
- On macOS, input helpers focus iPhone Mirroring automatically. On Windows,
  input goes directly through CoreDevice.

## Screen Workflow

- Prefer `ocr()` over eyeballing screenshots: every visible string comes back
  with a tap-ready center point — `[{text, confidence, x, y, w, h, ...}]`.
  Filter in Python before printing.
- Tap by label: `tap_text("Weather")`. On failure it raises with what IS
  visible. Multiple matches are rejected unless you deliberately pass an
  explicit `index`; refine the query instead of guessing.
- Icons without labels: `screenshot()`, view the image, compute the point
  and call `tap(x, y)`. On Windows, screenshot pixels are already the public
  tap coordinate space. On macOS, convert image pixels to global screen points
  using the capture/window scale and window origin from `screen_info()`.
- **Verify after every action**: `wait_stable()` then `ocr()`/`screenshot()`.
  There is no DOM to assert against; the capture is the ground truth.
- Navigation: `home()`, `open_app("Notes")`, `swipe("up")`, `scroll()`,
  `type_text("...")`, `long_press(x, y)`. `press("return")` and other raw key
  combos are macOS-only in the MVP.
- Windows MVP: `type_text()` supports printable ASCII; `app_switcher()` and
  arbitrary key combos are not supported. `open_app()` resolves an installed
  app name to its bundle identifier and launches it through developer services.
- **Scrolling a list**: use `scroll_collect(extract, key=...)` to walk a list
  to its true end, de-duping as it goes — it returns `{items, stop, scrolls}`
  where `stop` is `'reached-end'` or `'max-scrolls'`. Use `scroll_until(done)`
  to stop when a predicate on the visible OCR is met. Both decide "done" from
  whether the **screen actually moved**, not from whether your parser found
  new rows — a dense screen or a missed OCR line will not end the scroll
  early. Each step settles first so lazy-loaded content arrives before the
  movement check. `scroll_screen()` is the single-step primitive if you need
  it. macOS uses wheel scrolling; the Windows backend maps the same helper to
  a CoreDevice touch drag and must be judged by real-device behavior.
- Raw Quartz is a macOS-only escape hatch. Do not reach around the Windows
  backend with ad-hoc destructive `pymobiledevice3` commands.

## OCR-first, vision only as fallback

Do not send every screenshot to a vision model. Normal flow is:

`screenshot → local OCR → target coordinate → action → local verification`.

Use a vision-capable model only when OCR cannot identify an icon, the screen is
visually ambiguous, or OCR and the visible state disagree. Crop to the relevant
region when practical.

## Consent

This is the user's real phone. Stop and ask before anything outward-facing or
hard to reverse: sending a message, posting, purchasing, deleting, changing
settings. Navigating and reading for the user's own task is fine, but don't
linger in personal content (Messages, Photos, Mail) beyond what the task needs.

## Connection is the user's job

The harness never bypasses physical trust/lock/developer prompts. On Windows,
USB connection, unlock, Trust approval and Developer Mode confirmation can
require the user. On macOS, connecting/resuming iPhone Mirroring can require
opening the app and locking the physical phone.

`connection_state()` reports the host-specific state. Windows uses
`ready` / `no-device` / `ambiguous-device` / `transport-unavailable`; macOS keeps
`ready` / `blocked` / `no-window` / `not-running`. When a physical action is
required:

- **STOP and relay the exact physical action. Ask the user to do it themselves.**
- **Never** tap `Connect` / `Continue`, and **never** loop-poll waiting for the
  connection. Tapping Connect while the phone is unlocked does nothing, and
  polling just burns time — the only fix is the user locking/connecting the
  phone. Retry once *after they confirm they've done it*, not before.

## Gotchas

- **macOS: unfocused input is swallowed silently.** The window must be frontmost;
  helpers call `activate()` but if a click steals focus mid-task, re-activate.
- **macOS: the window is a video stream.** macOS accessibility sees nothing inside
  it; AppleScript `click at` fails silently. Only HID-level CGEvents work.
- **macOS: the window moves.** Never cache coordinates across calls; `ocr()` and
  `swipe()` re-query bounds every time.
- **macOS: unlocking the physical phone pauses the session** ("iPhone in Use"). Do not
  tap through the resume screen — stop and ask the user to lock/connect the
  phone (see "Connection is the user's job").
- **Windows: coordinates come from the latest screenshot.** Capture/OCR before
  coordinate-based input; after a rotation or major display change, do not
  reuse coordinates from an older screenshot.
- **`type_text` needs an iOS text field focused first** — tap the field, wait
  for the keyboard, then type.
- **macOS Home-Screen labels are not tap targets.** `tap_text("Weather")` hits
  the label and nothing happens; the icon is ~35 Mirroring points above it.
  `tap_icon("Weather")` is therefore a **macOS-only** calibrated helper.
  Windows must use `open_app("Weather")` until a screenshot-pixel icon offset
  is measured on real hardware. `tap_text` remains appropriate for in-app text
  controls on both hosts.
- Mouse taps map to touches 1:1, but there is no multi-touch: no pinch, no
  two-finger gestures.
