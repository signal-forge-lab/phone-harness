"""Generic slot-based semantic extraction from OCR/accessibility elements."""

from __future__ import annotations

import re
from dataclasses import dataclass


_LEVEL_RE = re.compile(r"\b(?:lv(?:l)?|level)\s*[\.:#-]*\s*(10|[1-9])\b", re.I)


@dataclass(frozen=True)
class SemanticSlot:
    bounds: dict
    expected_level: int | None = None


def semantic_slots_from_records(records):
    slots = []
    for record in records:
        bounds = record.get("bounds")
        if not isinstance(bounds, dict):
            raise ValueError("semantic slot record requires bounds")
        expected_level = record.get("expected_level")
        if expected_level is not None:
            expected_level = int(expected_level)
        slots.append(SemanticSlot(dict(bounds), expected_level=expected_level))
    return tuple(slots)


def _center(item):
    x = float(item.get("x", 0))
    y = float(item.get("y", 0))
    # phone-harness element coordinates are already center coordinates.
    return x, y


def elements_in_slot(elements, slot):
    b = slot.bounds
    left = float(b["x"])
    top = float(b["y"])
    right = left + float(b["w"])
    bottom = top + float(b["h"])
    selected = []
    for item in elements:
        cx, cy = _center(item)
        if left <= cx <= right and top <= cy <= bottom:
            selected.append(item)
    return sorted(selected, key=lambda item: (float(item.get("y", 0)), float(item.get("x", 0))))


def parse_level(text):
    if not isinstance(text, str):
        return None
    match = _LEVEL_RE.search(text)
    return int(match.group(1)) if match else None


def parse_identity_level(elements, slot, *, ignored_text=()):
    """Extract one identity/level record from a calibrated UI slot."""
    selected = elements_in_slot(elements, slot)
    texts = [item.get("text", "").strip() for item in selected if isinstance(item.get("text"), str)]
    texts = [text for text in texts if text]
    parsed_levels = [level for level in (parse_level(text) for text in texts) if level is not None]
    parsed_level = parsed_levels[0] if parsed_levels else None
    if slot.expected_level is not None:
        if parsed_level is not None and parsed_level != slot.expected_level:
            raise ValueError(
                f"slot expected level {slot.expected_level} but recognized level {parsed_level}"
            )
        level = slot.expected_level
    else:
        level = parsed_level
    if level is None:
        raise ValueError("slot does not contain a recognizable level")

    ignored = {str(value).strip().casefold() for value in ignored_text}
    candidates = []
    for text in texts:
        stripped = _LEVEL_RE.sub("", text).strip(" -:：・|/\t")
        if not stripped or stripped.casefold() in ignored:
            continue
        candidates.append(stripped)
    if not candidates:
        raise ValueError(f"slot level {level} does not contain an item identity")
    identity = max(candidates, key=lambda value: (len(value), value))
    return {"identity": identity, "level": level}


def parse_semantic_slots(elements, slots, *, ignored_text=()):
    return tuple(
        parse_identity_level(elements, slot, ignored_text=ignored_text)
        for slot in slots
    )
