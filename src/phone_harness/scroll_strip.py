"""Generic bounded enumeration for horizontally/vertically scrollable strips."""

from __future__ import annotations


def enumerate_scroll_strip(
    capture_items,
    scroll_next,
    *,
    key_fn=None,
    max_pages=8,
    stop_after_stagnant_pages=2,
):
    """Collect unique items across overlapping scroll pages.

    ``capture_items`` returns the currently visible item records. ``scroll_next``
    performs one local scroll and returns truthy when another capture should be
    attempted. The scanner is UI-agnostic; callers define stable visual/semantic
    item identity through ``key_fn``.
    """
    for name, value, upper in (
        ("max_pages", max_pages, 64),
        ("stop_after_stagnant_pages", stop_after_stagnant_pages, 16),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
            raise ValueError(f"{name} must be an integer between 1 and {upper}")
    if key_fn is None:
        key_fn = lambda item: item

    seen = {}
    pages = []
    stagnant = 0
    for page_index in range(max_pages):
        visible = list(capture_items())
        page_keys = []
        new_count = 0
        for item in visible:
            key = key_fn(item)
            if key is None:
                continue
            page_keys.append(key)
            if key not in seen:
                seen[key] = item
                new_count += 1
        pages.append({
            "page_index": page_index,
            "visible_count": len(visible),
            "new_count": new_count,
            "keys": page_keys,
        })

        stagnant = stagnant + 1 if new_count == 0 else 0
        if stagnant >= stop_after_stagnant_pages:
            break
        if page_index + 1 >= max_pages or not scroll_next():
            break

    return {
        "items": list(seen.values()),
        "unique_count": len(seen),
        "pages": pages,
        "page_count": len(pages),
        "stopped_stagnant": stagnant >= stop_after_stagnant_pages,
    }

