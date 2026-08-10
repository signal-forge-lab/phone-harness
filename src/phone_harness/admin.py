"""Diagnostics: `phone-harness --doctor` walks the permission/session ladder."""
import os, subprocess, sys, tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _check(label, ok, hint=""):
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" - {hint}" if not ok and hint else ""))
    return ok


def _windows_doctor():
    print("phone-harness doctor (Windows)\n")
    ok = True

    for package in ("pymobiledevice3", "paddlepaddle", "paddleocr", "Pillow"):
        try:
            installed = version(package)
        except PackageNotFoundError:
            _check(package, False, f"install {package} in the phone-harness Python environment")
            ok = False
        else:
            ok &= _check(f"{package} {installed}", True)
    if not ok:
        return 1

    from . import windows

    try:
        devices = windows.device_udids()
    except RuntimeError as exc:
        _check(
            "Apple Mobile Device/usbmux transport",
            False,
            f"install/repair Apple Devices or Microsoft Store iTunes ({exc})",
        )
        return 1

    ok &= _check("Apple Mobile Device/usbmux transport", True)
    if not devices:
        _check(
            "iPhone connected",
            False,
            "connect and unlock an iPhone over USB, then approve Trust if prompted",
        )
        return 1
    if len(devices) != 1:
        _check(
            "exactly one iPhone connected",
            False,
            f"found {len(devices)} devices; disconnect all but the intended phone",
        )
        return 1
    ok &= _check(f"iPhone connected ({len(devices)})", True)

    try:
        win = windows.ensure_window()
    except RuntimeError as exc:
        _check(
            "CoreDevice developer services / display info",
            False,
            f"enable Developer Mode and mount the DeveloperDiskImage if required ({exc})",
        )
        return 1
    ok &= _check(f"display info ({win['w']}x{win['h']})", True)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        try:
            path, capture_win = windows.capture(path)
            size = os.path.getsize(path)
        except RuntimeError as exc:
            _check("CoreDevice screenshot", False, str(exc))
            return 1
        ok &= _check(f"CoreDevice screenshot ({size} bytes)", size > 1000)

        try:
            from . import paddle_ocr

            count = len(paddle_ocr.recognize(path, capture_win))
        except Exception as exc:
            _check("PaddleOCR / PP-OCRv6 medium", False, str(exc))
            return 1
        ok &= _check(f"PaddleOCR works ({count} text boxes)", True)
    finally:
        Path(path).unlink(missing_ok=True)

    print("\nall clear" if ok else "\nfix the FAILs above, then re-run")
    return 0 if ok else 1


def run_doctor():
    if sys.platform == "win32":
        return _windows_doctor()

    print("phone-harness doctor\n")
    ok = True

    try:
        import Quartz, Vision, AppKit  # noqa: F401
        ok &= _check("pyobjc frameworks (Quartz, Vision, AppKit)", True)
    except ImportError as e:
        _check("pyobjc frameworks", False,
               f"pip install pyobjc-framework-Quartz pyobjc-framework-Vision ({e})")
        return 1

    from ApplicationServices import AXIsProcessTrusted
    ok &= _check(
        "Accessibility permission (taps & keystrokes)", AXIsProcessTrusted(),
        "System Settings > Privacy & Security > Accessibility: enable your terminal")

    import Quartz as Q
    ok &= _check(
        "Screen Recording permission (seeing the phone)",
        bool(Q.CGPreflightScreenCaptureAccess()),
        "System Settings > Privacy & Security > Screen Recording: enable your terminal")

    from . import mirror
    ok &= _check(f"{mirror.APP_NAME} installed", Path(mirror.APP_PATH).exists(),
                 "requires macOS Sequoia+ with a paired iPhone")

    running = mirror.running_app() is not None
    _check(f"{mirror.APP_NAME} running", running,
           "will auto-launch on first use — not fatal")

    win = mirror.find_window()
    _check("mirroring window found", win is not None,
           "open iPhone Mirroring once manually to pair the phone")

    if win:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            subprocess.run(
                ["screencapture", "-x", "-o", "-l", str(win["id"]), path],
                check=True)
            size = os.path.getsize(path)
            ok &= _check(f"window capture works ({size} bytes)", size > 20_000,
                         "capture is blank — Screen Recording permission "
                         "needs a terminal restart to take effect")
            if size > 20_000:
                from . import ocr
                n = len(ocr.recognize(path, win))
                _check(f"Vision OCR works ({n} text boxes)", True)
        finally:
            os.unlink(path)

    print("\nall clear" if ok else "\nfix the FAILs above, then re-run")
    print("\nnote: these are the permissions currently known to be required. A "
          "fresh\nmachine may still prompt for more the first time an action "
          "runs — approve\nthem in System Settings if a step silently does "
          "nothing despite this passing.")
    return 0 if ok else 1
