"""Windows transport for a real iPhone via pymobiledevice3 CoreDevice.

Coordinates exposed here are screenshot pixels. Touch commands convert them to
CoreDevice's 0..65535 normalized HID coordinate space at the boundary.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image


TMP = Path(tempfile.gettempdir()) / "phone-harness"
TMP.mkdir(exist_ok=True)
LOG = logging.getLogger(__name__)
_SCREEN_SIZE = None
_DEVICE_UDID = None
_PRODUCT_VERSION = None


def _clear_device_cache():
    global _DEVICE_UDID, _SCREEN_SIZE, _PRODUCT_VERSION
    _DEVICE_UDID = None
    _SCREEN_SIZE = None
    _PRODUCT_VERSION = None


def _run_pm3(*args, timeout=90):
    started = time.perf_counter()
    operation = "/".join(map(str, args[:4]))
    cmd = [sys.executable, "-m", "pymobiledevice3", *map(str, args)]
    env = os.environ.copy()
    if _DEVICE_UDID:
        env["PYMOBILEDEVICE3_UDID"] = _DEVICE_UDID
    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        _clear_device_cache()
        LOG.debug("operation=%s duration=%.3f result=timeout", operation, time.perf_counter() - started)
        raise RuntimeError(f"pymobiledevice3 timed out after {timeout}s: {operation}") from exc
    stdout, stderr = result.stdout.strip(), result.stderr.strip()
    if result.returncode != 0 or " ERROR " in stderr or "Traceback (" in stderr:
        _clear_device_cache()
        LOG.debug("operation=%s duration=%.3f result=fail", operation, time.perf_counter() - started)
        detail = stderr or stdout or f"exit {result.returncode}"
        raise RuntimeError(f"pymobiledevice3 failed: {detail}")
    LOG.debug("operation=%s duration=%.3f result=pass", operation, time.perf_counter() - started)
    return stdout


def _json_pm3(*args, timeout=90):
    text = _run_pm3(*args, timeout=timeout)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"pymobiledevice3 returned non-JSON output: {text[:500]}") from exc


def device_udids():
    """Return USB-connected UDIDs without opening lockdownd sessions."""
    return _json_pm3("usbmux", "list", "--usb", "--simple")


def _select_device(devices):
    if not devices:
        raise RuntimeError("no iPhone is connected through usbmux")
    if len(devices) != 1:
        raise RuntimeError(f"expected exactly one iPhone, found {len(devices)}")
    return devices[0]


def _require_device():
    global _DEVICE_UDID
    if _DEVICE_UDID is None:
        _DEVICE_UDID = _select_device(device_udids())
    return _DEVICE_UDID


def _product_version():
    global _PRODUCT_VERSION
    _require_device()
    if _PRODUCT_VERSION is None:
        _PRODUCT_VERSION = str(_json_pm3("lockdown", "get", "--key", "ProductVersion"))
    return _PRODUCT_VERSION


def _remote_control_supported(version):
    try:
        return int(str(version).split(".", 1)[0]) >= 27
    except ValueError:
        return False


def _require_remote_control():
    version = _product_version()
    if not _remote_control_supported(version):
        raise RuntimeError(
            f"CoreDevice touchscreen/virtual-keyboard remote control requires iOS 27.0 or later; "
            f"connected device is iOS {version}"
        )


def connection_state():
    """Return host transport/device readiness without mutating device state."""
    try:
        devices = device_udids()
    except RuntimeError:
        return "transport-unavailable"
    if not devices:
        return "no-device"
    if len(devices) != 1:
        return "ambiguous-device"
    return "ready"


def is_frontmost():
    return True


def activate():
    _require_device()


def _display_size(data):
    try:
        displays = data["displays"]
        display = next((item for item in displays if item.get("primary")), displays[0])
        size = display["currentMode"]["size"]
        if isinstance(size, dict):
            return int(size["width"]), int(size["height"])
        return int(size[0]), int(size[1])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(f"cannot determine iPhone display size from CoreDevice response: {data!r}") from exc


def ensure_window(timeout=90):
    """Return a mirror-compatible screen rectangle backed by CoreDevice info."""
    _require_device()
    if _SCREEN_SIZE is not None:
        width, height = _SCREEN_SIZE
    else:
        width, height = _display_size(
            _json_pm3("developer", "core-device", "get-display-info", timeout=timeout)
        )
    return {"x": 0, "y": 0, "w": width, "h": height}


def find_window():
    try:
        return ensure_window()
    except RuntimeError:
        return None


def capture(path=None, retries=0):
    """Capture the real iPhone screen as PNG and return (path, pixel bounds)."""
    global _SCREEN_SIZE
    del retries  # retained for mirror.capture call compatibility
    _SCREEN_SIZE = None
    _require_device()
    path = Path(path or TMP / "screen.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".png", dir=path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    temp_path.unlink(missing_ok=True)
    try:
        _run_pm3("developer", "core-device", "screen-capture", "screenshot", temp_path)
        if not temp_path.exists() or temp_path.stat().st_size == 0:
            raise RuntimeError("CoreDevice screenshot command completed without a PNG")
        with Image.open(temp_path) as image:
            width, height = image.size
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
    _SCREEN_SIZE = (width, height)
    return str(path), {"x": 0, "y": 0, "w": width, "h": height}


def pixel_to_hid(x, y, width, height):
    if width <= 0 or height <= 0:
        raise ValueError("display width and height must be positive")
    x = min(max(float(x), 0.0), float(width))
    y = min(max(float(y), 0.0), float(height))
    return round(x * 65535 / width), round(y * 65535 / height)


def _screen_point(x, y):
    win = ensure_window()
    return pixel_to_hid(x, y, win["w"], win["h"])


def tap(x, y):
    _require_remote_control()
    hx, hy = _screen_point(x, y)
    _run_pm3("developer", "core-device", "universal-hid-service", "tap", hx, hy)


def drag(x1, y1, x2, y2, duration=0.6, steps=30):
    _require_remote_control()
    win = ensure_window()
    h1 = pixel_to_hid(x1, y1, win["w"], win["h"])
    h2 = pixel_to_hid(x2, y2, win["w"], win["h"])
    _run_pm3(
        "developer", "core-device", "universal-hid-service", "drag",
        h1[0], h1[1], h2[0], h2[1], "--steps", steps, "--duration", duration,
    )


def long_press(x, y, duration=0.8):
    drag(x, y, x, y, duration=duration, steps=max(2, round(duration / 0.05)))


def scroll_wheel(dy, x, y, steps=10):
    """Mirror-compatible scroll implemented as a real touch drag on Windows."""
    if not dy:
        return
    win = ensure_window()
    target_y = min(max(y + dy, 0), win["h"])
    drag(x, y, x, target_y, duration=0.35, steps=steps)


def _is_printable_ascii(text):
    return all(32 <= ord(ch) <= 126 for ch in text)


def type_text(text, delay=0.03):
    del delay  # CoreDevice CLI owns key timing
    if not text:
        return
    if not _is_printable_ascii(text):
        raise ValueError("Windows CoreDevice typing currently supports printable ASCII only")
    _require_remote_control()
    _run_pm3("developer", "core-device", "universal-hid-service", "type", text)


def press(combo):
    key = combo.lower()
    if key in {"cmd+1", "home"}:
        _require_device()
        _run_pm3("developer", "core-device", "hid", "button", "home")
        return
    raise ValueError(f"Windows CoreDevice backend does not support key combo {combo!r}")


_SYSTEM_APP_ALIASES = {
    "settings": "com.apple.Preferences",
    "notes": "com.apple.mobilenotes",
    "weather": "com.apple.weather",
}


def _resolve_app_bundle(name, apps):
    query = name.casefold()
    alias = _SYSTEM_APP_ALIASES.get(query)
    if alias and any(app.get("bundleIdentifier") == alias for app in apps):
        return alias
    matches = []
    for app in apps:
        bundle = app.get("bundleIdentifier")
        if not bundle:
            continue
        values = (bundle, app.get("CFBundleDisplayName"), app.get("CFBundleName"))
        if any(isinstance(value, str) and value.casefold() == query for value in values):
            matches.append(bundle)
    matches = list(dict.fromkeys(matches))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(f"no installed app exactly matches {name!r}")
    raise RuntimeError(f"multiple installed apps match {name!r}: {matches}")


def _normalize_apps(data):
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected installed-app response: {type(data).__name__}")
    return [
        {**(info if isinstance(info, dict) else {}), "bundleIdentifier": bundle}
        for bundle, info in data.items()
    ]


def open_app(name):
    _require_device()
    apps = _normalize_apps(_json_pm3("apps", "list"))
    bundle = _resolve_app_bundle(name, apps)
    # CoreDevice's current CLI requires at least one application argument even
    # when the service call itself accepts none. DVT launch accepts the bundle
    # identifier alone and reaches the same user-visible result without a dummy
    # argument.
    _run_pm3("developer", "dvt", "launch", "--no-kill-existing", bundle)
    return bundle


def image_signature(path):
    """Small quantized grayscale signature for stable-screen comparisons."""
    with Image.open(path) as image:
        data = image.convert("L").resize((64, 64)).tobytes()
    return bytes(value // 16 for value in data)
