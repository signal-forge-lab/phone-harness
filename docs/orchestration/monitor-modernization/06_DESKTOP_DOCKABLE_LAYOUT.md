# Task 06 — Desktop IDE-Style Dockable Layout

## Goal

Convert desktop Monitor sections from fixed CSS Grid into a dockable IDE-like panel system while preserving the existing content/data logic.

## Candidate technology

Dockview is the current preferred candidate because it supports movable/resizable/tabbed/docked panels and layout serialization.

Before implementation, verify the current package/API and choose the smallest integration path that works with the existing mostly-vanilla HTML/JS Monitor.

Do not introduce React solely to use Dockview.

## Panels

Treat these existing regions as independent panels:

- Current Frame / Capture
- Decision
- Failure Judgment
- Human Teaching
- Human Teaching Inbox
- Timeline
- Runtime
- Pipeline Timing
- Analysis / Event Detail

## Required desktop behavior

- drag panel/group
- resize split
- tab compatible panels/groups
- move between groups
- maximize/focus a panel or group if supported cleanly
- preserve all existing panel content and update logic

## Responsive boundary

- Desktop: dockable layout.
- Tablet landscape: keep Task 05 dedicated layout.
- Phone portrait/narrow: keep vertical layout.

Do not force docking behavior onto phone/tablet merely because the library supports touch.

## Dependency rule

Prefer local/offline-capable assets. Do not make the Monitor depend on an external CDN for core UI functionality unless the orchestrator explicitly approves that tradeoff.

If bundling/build tooling is required, keep it minimal and document why.

## Architecture rule

Reuse existing Monitor DOM/data functions where possible. The task is layout architecture, not a rewrite of API/state/teaching logic.

## Tests/checks

- existing Monitor tests remain green;
- desktop runtime smoke test;
- panel move/resize/tab smoke test;
- tablet and phone modes still bypass desktop docking behavior;
- no console errors on initial load.

## Completion gate

Desktop users can rearrange the major Monitor regions without losing existing functionality, while tablet/phone layouts remain appropriate to their form factor.

