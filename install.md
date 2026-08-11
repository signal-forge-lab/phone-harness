# phone-harness install

Use once. For phone work, read `SKILL.md`.

## Requirements

### Windows

- Windows 11.
- Python 3.12 is the verified project interpreter.
- Apple device support. Install **Apple Devices** and, when needed for the
  usbmux/driver layer, the Microsoft Store version of **iTunes**.
- `pymobiledevice3` for USB/CoreDevice and HID transport.
- PaddlePaddle CPU + PaddleOCR 3.7 for local PP-OCRv6 OCR.

Verified Python environment:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip setuptools wheel
.venv\Scripts\python.exe -m pip install pymobiledevice3==10.7.1
.venv\Scripts\python.exe -m pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
.venv\Scripts\python.exe -m pip install paddleocr==3.7.0
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m pip check
.venv\Scripts\phone-harness.exe --doctor
```

`pymobiledevice3` is installed explicitly and remains an external
GPL-3.0-or-later dependency; its source is not copied or vendored into this
repository. The Windows implementation is verified against its **v10.7.1**
tag. The final `pip install -e .` only installs this package's own declared
platform dependencies (currently Pillow on Windows).

First transport check:

```bat
.venv\Scripts\pymobiledevice3.exe usbmux list
```

If it reports that the usbmuxd socket is unavailable, repair the Apple device
layer first. Do **not** rebuild Python/PaddleOCR for an Apple USB-driver error.

Once the iPhone is visible, unlock it and approve Trust if prompted. Developer
services can also require Developer Mode and a DeveloperDiskImage:

```bat
.venv\Scripts\pymobiledevice3.exe amfi enable-developer-mode
.venv\Scripts\pymobiledevice3.exe mounter auto-mount
```

The device can require a physical confirmation/restart when Developer Mode is
enabled. The agent must stop for that physical step rather than attempting to
bypass it.

The Windows doctor verifies the dependency stack, usbmux, a connected phone,
CoreDevice display info, native remote-input capability, screenshot capture and
PP-OCRv6 OCR in that order. Real-device testing on iOS 26.6 confirmed that the
current CoreDevice Universal HID touchscreen/virtual-keyboard path requires iOS
27.0 or later. On older iOS, doctor reports that limitation while still testing
capture/OCR; do not treat the native tap/type path as available.
The first PP-OCRv6 invocation downloads the medium detection/recognition models
to PaddleX's user cache; later runs reuse the local model files.

### macOS

- macOS Sequoia+ with iPhone Mirroring paired to the phone (open the app once
  manually to pair — pairing prompts need the physical phone).
- Python 3.12+ with pyobjc (`pip install pyobjc-framework-Quartz
  pyobjc-framework-Vision pyobjc-framework-AppKit`).
- The terminal app needs two permissions in System Settings > Privacy &
  Security. **The toggles require the user:**
  - **Accessibility** — taps and keystrokes. Takes effect immediately.
  - **Screen Recording** — seeing the phone. Takes effect after the terminal
    app restarts.

Open the panes directly:

```bash
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
```

> **Heads up — you may need to grant more than these two.** Accessibility and
> Screen Recording are the permissions we *know* are required, and they're all
> `--doctor` currently checks. But this was built on a machine that was already
> permissive, so a fresh Mac may prompt for additional approvals the first time
> an action runs. If `--doctor` passes but taps, typing, or capture silently do
> nothing, watch for a macOS permission prompt and check System Settings >
> Privacy & Security for a pane asking to approve your terminal. As we pin down
> exactly which extra permissions a clean install needs, they'll get added to
> `--doctor` as proper prerequisites.

## macOS Fast Path

```bash
git clone https://github.com/ShawnPana/phone-harness ~/.phone-harness   # canonical home
cd ~/.phone-harness
pip install pyobjc-framework-Quartz pyobjc-framework-Vision pyobjc-framework-AppKit
pip install -e . --no-deps            # installs the global `phone-harness` command

# register as an agent skill so Claude Code / Codex auto-use it (see below)
mkdir -p ~/.claude/skills/phone-harness
phone-harness skill > ~/.claude/skills/phone-harness/SKILL.md
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/phone-harness"
phone-harness skill > "${CODEX_HOME:-$HOME/.codex}/skills/phone-harness/SKILL.md"

phone-harness --doctor
phone-harness <<'PY'
print(screen_info())
PY
```

If `screen_info()` prints window bounds, you're done.

`~/.phone-harness` is the canonical home — a hidden folder in your home
directory (like `~/.oh-my-zsh` or `~/.nvm`), so the code, `helpers.py`,
`SKILL.md`, and your `agent-workspace/` always live at a path the agent knows,
on any machine. `pip install -e .` keeps that source editable while giving you a
`phone-harness` command on PATH, which is what lets the skill call
`phone-harness` from any directory. Because the install is editable it's bound
to that location, so keep the folder at `~/.phone-harness` (or re-run `pip
install -e .` if you relocate it). If your Python can't reach PyPI to resolve the
pyobjc deps, `--no-deps` skips them (they're installed by the line above).

## Register as a skill

So the agent reaches for phone-harness on its own, register `SKILL.md` as a
skill named `phone-harness` with `phone-harness skill` as its body (the Fast
Path does this for both Claude Code and Codex). The skill's trigger is:

```text
Control the user's real iPhone from macOS or Windows: read the screen with local
OCR, open apps, tap, type, swipe, and verify results.
```

Re-run the `phone-harness skill > …/SKILL.md` lines after pulling updates so the
registered copy stays current.

## If It Fails

`--doctor` walks the ladder in order: pyobjc → Accessibility → Screen
Recording → app installed → app running → window found → capture works → OCR
works. Fix the first FAIL; later checks depend on earlier ones.

Common cases:

- **Capture is blank/black**: Screen Recording was granted but the terminal
  hasn't restarted since.
- **Window not found**: the phone isn't paired, isn't in range, or iPhone
  Mirroring shows a connect screen — open the app manually once.
- **Taps do nothing**: Accessibility missing, or another window stole focus —
  events land only when the mirroring window is frontmost.
