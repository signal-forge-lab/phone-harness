# Task 09 — Phone Harness Process Status and Control

## Depends on

Task 08 Electron-local control-plane boundary.

## Goal

Extend the same local-only management model to the Phone Harness runtime itself.

## Required status

Where the runtime architecture makes the information reliable, expose:

- Running / Stopped / Unknown
- PID/process identity
- start time or uptime
- current device connection state
- active transport when already available from canonical runtime status

Do not infer hidden/ambiguous runtime state from weak heuristics when a canonical status source exists.

## Required controls

- Start Phone Harness
- Stop Phone Harness
- Restart Phone Harness

Only implement operations whose exact start/stop command and ownership semantics can be established from the repository/current runtime.

If multiple independent Phone Harness processes exist, model them explicitly or limit the scope rather than killing by broad wildcard.

## Security

Same boundary as Task 08:

- Electron-local only
- no LAN HTTP process endpoints
- no arbitrary command execution from renderer

## Separation

Keep Monitor Server controls and Phone Harness runtime controls logically distinct even if they share a dispatcher/helper.

## Tests/checks

- status reflects known process state;
- start creates the intended process only;
- stop targets the owned/selected intended process only;
- restart performs bounded stop/start;
- Monitor Server remains available when restarting Phone Harness unless the architecture explicitly requires otherwise;
- no LAN HTTP privileged endpoint added.

## Completion gate

The desktop Monitor can safely operate the intended Phone Harness runtime without broad process killing or LAN privilege exposure.

