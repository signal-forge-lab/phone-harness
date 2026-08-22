"""Responsive LAN web monitor for phone-harness.

The web surface is deliberately read-mostly. It exposes trace/status/preview
data and accepts only human-teaching answers; it does not expose phone action
endpoints. Requests are accepted only from loopback/private/link-local clients.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import ipaddress
import json
import mimetypes
import os
import socket
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from phone_harness.operator_broker import (
    DEFAULT_PENDING_FILE,
    DEFAULT_PRESENCE_FILE,
    HumanTeachingInbox,
    OperatorQuestionBroker,
    load_operator_presence,
    load_pending_question,
    set_operator_presence,
)
from phone_harness.runtime import STATUS_FILE
from phone_harness.runtime_control import (
    DEFAULT_CONTROL_FILE,
    load_runtime_control,
    set_runtime_paused,
)
from phone_harness.trace import DEFAULT_MONITOR_ROOT, DEFAULT_TRACE_FILE, emit_process_event, read_trace_events


DEFAULT_BIND = os.environ.get("PHONE_HARNESS_MONITOR_BIND", "0.0.0.0")
DEFAULT_PORT = int(os.environ.get("PHONE_HARNESS_MONITOR_PORT", "17678"))
SERVER_STATE_FILE = DEFAULT_MONITOR_ROOT / "server.json"
HTML_PATH = Path(__file__).with_name("monitor_web.html")
MANIFEST_PATH = Path(__file__).with_name("manifest.webmanifest")
MONITOR_UI_CSS_PATH = Path(__file__).with_name("monitor_ui.css")
MONITOR_UI_JS_PATH = Path(__file__).with_name("monitor_ui.js")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKVIEW_ROOT = Path(
    os.environ.get("PHONE_HARNESS_DOCKVIEW_DIR")
    or (_REPO_ROOT / "desktop-monitor" / "node_modules" / "dockview" / "dist")
)
DOCKVIEW_JS_PATH = _DOCKVIEW_ROOT / "dockview.min.js"
DOCKVIEW_CSS_PATH = _DOCKVIEW_ROOT / "styles" / "dockview.css"
TEACHING_INBOX = HumanTeachingInbox()
QUESTION_BROKER = OperatorQuestionBroker()
_ALLOWED_CLIENT_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "::1/128",
        "fe80::/10",
    )
)


def client_is_lan(address):
    """Allow only loopback, RFC1918, and link-local clients."""
    text = str(address).split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    return any(ip.version == network.version and ip in network for network in _ALLOWED_CLIENT_NETWORKS)


def preferred_lan_address():
    """Resolve the IPv4 address selected by the default route without sending traffic."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        host = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    return host if client_is_lan(host) and not ipaddress.ip_address(host).is_link_local else None


def lan_urls(port):
    addresses = {"127.0.0.1"}
    try:
        for _family, _socktype, _proto, _canonname, sockaddr in socket.getaddrinfo(socket.gethostname(), None):
            host = sockaddr[0].split("%", 1)[0]
            if client_is_lan(host) and host not in {"::1", "0.0.0.0"}:
                addresses.add(host)
    except OSError:
        pass
    preferred = preferred_lan_address()
    ordered = sorted(
        addresses,
        key=lambda item: (
            item != preferred,
            item == "127.0.0.1",
            ipaddress.ip_address(item.split("%", 1)[0]).is_link_local,
            ":" in item,
            item,
        ),
    )
    return [f"http://[{host}]:{port}/" if ":" in host else f"http://{host}:{port}/" for host in ordered]


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _latest_event(events, types):
    for event in reversed(events):
        if event.get("type") in types:
            return event
    return None


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * float(fraction)) + 0.999999) - 1))
    return ordered[index]


def _timing_summary(events):
    """Summarize recent non-overlapping observability samples by pipeline stage.

    Buckets intentionally remain independent. Their totals are not a workflow
    wall-clock total because capture/recognition/decision/action events can be
    nested in one higher-level operation.
    """
    buckets = {
        "capture": [],
        "recognition": [],
        "decision": [],
        "action": [],
        "learning": [],
        "action_cadence": [],
        "host_gap": [],
    }
    previous_action_ns = None
    previous_host_complete_ns = None
    for event in events:
        event_type = str(event.get("type") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        monotonic_ns = event.get("monotonic_ns")
        if event_type == "action.batch" and isinstance(monotonic_ns, int):
            if previous_action_ns is not None and monotonic_ns >= previous_action_ns:
                buckets["action_cadence"].append((monotonic_ns - previous_action_ns) / 1_000_000.0)
            previous_action_ns = monotonic_ns
        if event_type == "host.request.complete" and isinstance(monotonic_ns, int):
            previous_host_complete_ns = monotonic_ns
        elif (
            event_type == "host.request"
            and isinstance(monotonic_ns, int)
            and previous_host_complete_ns is not None
            and monotonic_ns >= previous_host_complete_ns
        ):
            # This is the measurable outside-tool gap. It includes model/planner
            # deliberation, orchestration and transport/UI idle time; it is not
            # a claim to expose private chain-of-thought.
            buckets["host_gap"].append((monotonic_ns - previous_host_complete_ns) / 1_000_000.0)
            previous_host_complete_ns = None
        value = None
        bucket = None
        if event_type == "observation.result":
            bucket = "capture"
            value = data.get("capture_ms")
        elif event_type in {"merge_boss.order_page", "merge_boss.board"}:
            bucket = "recognition"
            value = data.get("duration_ms")
        elif event_type in {"workflow.decision", "host.decision"}:
            bucket = "decision"
            value = data.get("duration_ms")
        elif event_type == "action.batch":
            bucket = "action"
            value = data.get("duration_ms")
        elif event_type == "knowledge.write":
            bucket = "learning"
            value = data.get("duration_ms")
        if bucket is None or not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if value < 0:
            continue
        buckets[bucket].append(float(value))

    result = {}
    for name, values in buckets.items():
        result[name] = {
            "count": len(values),
            "latest_ms": round(values[-1], 3) if values else None,
            "average_ms": round(sum(values) / len(values), 3) if values else None,
            "p95_ms": round(_percentile(values, 0.95), 3) if values else None,
            "total_ms": round(sum(values), 3) if values else 0.0,
        }
    return result


def _host_activity(events, status, pending):
    """Return monitor-safe host activity state without claiming hidden model state."""
    if pending is not None:
        return {
            "state": "waiting-human",
            "label": "WAITING HUMAN",
            "basis": "operator_question",
            "age_seconds": 0.0,
        }
    if isinstance(status, dict) and status.get("current_operation"):
        return {
            "state": "working",
            "label": "WORKING",
            "basis": "runtime_operation",
            "age_seconds": 0.0,
        }
    # Explicit lifecycle markers describe the whole host task, whereas
    # request.complete only describes one MCP call. Keep WORKING active across
    # many tool calls until the host explicitly marks completed/paused (or the
    # marker becomes stale enough to suspect an interruption).
    explicit = _latest_event(events, {"host.activity.marker"})
    if explicit is not None:
        data = explicit.get("data") if isinstance(explicit.get("data"), dict) else {}
        monotonic_ns = explicit.get("monotonic_ns")
        age = None
        if isinstance(monotonic_ns, int):
            age = max(0.0, (time.monotonic_ns() - monotonic_ns) / 1_000_000_000)
        marker = str(data.get("state") or explicit.get("status") or "idle")
        if marker == "working" and age is not None and age > 180:
            return {
                "state": "stale-or-interrupted",
                "label": "STALE / INTERRUPTED?",
                "basis": "working_marker_stale",
                "age_seconds": round(age, 1),
                "note": data.get("note"),
            }
        labels = {
            "working": "WORKING",
            "completed": "COMPLETED",
            "paused": "PAUSED",
        }
        return {
            "state": marker,
            "label": labels.get(marker, marker.upper()),
            "basis": "explicit_marker",
            "age_seconds": None if age is None else round(age, 1),
            "note": data.get("note"),
        }

    latest = _latest_event(events, {"host.request", "host.request.complete", "host.request.failed"})
    if latest is None:
        return {"state": "idle", "label": "IDLE", "basis": "no_host_event", "age_seconds": None}
    data = latest.get("data") if isinstance(latest.get("data"), dict) else {}
    monotonic_ns = latest.get("monotonic_ns")
    age = None
    if isinstance(monotonic_ns, int):
        age = max(0.0, (time.monotonic_ns() - monotonic_ns) / 1_000_000_000)
    if latest.get("type") == "host.request":
        if age is not None and age > 180:
            return {
                "state": "stale-or-interrupted",
                "label": "STALE / INTERRUPTED?",
                "basis": "unfinished_request_stale",
                "age_seconds": round(age, 1),
            }
        return {
            "state": "working",
            "label": "WORKING",
            "basis": "host_request",
            "age_seconds": None if age is None else round(age, 1),
        }
    if latest.get("type") == "host.request.failed":
        return {
            "state": "interrupted",
            "label": "REQUEST FAILED",
            "basis": "host_request_failed",
            "age_seconds": None if age is None else round(age, 1),
        }
    return {
        "state": "idle",
        "label": "TOOL COMPLETE",
        "basis": "host_request_complete",
        "age_seconds": None if age is None else round(age, 1),
    }


def _operator_conversation(events, *, limit=8):
    """Pair traced AI questions and human answers for durable monitor history."""
    by_id = {}
    order = []
    for event in events:
        event_type = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        question_id = data.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            continue
        if event_type == "operator.question":
            if question_id not in by_id:
                order.append(question_id)
            by_id[question_id] = {
                "question_id": question_id,
                "question": event.get("summary") or "Question",
                "context": data.get("context"),
                "choices": list(data.get("choices") or ()),
                "confidence": data.get("confidence"),
                "impact": data.get("impact"),
                "preview_path": event.get("preview_path"),
                "asked_at": event.get("at"),
                "answer": None,
                "choice": None,
                "answered_at": None,
                "status": "pending",
            }
        elif event_type == "operator.answer":
            item = by_id.get(question_id)
            if item is None:
                item = {
                    "question_id": question_id,
                    "question": "Question",
                    "context": None,
                    "choices": [],
                    "confidence": None,
                    "impact": "medium",
                    "preview_path": None,
                    "asked_at": None,
                    "answer": None,
                    "choice": None,
                    "answered_at": None,
                    "status": "pending",
                }
                by_id[question_id] = item
                order.append(question_id)
            item["answer"] = data.get("answer")
            item["choice"] = data.get("choice")
            item["answered_at"] = event.get("at")
            item["status"] = "consumed"
    return [by_id[question_id] for question_id in order[-max(0, int(limit)):]]


def _automation_control_view(control, runtime_status, events):
    """Expose requested vs accepted pause state without touching the runtime.

    The control file is the operator's durable request. ``runtime.pause.wait``
    is the strongest acknowledgement because it is emitted only after the
    runtime has reached a safe boundary. When no phone action is in flight,
    the pause gate is already effective for any future mutation, so the phone
    is also safe to use even before a workflow has had reason to enter the
    blocking wait loop.
    """
    value = dict(control) if isinstance(control, dict) else {}
    paused = value.get("paused") is True
    runtime_id = (
        runtime_status.get("runtime_id")
        if isinstance(runtime_status, dict)
        and isinstance(runtime_status.get("runtime_id"), str)
        and runtime_status.get("runtime_id")
        else None
    )
    latest_wait_index = -1
    latest_resume_index = -1
    latest_pause_request_index = -1
    latest_wait = None
    for index, event in enumerate(events or ()):
        event_type = event.get("type")
        if event_type == "runtime.pause.wait":
            if runtime_id is None or event.get("session_id") != runtime_id:
                continue
            latest_wait_index = index
            latest_wait = event
        elif event_type == "runtime.pause.resume":
            if runtime_id is None or event.get("session_id") != runtime_id:
                continue
            latest_resume_index = index
        elif event_type == "runtime.pause.control":
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            if data.get("paused") is True:
                latest_pause_request_index = index

    active_runtime_wait = (
        latest_wait_index > latest_resume_index
        and latest_wait_index > latest_pause_request_index
    )
    operation = runtime_status.get("current_operation") if isinstance(runtime_status, dict) else None
    phone_action_in_flight = (
        isinstance(operation, dict)
        and operation.get("kind") == "act"
    )

    if paused:
        if active_runtime_wait:
            data = latest_wait.get("data") if isinstance(latest_wait, dict) else {}
            return {
                **value,
                "state": "paused",
                "label": "PAUSED",
                "accepted": True,
                "basis": "runtime_wait",
                "boundary": data.get("boundary") if isinstance(data, dict) else None,
            }
        if phone_action_in_flight:
            return {
                **value,
                "state": "pause-requested",
                "label": "PAUSE REQUESTED",
                "accepted": False,
                "basis": "action_in_flight",
                "boundary": None,
            }
        return {
            **value,
            "state": "paused",
            "label": "PAUSED",
            "accepted": True,
            "basis": "safe_gate",
            "boundary": None,
        }

    if latest_wait_index > latest_resume_index:
        data = latest_wait.get("data") if isinstance(latest_wait, dict) else {}
        return {
            **value,
            "state": "resuming",
            "label": "RESUMING",
            "accepted": False,
            "basis": "runtime_wait",
            "boundary": data.get("boundary") if isinstance(data, dict) else None,
        }
    return {
        **value,
        "state": "running",
        "label": "RUNNING",
        "accepted": False,
        "basis": "control",
        "boundary": None,
    }


def _safe_preview_path(value):
    if not isinstance(value, str):
        return None
    candidate = Path(value)
    if candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return None
    try:
        if not candidate.is_file() or candidate.stat().st_size > 16 * 1024 * 1024:
            return None
    except OSError:
        return None
    return candidate


def build_state(*, event_limit=350, after_monotonic_ns=None):
    events = read_trace_events(DEFAULT_TRACE_FILE, limit=event_limit)
    visible_events = events
    if after_monotonic_ns is not None:
        try:
            cutoff = int(after_monotonic_ns)
        except (TypeError, ValueError):
            cutoff = 0
        visible_events = [
            event for event in events
            if isinstance(event.get("monotonic_ns"), int) and event["monotonic_ns"] > cutoff
        ]
    teaching_messages = []
    for item in TEACHING_INBOX.list(limit=20):
        teaching_messages.append({
            key: value for key, value in item.items()
            if key != "image_path"
        } | {"has_image": bool(item.get("image_path"))})
    operator_presence = load_operator_presence(DEFAULT_PRESENCE_FILE)
    trace_conversation = _operator_conversation(events, limit=20)
    # Older synchronous questions only existed in trace/pending.json. Preserve
    # unanswered ones as durable records so the operator can answer them later.
    for item in trace_conversation:
        if item.get("answer") is None and QUESTION_BROKER.get(item.get("question_id")) is None:
            QUESTION_BROKER.register_existing(item)
    question_records = QUESTION_BROKER.list(limit=20)
    pending_records = [item for item in question_records if item.get("status") == "pending"]
    # Surface the newest unresolved question in the primary answer box while
    # older questions remain replyable from conversation history.
    pending = pending_records[-1] if pending_records else None
    operator_conversation = []
    for item in question_records:
        operator_conversation.append({
            "question_id": item.get("question_id"),
            "question": item.get("question") or "Question",
            "context": item.get("context"),
            "choices": list(item.get("choices") or ()),
            "confidence": item.get("confidence"),
            "impact": item.get("impact"),
            "has_preview": bool(item.get("preview_path")),
            "asked_at": item.get("created_unix"),
            "answer": item.get("answer"),
            "choice": item.get("choice"),
            "answered_at": item.get("answered_unix"),
            "status": item.get("status"),
        })
    runtime_status = _read_json(STATUS_FILE)
    if not isinstance(runtime_status, dict):
        runtime_status = {}
    latest_status_event = _latest_event(events, {"status.result"})
    if latest_status_event is not None:
        status_data = latest_status_event.get("data") or {}
        if not runtime_status.get("connection_state") and status_data.get("connection_state"):
            runtime_status["connection_state"] = status_data["connection_state"]
        if not isinstance(runtime_status.get("last_health"), dict):
            runtime_status["last_health"] = {}
        for key in ("connection_state", "transport_mode", "active_transport"):
            if not runtime_status["last_health"].get(key) and status_data.get(key):
                runtime_status["last_health"][key] = status_data[key]
    automation_control = _automation_control_view(
        load_runtime_control(DEFAULT_CONTROL_FILE),
        runtime_status,
        events,
    )
    return {
        "schema_version": 1,
        "status": runtime_status,
        "automation_control": automation_control,
        "operator_presence": operator_presence,
        "pending": pending,
        "teaching_messages": teaching_messages,
        "operator_conversation": operator_conversation or trace_conversation,
        "events": visible_events,
        "timing": _timing_summary(events),
        "host_activity": _host_activity(events, runtime_status, pending),
        "latest_decision": _latest_event(events, {"host.decision", "workflow.decision"}),
        "latest_failure": _latest_event(events, {"failure.judgment", "operation.error", "bridge.exception"}),
        "latest_preview_event_id": next(
            (event.get("event_id") for event in reversed(events) if event.get("preview_path")),
            None,
        ),
        "preview_events": _preview_events(events),
        "server": _read_json(SERVER_STATE_FILE),
    }


def preview_path_for_event(event_id):
    if not isinstance(event_id, str) or not event_id:
        return None
    for event in reversed(read_trace_events(DEFAULT_TRACE_FILE, limit=700)):
        if event.get("event_id") != event_id:
            continue
        return _safe_preview_path(event.get("preview_path"))
    return None


def pending_preview_path(question_id):
    pending = QUESTION_BROKER.get(question_id)
    if pending is None:
        return None
    return _safe_preview_path(pending.get("preview_path"))


def teaching_image_path(message_id):
    payload = TEACHING_INBOX.get(message_id)
    if payload is None:
        return None
    return _safe_preview_path(payload.get("image_path"))


def _preview_events(events, *, limit=48):
    """Expose recent preview metadata without leaking local filesystem paths."""
    result = []
    for event in events:
        if _safe_preview_path(event.get("preview_path")) is None:
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        result.append({
            "event_id": event.get("event_id"),
            "at": event.get("at"),
            "monotonic_ns": event.get("monotonic_ns"),
            "type": event.get("type"),
            "summary": event.get("summary"),
            "image_profile": data.get("image_profile"),
            "image_bytes": data.get("image_bytes"),
        })
    return result[-max(0, int(limit)):]


class MonitorRequestHandler(BaseHTTPRequestHandler):
    server_version = "PhoneHarnessMonitor/1"

    def log_message(self, _format, *_args):
        return

    def _allowed(self):
        return client_is_lan(self.client_address[0])

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'",
        )
        self.send_header("Cache-Control", "no-store")

    def _json(self, value, status=HTTPStatus.OK):
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self._write_payload(data)

    def _write_payload(self, data):
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return False
        return True

    def _file(self, path, content_type=None):
        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self._write_payload(data)

    def do_GET(self):
        if not self._allowed():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        route = urlparse(self.path).path
        if route == "/":
            self._file(HTML_PATH, "text/html; charset=utf-8")
            return
        if route == "/manifest.webmanifest":
            self._file(MANIFEST_PATH, "application/manifest+json; charset=utf-8")
            return
        if route == "/assets/monitor-ui.css":
            self._file(MONITOR_UI_CSS_PATH, "text/css; charset=utf-8")
            return
        if route == "/assets/monitor-ui.js":
            self._file(MONITOR_UI_JS_PATH, "text/javascript; charset=utf-8")
            return
        if route == "/assets/dockview.css":
            self._file(DOCKVIEW_CSS_PATH, "text/css; charset=utf-8")
            return
        if route == "/assets/dockview.js":
            self._file(DOCKVIEW_JS_PATH, "text/javascript; charset=utf-8")
            return
        if route == "/api/state":
            query = parse_qs(urlparse(self.path).query)
            after = query.get("after", [None])[0]
            self._json(build_state(after_monotonic_ns=after))
            return
        if route.startswith("/api/preview/"):
            event_id = route.removeprefix("/api/preview/")
            path = preview_path_for_event(event_id)
            if path is None:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self._file(path)
            return
        if route.startswith("/api/question-preview/"):
            question_id = route.removeprefix("/api/question-preview/")
            path = pending_preview_path(question_id)
            if path is None:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self._file(path)
            return
        if route.startswith("/api/teaching-image/"):
            message_id = route.removeprefix("/api/teaching-image/")
            path = teaching_image_path(message_id)
            if path is None:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self._file(path)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if not self._allowed():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        route = urlparse(self.path).path
        if route not in {"/api/respond", "/api/teach", "/api/presence", "/api/pause"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        max_body = 12 * 1024 * 1024 if route == "/api/teach" else 64 * 1024
        if length <= 0 or length > max_body:
            self._json({"error": "invalid body"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(payload, dict):
            self._json({"error": "invalid payload"}, HTTPStatus.BAD_REQUEST)
            return
        if route == "/api/pause":
            paused = payload.get("paused")
            if not isinstance(paused, bool):
                self._json({"error": "paused must be boolean"}, HTTPStatus.BAD_REQUEST)
                return
            state = set_runtime_paused(
                paused,
                path=DEFAULT_CONTROL_FILE,
                source="monitor-web",
            )
            emit_process_event(
                "runtime.pause.control",
                summary="Operator requested automation pause" if paused else "Operator requested automation resume",
                phase="pause",
                status="paused" if paused else "resumed",
                data={"paused": paused, "source": "monitor-web"},
            )
            runtime_status = _read_json(STATUS_FILE)
            if not isinstance(runtime_status, dict):
                runtime_status = {}
            view = _automation_control_view(
                state,
                runtime_status,
                read_trace_events(DEFAULT_TRACE_FILE, limit=350),
            )
            self._json({"ok": True, "automation_control": view})
            return
        if route == "/api/presence":
            present = payload.get("present")
            if not isinstance(present, bool):
                self._json({"error": "present must be boolean"}, HTTPStatus.BAD_REQUEST)
                return
            state = set_operator_presence(present, path=DEFAULT_PRESENCE_FILE)
            emit_process_event(
                "operator.presence",
                summary="Human operator present" if present else "Human operator absent",
                phase="human_input",
                status="present" if present else "absent",
                data={"present": present},
            )
            self._json({"ok": True, "operator_presence": state})
            return
        if route == "/api/teach":
            text = payload.get("text", "")
            image_base64 = payload.get("image_base64")
            image_mime_type = payload.get("image_mime_type")
            image_name = payload.get("image_name")
            if not isinstance(text, str) or len(text) > 16000:
                self._json({"error": "invalid teaching text"}, HTTPStatus.BAD_REQUEST)
                return
            image_bytes = None
            if image_base64 is not None:
                if not isinstance(image_base64, str):
                    self._json({"error": "invalid teaching image"}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    image_bytes = base64.b64decode(image_base64, validate=True)
                except (ValueError, binascii.Error):
                    self._json({"error": "invalid teaching image encoding"}, HTTPStatus.BAD_REQUEST)
                    return
            try:
                message = TEACHING_INBOX.submit(
                    text,
                    image_bytes=image_bytes,
                    image_mime_type=image_mime_type,
                    image_name=image_name,
                )
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            emit_process_event(
                "operator.message",
                summary=(message.get("text") or "Human teaching image received")[:160],
                phase="human_input",
                status="pending",
                data={
                    "message_id": message["message_id"],
                    "has_image": bool(message.get("image_path")),
                    "image_name": message.get("image_name"),
                },
                preview_path=message.get("image_path"),
            )
            public = {key: value for key, value in message.items() if key != "image_path"}
            public["has_image"] = bool(message.get("image_path"))
            self._json({"ok": True, "message": public})
            return
        question_id = payload.get("question_id")
        question = QUESTION_BROKER.get(question_id) if isinstance(question_id, str) else None
        if question is None:
            self._json({"error": "question not found"}, HTTPStatus.NOT_FOUND)
            return
        if question.get("status") != "pending":
            self._json({"error": "question is already answered"}, HTTPStatus.CONFLICT)
            return
        answer = payload.get("answer", "")
        choice = payload.get("choice")
        if not isinstance(answer, str) or len(answer) > 16000:
            self._json({"error": "invalid answer"}, HTTPStatus.BAD_REQUEST)
            return
        if choice is not None and not isinstance(choice, str):
            self._json({"error": "invalid choice"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            response = OperatorQuestionBroker.submit_response(question_id, answer, choice=choice)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
            return
        self._json({"ok": True, "response": response})


class MonitorWebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(bind=DEFAULT_BIND, port=DEFAULT_PORT):
    server = MonitorWebServer((bind, int(port)), MonitorRequestHandler)
    actual_port = int(server.server_address[1])
    preferred = preferred_lan_address()
    state = {
        "schema_version": 1,
        "pid": os.getpid(),
        "bind": bind,
        "port": actual_port,
        "urls": lan_urls(actual_port),
        "preferred_url": f"http://{preferred}:{actual_port}/" if preferred else f"http://127.0.0.1:{actual_port}/",
        "lan_only": True,
    }
    _atomic_json(SERVER_STATE_FILE, state)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        current = _read_json(SERVER_STATE_FILE)
        if current is not None and current.get("pid") == os.getpid():
            SERVER_STATE_FILE.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Responsive LAN monitor for phone-harness")
    parser.add_argument("--bind", default=DEFAULT_BIND)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    serve(args.bind, args.port)


if __name__ == "__main__":
    main()

