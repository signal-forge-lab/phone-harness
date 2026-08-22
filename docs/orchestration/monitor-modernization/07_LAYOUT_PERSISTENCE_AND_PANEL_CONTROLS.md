# Task 07 — Layout Persistence, Reset, Pin/Collapse Controls

## Depends on

Task 06 desktop dock architecture.

## Goal

Make the desktop dock layout practical across sessions without overbuilding a full workspace/profile system.

## Required first implementation

- Persist the latest desktop layout.
- Restore it on next desktop Monitor launch.
- Provide `Reset Layout` to restore the canonical default.
- Handle corrupt/incompatible saved layout by falling back safely to default.

## Storage

Choose the narrowest appropriate storage boundary.

Because the LAN browser view must remain usable and desktop layout is primarily a desktop concern, prefer Electron-local persisted state when practical. Browser-only fallback may be used if it does not create cross-device confusion.

Document the chosen ownership.

## Panel controls

After persistence is stable, add only controls that are clearly useful and supported by the chosen dock system:

- collapse/minimize panel or group;
- pin/edge placement if supported cleanly;
- maximize/focus panel/group if not already delivered in Task 06.

Do not implement a custom VS Code-style auto-hide framework unless required to satisfy an observed usability problem.

## Deferred unless trivial

- named layout presets (`Debug`, `Teaching`, etc.)
- cloud/shared layout sync
- per-device profile management

These are not required for completion.

## Tests/checks

- move/resize panels, restart Electron, verify restore;
- reset layout, verify default returns;
- feed invalid saved state, verify safe fallback;
- verify phone/tablet layouts are not corrupted by desktop saved layout.

## Completion gate

The last useful desktop arrangement survives restart and users can reliably return to the canonical default.

