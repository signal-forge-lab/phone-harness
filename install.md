# phone-harness install

[English](install.md) | [日本語](install.ja.md)

This guide contains only the reusable public setup. Device identifiers, credentials, tunnel profiles, internal service URLs, workstation paths, and incident/recovery handoffs belong in local or private documentation and must not be committed to this repository.

For day-to-day agent usage, also read `SKILL.md`.

## Windows requirements

- Windows 11
- Python 3.12 for the normal phone-harness runtime
- Apple Devices and the Apple USB/device driver layer
- `pymobiledevice3` for USB/Wi-Fi CoreDevice, RSD, HID, and WDA transport
- PaddlePaddle CPU + PaddleOCR when OCR fallback is required

Create the project environment:

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

Check USB discovery first:

```bat
.venv\Scripts\pymobiledevice3.exe usbmux list
```

If the device is not visible, repair the Apple device-driver/trust layer before changing the Python or OCR environment. Unlock the phone and approve Trust or Developer Mode prompts physically when iOS requires them. Automation must not attempt to bypass those prompts.

## Optional Wi-Fi transport on Windows

USB is the safe bootstrap path. After the device is trusted, paired, in Developer Mode, and provisioned for the required automation backend, runtime control can use a Wi-Fi RemotePairing tunnel.

Keep the Wi-Fi `tunneld` runtime separate from the main environment when a newer Python runtime is required by `pymobiledevice3`:

```powershell
$pymobiledevice3 = "C:\path\to\pymobiledevice3"
py -3.14 -m venv .venv-tunneld
.\.venv-tunneld\Scripts\python.exe -m pip install -U pip
.\.venv-tunneld\Scripts\python.exe -m pip install -e $pymobiledevice3
.\.venv-tunneld\Scripts\python.exe -m pymobiledevice3 remote tunneld
```

Choose transport in the process that runs phone-harness:

```powershell
$env:PHONE_HARNESS_TRANSPORT = "wifi"   # usb | wifi | auto
# Optional when more than one device is available:
$env:PHONE_HARNESS_UDID = "YOUR_DEVICE_UDID"
.\.venv\Scripts\phone-harness.exe --doctor
```

Do not commit a real UDID or any generated pairing/runtime state.

## Windows iOS WDA setup

Generated tooling, profiles, certificates, signed applications, and downloads are local artifacts and are ignored by Git.

Prepare the tooling from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows-wda\setup_tools.ps1
```

Sign in and approve any required Apple/device prompts locally. Never put an Apple password, 2FA code, signing key, provisioning profile, or device identifier in source files, issue text, or script arguments that are intended to be shared.

Provision/sign/install WDA using the supplied helper after the local signing environment is ready:

```powershell
.\tools\windows-wda\ipaside-src\src\iPASide.Engine\.venv\Scripts\python.exe `
  .\tools\windows-wda\provision_and_sign_wda.py --install
```

Re-run provisioning when the local development profile expires or the runner must be re-signed.

## MCP server

The MCP server is in `mcp-server/`. Public source contains the transport/authentication implementation but not real credentials or environment-specific tunnel configuration.

Use the repository wrappers for local configuration and startup:

```powershell
.\tools\configure_phone_harness_mcp.ps1
.\tools\start_phone_harness_mcp.ps1
```

When prompted for environment-specific values, use your own local configuration. Keep owner tokens, control-plane/API keys, OAuth credentials, tunnel resource identifiers, and private URLs outside Git. Secret values should come from an external secret store or process-local environment rather than a checked-in configuration file.

Top-level lifecycle helpers are also available:

```powershell
.\tools\start_phone_harness.ps1
.\tools\stop_phone_harness.ps1
```

Environment-specific troubleshooting and recovery notes must be kept outside the public repository.

## macOS requirements

- macOS with iPhone Mirroring paired to the phone
- Python 3.12+
- PyObjC components used by the runtime
- Accessibility and Screen Recording permissions for the terminal/app that runs phone-harness

Install the required Python components and this repository:

```bash
git clone https://github.com/signal-forge-lab/phone-harness ~/.phone-harness
cd ~/.phone-harness
pip install pyobjc-framework-Quartz pyobjc-framework-Vision pyobjc-framework-AppKit
pip install -e . --no-deps
phone-harness --doctor
```

If macOS asks for additional permissions, approve them through System Settings. Physical device approval steps remain user actions.

## Register as an agent skill

Generate the skill text from the installed command and place it in the skill directory used by your agent host:

```bash
mkdir -p ~/.claude/skills/phone-harness
phone-harness skill > ~/.claude/skills/phone-harness/SKILL.md
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/phone-harness"
phone-harness skill > "${CODEX_HOME:-$HOME/.codex}/skills/phone-harness/SKILL.md"
```

Re-generate the registered skill after updating phone-harness.

## If setup fails

Use `phone-harness --doctor` and repair the first failing dependency or permission boundary before debugging higher layers. Keep failure logs and machine-specific recovery notes local unless they are sanitized into a reusable public issue or documentation update.
