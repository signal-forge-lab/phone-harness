# AliExpress Merge Boss

## Confirmed rules

User-confirmed on 2026-08-16:

1. The board is 7 columns by 9 rows.
2. Items with an electricity-like icon at the upper-right are producers, not merge targets.
3. Tapping a producer emits merge-target items onto the board.
4. If there are no merge-target items on the board, tap a producer.
5. Tapping an item shows its level and description above the board.
6. Merge-compatible items must be visually identical. Merely similar-looking items are not the same item.

## Operating implications

- Treat producer cells and merge-target cells as different roles.
- Do not propose a merge solely from semantic similarity or approximate appearance.
- Use the 7x9 grid to address cell centers rather than estimating free-form coordinates.
- A visual similarity score is only candidate evidence; the agent must not describe a merge as successful until the post-action board confirms the expected state transition.
- When identity is uncertain, tapping an item to inspect its displayed level/description is a valid disambiguation step.

## Still unknown

- Exact board bounding rectangle for each device/layout.
- Exact producer badge detection rule.
- Whether animations or overlays can temporarily alter otherwise identical item pixels.
- Complete merge progression and any special-item rules.
