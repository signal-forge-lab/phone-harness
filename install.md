# phone-harness install

Use once. For phone work, read `SKILL.md`.

## Requirements

### Windows

- Windows 11.
- Python 3.12 is the verified project interpreter.
- Python 3.14 is used only for the separate Wi-Fi `tunneld` environment on
  Windows. The normal phone-harness runtime remains on Python 3.12.
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

On Windows, keep `tunneld` in a separate Python 3.14 virtual environment. The
tested setup uses the same pymobiledevice3 source/revision as the normal runtime:

```powershell
$pymobiledevice3 = "C:\path\to\pymobiledevice3"
py -3.14 -m venv .venv-tunneld
.\.venv-tunneld\Scripts\python.exe -m pip install -U pip
.\.venv-tunneld\Scripts\python.exe -m pip install -e $pymobiledevice3
```

Python 3.14 is intentional here: the Windows TCP tunnel path uses the stdlib
TLS-PSK APIs available there. Do not switch the main phone-harness/PaddleOCR
environment away from its verified Python 3.12 interpreter just for tunneld.

Start pymobiledevice3 `tunneld` from an **elevated** terminal and leave it
running:

```powershell
.\.venv-tunneld\Scripts\python.exe -m pymobiledevice3 remote tunneld `
  --no-usb --wifi --no-usbmux --no-mobdev2 --protocol tcp
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

Keep this tunneld process alive rather than restarting it per phone operation.
phone-harness never auto-elevates or starts it implicitly. If the tunnel drops,
failed device commands clear the cached RSD/device selection; after tunneld
recovers, the next runtime call re-discovers the phone. This behavior is shared
by a future dedicated MCP and direct local `PhoneRuntime` use.

#### ChatGPT MCP startup

Do not keep the ChatGPT MCP configuration only in one temporary PowerShell
session. Configure it once with the repository wrapper:

```powershell
.\tools\configure_phone_harness_mcp.ps1
```

On the first run it asks for the OAuth issuer, Secure MCP Tunnel resource URL
and any Python path it cannot infer. It also asks once for the owner token and
stores that secret with Windows DPAPI outside the repository. Later Tunnel URL
changes are configuration changes, not source changes:

```powershell
.\tools\configure_phone_harness_mcp.ps1 `
  -OAuthResourceUrl "https://tunnel-service.gateway.unified-0.internal.api.openai.org/v1/mcp/YOUR-TUNNEL-ID"
```

Normal MCP restart is then one command:

```powershell
.\tools\start_phone_harness_mcp.ps1
```

For normal phone-harness operation, including the elevated pymobiledevice3
`tunneld`, use the top-level lifecycle commands instead:

```powershell
.\tools\start_phone_harness.ps1
.\tools\stop_phone_harness.ps1
```

`start_phone_harness.ps1` starts or adopts the local pymobiledevice3 tunneld,
then starts the MCP and Secure MCP Tunnel. Only the tunneld step requests UAC
elevation. `stop_phone_harness.ps1` stops the managed Secure MCP Tunnel, MCP,
and tunneld in reverse order; it requests elevation only for the tunneld stop.

The wrapper builds the MCP, replaces the previous wrapper-managed Node listener,
verifies `/healthz`, the canonical Secure MCP Tunnel protected-resource
metadata, the loopback compatibility metadata used by local tunnel diagnostics,
the unauthenticated 401 OAuth challenge, `tunnel-client doctor`, and the Secure
MCP Tunnel runtime. If the port is still owned by an older manually started
Node, stop that process once; the wrapper will not kill an unmanaged listener.
Only after it prints `READY` should ChatGPT scan/reconnect the App.

For PC-reboot-safe Tunnel startup, keep the API key value out of the JSON
configuration and store only its environment-variable name there. Persist the
current value once in the Windows User environment:

```powershell
$env:CONTROL_PLANE_API_KEY = "<runtime API key>"
.\tools\configure_phone_harness_mcp.ps1 -PersistControlPlaneApiKey
```

The resulting config contains `controlPlaneApiKeyEnvName` plus the tunnel-client
executable/profile paths; it never contains the API key value. Windows User
environment variables are readable by processes running as that Windows user,
which is the intentional convenience/security tradeoff requested for reboot
startup.

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
