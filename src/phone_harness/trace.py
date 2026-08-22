"""Local observability event transport for phone-harness.

The monitor is intentionally decoupled from the runtime. Runtime code appends
compact JSONL events; an independent GUI can tail them without holding locks or
participating in phone control.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_MONITOR_ROOT = Path(
    os.environ.get(
        "PHONE_HARNESS_MONITOR_DIR",
        str(Path(tempfile.gettempdir()) / "phone-harness" / "monitor"),
    )
)
DEFAULT_TRACE_FILE = DEFAULT_MONITOR_ROOT / "trace" / "events.jsonl"
DEFAULT_PREVIEW_DIR = DEFAULT_MONITOR_ROOT / "previews"
_PROCESS_SINK = None


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _json_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return str(value)


class TraceSink:
    """Best-effort JSONL trace sink.

    Trace failures must never stop device automation, so ``emit`` deliberately
    swallows local filesystem errors after keeping an in-process last-event
    snapshot for tests/diagnostics.
    """

    def __init__(self, session_id, path=None, *, max_bytes=8 * 1024 * 1024):
        self.session_id = str(session_id)
        self.path = Path(path) if path is not None else DEFAULT_TRACE_FILE
        self.max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        self.last_event = None

    def _rotate_if_needed(self):
        if self.max_bytes <= 0 or not self.path.exists():
            return
        if self.path.stat().st_size < self.max_bytes:
            return
        previous = self.path.with_suffix(self.path.suffix + ".1")
        previous.unlink(missing_ok=True)
        self.path.replace(previous)

    @staticmethod
    def _persist_preview(source, event_id):
        source = Path(source)
        if not source.is_file() or source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            return None
        try:
            DEFAULT_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
            if source.stat().st_size > 2 * 1024 * 1024:
                from PIL import Image

                target = DEFAULT_PREVIEW_DIR / f"{event_id}.jpg"
                temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
                with Image.open(source) as image:
                    preview = image.convert("RGB")
                    preview.thumbnail((256, 1024), Image.Resampling.LANCZOS)
                    preview.save(temporary, format="JPEG", quality=28, optimize=True)
                os.replace(temporary, target)
            else:
                suffix = ".jpg" if source.suffix.lower() in {".jpg", ".jpeg"} else source.suffix.lower()
                target = DEFAULT_PREVIEW_DIR / f"{event_id}{suffix}"
                temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
            previews = sorted(
                (path for path in DEFAULT_PREVIEW_DIR.iterdir() if path.is_file()),
                key=lambda path: path.stat().st_mtime,
            )
            for stale in previews[:-500]:
                stale.unlink(missing_ok=True)
            return target
        except Exception:
            return None

    def emit(
        self,
        event_type,
        *,
        summary=None,
        phase=None,
        status=None,
        data=None,
        preview_path=None,
    ):
        event_id = uuid.uuid4().hex
        event = {
            "schema_version": 1,
            "event_id": event_id,
            "session_id": self.session_id,
            "at": _utc_now(),
            "monotonic_ns": time.monotonic_ns(),
            "type": str(event_type),
        }
        if summary is not None:
            event["summary"] = str(summary)
        if phase is not None:
            event["phase"] = str(phase)
        if status is not None:
            event["status"] = str(status)
        if data is not None:
            event["data"] = _json_value(data)
        if preview_path is not None:
            persisted = self._persist_preview(preview_path, event_id)
            event["preview_path"] = str(persisted or preview_path)
        self.last_event = event
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed()
                with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass
        return event


def read_trace_events(path=None, *, limit=500):
    source = Path(path) if path is not None else DEFAULT_TRACE_FILE
    if not source.exists():
        return []
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    events = []
    for line in lines[-max(0, int(limit)):]:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def emit_process_event(event_type, **kwargs):
    """Emit from helper modules that do not own a PhoneRuntime instance."""
    global _PROCESS_SINK
    if _PROCESS_SINK is None:
        _PROCESS_SINK = TraceSink(f"process-{os.getpid()}")
    return _PROCESS_SINK.emit(event_type, **kwargs)

