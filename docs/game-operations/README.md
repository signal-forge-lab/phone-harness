# Game operation knowledge

Game/app knowledge must survive chat rotation. For each supported game, keep
user-taught rules and real-device-learned behavior in repository documentation
under that game's directory. Do not rely on conversation memory alone.

Use the following evidence labels when accumulating knowledge:

- `USER_CONFIRMED` for information explicitly taught by the user;
- `DEVICE_CONFIRMED` for behavior verified on the real device;
- `HYPOTHESIS` for useful but unverified interpretations.

Producer/output tables, merge progressions and other reusable learned mappings
belong in the game's durable knowledge file. Update them in the same work
session in which the information is learned so a new chat can immediately
continue operation.

For Merge Boss, keep three durable files together:

- `knowledge.mbk` — AI-only compact durable learned facts (MBK1 JSONL);
- `catalog.json` — machine-readable item-family / merge / producer knowledge;
- `calibration.json` — machine-readable screen geometry and semantic-slot
  calibration. Keep `calibrated=false` until the live order strip, scroll
  gestures and hint readers have all been verified.

For Merge Boss, a complete item-family hint learned from either a board-item or
order-item `i` view is also persisted in the machine-readable `catalog.json`.
The durable unit is the full `Lvl.1..Lvl.10` chain. Reuse the learned family for
later order-target identification and merge planning instead of reopening the
same hint on every turn.

When a workflow needs learned facts programmatically, keep a small machine-
readable catalog beside the human-readable knowledge file. The Markdown remains
the evidence/audit source; the catalog mirrors only planner inputs such as merge
transitions and producer possible-output lists. Update both in the same session
when new device evidence or user-confirmed rules change the planner inputs.

This directory stores user-confirmed rules and operating knowledge for games controlled through phone-harness.

- Keep one directory per app/game.
- Record confirmed rules separately from observations or hypotheses.
- Prefer stable concepts (board geometry, object roles, success conditions) over transient screen coordinates.
- Do not generalize one game's rules into phone-harness runtime behavior.
