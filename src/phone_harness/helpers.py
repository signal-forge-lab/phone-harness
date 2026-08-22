"""Cross-platform phone control helpers.

Core helpers live here. Agent-editable helpers live in
PH_AGENT_WORKSPACE/agent_helpers.py (defaults to <repo>/agent-workspace).
On macOS, raw Quartz remains available as an escape hatch. Windows uses the
CoreDevice backend and should stay within its explicit helpers.
"""
import hashlib, importlib, importlib.util, os, sys, time
from pathlib import Path

from .frame import FrameBroker

_WINDOWS = sys.platform == "win32"
if _WINDOWS:
    from . import paddle_ocr as _ocr
    from . import windows as mirror
else:
    from . import ocr as _ocr

    # The macOS background backend avoids taking focus. If its private SkyLight
    # symbols are unavailable, preserve upstream's fallback to classic mirroring.
    _BACKGROUND = os.environ.get("PHONE_HARNESS_BACKGROUND", "1").lower() not in (
        "0", "false", "no"
    )
    if _BACKGROUND:
        try:
            mirror = importlib.import_module(".background", __package__)
        except Exception:
            mirror = importlib.import_module(".mirror", __package__)
            _BACKGROUND = False
    else:
        mirror = importlib.import_module(".mirror", __package__)

tap = mirror.tap
long_press = mirror.long_press
drag = mirror.drag
press = mirror.press
type_text = mirror.type_text
activate = mirror.activate
find_window = mirror.find_window

_FRAME_BROKER = FrameBroker(lambda: mirror.capture())

CORE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CORE_DIR.parent.parent
AGENT_WORKSPACE = Path(
    os.environ.get("PH_AGENT_WORKSPACE", REPO_ROOT / "agent-workspace"))


# --- session / state ---

# Distinctive text on the not-connected interstitials. Reconnecting past any of
# these is a PHYSICAL action only the user can do (open the app, and if it says
# "iPhone in Use", LOCK the phone). The agent must never tap through them.
_BLOCKED_MARKERS = ("iphone in use", "lock your iphone", "mirroring ended",
                    "to connect")


def connection_state():
    """Return the host-specific connection state.

    Windows: ready / no-device / ambiguous-device / transport-unavailable.
    macOS: ready / blocked / no-window / not-running.
    """
    if _WINDOWS:
        return mirror.connection_state()
    if mirror.running_app() is None:
        return "not-running"
    if mirror.find_window() is None:
        return "no-window"
    path, win = mirror.capture()  # window exists, so this won't launch anything
    texts = " ".join(o["text"] for o in _ocr.recognize(path, win)).lower()
    return "blocked" if any(m in texts for m in _BLOCKED_MARKERS) else "ready"


def ensure_mirroring():
    """Return active phone-screen bounds when the host backend is ready.

    The legacy name comes from the macOS Mirroring backend. It never bypasses a
    physical trust/connect step; if the session is not ready, it raises with a
    host-specific recovery message.
    """
    state = connection_state()
    if _WINDOWS:
        if state == "ready":
            return mirror.ensure_window()
        if state == "no-device":
            mode = mirror._transport_mode()
            raise RuntimeError(
                f"No iPhone is visible through the configured Windows transport ({mode}). "
                "For USB, connect/unlock the phone and approve Trust. For Wi-Fi, start "
                "pymobiledevice3 tunneld and ensure the paired phone is reachable on the same network.")
        if state == "ambiguous-device":
            raise RuntimeError(
                "More than one iPhone is visible through the configured transport. Set "
                "PHONE_HARNESS_UDID to select one device before retrying.")
        raise RuntimeError(
            "The configured Windows phone transport is unavailable. Repair Apple Mobile Device "
            "for USB, or start pymobiledevice3 tunneld from an elevated terminal for Wi-Fi.")
    if state == "ready":
        mirror.activate()
        return mirror.find_window()
    if state == "not-running":
        raise RuntimeError(
            "iPhone Mirroring isn't running. Please open the iPhone Mirroring "
            "app and connect your phone, then retry — reconnecting is physical, "
            "so I can't do it for you.")
    if state == "no-window":
        raise RuntimeError(
            "iPhone Mirroring is open but no phone is connected. Please connect "
            "your phone in the app, then retry.")
    raise RuntimeError(
        "iPhone Mirroring is not connected — it's showing a connect / 'iPhone "
        "in Use' screen. This needs you: open iPhone Mirroring and connect the "
        "phone, and if it says 'iPhone in Use', LOCK your iPhone so mirroring "
        "can resume. Then retry. I will not tap Connect for you.")


def screen_info():
    """Return host bounds and capture size.

    macOS ``window`` coordinates are global screen points. Windows coordinates
    are screenshot pixels with an origin of (0, 0).
    """
    path, win = mirror.capture()
    w, h = _ocr.image_size(path)
    return {"window": win, "frontmost": mirror.is_frontmost(), "img_px": [w, h]}


def capture_frame():
    """Capture one reusable screen frame for a single observation pipeline."""
    return _FRAME_BROKER.capture()


def frame_source_name():
    """Return the privacy-safe name of the current visual frame source."""
    return _FRAME_BROKER.source_name


def normalize_region(region):
    """Validate and normalize an absolute screen-pixel rectangle."""
    if not isinstance(region, dict):
        raise ValueError("region must be an object with x, y, w and h")
    values = {}
    for key in ("x", "y", "w", "h"):
        value = region.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"region {key} must be numeric")
        values[key] = float(value)
    if values["w"] <= 0 or values["h"] <= 0:
        raise ValueError("region width and height must be positive")
    win = _win()
    if (
        values["x"] < win["x"]
        or values["y"] < win["y"]
        or values["x"] + values["w"] > win["x"] + win["w"]
        or values["y"] + values["h"] > win["y"] + win["h"]
    ):
        raise ValueError("region must fit inside the phone screen")
    return values


def _in_region(item, region):
    try:
        x = float(item["x"])
        y = float(item["y"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        region["x"] <= x <= region["x"] + region["w"]
        and region["y"] <= y <= region["y"] + region["h"]
    )


def screenshot(path=None, region=None):
    """Capture the phone screen to a PNG and return its path. View it to see
    the phone; combine with ocr() for coordinates. When ``region`` is supplied,
    the PNG is cropped while coordinates elsewhere remain absolute screen pixels.
    """
    if region is None:
        p, _ = mirror.capture(path)
        return p

    normalized = normalize_region(region)
    p, win = mirror.capture()
    from PIL import Image

    source = Path(p)
    output = Path(path) if path is not None else source.with_name(f"{source.stem}-region.png")
    with Image.open(source) as image:
        sx = image.width / win["w"]
        sy = image.height / win["h"]
        left = round((normalized["x"] - win["x"]) * sx)
        top = round((normalized["y"] - win["y"]) * sy)
        right = round((normalized["x"] + normalized["w"] - win["x"]) * sx)
        bottom = round((normalized["y"] + normalized["h"] - win["y"]) * sy)
        image.crop((left, top, right, bottom)).save(output, format="PNG")
    return str(output)


def screen(path=None):
    """Alias for screenshot(); retained as the concise agent-facing API."""
    return screenshot(path)


# --- reading the screen ---

def _accessibility_is_sparse(items):
    informative = 0
    for item in items:
        if item.get("role") == "XCUIElementTypeApplication":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            informative += 1
    return informative < 3


def _same_text_nearby(first, second):
    first_text = first.get("text")
    second_text = second.get("text")
    if not isinstance(first_text, str) or not isinstance(second_text, str):
        return False
    if first_text.strip().casefold() != second_text.strip().casefold():
        return False
    try:
        first_x, first_y = float(first["x"]), float(first["y"])
        second_x, second_y = float(second["x"]), float(second["y"])
        first_w, first_h = float(first.get("w") or 0), float(first.get("h") or 0)
        second_w, second_h = float(second.get("w") or 0), float(second.get("h") or 0)
    except (KeyError, TypeError, ValueError):
        return False
    x_tolerance = max(24.0, (first_w + second_w) / 2)
    y_tolerance = max(16.0, (first_h + second_h) / 2)
    return abs(first_x - second_x) <= x_tolerance and abs(first_y - second_y) <= y_tolerance


def _merge_accessibility_and_ocr(accessible, ocr_boxes):
    merged = list(accessible)
    for item in ocr_boxes:
        candidate = dict(item)
        candidate.setdefault("source", "ocr")
        if any(_same_text_nearby(existing, candidate) for existing in accessible):
            continue
        merged.append(candidate)
    return merged

def elements(min_confidence=0.3, region=None, frame=None):
    """Visible text/elements with tap-ready centers.

    Windows prefers WDA accessibility and falls back to local OCR when the
    current UI is not exposed through accessibility. macOS keeps using Vision
    OCR.
    """
    if _WINDOWS:
        normalized_region = normalize_region(region) if region is not None else None
        try:
            accessible = mirror.accessibility_elements()
        except RuntimeError:
            accessible = []
        if normalized_region is not None:
            accessible = [item for item in accessible if _in_region(item, normalized_region)]
        if accessible and not _accessibility_is_sparse(accessible):
            return accessible
        if accessible:
            try:
                if frame is not None:
                    if normalized_region is None:
                        path = frame.variant(max_long_edge=1600, image_format="PNG")
                        win = frame.window
                    else:
                        path = frame.variant(
                            region=normalized_region,
                            max_long_edge=1600,
                            image_format="PNG",
                        )
                        win = normalized_region
                elif normalized_region is None:
                    path, win = mirror.capture()
                else:
                    path, win = screenshot(region=normalized_region), normalized_region
                ocr_boxes = [
                    o for o in _ocr.recognize(path, win)
                    if o["confidence"] >= min_confidence
                ]
            except Exception:
                return accessible
            return _merge_accessibility_and_ocr(accessible, ocr_boxes)
        if frame is not None:
            if normalized_region is None:
                path = frame.variant(max_long_edge=1600, image_format="PNG")
                win = frame.window
            else:
                path = frame.variant(
                    region=normalized_region,
                    max_long_edge=1600,
                    image_format="PNG",
                )
                win = normalized_region
            return [o for o in _ocr.recognize(path, win)
                    if o["confidence"] >= min_confidence]
        if normalized_region is not None:
            path, win = screenshot(region=normalized_region), normalized_region
        else:
            path, win = mirror.capture()
        return [o for o in _ocr.recognize(path, win)
                if o["confidence"] >= min_confidence]
    path, win = mirror.capture()
    return [o for o in _ocr.recognize(path, win)
            if o["confidence"] >= min_confidence]


def ocr(min_confidence=0.3, region=None):
    """Compatibility alias for elements(); accessibility-first on Windows."""
    return elements(min_confidence=min_confidence, region=region)


def find_text(query, exact=False):
    """OCR results matching query (case-insensitive substring by default)."""
    if not query:
        raise ValueError("text query must not be empty")
    q = query.lower()
    return [o for o in ocr()
            if (o["text"].lower() == q if exact else q in o["text"].lower())]


def tap_text(query, index=None, exact=False):
    """Find text on screen and tap its center. Raises with what IS visible on
    failure, so the next step is informed."""
    if not query:
        raise ValueError("text query must not be empty")
    visible_boxes = ocr()
    q = query.lower()
    hits = [o for o in visible_boxes
            if (o["text"].lower() == q if exact else q in o["text"].lower())]
    if not hits:
        visible = [o["text"] for o in visible_boxes][:30]
        raise RuntimeError(f"no visible text matches {query!r}; saw: {visible}")
    if index is None and len(hits) != 1:
        raise RuntimeError(
            f"visible text {query!r} is ambiguous ({len(hits)} matches); "
            "refine the query or pass an explicit index")
    if index is None:
        index = 0
    hit = hits[index]
    if _WINDOWS and hit.get("source") == "accessibility" and len(hits) == 1:
        mirror.tap_accessibility(hit)
    else:
        tap(hit["x"], hit["y"])
    return hit


# --- gestures relative to the visible phone screen ---

def _win():
    return mirror.ensure_window()


def swipe(direction, distance=0.4):
    """swipe('up'|'down'|'left'|'right') — a touch-drag centered on screen.
    Direction is finger motion: swipe('up') moves content up (scrolls down)."""
    w = _win()
    cx, cy = w["x"] + w["w"] / 2, w["y"] + w["h"] / 2
    dx = {"left": -1, "right": 1}.get(direction, 0) * w["w"] * distance
    dy = {"up": -1, "down": 1}.get(direction, 0) * w["h"] * distance
    if not dx and not dy:
        raise ValueError(f"unknown direction {direction!r}")
    # Fast, short drag = a momentum flick. A slow drag barely registers on iOS
    # (it won't even flip a Home-Screen page); the flick is what snaps pages
    # and carousels. For scrolling lists use scroll()/scroll_collect() instead.
    mirror.drag(cx - dx / 2, cy - dy / 2, cx + dx / 2, cy + dy / 2,
                duration=0.12, steps=6)


def scroll(amount=300):
    """Scroll at screen center; use swipe() when momentum matters."""
    w = _win()
    mirror.scroll_wheel(-amount, w["x"] + w["w"] / 2, w["y"] + w["h"] / 2)


# --- scrolling through lists ---
#
# End-of-list is decided by whether the SCREEN MOVED, never by whether the
# caller's parser found new items. A dense list or a missed OCR row must not
# read as "done" — only the pixels going still (after a settle window that lets
# lazy-loaded content arrive) means the end.

def _content_texts(min_conf=0.4, top_frac=0.06, bottom_frac=0.92):
    """Visible elements in the scrollable area, excluding the volatile status bar
    (clock/battery) at top and the nav/home strip at bottom."""
    win = _win()
    top = win["y"] + win["h"] * top_frac
    bot = win["y"] + win["h"] * bottom_frac
    return [o for o in elements(min_confidence=min_conf)
            if top < o["y"] < bot and o["confidence"] >= min_conf]


def _text_set(boxes):
    return frozenset(o["text"].strip() for o in boxes if o["text"].strip())


def _overlap(a, b):
    """Jaccard overlap of two text sets: ~1.0 = same screen, low = it moved."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def scroll_screen(direction="up", amount=0.6, settle=2.5, moved_thresh=0.6):
    """One scroll gesture, then wait for the screen to settle (so a lazy-load
    spinner resolves before we judge movement).

    Returns {moved, overlap, before, after, boxes} — `boxes` is the settled
    content OCR, ready to parse. `moved` is False when overlap >= moved_thresh:
    the list didn't advance. The default 0.6 sits in the empirical gap between
    real forward progress (overlap < ~0.45) and overscroll bounce at a boundary
    (overlap > ~0.7), which springs the content and would otherwise read as
    movement and defeat end-detection.
    """
    w = mirror.ensure_window()
    sign = {"up": -1, "down": 1}.get(direction)  # 'up' reveals content below
    if sign is None:
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")
    before = _text_set(_content_texts())
    # macOS uses wheel scrolling here. The Windows backend maps this helper to
    # a CoreDevice touch drag because there is no mirroring-window wheel event.
    # amount is a fraction of the visible screen height.
    mirror.scroll_wheel(sign * int(w["h"] * amount),
                        w["x"] + w["w"] / 2, w["y"] + w["h"] / 2, steps=10)
    time.sleep(0.4)
    prev_boxes, prev = None, None
    deadline = time.time() + settle
    while time.time() < deadline:
        boxes = _content_texts()
        cur = _text_set(boxes)
        if cur == prev:                 # two identical captures = settled
            break
        prev, prev_boxes = cur, boxes
        time.sleep(0.35)
    after = prev or frozenset()
    return {"moved": _overlap(before, after) < moved_thresh,
            "overlap": round(_overlap(before, after), 3),
            "before": before, "after": after, "boxes": prev_boxes or []}


def scroll_until(done, direction="up", amount=0.6, max_scrolls=60, settle=2.5):
    """Scroll until `done(boxes)` is truthy or the list stops moving.

    `done` receives the current content OCR (list of boxes) and returns a
    truthy value to stop; that value is returned. Returns None if the end of
    the list is reached first.
    """
    boxes = _content_texts()
    hit = done(boxes)
    if hit:
        return hit
    stale = 0
    for _ in range(max_scrolls):
        res = scroll_screen(direction, amount, settle)
        hit = done(res["boxes"])
        if hit:
            return hit
        if res["moved"]:
            stale = 0
        else:
            stale += 1
            if stale >= 2:              # confirmed still after a retry
                return None
            time.sleep(0.8)
            mirror.activate()
    return None


def scroll_collect(extract=None, key=None, direction="up", amount=0.6,
                   max_scrolls=400, end_after=3, settle=2.5, on_progress=None):
    """Scroll a list top-to-bottom, extracting and de-duping items each screen,
    until the list reaches its true end.

    - extract(boxes) -> list of items for the current screen. Default returns
      each content text line, so a bare scroll_collect() gathers all text.
    - key(item) -> hashable de-dup key (default: the item itself).
    - Stops after `end_after` consecutive non-moving scrolls (the settle window
      already gave lazy-load a chance), or `max_scrolls`.

    Returns {items, stop, scrolls}. `stop` is 'reached-end' or 'max-scrolls'.
    Use amount < 1.0 so screens overlap and no row falls between captures.
    """
    extract = extract or (lambda boxes: [o["text"].strip() for o in boxes
                                         if o["text"].strip()])
    key = key or (lambda x: x)
    seen, order = set(), []

    def ingest(boxes):
        new = 0
        for item in extract(boxes):
            k = key(item)
            if k in seen:
                continue
            seen.add(k)
            order.append(item)
            new += 1
        return new

    ingest(_content_texts())
    stale = 0
    for i in range(1, max_scrolls + 1):
        res = scroll_screen(direction, amount, settle)
        new = ingest(res["boxes"])
        if on_progress:
            on_progress(i, len(order), new, res["moved"], res["overlap"])
        if res["moved"]:
            stale = 0
        else:
            stale += 1
            if stale >= end_after:
                return {"items": order, "stop": "reached-end", "scrolls": i}
            time.sleep(0.8)            # extra grace for a slow lazy-load
            mirror.activate()
    return {"items": order, "stop": "max-scrolls", "scrolls": max_scrolls}


# --- navigation ---

def home():
    """Go to the iPhone Home Screen."""
    press("cmd+1")
    time.sleep(0.8)


def app_switcher():
    if _WINDOWS:
        raise RuntimeError("app_switcher() is not supported by the Windows CoreDevice MVP")
    press("cmd+2")
    time.sleep(0.8)


def open_app(name):
    """Open an installed app by name.

    On Windows the caller should use a fresh observation as the readiness
    boundary instead of paying for a second screenshot-based stability check.
    """
    if _WINDOWS:
        mirror.open_app(name)
        return
    press("cmd+3")
    time.sleep(0.9)
    type_text(name)
    time.sleep(1.2)  # let results populate before committing
    press("return")
    wait_stable()


# --- timing ---

def wait(seconds=1.0):
    time.sleep(seconds)


def wait_stable(timeout=6.0, interval=0.5, settle=2):
    """Wait for a static screen or a bounded ambient-animation steady state.

    Windows first preserves the old strict signature comparison. If a screen
    never becomes pixel-still because of a small spinner/glow/video overlay,
    three consecutive low and similarly-sized frame deltas are accepted as a
    stable dynamic state. Large or erratic transitions continue waiting.
    """
    prev, same = None, 0
    motion = []
    dynamic_window = 3
    dynamic_max_distance = 0.05
    dynamic_max_spread = 0.02
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        path, _ = mirror.capture()
        digest = (mirror.image_signature(path) if _WINDOWS
                  else hashlib.md5(Path(path).read_bytes()).hexdigest())
        unchanged = (mirror.image_signatures_close(prev, digest) if _WINDOWS and prev is not None
                     else digest == prev)
        same = same + 1 if unchanged else 0
        if same >= settle - 1:
            return True
        if _WINDOWS and prev is not None:
            distance = mirror.image_signature_distance(prev, digest)
            motion.append(distance)
            motion = motion[-dynamic_window:]
            if (
                len(motion) == dynamic_window
                and max(motion) <= dynamic_max_distance
                and max(motion) - min(motion) <= dynamic_max_spread
            ):
                return True
        prev = digest
        time.sleep(interval)
    return False


def _load_agent_helpers():
    p = AGENT_WORKSPACE / "agent_helpers.py"
    if not p.exists():
        return
    spec = importlib.util.spec_from_file_location("phone_harness_agent_helpers", p)
    if not spec or not spec.loader:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, value in vars(module).items():
        if not name.startswith("_"):
            globals()[name] = value


_load_agent_helpers()
