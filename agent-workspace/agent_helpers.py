"""Agent-editable phone helpers.

Add task-specific primitives here. Core helpers from phone_harness.helpers
load this file at import time; anything defined here is available in
phone-harness scripts alongside the core helpers.
"""


def tap_icon(label, index=0):
    """Tap a macOS Mirroring Home-Screen app icon by its label.

    Learned: tapping the label text itself does NOT launch the app in the
    mirrored Home Screen — the tappable icon is ~35 points above the label.
    Verified against Weather (label tap: no-op; icon tap: launches).
    """
    import sys

    if sys.platform == "win32":
        raise RuntimeError(
            "tap_icon() uses a macOS Mirroring point offset that is not calibrated "
            "for Windows screenshot pixels; use open_app() on Windows"
        )
    from phone_harness.helpers import find_text, tap
    hits = find_text(label)
    if not hits:
        raise RuntimeError(f"no Home-Screen label matching {label!r}")
    h = hits[index]
    tap(h["x"], h["y"] - 35)
    return h
