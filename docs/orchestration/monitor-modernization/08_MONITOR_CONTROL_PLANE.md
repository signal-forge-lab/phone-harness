# Task 08 — Electron-Local Monitor Control Plane

## Goal

Expose safe Monitor Server controls from the desktop Monitor UI without exposing OS/process control to LAN clients.

## Required controls

- Monitor Server state: owned / external / stopped / starting / running / failed.
- PID when owned/known.
- uptime or start time when owned.
- Stop Monitor Server.
- Restart Monitor Server.
- Reload Monitor UI independently from server restart.

## Security architecture

Controls MUST use an Electron-local boundary such as:

`renderer -> contextBridge/preload -> ipcRenderer -> ipcMain -> allowlisted lifecycle methods`

Do not add `/api/restart`, `/api/stop`, shell endpoints, or equivalent LAN-accessible process controls to `monitor_web.py`.

Use a narrow API. Do not expose raw `ipcRenderer`, `child_process`, filesystem, or arbitrary command strings to the renderer.

## UI placement

Keep runtime controls visually distinct from normal Monitor data panels. A small desktop-only control/header area or dedicated Control panel is acceptable.

LAN browser users should either not see these controls or see them disabled with no privileged backend path.

## External server behavior

If the Electron app attached to a Monitor Server it does not own:

- do not stop/kill it;
- clearly mark it as external;
- only allow safe operations such as UI reload, or an explicit reconnect if implemented.

## Tests/checks

- owned start/stop/restart;
- external server cannot be killed;
- LAN HTTP surface has no new process-control endpoint;
- preload API contains only allowlisted methods;
- normal Monitor data/teaching actions continue to work.

## Completion gate

Desktop Monitor can manage its own Monitor Server lifecycle safely from UI while LAN clients remain non-privileged.

