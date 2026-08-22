# Task 04 — Startup / Firewall Consistency

## Goal

Eliminate the current mismatch between Monitor startup executables and Windows Firewall assumptions.

## Known context to verify, not blindly assume

Recent runtime evidence showed:

- LAN Monitor uses TCP/17678.
- `python.exe` had an existing TCP allow rule.
- `pythonw.exe` had only a UDP rule in the observed environment.
- Electron successfully hid `python.exe` using `windowsHide: true`.

Re-verify before changing anything because firewall state is machine-local.

## Required outcome

- Normal Electron path consistently uses an executable compatible with the intended firewall rule.
- Legacy/manual startup scripts do not silently choose a contradictory executable.
- LAN bind remains `0.0.0.0:17678` when LAN Monitor mode is requested.
- Firewall helper scripts, if retained, are narrow:
  - inbound TCP
  - port 17678
  - Private profile
  - LocalSubnet where appropriate
- No broad Public/Any rule should be introduced as a convenience.

## Constraints

- Do not require admin elevation during ordinary Electron launch.
- Do not modify firewall rules automatically on every startup.
- Keep firewall setup an explicit install/setup action.

## Files likely owned

- `tools/start_phone_harness_monitor*.cmd`
- `tools/enable_phone_harness_monitor_lan_firewall.ps1`
- `tools/disable_phone_harness_monitor_lan_firewall.ps1`
- installation/runtime documentation if needed

Avoid changing Electron lifecycle internals except for a proven executable-selection bug; Task 03 owns those files.

## Verification

- inspect effective startup executable;
- inspect effective firewall rule shape;
- start Monitor;
- verify local HTTP 200;
- if a LAN device is available, verify `http://<LAN-IP>:17678/` opens;
- do not claim remote firewall success from a PC-local HTTP check alone.

## Completion gate

Source, scripts, documentation, and expected firewall rule all describe the same runtime path.

