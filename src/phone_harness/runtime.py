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
from .operator_broker import HumanTeachingInbox, OperatorQuestionBroker
from .runtime_control import load_runtime_control
from .trace import TraceSink
from .visual_quality import VISUAL_PROFILE_BY_NAME


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

    def __init__(self, observation_ttl=0.25, visual_reuse_ttl=30.0):
        self.observation_ttl = float(observation_ttl)
        self.visual_reuse_ttl = float(visual_reuse_ttl)
        if self.observation_ttl < 0:
            raise ValueError("observation_ttl must be non-negative")
        if self.visual_reuse_ttl <= 0:
            raise ValueError("visual_reuse_ttl must be positive")
        self.runtime_id = uuid.uuid4().hex
        self.trace = TraceSink(self.runtime_id)
        self.operator_broker = OperatorQuestionBroker(trace=self.trace)
        self.teaching_inbox = HumanTeachingInbox(trace=self.trace)
        self.started_at = _utc_now()
        self._observation = None
        self._observation_id = None
        self._observation_scope = None
        self._observation_frame = None
        self._next_observation_id = 1
        self._observed_at = 0.0
        self._last_health = None
        self._last_observation_status = None
        self._last_action_status = None
        self._current_operation = None
        self._workflow_cache = {}
        initial_control = load_runtime_control()
        self._pause_generation_seen = int(initial_control.get("pause_generation") or 0)
        self.trace.emit(
            "runtime.start",
            summary="PhoneRuntime started",
            phase="runtime",
            status="ok",
            data={"runtime_id": self.runtime_id, "pid": os.getpid()},
        )
        self._counters = {"status": 0, "observe": 0, "act": 0, "errors": 0}

    def close(self):
        """Release process-owned device helpers before the runtime exits."""
        self.trace.emit(
            "runtime.close",
            summary="PhoneRuntime closing",
            phase="runtime",
            status="ok",
        )
        if self._observation_frame is not None:
            self._observation_frame.close()
            self._observation_frame = None
        if sys.platform == "win32":
            from . import windows

            windows.shutdown_runtime()

    @staticmethod
    def _control_pause_generation(state):
        value = state.get("pause_generation", 0) if isinstance(state, dict) else 0
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    def pause_checkpoint(self, *, boundary="workflow", poll_interval=0.2):
        """Honor Pause and report whether pre-checkpoint perception is stale.

        The wait is intentionally file-polled only while paused. Normal game
        progression pays for a single tiny control-file read per safe boundary,
        while an operator can keep the phone for an arbitrary short interval
        without terminating the workflow. ``pause_generation`` also records a
        pause that began and ended while the runtime was idle, so stale
        pre-pause observations can never survive a quick Pause -> Resume.
        """
        state = load_runtime_control()
        generation = self._control_pause_generation(state)
        generation_changed = generation > self._pause_generation_seen
        if generation_changed:
            self._pause_generation_seen = generation
            self.invalidate()

        if not state["paused"]:
            if generation_changed:
                try:
                    self.trace.emit(
                        "runtime.pause.reconcile",
                        summary="Past operator pause requires fresh perception",
                        phase="pause",
                        status="replan",
                        data={"boundary": str(boundary), "pause_generation": generation},
                    )
                except Exception:
                    pass
            return {
                "wait_ms": 0.0,
                "replan_required": generation_changed,
                "pause_generation": generation,
            }

        interval = min(1.0, max(0.05, float(poll_interval)))
        started = time.perf_counter()
        self.invalidate()
        try:
            self.trace.emit(
                "runtime.pause.wait",
                summary="Phone automation paused by operator",
                phase="pause",
                status="waiting",
                data={
                    "boundary": str(boundary),
                    "pause_generation": generation,
                },
            )
        except Exception:
            pass
        while state["paused"]:
            time.sleep(interval)
            state = load_runtime_control()
        waited_ms = _duration_ms(started)
        final_generation = self._control_pause_generation(state)
        if final_generation > self._pause_generation_seen:
            self._pause_generation_seen = final_generation
        # A human may have navigated, merged, or changed selection while the
        # workflow was paused. Never reuse pre-pause observation bindings.
        self.invalidate()
        try:
            self.trace.emit(
                "runtime.pause.resume",
                summary="Phone automation resumed",
                phase="pause",
                status="resumed",
                data={
                    "boundary": str(boundary),
                    "wait_ms": waited_ms,
                    "pause_generation": self._pause_generation_seen,
                },
            )
        except Exception:
            pass
        return {
            "wait_ms": waited_ms,
            "replan_required": True,
            "pause_generation": self._pause_generation_seen,
        }

    def wait_if_paused(self, *, boundary="workflow", poll_interval=0.2):
        """Compatibility wrapper returning only actual pause wait time."""
        return self.pause_checkpoint(
            boundary=boundary,
            poll_interval=poll_interval,
        )["wait_ms"]

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
        self.trace.emit(
            "operation.start",
            summary=f"{kind} started",
            phase=fields.get("phase"),
            status="running",
            data={"kind": kind, **fields},
        )
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
        self.trace.emit(
            "operation.error",
            summary=f"{kind} failed: {error.code}",
            phase=error.phase,
            status="error",
            data={
                "kind": kind,
                "duration_ms": entry["duration_ms"],
                "error_code": error.code,
                **({"backend": backend} if backend is not None else {}),
                **({"action_index": error.action_index} if error.action_index is not None else {}),
                **({"completed_actions": error.completed_actions} if error.completed_actions is not None else {}),
            },
        )
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
        self.trace.emit(
            "action.batch",
            summary=f"Executed {len(results)} phone action(s)",
            phase="execute",
            status="ok",
            data={
                "backend": backend,
                "action_count": len(results),
                "duration_ms": result["duration_ms"],
                "subprocess_count": result["subprocess_count"],
                "consumed_observation_id": consumed_observation_id,
            },
        )
        self._finish_operation()
        self._write_monitor_state()
        return result

    def status(self):
        started = time.perf_counter()
        self._counters["status"] += 1
        self._begin_operation("status", phase="probe")
        try:
            state = helpers.connection_state()
            result = {
                "contract_version": RUNTIME_CONTRACT_VERSION,
                "connection_state": state,
                "frame_source": helpers.frame_source_name(),
                "automation_control": load_runtime_control(),
            }
            pending_teaching = self.teaching_inbox.list(status="pending", limit=500)
            result["human_teaching"] = {
                "pending_count": len(pending_teaching),
                "oldest_message_id": (
                    pending_teaching[0].get("message_id") if pending_teaching else None
                ),
            }
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
                "frame_source": result["frame_source"],
                "duration_ms": result["duration_ms"],
            }
            for key in ("transport_mode", "active_transport", "tunneld"):
                if key in result:
                    self._last_health[key] = copy.deepcopy(result[key])
            self.trace.emit(
                "status.result",
                summary=f"Connection: {state}",
                phase="probe",
                status="ok",
                data={
                    "connection_state": state,
                    "frame_source": result["frame_source"],
                    "duration_ms": result["duration_ms"],
                    **({"transport_mode": result["transport_mode"]} if "transport_mode" in result else {}),
                    **({"active_transport": result["active_transport"]} if "active_transport" in result else {}),
                },
            )
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

    def observe(
        self,
        force=False,
        include_text_content=False,
        region=None,
        include_image=False,
        mode="semantic",
        image_profile="full",
        reuse_observation_id=None,
    ):
        started = time.perf_counter()
        pm3_before = self._pm3_process_count()
        self._counters["observe"] += 1
        self._begin_operation("observe", phase="read", force=bool(force))
        try:
            if mode not in {"semantic", "visual"}:
                raise ValueError("mode must be 'semantic' or 'visual'")
            if image_profile != "full" and image_profile not in VISUAL_PROFILE_BY_NAME:
                raise ValueError(f"unknown image_profile {image_profile!r}")
            if mode == "visual":
                include_image = True
            normalized_region = helpers.normalize_region(region) if region is not None else None
            region_scope = None if normalized_region is None else tuple(
                normalized_region[key] for key in ("x", "y", "w", "h")
            )
            scope = (mode, region_scope)
            now = time.monotonic()
            explicit_reuse = reuse_observation_id is not None
            if explicit_reuse:
                if force:
                    raise ValueError("force cannot be combined with reuse_observation_id")
                if mode != "visual":
                    raise ValueError("reuse_observation_id is only supported for visual observations")
                if (
                    isinstance(reuse_observation_id, bool)
                    or not isinstance(reuse_observation_id, int)
                    or reuse_observation_id <= 0
                ):
                    raise ValueError("reuse_observation_id must be a positive integer")
                if (
                    self._observation_id != reuse_observation_id
                    or self._observation is None
                    or self._observation_frame is None
                    or self._observation_scope != scope
                    or now - self._observed_at > self.visual_reuse_ttl
                ):
                    raise PhoneRuntimeError(
                        "STALE_OBSERVATION",
                        "reuse_observation_id is not the current retained visual observation",
                        retryable=True,
                        phase="preflight",
                    )
            cached = explicit_reuse or (
                not force
                and self._observation is not None
                and self._observation_scope == scope
                and now - self._observed_at <= self.observation_ttl
                and (not include_image or self._observation_frame is not None)
            )
            analysis_ms = 0.0
            if not cached:
                if self._observation_frame is not None:
                    self._observation_frame.close()
                    self._observation_frame = None
                frame = helpers.capture_frame() if include_image else None
                analysis_started = time.perf_counter()
                if mode == "visual":
                    raw_elements = []
                else:
                    raw_elements = (
                        helpers.elements(region=normalized_region, frame=frame)
                        if normalized_region is not None and frame is not None
                        else helpers.elements(region=normalized_region)
                        if normalized_region is not None
                        else helpers.elements(frame=frame)
                        if frame is not None
                        else helpers.elements()
                    )
                analysis_ms = _duration_ms(analysis_started)
                elements = [
                    {**item, "element_ref": f"e{index}"}
                    for index, item in enumerate(raw_elements, 1)
                ]
                sources = {item.get("source", "ocr") for item in elements}
                if mode == "visual":
                    source = "visual"
                elif not sources:
                    source = "none"
                elif len(sources) == 1:
                    source = next(iter(sources))
                else:
                    source = "hybrid"
                self._observation = {
                    "source": source,
                    "elements": elements,
                    "mode": mode,
                    **({"region": normalized_region} if normalized_region is not None else {}),
                }
                self._observation_scope = scope
                self._observation_frame = frame
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
            if explicit_reuse:
                result["reused_observation"] = True
            result["analysis_ms"] = analysis_ms
            if include_image and self._observation_frame is not None:
                image_started = time.perf_counter()
                variant_args = {
                    **({"region": normalized_region} if normalized_region is not None else {}),
                }
                if image_profile == "full":
                    image_path = self._observation_frame.variant(
                        **variant_args,
                        image_format="PNG",
                    )
                    mime_type = "image/png"
                else:
                    profile = VISUAL_PROFILE_BY_NAME[image_profile]
                    image_path = self._observation_frame.variant(
                        **variant_args,
                        max_long_edge=profile.max_long_edge,
                        grayscale=profile.grayscale,
                        image_format=profile.image_format,
                        quality=profile.quality,
                    )
                    mime_type = "image/jpeg" if profile.image_format.upper() == "JPEG" else "image/png"
                result["_image_path"] = str(image_path)
                result["_image_mime_type"] = mime_type
                result["image_profile"] = image_profile
                result["image_bytes"] = image_path.stat().st_size
                result["image_prepare_ms"] = _duration_ms(image_started)
                result["capture_ms"] = self._observation_frame.capture_ms
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
                "analysis_ms": result["analysis_ms"],
                "subprocess_count": result["subprocess_count"],
                **({"capture_ms": result["capture_ms"]} if "capture_ms" in result else {}),
                **({"image_prepare_ms": result["image_prepare_ms"]} if "image_prepare_ms" in result else {}),
            }
            self.trace.emit(
                "observation.result",
                summary=f"{mode} observation #{self._observation_id}",
                phase="read",
                status="ok",
                data={
                    "observation_id": self._observation_id,
                    "source": result["source"],
                    "mode": mode,
                    "image_profile": image_profile if include_image else None,
                    "element_count": len(result["elements"]),
                    "text_elements": [
                        {
                            key: item[key]
                            for key in ("text", "source", "role", "confidence", "x", "y")
                            if key in item
                        }
                        for item in result["elements"][:24]
                        if isinstance(item.get("text"), str) and item.get("text")
                    ],
                    "cached": cached,
                    "duration_ms": result["duration_ms"],
                    "analysis_ms": result["analysis_ms"],
                    "subprocess_count": result["subprocess_count"],
                    **({"capture_ms": result["capture_ms"]} if "capture_ms" in result else {}),
                    **({"image_prepare_ms": result["image_prepare_ms"]} if "image_prepare_ms" in result else {}),
                    **({"image_bytes": result["image_bytes"]} if "image_bytes" in result else {}),
                    **({"region": normalized_region} if normalized_region is not None else {}),
                },
                preview_path=result.get("_image_path"),
            )
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
        if self._observation_frame is not None:
            self._observation_frame.close()
            self._observation_frame = None
        self._observation = None
        self._observation_id = None
        self._observation_scope = None
        self._observed_at = 0.0

    def retained_screen_size(self):
        """Return the current retained frame size without capturing the phone."""
        if self._observation_frame is None:
            return None
        return (int(self._observation_frame.width), int(self._observation_frame.height))

    @staticmethod
    def _safe_host_request_params(method, params):
        if not isinstance(params, dict):
            return {}
        if method == "act":
            safe_actions = []
            for raw in params.get("actions") or []:
                if not isinstance(raw, dict):
                    continue
                item = {key: value for key, value in raw.items() if key != "text"}
                if raw.get("op") == "type_text":
                    value = raw.get("text")
                    item["text"] = f"[text omitted:{len(value) if isinstance(value, str) else 0}]"
                elif raw.get("op") == "tap_text" and isinstance(raw.get("text"), str):
                    item["text"] = raw["text"][:80]
                safe_actions.append(item)
            return {
                "observation_id": params.get("observation_id"),
                "actions": safe_actions,
            }
        if method == "operator_question":
            return {
                key: params.get(key)
                for key in ("question", "choices", "context", "confidence", "impact", "timeout")
                if key in params
            }
        return copy.deepcopy(params)

    def trace_host_request(self, method, params):
        self.trace.emit(
            "host.request",
            summary=f"Host requested {method}",
            phase="host",
            status="received",
            data={"method": method, "params": self._safe_host_request_params(method, params)},
        )

    def trace_decision(self, context):
        if not isinstance(context, dict):
            raise ValueError("decision context must be an object")
        summary = context.get("summary") or context.get("decision_summary") or "Decision"
        self.trace.emit(
            "host.decision",
            summary=summary,
            phase="decision",
            status="selected",
            data=context,
        )
        return {"recorded": True}

    def mark_host_activity(self, state, *, note=None):
        """Persist an explicit host-side lifecycle marker for the monitor.

        This is deliberately explicit metadata. It does not attempt to expose
        or infer hidden model reasoning.
        """
        if state not in {"working", "completed", "paused"}:
            raise ValueError("host activity state must be working, completed, or paused")
        if note is not None and not isinstance(note, str):
            raise ValueError("host activity note must be a string or None")
        event = self.trace.emit(
            "host.activity.marker",
            summary=f"Host activity: {state}",
            phase="host",
            status=state,
            data={"state": state, "note": None if note is None else note[:500]},
        )
        return {
            "recorded": True,
            "state": state,
            "event_id": event.get("event_id"),
        }

    def ask_operator(
        self,
        question,
        *,
        choices=(),
        context=None,
        confidence=None,
        impact="medium",
        timeout=300.0,
        preview_path=None,
        promote_answer_to_teaching=False,
    ):
        response = self.operator_broker.ask(
            question,
            choices=choices,
            context=context,
            confidence=confidence,
            impact=impact,
            timeout=float(timeout),
            preview_path=preview_path,
            promote_answer_to_teaching=promote_answer_to_teaching,
        )
        teaching_message_id = None
        if response is not None and promote_answer_to_teaching:
            teaching_message_id = self._promote_operator_answer_to_teaching(
                {
                    "question": str(question).strip(),
                    "context": context,
                },
                response,
            )
        return {
            "answered": response is not None,
            "answer": None if response is None else response.get("answer"),
            "choice": None if response is None else response.get("choice"),
            "skipped": response is None and not self.operator_broker.is_present(),
            "teaching_message_id": teaching_message_id,
        }

    def post_operator_question(
        self,
        question,
        *,
        choices=(),
        context=None,
        confidence=None,
        impact="medium",
        preview_path=None,
        promote_answer_to_teaching=True,
        dedupe_key=None,
    ):
        """Post a durable question without pausing autonomous work for an answer."""
        payload = self.operator_broker.post(
            question,
            choices=choices,
            context=context,
            confidence=confidence,
            impact=impact,
            preview_path=preview_path,
            promote_answer_to_teaching=promote_answer_to_teaching,
            dedupe_key=dedupe_key,
        )
        return {
            "posted": True,
            "question_id": payload.get("question_id"),
            "pending": payload.get("status") == "pending",
            "operator_present": self.operator_broker.is_present(),
        }

    def _promote_operator_answer_to_teaching(self, question, response):
        answer = str(response.get("answer") or response.get("choice") or "").strip()
        if not answer:
            return None
        teaching_text = (
            "Phone Harnessからの質問に対するHuman Teaching回答です。\n"
            f"質問: {str((question or {}).get('question') or 'Question').strip()}\n"
            + (
                f"文脈: {str((question or {}).get('context')).strip()}\n"
                if (question or {}).get("context")
                else ""
            )
            + f"回答: {answer}"
        )
        teaching = self.teaching_inbox.submit(
            teaching_text,
            source="operator_question_answer",
            blocking=False,
        )
        return teaching.get("message_id")

    def poll_operator_answers(self, *, limit=20):
        """Consume late answers at safe boundaries without ever waiting for them."""
        consumed = []
        for item in self.operator_broker.consume_responses(limit=limit):
            question = item.get("question") or {}
            response = item.get("response") or {}
            teaching_message_id = None
            if question.get("promote_answer_to_teaching"):
                teaching_message_id = self._promote_operator_answer_to_teaching(question, response)
            consumed.append({
                "question_id": response.get("question_id"),
                "answer": response.get("answer"),
                "choice": response.get("choice"),
                "teaching_message_id": teaching_message_id,
            })
        return consumed

    def next_human_teaching(self):
        """Return the oldest unhandled operator-initiated teaching message."""
        payload = self.teaching_inbox.next_pending()
        if payload is None:
            return {"pending": False}
        result = {
            "pending": True,
            "message_id": payload.get("message_id"),
            "text": payload.get("text") or "",
            "created_unix": payload.get("created_unix"),
            "image_name": payload.get("image_name"),
            "has_image": bool(payload.get("image_path")),
        }
        image_path = payload.get("image_path")
        if isinstance(image_path, str) and image_path:
            result["_image_path"] = image_path
            result["_image_mime_type"] = payload.get("image_mime_type")
        return result

    def handle_human_teaching(self, message_id, *, reply=None, learned=False):
        payload = self.teaching_inbox.handle(
            message_id,
            reply=reply,
            learned=bool(learned),
        )
        return {
            "handled": True,
            "message_id": payload.get("message_id"),
            "reply": payload.get("reply"),
            "learned": bool(payload.get("learned")),
        }

    def pending_human_teaching(self):
        """Cheap safe-boundary probe used by long local workflows."""
        payload = next(
            (
                item
                for item in self.teaching_inbox.list(status="pending", limit=500)
                if item.get("blocking", True) is not False
            ),
            None,
        )
        if payload is None:
            return None
        return {
            "message_id": payload.get("message_id"),
            "text": payload.get("text") or "",
            "has_image": bool(payload.get("image_path")),
            "image_name": payload.get("image_name"),
            "created_unix": payload.get("created_unix"),
        }

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

    def run_workflow(self, name, options=None):
        """Run one explicitly bounded app workflow inside this long-lived runtime."""
        if not isinstance(name, str) or not name:
            raise PhoneRuntimeError(
                "INVALID_REQUEST", "workflow name must be a non-empty string", phase="preflight"
            )
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise PhoneRuntimeError(
                "INVALID_REQUEST", "workflow options must be an object", phase="preflight"
            )
        pause = self.pause_checkpoint(boundary=f"{name}.start")
        if pause["replan_required"]:
            # Perception adapters may keep order-strip or app-specific caches
            # across workflow calls. Human phone use invalidates those just as
            # surely as it invalidates the retained screen observation.
            self._workflow_cache.clear()
        if name == "merge_boss_once":
            from phone_harness.workflows.merge_boss import MergeBossFastWorkflow

            allowed = {"max_producer_taps", "max_relaxed_checks"}
            unknown = set(options) - allowed
            if unknown:
                raise PhoneRuntimeError(
                    "INVALID_REQUEST",
                    f"unsupported merge_boss_once option(s): {', '.join(sorted(unknown))}",
                    phase="preflight",
                )
            return MergeBossFastWorkflow(runtime=self).run_one_merge(**options)
        if name == "merge_boss_turn":
            from phone_harness.workflows.merge_boss_control import MergeBossTurnController
            from phone_harness.workflows.merge_boss_perception import MergeBossLivePerception

            allowed = {
                "max_cycles", "max_merges", "max_emissions", "uncertain_burst_size",
                "max_recoveries", "order_rescan_every",
            }
            unknown = set(options) - allowed
            if unknown:
                raise PhoneRuntimeError(
                    "INVALID_REQUEST",
                    f"unsupported merge_boss_turn option(s): {', '.join(sorted(unknown))}",
                    phase="preflight",
                )
            perception = self._workflow_cache.get("merge_boss_turn.perception")
            if perception is None:
                perception = MergeBossLivePerception(self)
                self._workflow_cache["merge_boss_turn.perception"] = perception
            status = perception.status()
            if not status["ready"]:
                return {
                    "executed": False,
                    "workflow": "merge_boss_turn",
                    **status,
                }
            return {
                "executed": True,
                "workflow": "merge_boss_turn",
                **MergeBossTurnController(self, perception).run(**options),
            }
        raise PhoneRuntimeError(
            "INVALID_REQUEST", f"unsupported workflow {name!r}", phase="preflight"
        )

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
        pause = self.pause_checkpoint(boundary="action")
        if pause["replan_required"]:
            # The operator may have changed the screen while paused. Returning
            # a retryable, zero-action signal lets autonomous workflows re-read
            # truth without replaying a potentially stale/destructive batch.
            error = PhoneRuntimeError(
                "PAUSE_REPLAN_REQUIRED",
                "operator pause ended; re-observe before acting",
                retryable=True,
                phase="pause",
                completed_actions=0,
            )
            self._record_error("act", error, started)
            raise error
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

            def is_wda_batchable(item):
                op, _action, target = item
                return (
                    op in {"tap_text", "tap_element", "tap", "drag", "type_text", "swipe", "scroll"}
                    and (op not in {"tap_text", "tap_element"} or target.get("source") == "accessibility")
                )

            wda_batchable_with_wait = all(
                is_wda_batchable(item) or item[0] == "wait_stable"
                for item in prepared
            )
            if (
                prepared
                and any(is_wda_batchable(item) for item in prepared)
                and wda_batchable_with_wait
                and windows.wda_runtime_batch_supported()
            ):
                self._update_operation(phase="execute", backend="wda_batch")
                results = []
                pending_wda_actions = []
                pending_results = []
                pending_start_index = None

                def flush_wda_batch():
                    nonlocal pending_start_index
                    if pending_wda_actions:
                        try:
                            windows.run_wda_runtime_batch(list(pending_wda_actions))
                        except Exception as exc:
                            error = self._action_error(
                                started,
                                "wda_batch",
                                action_index=pending_start_index,
                                completed_actions=len(results),
                            )
                            raise error from exc
                    results.extend(pending_results)
                    pending_wda_actions.clear()
                    pending_results.clear()
                    pending_start_index = None

                for action_index, (op, action, target) in enumerate(prepared):
                    if op == "wait_stable":
                        flush_wda_batch()
                        try:
                            result = helpers.wait_stable(
                                timeout=action.get("timeout", 6.0),
                                interval=action.get("interval", 0.5),
                                settle=action.get("settle", 2),
                            )
                        except Exception as exc:
                            error = self._action_error(
                                started,
                                "wda_batch",
                                action_index=action_index,
                                completed_actions=len(results),
                            )
                            raise error from exc
                        results.append({"op": op, "result": result})
                        continue

                    if pending_start_index is None:
                        pending_start_index = action_index
                    if op in {"tap_text", "tap_element"}:
                        pending_wda_actions.append(windows._wda_action_for_accessibility(target))
                        result = _target_result(target)
                    elif op == "tap":
                        pending_wda_actions.append(windows._wda_action_for_tap(action["x"], action["y"]))
                        result = None
                    elif op == "drag":
                        pending_wda_actions.append(
                            windows._wda_action_for_drag(
                                action["x1"], action["y1"], action["x2"], action["y2"],
                                duration=action.get("duration", 0.6),
                            )
                        )
                        result = None
                    elif op == "type_text":
                        pending_wda_actions.append({"op": "type", "text": action["text"]})
                        result = None
                    elif op == "swipe":
                        pending_wda_actions.append(
                            windows._wda_action_for_swipe(
                                action["direction"],
                                action.get("distance", 0.4),
                            )
                        )
                        result = None
                    else:
                        scroll_action = windows._wda_action_for_scroll(action.get("amount", 300))
                        if scroll_action is not None:
                            pending_wda_actions.append(scroll_action)
                        result = None
                    pending_results.append({"op": op, "result": result})
                flush_wda_batch()
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
