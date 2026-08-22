# Task 05 — Dedicated Tablet Landscape Layout

## Goal

Fix the current poor tablet landscape layout without changing the existing phone portrait philosophy.

## Device modes

Preserve three conceptual modes:

1. Phone / narrow portrait: vertical stack.
2. Tablet landscape: dedicated fixed responsive layout optimized for touch.
3. Desktop: later converted to dockable IDE layout by Task 06.

## Tablet landscape target

Use a layout concept similar to:

```text
┌──────────────────────┬──────────────┐
│                      │ Decision     │
│ Current Frame        ├──────────────┤
│                      │ Runtime      │
│                      ├──────────────┤
│                      │ Failure      │
├──────────────────────┴──────────────┤
│ Timeline                            │
├──────────────────────┬──────────────┤
│ Human Teaching       │ Detail       │
└──────────────────────┴──────────────┘
```

Exact proportions should be derived from the current content and actual viewport, not copied mechanically.

## Requirements

- Current Frame remains visually dominant.
- Decision/Runtime/Failure are readable without tiny text.
- Timeline gets meaningful horizontal width.
- Teaching remains touch-friendly.
- Avoid desktop-style tiny dock tabs on tablet.
- Avoid horizontal page scrolling.
- Preserve phone vertical behavior.
- Do not yet introduce Dockview; Task 06 owns desktop docking.

## Files likely owned

- `src/phone_harness/monitor_web.html`
- relevant responsive/UI tests only

## Testing

At minimum validate representative viewports such as:

- phone portrait around 390x844
- tablet landscape around 1024x768
- larger tablet landscape around 1366x1024 where applicable
- desktop around 1440x900 to ensure no accidental regression before Task 06

Use existing browser/visual tooling if available. Do not overwrite visual baselines blindly.

## Completion gate

Phone portrait is unchanged in usability and tablet landscape no longer behaves like an awkward compressed desktop layout.

