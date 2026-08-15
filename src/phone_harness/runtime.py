"""Long-lived phone runtime intended to be hosted by a future MCP server."""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import helpers


RUNTIME_CONTRACT_VERSION = 2
MONITOR_SCHEMA_VERSION = 1
_ACTIONABLE_ROLES = {
    "XCUIElementTypeButton", "XCUIElementTypeCell", "XCUIElementTypeIcon",
    "XCUIElementTypeKey", "XCUIElementTypeLink", "XCUIElementTypeSearchField",
    "XCUIElementTypeSwitch", "XCUIElementTypeTextField",
}
_DOCUMENT_ROLES = {"XCUIElementTypeTextView"}
_DOCUMENT_TEXT_LIMIT = 240
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ \u3000-]?){12,18}\d(?!\d)")
STATUS_FILE = Path(
    os.environ.get(
        "PHONE_HARNESS_STATUS_FILE",
        str(Path(tempfile.gettempdir()) / "phone-harness" / "runtime-status.json"),
    )
)


def _duration_ms(started):
    return round((time.perf_counter() - started) * 1000, 3)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _redact_payment_cards(text):
    if not isinstance(text, str):
        return text

    def replace(match):
        digits = "".join(character for character in match.group(0) if character.isdigit())
        total = 0
        parity = len(digits) % 2
        for index, character in enumerate(digits):
            value = int(character)
            if index % 2 == parity:
                value *= 2
                if value > 9:
                    value -= 9
            total += value
        return "[payment card redacted]" if total % 10 == 0 else match.group(0)

    return _CARD_CANDIDATE.sub(replace, text)


def _public_elements(elements, include_text_content=False):
    result = []
    seen = set()
    for item in elements:
        text = item.get("text")
        if isinstance(text, str) and text.startswith("/var/containers/"):
            continue
        hidden = (
            not include_text_content
            and item.get("role") in _DOCUMENT_ROLES
            and isinstance(text, str)
            and text == item.get("value")
            and bool(text)
        )
        public = {
            key: item[key]
            for key in ("element_ref", "confidence", "x", "y", "w", "h", "source", "role")
            if key in item
        }
        public["text"] = "[text content hidden]" if hidden else _redact_payment_cards(text)
        if hidden:
            public["content_hidden"] = True
        name = item.get("name")
        if isinstance(name, str) and name and name != text and not name.startswith("/var/containers/"):
            public["name"] = _redact_payment_cards(name[:_DOCUMENT_TEXT_LIMIT])
        key = tuple(public.get(field) for field in ("text", "role", "x", "y", "w", "h"))
        if key in seen:
            continue
        seen.add(key)
        result.append(public)
    return result


def _target_result(target):
    public = _public_elements([target])
    item = public[0] if public else {"text": "[target hidden]"}
    return {
        "text": item.get("text"),
        "source": target.get("source", "ocr"),
        **({"element_ref": target["element_ref"]} if target.get("element_ref") else {}),
    }


class PhoneRuntimeError(RuntimeError):
    """Machine-readable failure at the PhoneRuntime seam."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        phase: str = "runtime",
        action_index: int | None = None,
        completed_actions: int | None = 0,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = bool(retryable)
        self.phase = phase
        self.action_index = action_index
        self.completed_actions = completed_actions

    def to_dict(self):
        return {
            "contract_version": RUNTIME_CONTRACT_VERSION,
            "error": {
                "code": self.code,
                "message": str(self),
                "retryable": self.retryable,
                "phase": self.phase,
                "action_index": self.action_index,
                "completed_actions": self.completed_actions,
            },
        }


class PhoneRuntime:
    """Small stable interface over observation, batched actions and status."""

    def __init__(self, observation_ttl=0.25):
        self.observation_ttl = float(observation_ttl)
        self.runtime_id = uuid.uuid4().hex
        self.started_at = _utc_now()
        self._observation = None
        self._observation_id = None
        self._next_observation_id = 1
        self._observed_at = 0.0
        self._last_health = None
        self._last_observation_status = None
        self._last_action_status = None
        self._current_operation = None
        self._counters = {"status": 0, "observe": 0, "act": 0, "errors": 0}

    def close(self):
        """Release process-owned device helpers before the runtime exits."""
        if sys.platform == "win32":
            from . import windows

            windows.shutdown_runtime()

    @staticmethod
    def _pm3_process_count():
        if sys.platform != "win32":
            return 0
        from . import windows

        return windows.runtime_transport_status()["pm3_process_count"]

    def _monitor_state(self):
        state = {
            "schema_version": MONITOR_SCHEMA_VERSION,
            "contract_version": RUNTIME_CONTRACT_VERSION,
            "runtime_id": self.runtime_id,
            "pid": os.getpid(),
            "process_state": "running",
            "started_at": self.started_at,
            "updated_at": _utc_now(),
            "counters": dict(self._counters),
            "health": copy.deepcopy(self._last_health),
            "current_operation": copy.deepcopy(self._current_operation),
            "last_observation": copy.deepcopy(self._last_observation_status),
            "last_action": copy.deepcopy(self._last_action_status),
        }
        if sys.platform == "win32":
            from . import windows

            state["transport"] = windows.runtime_transport_status()
        return state

    def _write_monitor_state(self):
        try:
            STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            temp = STATUS_FILE.with_name(f".{STATUS_FILE.name}.{os.getpid()}.tmp")
            temp.write_text(
                json.dumps(self._monitor_state(), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temp, STATUS_FILE)
        except OSError:
            pass

    def _begin_operation(self, kind, **fields):
        self._current_operation = {"kind": kind, "started_at": _utc_now(), **fields}
        self._write_monitor_state()

    def _update_operation(self, **fields):
        if self._current_operation is not None:
            self._current_operation.update(fields)
            self._write_monitor_state()

    def _finish_operation(self):
        self._current_operation = None

    def _record_error(self, kind, error, started, *, backend=None):
        self._counters["errors"] += 1
        entry = {
            "at": _utc_now(),
            "ok": False,
            "duration_ms": _duration_ms(started),
            "error_code": error.code,
            "phase": error.phase,
        }
        if backend is not None:
            entry["backend"] = backend
        if error.action_index is not None:
            entry["action_index"] = error.action_index
        if error.completed_actions is not None:
            entry["completed_actions"] = error.completed_actions
        if kind == "observe":
            self._last_observation_status = entry
        elif kind == "act":
            self._last_action_status = entry
        else:
            self._last_health = entry
        self._finish_operation()
        self._write_monitor_state()

    def _action_error(
        self,
        started,
        backend,
        *,
        action_index=None,
        completed_actions=None,
    ):
        self.invalidate()
        error = PhoneRuntimeError(
            "ACTION_FAILED",
            "phone action failed",
            retryable=False,
            phase="execute",
            action_index=action_index,
            completed_actions=completed_actions,
        )
        self._record_error("act", error, started, backend=backend)
        return error

    def _complete_action(self, results, backend, started, pm3_before, consumed_observation_id):
        self.invalidate()
        result = {
            "contract_version": RUNTIME_CONTRACT_VERSION,
            "count": len(results),
            "results": results,
            "backend": backend,
            "consumed_observation_id": consumed_observation_id,
            "duration_ms": _duration_ms(started),
            "subprocess_count": self._pm3_process_count() - pm3_before,
        }
        self._last_action_status = {
            "at": _utc_now(),
            "ok": True,
            "count": len(results),
            "backend": backend,
            "duration_ms": result["duration_ms"],
            "subprocess_count": result["subprocess_count"],
            "consumed_observation_id": consumed_observation_id,
        }
        self._finish_operation()
        self._write_monitor_state()
        return result

    def status(self):
        started = time.perf_counter()
        self._counters["status"] += 1
        self._begin_operation("status", phase="probe")
        try:
            state = helpers.connection_state()
            result = {"contract_version": RUNTIME_CONTRACT_VERSION, "connection_state": state}
            if sys.platform == "win32":
                from . import windows

                result["transport_mode"] = windows.transport_mode()
                if result["transport_mode"] in {"wifi", "auto"}:
                    result["tunneld"] = windows.tunneld_status()
                if state == "ready":
                    result["active_transport"] = windows.active_transport()
                result["transport_metrics"] = windows.runtime_transport_status()
            result["duration_ms"] = _duration_ms(started)
            self._last_health = {
                "at": _utc_now(),
                "ok": True,
                "connection_state": state,
                "duration_ms": result["duration_ms"],
            }
            for key in ("transport_mode", "active_transport", "tunneld"):
                if key in result:
                    self._last_health[key] = copy.deepcopy(result[key])
            self._finish_operation()
            self._write_monitor_state()
            return result
        except PhoneRuntimeError:
            raise
        except Exception as exc:
            error = PhoneRuntimeError(
                "STATUS_FAILED", "phone status probe failed", retryable=True, phase="status"
            )
            self._record_error("status", error, started)
            raise error from exc

    def observe(self, force=False, include_text_content=False):
        started = time.perf_counter()
        pm3_before = self._pm3_process_count()
        self._counters["observe"] += 1
        self._begin_operation("observe", phase="read", force=bool(force))
        try:
            now = time.monotonic()
            cached = (
                not force
                and self._observation is not None
                and now - self._observed_at <= self.observation_ttl
            )
            if not cached:
                elements = [
                    {**item, "element_ref": f"e{index}"}
                    for index, item in enumerate(helpers.elements(), 1)
                ]
                source = elements[0].get("source", "ocr") if elements else "none"
                self._observation = {"source": source, "elements": elements}
                self._observation_id = self._next_observation_id
                self._next_observation_id += 1
                self._observed_at = now
            assert self._observation is not None
            assert self._observation_id is not None
            result = copy.deepcopy(self._observation)
            result["elements"] = _public_elements(
                self._observation["elements"], include_text_content=include_text_content
            )
            result["contract_version"] = RUNTIME_CONTRACT_VERSION
            result["observation_id"] = self._observation_id
            result["cached"] = cached
            result["duration_ms"] = _duration_ms(started)
            result["subprocess_count"] = self._pm3_process_count() - pm3_before
            self._last_observation_status = {
                "at": _utc_now(),
                "ok": True,
                "observation_id": self._observation_id,
                "source": result["source"],
                "element_count": len(result["elements"]),
                "cached": cached,
                "duration_ms": result["duration_ms"],
                "subprocess_count": result["subprocess_count"],
            }
            self._finish_operation()
            self._write_monitor_state()
            return result
        except PhoneRuntimeError:
            raise
        except Exception as exc:
            error = PhoneRuntimeError(
                "OBSERVE_FAILED", "phone observation failed", retryable=True, phase="observe"
            )
            self._record_error("observe", error, started)
            raise error from exc

    def invalidate(self):
        self._observation = None
        self._observation_id = None
        self._observed_at = 0.0

    def _require_observation(self, observation_id):
        if (
            not isinstance(observation_id, int)
            or isinstance(observation_id, bool)
            or self._observation is None
            or self._observation_id != observation_id
        ):
            raise PhoneRuntimeError(
                "STALE_OBSERVATION",
                "tap_text requires the current observation_id from observe()",
                retryable=True,
                phase="preflight",
            )
        return self._observation

    @staticmethod
    def _match(elements, text, exact=False, index=None):
        if not isinstance(text, str) or not text:
            raise ValueError("tap_text requires non-empty text")
        query = text.casefold()
        hits = [
            item for item in elements
            if isinstance(item.get("text"), str)
            and (item["text"].casefold() == query if exact else query in item["text"].casefold())
        ]
        if not hits:
            raise PhoneRuntimeError(
                "TARGET_NOT_FOUND",
                f"no visible text matches {text!r}",
                retryable=True,
                phase="preflight",
            )
        if index is None:
            actionable = [item for item in hits if item.get("role") in _ACTIONABLE_ROLES]
            if len(actionable) == 1:
                return actionable[0]
            if len(hits) != 1:
                raise PhoneRuntimeError(
                    "TARGET_AMBIGUOUS",
                    f"visible text {text!r} is ambiguous ({len(hits)} matches)",
                    retryable=True,
                    phase="preflight",
                )
            return hits[0]
        if not isinstance(index, int) or index < 0 or index >= len(hits):
            raise ValueError(f"tap_text index {index!r} is outside {len(hits)} matches")
        return hits[index]

    @staticmethod
    def _match_ref(elements, element_ref):
        if not isinstance(element_ref, str) or not element_ref:
            raise ValueError("tap_element requires non-empty element_ref")
        for item in elements:
            if item.get("element_ref") == element_ref:
                return item
        raise PhoneRuntimeError(
            "TARGET_NOT_FOUND",
            f"element_ref {element_ref!r} is not present in the current observation",
            retryable=True,
            phase="preflight",
        )

    @staticmethod
    def _number(action, key, default=None):
        value = action.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{action.get('op')} requires numeric {key}")
        return value

    def _prepare(self, actions, observation_id=None):
        if not isinstance(actions, list) or not actions:
            raise ValueError("actions must be a non-empty list")
        if len(actions) > 100:
            raise ValueError("a batch may contain at most 100 actions")

        prepared = []
        observation = None
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError("each action must be an object")
            op = action.get("op")
            if op == "tap_text":
                exact = action.get("exact", False)
                index = action.get("index")
                if not isinstance(exact, bool):
                    raise ValueError("tap_text exact must be boolean")
                if index is not None and (isinstance(index, bool) or not isinstance(index, int)):
                    raise ValueError("tap_text index must be an integer")
                if observation is None:
                    observation = self._require_observation(observation_id)
                target = self._match(
                    observation["elements"],
                    action.get("text"),
                    exact,
                    index,
                )
                prepared.append((op, action, target))
            elif op == "tap_element":
                if observation is None:
                    observation = self._require_observation(observation_id)
                prepared.append((op, action, self._match_ref(observation["elements"], action.get("element_ref"))))
            elif op == "tap":
                self._number(action, "x")
                self._number(action, "y")
                prepared.append((op, action, None))
            elif op == "drag":
                for key in ("x1", "y1", "x2", "y2"):
                    self._number(action, key)
                self._number(action, "duration", 0.6)
                prepared.append((op, action, None))
            elif op == "type_text":
                if not isinstance(action.get("text"), str):
                    raise ValueError("type_text requires text")
                prepared.append((op, action, None))
            elif op == "open_app":
                if not isinstance(action.get("name"), str) or not action["name"]:
                    raise ValueError("open_app requires non-empty name")
                prepared.append((op, action, None))
            elif op == "home":
                prepared.append((op, action, None))
            elif op == "swipe":
                if action.get("direction") not in {"up", "down", "left", "right"}:
                    raise ValueError("swipe direction must be up/down/left/right")
                self._number(action, "distance", 0.4)
                prepared.append((op, action, None))
            elif op == "scroll":
                self._number(action, "amount", 300)
                prepared.append((op, action, None))
            elif op == "press":
                if not isinstance(action.get("combo"), str) or not action["combo"]:
                    raise ValueError("press requires non-empty combo")
                prepared.append((op, action, None))
            elif op == "wait_stable":
                self._number(action, "timeout", 6.0)
                self._number(action, "interval", 0.5)
                settle = action.get("settle", 2)
                if isinstance(settle, bool) or not isinstance(settle, int) or settle < 1:
                    raise ValueError("wait_stable settle must be a positive integer")
                prepared.append((op, action, None))
            else:
                raise ValueError(f"unsupported phone action {op!r}")
        return prepared

    def act(self, actions, observation_id=None):
        """Execute one batch prepared from the current observed screen.

        All ``tap_text`` targets are resolved before the first mutation. Split
        workflows at an observe boundary when an action changes to a new screen.
        """
        started = time.perf_counter()
        pm3_before = self._pm3_process_count()
        self._counters["act"] += 1
        action_count = len(actions) if isinstance(actions, list) else None
        self._begin_operation("act", phase="preflight", action_count=action_count)
        try:
            if observation_id is not None:
                self._require_observation(observation_id)
            prepared = self._prepare(actions, observation_id=observation_id)
            if sys.platform == "win32":
                from . import windows
                for op, action, _target in prepared:
                    if op == "open_app":
                        windows.preflight_open_app(action["name"])
        except PhoneRuntimeError as error:
            self._record_error("act", error, started)
            raise
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            error = PhoneRuntimeError(
                "INVALID_REQUEST", str(exc), phase="preflight"
            )
            self._record_error("act", error, started)
            raise error from exc

        consumed_observation_id = observation_id
        if sys.platform == "win32":
            from . import windows

            wda_batchable = all(
                op in {"tap_text", "tap_element", "tap", "drag", "type_text", "swipe", "scroll"}
                and (op not in {"tap_text", "tap_element"} or target.get("source") == "accessibility")
                for op, _action, target in prepared
            )
            if prepared and wda_batchable and windows.wda_runtime_batch_supported():
                self._update_operation(phase="execute", backend="wda_batch")
                wda_actions = []
                results = []
                for op, action, target in prepared:
                    if op in {"tap_text", "tap_element"}:
                        wda_actions.append(windows._wda_action_for_accessibility(target))
                        result = _target_result(target)
                    elif op == "tap":
                        wda_actions.append(windows._wda_action_for_tap(action["x"], action["y"]))
                        result = None
                    elif op == "drag":
                        wda_actions.append(
                            windows._wda_action_for_drag(
                                action["x1"], action["y1"], action["x2"], action["y2"],
                                duration=action.get("duration", 0.6),
                            )
                        )
                        result = None
                    elif op == "type_text":
                        wda_actions.append({"op": "type", "text": action["text"]})
                        result = None
                    elif op == "swipe":
                        wda_actions.append(
                            windows._wda_action_for_swipe(
                                action["direction"],
                                action.get("distance", 0.4),
                            )
                        )
                        result = None
                    else:
                        scroll_action = windows._wda_action_for_scroll(action.get("amount", 300))
                        if scroll_action is not None:
                            wda_actions.append(scroll_action)
                        result = None
                    results.append({"op": op, "result": result})
                try:
                    if wda_actions:
                        windows.run_wda_runtime_batch(wda_actions)
                except Exception as exc:
                    error = self._action_error(
                        started,
                        "wda_batch",
                    )
                    raise error from exc
                return self._complete_action(
                    results, "wda_batch", started, pm3_before, consumed_observation_id
                )

        if (
            sys.platform == "win32"
            and prepared
            and all(op in {"tap_text", "tap_element"} and target.get("source") == "accessibility" for op, _action, target in prepared)
        ):
            from . import windows

            self._update_operation(phase="execute", backend="native_batch")
            targets = [target for _op, _action, target in prepared]
            try:
                windows.tap_accessibility_batch(targets)
            except Exception as exc:
                error = self._action_error(
                    started,
                    "native_batch",
                )
                raise error from exc
            results = [
                {"op": op, "result": _target_result(target)}
                for op, _action, target in prepared
            ]
            return self._complete_action(
                results, "native_batch", started, pm3_before, consumed_observation_id
            )

        self._update_operation(phase="execute", backend="sequential")
        results = []
        for action_index, (op, action, target) in enumerate(prepared):
            try:
                if op in {"tap_text", "tap_element"}:
                    if sys.platform == "win32" and target.get("source") == "accessibility":
                        from . import windows

                        windows.tap_accessibility(target)
                    else:
                        helpers.tap(target["x"], target["y"])
                    result = _target_result(target)
                elif op == "tap":
                    helpers.tap(action["x"], action["y"])
                    result = None
                elif op == "drag":
                    helpers.drag(
                        action["x1"], action["y1"], action["x2"], action["y2"],
                        duration=action.get("duration", 0.6),
                    )
                    result = None
                elif op == "type_text":
                    helpers.type_text(action["text"])
                    result = None
                elif op == "open_app":
                    result = helpers.open_app(action["name"])
                elif op == "home":
                    helpers.home()
                    result = None
                elif op == "swipe":
                    helpers.swipe(action["direction"], distance=action.get("distance", 0.4))
                    result = None
                elif op == "scroll":
                    helpers.scroll(action.get("amount", 300))
                    result = None
                elif op == "press":
                    helpers.press(action["combo"])
                    result = None
                else:  # wait_stable
                    result = helpers.wait_stable(
                        timeout=action.get("timeout", 6.0),
                        interval=action.get("interval", 0.5),
                        settle=action.get("settle", 2),
                    )
            except Exception as exc:
                error = self._action_error(
                    started,
                    "sequential",
                    action_index=action_index,
                    completed_actions=len(results),
                )
                raise error from exc
            results.append({"op": op, "result": result})
        return self._complete_action(
            results, "sequential", started, pm3_before, consumed_observation_id
        )
