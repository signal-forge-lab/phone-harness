# Task 10 — Developer Controls and Build / Build & Restart

## Depends on

Task 08 control-plane boundary. Prefer Task 09 runtime-control primitives when Build & Restart needs runtime restart.

## Goal

Add explicitly developer-facing controls without mixing them into ordinary operational controls.

## Required developer features

At minimum evaluate and implement the useful subset of:

- Open DevTools
- view/reveal Monitor Server logs
- view/reveal Phone Harness runtime logs where a canonical log exists
- Build
- Build & Restart

## UI separation

Keep developer actions grouped separately, for example:

```text
Runtime
[Stop] [Restart] [Reload UI]

Developer
[Build] [Build & Restart] [DevTools]
```

## Command architecture

Build operations MUST map to a fixed allowlist of repository-defined commands/actions.

Never accept renderer-supplied arbitrary shell command strings.

Before implementing Build, determine what "build" actually means for this repository/current development path. Do not invent a broad build command if there is no canonical one.

## Concurrency/locking

Prevent obviously conflicting operations such as:

- two builds at once;
- build and build-and-restart simultaneously;
- restart while the same controlled process is already transitioning.

Simple in-memory operation state is sufficient unless current architecture demands more.

## Feedback

Show bounded state such as:

- idle
- building
- succeeded
- failed

Include a concise failure reason/log location. Do not dump unbounded logs into the UI.

## Tests/checks

- allowlisted action dispatch only;
- duplicate/conflicting build request rejected/disabled;
- build failure leaves current Monitor controllable;
- Build & Restart restarts only after successful build unless explicitly designed otherwise;
- LAN HTTP has no build endpoint.

## Completion gate

Developer build/restart workflow is available from Electron without creating a remote shell surface.

