# Task 11 — Windows Executable Packaging and Window State

## Goal

Turn the development Electron Monitor into a normal Windows desktop application while preserving the same runtime behavior.

## Required packaging outcome

- Produce a Windows executable/package using a conventional Electron packaging path appropriate to the repository.
- Application name: `Phone Harness Monitor` unless existing naming conventions require a more precise name.
- Include application icon if an approved project icon already exists; otherwise use a minimal temporary project-owned icon and document it.
- No Python console window appears during normal packaged use.
- Packaged app can locate/start the intended Python environment/runtime according to an explicit strategy.

## Python/runtime strategy

Do not assume the developer worktree path will exist on another machine.

Explicitly decide whether the packaged app is:

1. a developer-local launcher tied to the existing repository/environment, or
2. a distributable package that must ship/locate its own Python/runtime assets.

For the current internal tool, prefer the smaller developer-local packaging strategy unless distribution is explicitly required.

## Window state persistence

Persist and restore:

- window width/height;
- window position;
- maximized state.

Validate saved bounds against currently available displays so an old monitor configuration cannot reopen the app permanently off-screen.

## No tray

Do not implement tray behavior.

## Tests/checks

- package/build succeeds;
- packaged executable launches;
- Monitor HTTP reaches 200;
- close cleans up Electron-owned Monitor Server;
- reopen restores sensible window state;
- invalid/off-screen stored bounds recover to visible defaults.

## Completion gate

The user can launch Phone Harness Monitor as a normal Windows desktop application without VBS and without a visible console.

