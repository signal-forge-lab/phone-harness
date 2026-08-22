"""Small cross-process control surface for long-running phone automation."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from phone_harness.trace import DEFAULT_MONITOR_ROOT


DEFAULT_CONTROL_FILE = DEFAULT_MONITOR_ROOT / "control.json"


def _default_state():
    return {
        "schema_version": 1,
        "paused": False,
        "changed_unix": None,
        "source": None,
        "pause_generation": 0,
        "last_pause_unix": None,
    }


def load_runtime_control(path=None):
    """Read the operator control state without failing phone automation."""
    source = Path(path) if path is not None else DEFAULT_CONTROL_FILE
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _default_state()
    if not isinstance(value, dict) or not isinstance(value.get("paused"), bool):
        return _default_state()
    generation = value.get("pause_generation", 0)
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        generation = 0
    return {
        "schema_version": 1,
        "paused": value["paused"],
        "changed_unix": value.get("changed_unix"),
        "source": value.get("source"),
        "pause_generation": generation,
        "last_pause_unix": value.get("last_pause_unix"),
    }


def set_runtime_paused(paused, *, path=None, source="monitor"):
    """Atomically set whether autonomous phone workflows should wait."""
    if not isinstance(paused, bool):
        raise ValueError("paused must be boolean")
    target = Path(path) if path is not None else DEFAULT_CONTROL_FILE
    previous = load_runtime_control(target)
    changed_unix = time.time()
    legacy_paused_without_generation = (
        paused
        and previous["paused"]
        and previous["pause_generation"] == 0
        and previous["last_pause_unix"] is None
    )
    entering_pause = paused and (
        not previous["paused"] or legacy_paused_without_generation
    )
    pause_generation = previous["pause_generation"] + (1 if entering_pause else 0)
    last_pause_unix = changed_unix if entering_pause else previous["last_pause_unix"]
    state = {
        "schema_version": 1,
        "paused": paused,
        "changed_unix": changed_unix,
        "source": str(source)[:80] if source is not None else None,
        # Persist the fact that a pause happened even after Resume. A runtime
        # that was idle for the whole pause interval can then discard stale
        # observations before its next destructive action.
        "pause_generation": pause_generation,
        "last_pause_unix": last_pause_unix,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return state
