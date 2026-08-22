"""Windows transport for a real iPhone via pymobiledevice3.

Coordinates exposed here are screenshot pixels. Touch commands convert them to
CoreDevice HID coordinates on iOS 27+ or WDA logical screen coordinates on
older supported devices at the boundary.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from defusedxml import ElementTree as DefusedET
from PIL import Image


TMP = Path(tempfile.gettempdir()) / "phone-harness"
TMP.mkdir(exist_ok=True)
LOG = logging.getLogger(__name__)
_SCREEN_SIZE = None
_DEVICE_UDID = None
_PRODUCT_VERSION = None
_WDA_RUNNER_BUNDLE = None
_WDA_READY = False
_WDA_RUNNER_PROCESS = None
_WDA_RECOVERY_COUNT = 0
_WDA_STATE = "not_started"
_WDA_STARTUP_BACKOFF_UNTIL = 0.0
_WDA_REPEAT_TAP_SETTLE_SECONDS = 0.25
_WDA_STARTUP_BACKOFF_SECONDS = 20.0
_WDA_RUNNER_STARTUP_TIMEOUT_SECONDS = 600
_POINT_SCALE = None
_DEVICE_TRANSPORT = None
_DEVICE_RSD = None
_APP_BUNDLE_CACHE = {}
_INPROCESS_LOOP = None
_INPROCESS_RSD_PROVIDER = None
_INPROCESS_RSD_ENDPOINT = None
_INPROCESS_WDA_CLIENT = None
_WDA_CAPTURE_PROBE_BACKOFF_UNTIL = 0.0
_PM3_PROCESS_COUNT = 0
_PM3_LAST_OPERATION = None
_PM3_LAST_DURATION_MS = None
_PM3_LAST_RESULT = None
_WDA_BUNDLE_PREFIX = "com.iw.phoneharness.wda"
_TUNNELD_URL = "http://127.0.0.1:49151"


def _clear_device_cache():
    global _DEVICE_UDID, _DEVICE_TRANSPORT, _DEVICE_RSD, _SCREEN_SIZE, _PRODUCT_VERSION, _WDA_RUNNER_BUNDLE, _WDA_READY, _WDA_STATE, _WDA_STARTUP_BACKOFF_UNTIL, _WDA_CAPTURE_PROBE_BACKOFF_UNTIL, _POINT_SCALE
    _close_inprocess_rsd()
    _DEVICE_UDID = None
    _DEVICE_TRANSPORT = None
    _DEVICE_RSD = None
    _SCREEN_SIZE = None
    _PRODUCT_VERSION = None
    _WDA_RUNNER_BUNDLE = None
    _WDA_READY = False
    _WDA_STATE = "not_started"
    _WDA_STARTUP_BACKOFF_UNTIL = 0.0
    _WDA_CAPTURE_PROBE_BACKOFF_UNTIL = 0.0
    _POINT_SCALE = None
    _APP_BUNDLE_CACHE.clear()


def _transport_mode():
    mode = os.environ.get("PHONE_HARNESS_TRANSPORT", "usb").strip().lower()
    if mode not in {"usb", "wifi", "auto"}:
        raise RuntimeError("PHONE_HARNESS_TRANSPORT must be usb, wifi, or auto")
    return mode


def transport_mode():
    """Return configured Windows transport mode without touching the device."""
    return _transport_mode()


def _configured_udid():
    return os.environ.get("PHONE_HARNESS_UDID") or os.environ.get("PYMOBILEDEVICE3_UDID")


def _transport_cli_args():
    if _DEVICE_TRANSPORT == "wifi" and _DEVICE_RSD:
        return ["--rsd", _DEVICE_RSD[0], str(_DEVICE_RSD[1])]
    return []


def _developer_transport_cli_args():
    """Use tunneld explicitly for pymobiledevice3 developer commands on Wi-Fi."""
    if _DEVICE_TRANSPORT == "wifi" and _DEVICE_RSD:
        return ["--tunnel", _require_device()]
    return _transport_cli_args()


def _inprocess_supported():
    return _DEVICE_TRANSPORT == "wifi" and _DEVICE_RSD is not None


def _async_loop():
    global _INPROCESS_LOOP
    if _INPROCESS_LOOP is None or _INPROCESS_LOOP.is_closed():
        _INPROCESS_LOOP = asyncio.new_event_loop()
    return _INPROCESS_LOOP


def _run_async(awaitable, timeout=30):
    loop = _async_loop()
    return loop.run_until_complete(asyncio.wait_for(awaitable, timeout=timeout))


def _close_inprocess_rsd():
    global _INPROCESS_RSD_PROVIDER, _INPROCESS_RSD_ENDPOINT, _INPROCESS_WDA_CLIENT
    provider = _INPROCESS_RSD_PROVIDER
    _INPROCESS_RSD_PROVIDER = None
    _INPROCESS_RSD_ENDPOINT = None
    _INPROCESS_WDA_CLIENT = None
    if provider is None:
        return
    try:
        _run_async(provider.close(), timeout=3)
    except Exception:
        pass


def _inprocess_rsd():
    global _INPROCESS_RSD_PROVIDER, _INPROCESS_RSD_ENDPOINT
    _require_device()
    if not _inprocess_supported():
        return None
    endpoint = _DEVICE_RSD
    if _INPROCESS_RSD_PROVIDER is not None and _INPROCESS_RSD_ENDPOINT == endpoint:
        return _INPROCESS_RSD_PROVIDER
    _close_inprocess_rsd()
    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService

    provider = RemoteServiceDiscoveryService(endpoint)
    try:
        _run_async(provider.connect(), timeout=10)
    except Exception as exc:
        try:
            _run_async(provider.close(), timeout=2)
        except Exception:
            pass
        raise RuntimeError("pymobiledevice3 in-process RSD connection failed") from exc
    _INPROCESS_RSD_PROVIDER = provider
    _INPROCESS_RSD_ENDPOINT = endpoint
    return provider


def _inprocess_wda_client():
    global _INPROCESS_WDA_CLIENT
    provider = _inprocess_rsd()
    if provider is None:
        return None
    if _INPROCESS_WDA_CLIENT is None:
        from pymobiledevice3.services.wda import WdaServiceClient

        _INPROCESS_WDA_CLIENT = WdaServiceClient(provider)
    return _INPROCESS_WDA_CLIENT


def _inprocess_wda_status(timeout=5):
    client = _inprocess_wda_client()
    if client is None:
        return None
    try:
        return _run_async(client.get_status(), timeout=timeout)
    except Exception as exc:
        raise RuntimeError("pymobiledevice3 in-process WDA status failed") from exc


def _wda_items_from_source(source):
    root = DefusedET.fromstring(source)
    items = []
    for elem in root.iter():
        attrs = elem.attrib
        rect = {
            "x": attrs.get("x"),
            "y": attrs.get("y"),
            "width": attrs.get("width"),
            "height": attrs.get("height"),
        }
        if all(value is None for value in rect.values()):
            rect = None
        name = attrs.get("name")
        label = attrs.get("label")
        value = attrs.get("value")
        if not (name or label or value or rect):
            continue
        items.append({
            "type": elem.tag,
            "name": name,
            "label": label,
            "value": value,
            "visible": attrs.get("visible"),
            "rect": rect,
        })
    return items


def _inprocess_wda_items(timeout=30):
    client = _inprocess_wda_client()
    if client is None:
        return None
    try:
        source = _run_async(
            client.get_source(
                excluded_attributes=["accessible", "index", "placeholderValue", "traits", "enabled"]
            ),
            timeout=timeout,
        )
    except Exception as exc:
        raise RuntimeError(f"pymobiledevice3 in-process WDA failed: {exc}") from exc
    return _wda_items_from_source(source)


def _inprocess_wda_screenshot(timeout=10):
    client = _inprocess_wda_client()
    if client is None:
        return None
    try:
        return _run_async(client.get_screenshot(), timeout=timeout)
    except Exception as exc:
        raise RuntimeError(f"pymobiledevice3 in-process WDA screenshot failed: {exc}") from exc


def _probe_existing_wda_screenshot():
    """Try one fast screenshot from an already-running WDA.

    The screenshot itself is the readiness probe, avoiding a separate status
    round-trip on the first visual-only capture in a fresh process.
    """
    global _WDA_READY, _WDA_STATE, _WDA_CAPTURE_PROBE_BACKOFF_UNTIL
    if _WDA_READY:
        return None
    if not _inprocess_supported():
        return None
    now = time.monotonic()
    if now < _WDA_CAPTURE_PROBE_BACKOFF_UNTIL:
        return None
    try:
        data = _inprocess_wda_screenshot(timeout=1)
    except Exception:
        _WDA_CAPTURE_PROBE_BACKOFF_UNTIL = now + 20.0
        return None
    if not data:
        _WDA_CAPTURE_PROBE_BACKOFF_UNTIL = now + 20.0
        return None
    _WDA_READY = True
    _WDA_STATE = "ready"
    _WDA_CAPTURE_PROBE_BACKOFF_UNTIL = 0.0
    return data


def _inprocess_wda_batch(actions, timeout=30):
    client = _inprocess_wda_client()
    if client is None:
        return False

    repeated_same_coordinate_taps = (
        len(actions) > 1
        and all(
            isinstance(action, dict)
            and action.get("op") == "tap-coordinate"
            and action.get("x") == actions[0].get("x")
            and action.get("y") == actions[0].get("y")
            for action in actions
        )
    )

    async def task():
        session_id = client.session_id
        if not session_id:
            session_id = await client.start_session_for_active_app()
            await client.set_wait_for_idle_timeout(0, session_id=session_id)
        if repeated_same_coordinate_taps:
            # Repeated taps at one board coordinate are producer bursts. WDA
            # can deliver them faster than Merge Boss completes its
            # select->emit state transition, causing apparently successful
            # batches with no produced item. Keep every other mixed batch on
            # the original zero-extra-delay fast path and add only a tiny
            # producer-specific settle between taps.
            for index, action in enumerate(actions):
                await client.run_batch_actions([action], session_id=session_id)
                if index + 1 < len(actions):
                    await asyncio.sleep(_WDA_REPEAT_TAP_SETTLE_SECONDS)
        else:
            await client.run_batch_actions(actions, session_id=session_id)
        return {"sessionId": session_id, "count": len(actions)}

    try:
        _run_async(task(), timeout=timeout)
    except Exception as exc:
        client.session_id = None
        raise RuntimeError(f"pymobiledevice3 in-process WDA batch failed: {exc}") from exc
    return True


def _inprocess_get_display_info(timeout=30):
    provider = _inprocess_rsd()
    if provider is None:
        return None

    async def task():
        from pymobiledevice3.remote.core_device.device_info import DeviceInfoService

        async with DeviceInfoService(provider) as service:
            return await service.get_display_info()

    try:
        return _run_async(task(), timeout=timeout)
    except Exception as exc:
        raise RuntimeError("pymobiledevice3 in-process display info failed") from exc


def _inprocess_list_apps(timeout=30):
    provider = _inprocess_rsd()
    if provider is None:
        return None
    from pymobiledevice3.services.installation_proxy import InstallationProxyService

    try:
        return _run_async(
            InstallationProxyService(lockdown=provider).get_apps(),
            timeout=timeout,
        )
    except Exception as exc:
        raise RuntimeError("pymobiledevice3 in-process app listing failed") from exc


def _inprocess_launch_app(bundle, timeout=30):
    provider = _inprocess_rsd()
    if provider is None:
        return False

    async def task():
        from pymobiledevice3.remote.core_device.app_service import AppServiceService

        async with AppServiceService(provider) as service:
            return await service.launch_application(
                bundle,
                [""],
                kill_existing=False,
                start_suspended=False,
                environment={},
            )

    try:
        _run_async(task(), timeout=timeout)
    except Exception as exc:
        raise RuntimeError("pymobiledevice3 in-process app launch failed") from exc
    return True


def _inprocess_press_home(timeout=10):
    provider = _inprocess_rsd()
    if provider is None:
        return False

    async def task():
        from pymobiledevice3.remote.core_device.hid_service import (
            HID_BUTTON_STATE_DOWN,
            HID_BUTTON_STATE_UP,
            IndigoHIDService,
        )

        async with IndigoHIDService(provider) as service:
            await service.send_button(0x0C, 0x40, HID_BUTTON_STATE_DOWN)
            await asyncio.sleep(0.05)
            await service.send_button(0x0C, 0x40, HID_BUTTON_STATE_UP)

    try:
        _run_async(task(), timeout=timeout)
    except Exception as exc:
        raise RuntimeError("pymobiledevice3 in-process home press failed") from exc
    return True


def _run_pm3(*args, timeout=90, use_transport=True, input_text=None):
    global _PM3_PROCESS_COUNT, _PM3_LAST_OPERATION, _PM3_LAST_DURATION_MS, _PM3_LAST_RESULT
    started = time.perf_counter()
    operation = "/".join(map(str, args[:3]))
    _PM3_PROCESS_COUNT += 1
    _PM3_LAST_OPERATION = operation
    cmd = [sys.executable, "-m", "pymobiledevice3", *map(str, args)]
    if use_transport:
        cmd.extend(_developer_transport_cli_args() if args and args[0] == "developer" else _transport_cli_args())
    env = os.environ.copy()
    if _DEVICE_UDID:
        env["PYMOBILEDEVICE3_UDID"] = _DEVICE_UDID
    try:
        result = subprocess.run(
            cmd,
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        _PM3_LAST_DURATION_MS = round((time.perf_counter() - started) * 1000, 3)
        _PM3_LAST_RESULT = "timeout"
        _clear_device_cache()
        LOG.debug("operation=%s duration=%.3f result=timeout", operation, time.perf_counter() - started)
        raise RuntimeError(f"pymobiledevice3 timed out after {timeout}s: {operation}") from exc
    stdout, stderr = result.stdout.strip(), result.stderr.strip()
    if result.returncode != 0 or " ERROR " in stderr or "Traceback (" in stderr:
        _PM3_LAST_DURATION_MS = round((time.perf_counter() - started) * 1000, 3)
        _PM3_LAST_RESULT = "fail"
        _clear_device_cache()
        LOG.debug("operation=%s duration=%.3f result=fail", operation, time.perf_counter() - started)
        detail = stderr or stdout or f"exit {result.returncode}"
        raise RuntimeError(f"pymobiledevice3 failed: {detail}")
    _PM3_LAST_DURATION_MS = round((time.perf_counter() - started) * 1000, 3)
    _PM3_LAST_RESULT = "pass"
    LOG.debug("operation=%s duration=%.3f result=pass", operation, time.perf_counter() - started)
    return stdout


def _json_pm3(*args, timeout=90, use_transport=True):
    text = _run_pm3(*args, timeout=timeout, use_transport=use_transport)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"pymobiledevice3 returned non-JSON output: {text[:500]}") from exc


def _usb_device_udids():
    return _json_pm3("usbmux", "list", "--usb", "--simple", use_transport=False)


def _tunneld_wifi_devices(timeout=1.5):
    try:
        with urlopen(_TUNNELD_URL, timeout=timeout) as response:
            data = json.load(response)
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "pymobiledevice3 tunneld is unavailable; start it from an elevated terminal for Wi-Fi transport"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError("pymobiledevice3 tunneld returned an unexpected device listing")
    devices = []
    for udid, tunnels in data.items():
        if not isinstance(tunnels, list):
            continue
        for tunnel in tunnels:
            if not isinstance(tunnel, dict):
                continue
            address = tunnel.get("tunnel-address")
            port = tunnel.get("tunnel-port")
            if isinstance(address, str) and isinstance(port, int):
                devices.append((udid, (address, port)))
                break
    return devices


def _discover_devices():
    mode = _transport_mode()
    target = _configured_udid()

    if mode in {"usb", "auto"}:
        try:
            usb = _usb_device_udids()
        except RuntimeError:
            if mode == "usb":
                raise
            usb = []
        if target:
            usb = [udid for udid in usb if udid == target]
        if usb or mode == "usb":
            return [(udid, "usb", None) for udid in usb]

    try:
        wifi = _tunneld_wifi_devices()
    except RuntimeError:
        if mode == "wifi":
            raise
        return []
    if target:
        wifi = [(udid, rsd) for udid, rsd in wifi if udid == target]
    return [(udid, "wifi", rsd) for udid, rsd in wifi]


def device_udids():
    """Return devices visible through the configured USB/Wi-Fi transport."""
    return [udid for udid, _transport, _rsd in _discover_devices()]


def _select_device(devices):
    if not devices:
        raise RuntimeError("no iPhone is available through the configured transport")
    if len(devices) != 1:
        raise RuntimeError(f"expected exactly one iPhone, found {len(devices)}")
    return devices[0]


def _require_device():
    global _DEVICE_UDID, _DEVICE_TRANSPORT, _DEVICE_RSD
    if _DEVICE_UDID is None:
        devices = _discover_devices()
        _DEVICE_UDID = _select_device([udid for udid, _transport, _rsd in devices])
        _DEVICE_TRANSPORT, _DEVICE_RSD = next(
            (transport, rsd) for udid, transport, rsd in devices if udid == _DEVICE_UDID
        )
    return _DEVICE_UDID


def active_transport():
    _require_device()
    return _DEVICE_TRANSPORT


def runtime_transport_status():
    """Return process-local transport telemetry without device identifiers."""
    runner_alive = _WDA_RUNNER_PROCESS is not None and _WDA_RUNNER_PROCESS.poll() is None
    return {
        "active_transport": _DEVICE_TRANSPORT,
        "device_cached": _DEVICE_UDID is not None,
        "rsd_cached": _DEVICE_RSD is not None,
        "wda_ready": bool(_WDA_READY),
        "wda_state": _WDA_STATE,
        "wda_runner_alive": runner_alive,
        "wda_recovery_count": _WDA_RECOVERY_COUNT,
        "pm3_process_count": _PM3_PROCESS_COUNT,
        "last_pm3_operation": _PM3_LAST_OPERATION,
        "last_pm3_duration_ms": _PM3_LAST_DURATION_MS,
        "last_pm3_result": _PM3_LAST_RESULT,
    }


def tunneld_status(timeout=0.5):
    """Return local tunneld reachability without exposing device identifiers."""
    try:
        devices = _tunneld_wifi_devices(timeout=timeout)
    except RuntimeError as exc:
        return {"reachable": False, "device_count": 0, "error": str(exc)}
    return {"reachable": True, "device_count": len(devices)}


def _product_version():
    global _PRODUCT_VERSION
    _require_device()
    if _PRODUCT_VERSION is None:
        provider = _inprocess_rsd() if _inprocess_supported() else None
        if provider is not None:
            _PRODUCT_VERSION = str(provider.product_version)
        else:
            _PRODUCT_VERSION = str(_json_pm3("lockdown", "get", "--key", "ProductVersion"))
    return _PRODUCT_VERSION


def _remote_control_supported(version):
    try:
        return int(str(version).split(".", 1)[0]) >= 27
    except ValueError:
        return False


def _wda_runner_bundle():
    global _WDA_RUNNER_BUNDLE
    if _WDA_RUNNER_BUNDLE is None:
        apps = _inprocess_list_apps() if _inprocess_supported() else None
        if apps is None:
            apps = _json_pm3("apps", "list")
        candidates = [
            bundle_id for bundle_id in apps
            if bundle_id == _WDA_BUNDLE_PREFIX or bundle_id.startswith(f"{_WDA_BUNDLE_PREFIX}.")
        ]
        if len(candidates) != 1:
            raise RuntimeError(
                f"iOS {_product_version()} requires the signed phone-harness WDA runner; "
                f"found {len(candidates)} matching installed apps"
            )
        _WDA_RUNNER_BUNDLE = candidates[0]
    return _WDA_RUNNER_BUNDLE


def _ensure_wda_runner():
    global _WDA_READY, _WDA_RUNNER_PROCESS, _WDA_STATE, _WDA_STARTUP_BACKOFF_UNTIL
    if _WDA_READY:
        _WDA_STATE = "ready"
        return

    now = time.monotonic()
    runner_alive = _WDA_RUNNER_PROCESS is not None and _WDA_RUNNER_PROCESS.poll() is None
    pending_backoff = runner_alive and now < _WDA_STARTUP_BACKOFF_UNTIL
    try:
        if _inprocess_supported():
            try:
                _inprocess_wda_status(timeout=1 if pending_backoff else 5)
            except RuntimeError:
                # A long-lived Wi-Fi runtime can retain an RSD client after its
                # transport session has gone stale even though the separately
                # owned WDA runner is healthy. Refresh only after backoff has
                # elapsed so normal startup keeps the short probe fast.
                if not runner_alive or pending_backoff:
                    raise
                _close_inprocess_rsd()
                _inprocess_wda_status(timeout=5)
        else:
            _run_pm3("developer", "wda", "status", timeout=1 if pending_backoff else 5)
        _WDA_READY = True
        _WDA_STATE = "ready"
        _WDA_STARTUP_BACKOFF_UNTIL = 0.0
        return
    except RuntimeError:
        pass

    if pending_backoff:
        _WDA_STATE = "pending"
        raise RuntimeError("WDA runner is still starting or awaiting device confirmation")

    udid = _require_device()
    if _WDA_RUNNER_PROCESS is None or _WDA_RUNNER_PROCESS.poll() is not None:
        runner = _wda_runner_bundle()
        env = os.environ.copy()
        env["PYMOBILEDEVICE3_UDID"] = udid
        _WDA_RUNNER_PROCESS = subprocess.Popen(
            [
                sys.executable, "-m", "pymobiledevice3", "developer", "wda", "run-xctrunner", runner,
                "--startup-timeout", str(_WDA_RUNNER_STARTUP_TIMEOUT_SECONDS),
                *_developer_transport_cli_args(),
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    _WDA_STATE = "starting"

    deadline = time.monotonic() + 35
    last_error = None
    while time.monotonic() < deadline:
        if _WDA_RUNNER_PROCESS is not None and _WDA_RUNNER_PROCESS.poll() is not None:
            _WDA_STATE = "failed"
            _WDA_STARTUP_BACKOFF_UNTIL = 0.0
            raise RuntimeError("WDA runner exited before becoming ready")
        try:
            if _inprocess_supported():
                _inprocess_wda_status(timeout=5)
            else:
                _run_pm3("developer", "wda", "status", timeout=5)
            _WDA_READY = True
            _WDA_STATE = "ready"
            _WDA_STARTUP_BACKOFF_UNTIL = 0.0
            return
        except RuntimeError as exc:
            last_error = exc
            _require_device()
            time.sleep(0.25)
    _WDA_STATE = "pending"
    _WDA_STARTUP_BACKOFF_UNTIL = time.monotonic() + _WDA_STARTUP_BACKOFF_SECONDS
    raise RuntimeError("WDA runner did not become ready within 35 seconds") from last_error


def _is_stale_wda_application_error(exc):
    text = " ".join(str(exc).split())
    return (
        "previously found element" in text
        and "Application 'local.pid." in text
        and "is not running" in text
    )


def _is_wda_ui_testing_unauthorized_error(exc):
    text = " ".join(str(exc).split()).lower()
    return (
        "not authorized for performing ui testing actions" in text
        or ("xctdaemonerrordomain" in text and "code=41" in text)
    )


def _stop_owned_process_tree(process, timeout=5):
    """Stop a subprocess tree rooted at a process owned by this runtime."""
    if process is None or process.poll() is not None:
        return
    if sys.platform == "win32":
        command = ["taskkill", "/PID", str(process.pid), "/T"]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            pass
        subprocess.run(
            [*command, "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        process.wait(timeout=timeout)
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def _restart_wda_runner():
    """Restart only the phone-harness WDA runner after a proven stale-app failure."""
    global _WDA_READY, _WDA_RUNNER_PROCESS, _WDA_RECOVERY_COUNT, _WDA_STATE, _WDA_STARTUP_BACKOFF_UNTIL

    runner = _wda_runner_bundle()
    process = _WDA_RUNNER_PROCESS
    owned_runner_stopped = False
    _WDA_READY = False
    _WDA_RUNNER_PROCESS = None
    _WDA_STATE = "not_started"
    _WDA_STARTUP_BACKOFF_UNTIL = 0.0

    if process is not None and process.poll() is None:
        _stop_owned_process_tree(process)
        owned_runner_stopped = True

    # A runner can outlive the owning Python runtime. Stop only the signed
    # phone-harness WDA bundle on-device so an orphaned runner cannot keep a
    # stale Application cache alive across MCP/runtime restarts.
    try:
        _run_pm3("developer", "dvt", "pkill", runner, "--bundle", timeout=10)
    except RuntimeError:
        # The local runner may already have torn down the device process.
        # Without an owned runner handle, however, continuing could reuse the
        # same orphaned stale runner, so fail closed and let OCR take over.
        if not owned_runner_stopped:
            raise

    _ensure_wda_runner()
    _WDA_RECOVERY_COUNT += 1


def shutdown_runtime():
    """Stop only a WDA runner owned by this Python runtime."""
    global _WDA_READY, _WDA_RUNNER_PROCESS, _WDA_STATE, _WDA_STARTUP_BACKOFF_UNTIL

    process = _WDA_RUNNER_PROCESS
    runner = _WDA_RUNNER_BUNDLE
    _WDA_READY = False
    _WDA_RUNNER_PROCESS = None
    _WDA_STATE = "not_started"
    _WDA_STARTUP_BACKOFF_UNTIL = 0.0
    if process is not None and process.poll() is None:
        if runner:
            try:
                _run_pm3("developer", "dvt", "pkill", runner, "--bundle", timeout=3)
            except RuntimeError:
                # Runtime shutdown is best-effort. The owned host process tree is
                # still terminated below so the bridge cannot leave a local orphan.
                pass
        _stop_owned_process_tree(process, timeout=2)
    _close_inprocess_rsd()


def _run_wda(*args, timeout=30):
    _ensure_wda_runner()
    return _run_pm3("developer", "wda", *args, timeout=timeout)


def _run_wda_batch(actions, timeout=30):
    _ensure_wda_runner()
    if _inprocess_supported() and _inprocess_wda_batch(actions, timeout=timeout):
        return None
    return _run_pm3(
        "developer", "wda", "batch", "--attach-active-app", "--wait-for-idle-timeout", 0,
        timeout=timeout,
        input_text=json.dumps(actions),
    )


def _wda_action_for_accessibility(item):
    x = item.get("x")
    y = item.get("y")
    if (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and isinstance(y, (int, float))
        and not isinstance(y, bool)
    ):
        x, y = _wda_point(x, y)
        return {"op": "tap-coordinate", "x": x, "y": y}
    name = item.get("name")
    label = item.get("label")
    if isinstance(name, str) and name:
        return {"op": "tap", "selector": name, "using": "accessibility id"}
    if isinstance(label, str) and label:
        return {"op": "tap", "selector": label, "using": "label"}
    raise RuntimeError("accessibility target has neither coordinates nor a usable selector")


def _wda_action_for_tap(x, y):
    x, y = _wda_point(x, y)
    return {"op": "tap-coordinate", "x": x, "y": y}


def _wda_action_for_drag(x1, y1, x2, y2, duration=0.6):
    start_x, start_y = _wda_point(x1, y1)
    end_x, end_y = _wda_point(x2, y2)
    return {
        "op": "swipe",
        "start_x": start_x,
        "start_y": start_y,
        "end_x": end_x,
        "end_y": end_y,
        "duration": float(duration),
    }


def _wda_action_for_swipe(direction, distance=0.4):
    if direction not in {"up", "down", "left", "right"}:
        raise ValueError(f"unknown direction {direction!r}")
    # WDA's native swipe is intentionally directional rather than coordinate based.
    # It avoids turning a page swipe into the press-and-hold gesture used by drag.
    return {"op": "swipe-direction", "direction": direction}


def _wda_action_for_scroll(amount=300):
    win = ensure_window()
    amount = float(amount)
    if amount == 0:
        return None
    distance = min(abs(amount) / float(win["h"]), 1.0)
    return {
        "op": "scroll-direction",
        "direction": "down" if amount > 0 else "up",
        "distance": distance,
    }


def wda_runtime_batch_supported():
    """True when iOS input must use WDA instead of native CoreDevice HID."""
    return not _remote_control_supported(_product_version())


def run_wda_runtime_batch(actions):
    """Execute already-normalized WDA actions in one pymobiledevice3 process/session."""
    if not wda_runtime_batch_supported():
        raise RuntimeError("WDA runtime batching is only used when native CoreDevice HID is unavailable")
    return _run_wda_batch(actions)


def _json_wda(*args, timeout=30):
    text = _run_wda(*args, timeout=timeout)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"WDA returned non-JSON output: {text[:500]}") from exc


def _display_point_scale(data):
    try:
        displays = data["displays"]
        display = next((item for item in displays if item.get("primary")), displays[0])
        scale = float(display["pointScale"])
        if scale <= 0:
            raise ValueError
        return scale
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(f"cannot determine iPhone point scale from CoreDevice response: {data!r}") from exc


def _wda_point(x, y):
    global _POINT_SCALE
    win = ensure_window()
    if _POINT_SCALE is None:
        _POINT_SCALE = _display_point_scale(
            _json_pm3("developer", "core-device", "get-display-info")
        )
    x = min(max(float(x), 0.0), float(win["w"]))
    y = min(max(float(y), 0.0), float(win["h"]))
    return round(x / _POINT_SCALE), round(y / _POINT_SCALE)


def _wda_rect_to_pixels(rect):
    global _POINT_SCALE
    if _POINT_SCALE is None:
        ensure_window()
    assert _POINT_SCALE is not None
    try:
        scale = float(_POINT_SCALE)
        x = float(rect["x"]) * scale
        y = float(rect["y"]) * scale
        width = float(rect["width"]) * scale
        height = float(rect["height"]) * scale
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid WDA element rect: {rect!r}") from exc
    return x, y, width, height


def accessibility_elements():
    """Return visible WDA accessibility text in screenshot-pixel coordinates."""
    _require_device()
    _wda_runner_bundle()
    try:
        if _inprocess_supported():
            _ensure_wda_runner()
            items = _inprocess_wda_items()
        else:
            items = _json_wda("list-items", "--with-rect", "--lean-source")
    except RuntimeError as exc:
        if not _is_stale_wda_application_error(exc):
            raise
        _restart_wda_runner()
        if _inprocess_supported():
            items = _inprocess_wda_items()
        else:
            items = _json_wda("list-items", "--with-rect", "--lean-source")
    if not isinstance(items, list):
        raise RuntimeError("WDA returned an unexpected accessibility listing")

    result = []
    for item in items:
        if not isinstance(item, dict) or item.get("visible") == "false" or not item.get("rect"):
            continue
        try:
            x, y, width, height = _wda_rect_to_pixels(item["rect"])
        except RuntimeError:
            continue
        label = item.get("label")
        value = item.get("value")
        name = item.get("name")
        texts = []
        for text in (label, value):
            if isinstance(text, str) and text.strip() and text not in texts:
                texts.append(text)
        if not texts and isinstance(name, str) and name.strip():
            texts.append(name)
        for text in texts:
            result.append({
                "text": text,
                "confidence": 1.0,
                "x": x + width / 2,
                "y": y + height / 2,
                "w": width,
                "h": height,
                "source": "accessibility",
                "role": item.get("type"),
                "name": name,
                "label": label,
                "value": value,
            })
    return result


def tap_accessibility(item):
    """Tap a uniquely selected WDA accessibility element."""
    if _remote_control_supported(_product_version()):
        tap(item["x"], item["y"])
        return
    name = item.get("name")
    label = item.get("label")
    if isinstance(name, str) and name:
        _run_wda("tap", name, "--using", "accessibility id", "--attach-active-app")
        return
    if isinstance(label, str) and label:
        _run_wda("tap", label, "--using", "label", "--attach-active-app")
        return
    tap(item["x"], item["y"])


def tap_accessibility_batch(items):
    """Tap multiple already-resolved accessibility targets in one WDA session."""
    if _remote_control_supported(_product_version()):
        for item in items:
            tap(item["x"], item["y"])
        return
    actions = [_wda_action_for_accessibility(item) for item in items]
    _run_wda_batch(actions)


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
    global _SCREEN_SIZE, _POINT_SCALE
    _require_device()
    if _SCREEN_SIZE is not None and _POINT_SCALE is not None:
        width, height = _SCREEN_SIZE
    else:
        data = _inprocess_get_display_info(timeout=timeout) if _inprocess_supported() else None
        if data is None:
            data = _json_pm3("developer", "core-device", "get-display-info", timeout=timeout)
        width, height = _display_size(data)
        _SCREEN_SIZE = (width, height)
        _POINT_SCALE = _display_point_scale(data)
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
        width = height = None
        if _inprocess_supported() and not _remote_control_supported(_product_version()):
            _ensure_wda_runner()
            try:
                data = _inprocess_wda_screenshot(timeout=10)
            except Exception as exc:
                # A WDA status probe can remain healthy while the underlying
                # XCTest session has lost authorization for UI-testing actions.
                # Restart only the signed phone-harness runner once, then retry
                # the screenshot so a stale authorized session does not strand
                # all observation until the whole MCP process is restarted.
                if not _is_wda_ui_testing_unauthorized_error(exc):
                    raise
                _restart_wda_runner()
                data = _inprocess_wda_screenshot(timeout=10)
            if not data:
                raise RuntimeError("WDA screenshot returned no PNG data")
            temp_path.write_bytes(data)
            with Image.open(temp_path) as image:
                width, height = image.size
        elif _inprocess_supported():
            try:
                data = (
                    _inprocess_wda_screenshot(timeout=10)
                    if _WDA_READY
                    else _probe_existing_wda_screenshot()
                )
                if data:
                    temp_path.write_bytes(data)
                    with Image.open(temp_path) as image:
                        width, height = image.size
            except Exception:
                temp_path.unlink(missing_ok=True)
                width = height = None

        if width is None or height is None:
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
    if _remote_control_supported(_product_version()):
        hx, hy = _screen_point(x, y)
        _run_pm3("developer", "core-device", "universal-hid-service", "tap", hx, hy)
        return
    wx, wy = _wda_point(x, y)
    _run_wda("tap-coordinate", wx, wy, "--attach-active-app")


def drag(x1, y1, x2, y2, duration=0.6, steps=30):
    if _remote_control_supported(_product_version()):
        win = ensure_window()
        h1 = pixel_to_hid(x1, y1, win["w"], win["h"])
        h2 = pixel_to_hid(x2, y2, win["w"], win["h"])
        _run_pm3(
            "developer", "core-device", "universal-hid-service", "drag",
            h1[0], h1[1], h2[0], h2[1], "--steps", steps, "--duration", duration,
        )
        return
    del steps  # WDA owns gesture sampling.
    start = _wda_point(x1, y1)
    end = _wda_point(x2, y2)
    _run_wda(
        "swipe", start[0], start[1], end[0], end[1],
        "--duration", duration, "--attach-active-app",
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
    del delay  # Backend CLI owns key timing.
    if not text:
        return
    if _remote_control_supported(_product_version()):
        if not _is_printable_ascii(text):
            raise ValueError("Windows CoreDevice typing currently supports printable ASCII only")
        _run_pm3("developer", "core-device", "universal-hid-service", "type", text)
        return
    _run_wda("type", text, "--attach-active-app")


def press(combo):
    key = combo.lower()
    if key in {"cmd+1", "home"}:
        _require_device()
        pressed = _inprocess_press_home() if _inprocess_supported() else False
        if not pressed:
            _run_pm3("developer", "core-device", "hid", "button", "home")
        return
    raise ValueError(f"Windows CoreDevice backend does not support key combo {combo!r}")


_SYSTEM_APP_ALIASES = {
    "settings": "com.apple.Preferences",
    "設定": "com.apple.Preferences",
    "notes": "com.apple.mobilenotes",
    "メモ": "com.apple.mobilenotes",
    "weather": "com.apple.weather",
    "天気": "com.apple.weather",
    "calculator": "com.apple.calculator",
    "計算機": "com.apple.calculator",
    "clock": "com.apple.mobiletimer",
    "時計": "com.apple.mobiletimer",
    "calendar": "com.apple.mobilecal",
    "カレンダー": "com.apple.mobilecal",
    "photos": "com.apple.mobileslideshow",
    "写真": "com.apple.mobileslideshow",
    "camera": "com.apple.camera",
    "カメラ": "com.apple.camera",
    "maps": "com.apple.Maps",
    "マップ": "com.apple.Maps",
    "reminders": "com.apple.reminders",
    "リマインダー": "com.apple.reminders",
    "files": "com.apple.DocumentsApp",
    "ファイル": "com.apple.DocumentsApp",
    "safari": "com.apple.mobilesafari",
    "mail": "com.apple.mobilemail",
    "メール": "com.apple.mobilemail",
    "messages": "com.apple.MobileSMS",
    "メッセージ": "com.apple.MobileSMS",
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


def preflight_open_app(name):
    """Resolve and cache an app name without launching it."""
    _require_device()
    cache_key = name.casefold()
    bundle = _APP_BUNDLE_CACHE.get(cache_key)
    if bundle is not None:
        return bundle
    app_mapping = _inprocess_list_apps() if _inprocess_supported() else None
    if app_mapping is None:
        app_mapping = _json_pm3("apps", "list")
    bundle = _resolve_app_bundle(name, _normalize_apps(app_mapping))
    _APP_BUNDLE_CACHE[cache_key] = bundle
    return bundle


def open_app(name):
    _require_device()
    cache_key = name.casefold()
    bundle = preflight_open_app(name)
    # CoreDevice's service accepts an empty argument list, while the current
    # CLI requires at least one positional application argument. Passing one
    # empty string preserves the service semantics and also works for system
    # apps that DVT launch can reject (for example Calculator on iOS 26).
    try:
        launched = _inprocess_launch_app(bundle) if _inprocess_supported() else False
        if not launched:
            _run_pm3(
                "developer",
                "core-device",
                "launch-application",
                "--no-kill-existing",
                bundle,
                "",
            )
    except RuntimeError:
        _APP_BUNDLE_CACHE.pop(cache_key, None)
        raise
    return bundle


def image_signature(path):
    """Small quantized grayscale signature for stable-screen comparisons."""
    with Image.open(path) as image:
        data = image.convert("L").resize((64, 64)).tobytes()
    return bytes(value // 16 for value in data)


def image_signatures_close(first, second, tolerance=0.01):
    """Treat tiny live-screen noise as stable without hiding real UI changes."""
    if len(first) != len(second) or not first:
        return first == second
    return image_signature_distance(first, second) <= tolerance


def image_signature_distance(first, second):
    """Mean absolute distance between quantized grayscale screen signatures."""
    if len(first) != len(second) or not first:
        if first == second:
            return 0.0
        raise ValueError("screen signatures must be non-empty and have equal length")
    return sum(abs(a - b) for a, b in zip(first, second)) / len(first)
