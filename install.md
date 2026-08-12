# phone-harness install

Use once. For phone work, read `SKILL.md`.

## Requirements

### Windows

- Windows 11.
- Python 3.12 is the verified project interpreter.
- Apple device support. Install **Apple Devices** and, when needed for the
  usbmux/driver layer, the Microsoft Store version of **iTunes**.
- `pymobiledevice3` for USB/Wi-Fi CoreDevice, RSD and HID/WDA transport.
- PaddlePaddle CPU + PaddleOCR 3.7 for local PP-OCRv6 OCR.

Verified Python environment:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip setuptools wheel
.venv\Scripts\python.exe -m pip install "git+https://github.com/signal-forge-lab/pymobiledevice3.git@master"
.venv\Scripts\python.exe -m pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
.venv\Scripts\python.exe -m pip install paddleocr==3.7.0
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m pip check
.venv\Scripts\phone-harness.exe --doctor
```

`pymobiledevice3` is installed explicitly and remains an external
GPL-3.0-or-later dependency; its source is not copied or vendored into this
repository. The Windows path currently uses the `signal-forge-lab` fork for
the small WDA coordinate-tap, persistent-XCTest-runner and batched-action extensions required
by the iOS 26 fallback. The final `pip install -e .` only installs this
package's own declared platform dependencies (currently Pillow on Windows).

First USB transport check:

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

#### Optional Wi-Fi transport

USB remains the safe default. After the phone has already been trusted, paired,
put in Developer Mode and provisioned with WDA, normal runtime control can use a
Wi-Fi RemotePairing tunnel instead.

Start pymobiledevice3 `tunneld` from an **elevated** terminal and leave it
running:

```powershell
.\.venv\Scripts\python.exe -m pymobiledevice3 remote tunneld --no-usb --no-usbmux
```

Then choose the transport in the terminal that runs phone-harness:

```powershell
$env:PHONE_HARNESS_TRANSPORT = "wifi"   # usb | wifi | auto
# Optional when tunneld exposes more than one phone:
$env:PHONE_HARNESS_UDID = "YOUR_DEVICE_UDID"
.\.venv\Scripts\phone-harness.exe --doctor
```

`auto` prefers USB when the selected phone is attached and otherwise falls back
to a device exposed by local tunneld. For an explicit `wifi` session, use the
Wi-Fi-only tunneld command above so its RSD listing cannot resolve back to a USB
tunnel for the same phone. Initial Trust/Developer Mode/WDA provisioning and
recovery remain USB-first operations.

The Windows doctor verifies the dependency stack, selected transport, a connected phone,
CoreDevice display info, the selected remote-input backend, screenshot capture
and PP-OCRv6 OCR in that order. iOS 27+ uses native CoreDevice Universal HID.
iOS 26 uses the signed WDA runner described below.
The first PP-OCRv6 invocation downloads the medium detection/recognition models
to PaddleX's user cache; later runs reuse the local model files.

Windows screen reading is accessibility-first: WDA labels, values and bounds
are returned by `elements()` (and the compatibility alias `ocr()`) when
available. PaddleOCR is used only when WDA does not expose usable text.

### Windows iOS 26 WDA setup

WDA provisioning is needed once per free-development profile lifetime. The
runtime harness then starts/reuses the installed WDA automatically; Appium is a
setup/signing tool only and is not a permanent runtime server.

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows-wda\setup_tools.ps1
```

This prepares pinned Appium/XCUITest tooling, a pinned iPASide checkout and the
Windows `appium/resigner` binary under `tools/windows-wda/`. Generated tooling,
profiles, certificates, signed apps and downloads are ignored by Git.

Sign in to iPASide locally; never put the password or 2FA code in a chat or
script argument:

```powershell
cd .\tools\windows-wda\ipaside-src\src\iPASide.Engine
.\.venv\Scripts\python.exe -m ipaside_engine login YOUR_APPLE_ID
```

Then return to the repository root, provision/sign/install WDA in one command:

```powershell
.\tools\windows-wda\ipaside-src\src\iPASide.Engine\.venv\Scripts\python.exe `
  .\tools\windows-wda\provision_and_sign_wda.py --install
```

The script keeps iPASide's original PKCS#12 identity untouched and creates only
a temporary legacy-compatible PKCS#12 copy for `resigner`. If iOS asks, approve
the Developer App/XCTest runner on the physical phone. Re-run the same
`--install` command when a free-development profile expires or WDA must be
re-signed. Normal `phone-harness` tap/drag/type calls start and reuse WDA
automatically on iOS 26.

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
