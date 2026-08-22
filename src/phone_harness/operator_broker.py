"""File-backed human teaching/question broker used by the Windows monitor."""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path

from phone_harness.trace import DEFAULT_MONITOR_ROOT


DEFAULT_OPERATOR_ROOT = DEFAULT_MONITOR_ROOT / "operator"
DEFAULT_PENDING_FILE = DEFAULT_OPERATOR_ROOT / "pending.json"
DEFAULT_RESPONSE_DIR = DEFAULT_OPERATOR_ROOT / "responses"
DEFAULT_QUESTION_DIR = DEFAULT_OPERATOR_ROOT / "questions"
DEFAULT_QUESTION_PREVIEW_DIR = DEFAULT_OPERATOR_ROOT / "question-previews"
DEFAULT_TEACHING_DIR = DEFAULT_OPERATOR_ROOT / "teaching"
DEFAULT_TEACHING_ATTACHMENT_DIR = DEFAULT_OPERATOR_ROOT / "teaching-attachments"
DEFAULT_PRESENCE_FILE = DEFAULT_OPERATOR_ROOT / "presence.json"
TEACHING_IMAGE_MIME_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
MAX_TEACHING_IMAGE_BYTES = 8 * 1024 * 1024


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def load_operator_presence(path=None):
    """Return durable human-presence state; missing state preserves old behavior."""
    source = Path(path) if path is not None else DEFAULT_PRESENCE_FILE
    value = _load_json(source)
    return {
        "schema_version": 1,
        "present": True if value is None else bool(value.get("present", True)),
        "updated_unix": None if value is None else value.get("updated_unix"),
    }


def set_operator_presence(present, *, path=None, trace=None):
    if not isinstance(present, bool):
        raise ValueError("operator presence must be boolean")
    target = Path(path) if path is not None else DEFAULT_PRESENCE_FILE
    payload = {
        "schema_version": 1,
        "present": present,
        "updated_unix": time.time(),
    }
    _atomic_json(target, payload)
    if trace is not None:
        trace.emit(
            "operator.presence",
            summary="Human operator present" if present else "Human operator absent",
            phase="human_input",
            status="present" if present else "absent",
            data={"present": present},
        )
    return payload


class HumanTeachingInbox:
    """Durable operator-initiated messages, optionally with one image."""

    def __init__(self, *, trace=None, message_dir=None, attachment_dir=None):
        self.trace = trace
        self.message_dir = Path(message_dir) if message_dir is not None else DEFAULT_TEACHING_DIR
        self.attachment_dir = (
            Path(attachment_dir) if attachment_dir is not None else DEFAULT_TEACHING_ATTACHMENT_DIR
        )

    def submit(
        self,
        text="",
        *,
        image_bytes=None,
        image_mime_type=None,
        image_name=None,
        source="operator",
        blocking=True,
    ):
        if not isinstance(text, str):
            raise ValueError("teaching text must be a string")
        text = text.strip()
        if len(text) > 16000:
            raise ValueError("teaching text is too long")
        if image_bytes is not None and not isinstance(image_bytes, (bytes, bytearray)):
            raise ValueError("teaching image must be bytes")
        image_bytes = bytes(image_bytes) if image_bytes is not None else None
        if not text and not image_bytes:
            raise ValueError("teaching message requires text or an image")
        if image_bytes is not None:
            if image_mime_type not in TEACHING_IMAGE_MIME_TYPES:
                raise ValueError("teaching image must be PNG, JPEG, or WebP")
            if len(image_bytes) > MAX_TEACHING_IMAGE_BYTES:
                raise ValueError("teaching image exceeds 8 MiB")

        message_id = uuid.uuid4().hex
        attachment_path = None
        if image_bytes is not None:
            self.attachment_dir.mkdir(parents=True, exist_ok=True)
            attachment_path = self.attachment_dir / f"{message_id}{TEACHING_IMAGE_MIME_TYPES[image_mime_type]}"
            temporary = attachment_path.with_name(f".{attachment_path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(image_bytes)
            os.replace(temporary, attachment_path)

        payload = {
            "schema_version": 1,
            "message_id": message_id,
            "text": text,
            "image_path": str(attachment_path) if attachment_path is not None else None,
            "image_mime_type": image_mime_type if attachment_path is not None else None,
            "image_name": str(image_name)[:255] if image_name else None,
            "created_unix": time.time(),
            "source": str(source or "operator")[:120],
            "blocking": bool(blocking),
            "status": "pending",
            "reply": None,
            "learned": None,
            "handled_unix": None,
        }
        _atomic_json(self.message_dir / f"{message_id}.json", payload)
        if self.trace is not None:
            self.trace.emit(
                "operator.message",
                summary=text[:160] or "Human teaching image received",
                phase="human_input",
                status="pending",
                data={
                    "message_id": message_id,
                    "has_image": attachment_path is not None,
                    "image_name": payload["image_name"],
                    "source": payload["source"],
                    "blocking": payload["blocking"],
                },
                preview_path=attachment_path,
            )
        return payload

    def list(self, *, status=None, limit=50):
        if status is not None and status not in {"pending", "handled"}:
            raise ValueError("unsupported teaching status")
        if not self.message_dir.exists():
            return []
        values = []
        for path in self.message_dir.glob("*.json"):
            value = _load_json(path)
            if value is None:
                continue
            if status is not None and value.get("status") != status:
                continue
            values.append(value)
        values.sort(key=lambda item: (float(item.get("created_unix", 0)), str(item.get("message_id", ""))))
        return values[-max(0, int(limit)):]

    def next_pending(self):
        values = self.list(status="pending", limit=500)
        return values[0] if values else None

    def get(self, message_id):
        if not isinstance(message_id, str) or not message_id:
            return None
        return _load_json(self.message_dir / f"{message_id}.json")

    def handle(self, message_id, *, reply=None, learned=False):
        payload = self.get(message_id)
        if payload is None:
            raise ValueError("teaching message not found")
        if reply is not None and not isinstance(reply, str):
            raise ValueError("teaching reply must be a string")
        payload["status"] = "handled"
        payload["reply"] = reply
        payload["learned"] = bool(learned)
        payload["handled_unix"] = time.time()
        _atomic_json(self.message_dir / f"{message_id}.json", payload)
        if self.trace is not None:
            self.trace.emit(
                "operator.message.handled",
                summary="Human teaching handled",
                phase="human_input",
                status="ok",
                data={
                    "message_id": message_id,
                    "reply": reply,
                    "learned": bool(learned),
                },
            )
        return payload


class OperatorQuestionBroker:
    def __init__(
        self,
        *,
        trace=None,
        pending_file=None,
        response_dir=None,
        question_dir=None,
        question_preview_dir=None,
        presence_file=None,
    ):
        self.trace = trace
        self.pending_file = Path(pending_file) if pending_file is not None else DEFAULT_PENDING_FILE
        self.response_dir = Path(response_dir) if response_dir is not None else DEFAULT_RESPONSE_DIR
        if question_dir is not None:
            self.question_dir = Path(question_dir)
        elif pending_file is not None:
            self.question_dir = Path(pending_file).parent / "questions"
        elif response_dir is not None:
            self.question_dir = Path(response_dir).parent / "questions"
        else:
            self.question_dir = DEFAULT_QUESTION_DIR
        if question_preview_dir is not None:
            self.question_preview_dir = Path(question_preview_dir)
        else:
            self.question_preview_dir = self.question_dir.parent / "question-previews"
        self.presence_file = Path(presence_file) if presence_file is not None else DEFAULT_PRESENCE_FILE

    def _persist_preview(self, question_id, preview_path):
        if preview_path is None:
            return None
        source = Path(preview_path)
        try:
            if not source.is_file():
                return None
            suffix = source.suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                return None
            if source.stat().st_size > 16 * 1024 * 1024:
                return None
        except OSError:
            return None
        self.question_preview_dir.mkdir(parents=True, exist_ok=True)
        target = self.question_preview_dir / f"{question_id}{suffix}"
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
        return str(target)

    def is_present(self):
        return bool(load_operator_presence(self.presence_file)["present"])

    def post(
        self,
        question,
        *,
        choices=(),
        context=None,
        confidence=None,
        impact="medium",
        preview_path=None,
        promote_answer_to_teaching=False,
        dedupe_key=None,
    ):
        if not isinstance(question, str) or not question.strip():
            raise ValueError("operator question must be non-empty")
        choices = tuple(str(choice) for choice in choices)
        if len(choices) > 8:
            raise ValueError("operator question may contain at most 8 choices")
        if confidence is not None and not 0 <= float(confidence) <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if impact not in {"low", "medium", "high"}:
            raise ValueError("impact must be low, medium, or high")
        if dedupe_key:
            for existing in self.list(status="pending", limit=500):
                if existing.get("dedupe_key") == str(dedupe_key):
                    return existing
        question_id = uuid.uuid4().hex
        persisted_preview = self._persist_preview(question_id, preview_path)
        payload = {
            "schema_version": 1,
            "question_id": question_id,
            "question": question.strip(),
            "choices": list(choices),
            "context": context,
            "confidence": confidence,
            "impact": impact,
            "preview_path": persisted_preview,
            "created_unix": time.time(),
            "status": "pending",
            "answer": None,
            "choice": None,
            "answered_unix": None,
            "consumed_unix": None,
            "promote_answer_to_teaching": bool(promote_answer_to_teaching),
            "dedupe_key": str(dedupe_key) if dedupe_key else None,
        }
        _atomic_json(self.question_dir / f"{question_id}.json", payload)
        self._refresh_pending_file()
        if self.trace is not None:
            self.trace.emit(
                "operator.question",
                summary=payload["question"],
                phase="waiting_human",
                status="pending",
                data={
                    "question_id": question_id,
                    "choices": list(choices),
                    "context": context,
                    "confidence": confidence,
                    "impact": impact,
                },
                preview_path=persisted_preview,
            )
        return payload

    def get(self, question_id):
        if not isinstance(question_id, str) or not question_id:
            return None
        return _load_json(self.question_dir / f"{question_id}.json")

    def list(self, *, status=None, limit=50):
        if status is not None and status not in {"pending", "answered", "consumed"}:
            raise ValueError("unsupported operator question status")
        if not self.question_dir.exists():
            return []
        values = []
        for path in self.question_dir.glob("*.json"):
            value = _load_json(path)
            if value is None:
                continue
            if status is not None and value.get("status") != status:
                continue
            values.append(value)
        values.sort(key=lambda item: (float(item.get("created_unix", 0)), str(item.get("question_id", ""))))
        return values[-max(0, int(limit)):]

    def register_existing(self, record):
        """Persist one trace-only historical question so it stays answerable."""
        question_id = record.get("question_id") if isinstance(record, dict) else None
        if not isinstance(question_id, str) or not question_id:
            return None
        current = self.get(question_id)
        if current is not None:
            return current
        payload = {
            "schema_version": 1,
            "question_id": question_id,
            "question": str(record.get("question") or "Question"),
            "choices": list(record.get("choices") or ()),
            "context": record.get("context"),
            "confidence": record.get("confidence"),
            "impact": record.get("impact") or "medium",
            "preview_path": self._persist_preview(question_id, record.get("preview_path")),
            "created_unix": float(record.get("created_unix") or time.time()),
            "status": "pending" if record.get("answer") is None else "consumed",
            "answer": record.get("answer"),
            "choice": record.get("choice"),
            "answered_unix": record.get("answered_unix"),
            "consumed_unix": record.get("answered_unix"),
            "promote_answer_to_teaching": bool(record.get("promote_answer_to_teaching", True)),
            "dedupe_key": record.get("dedupe_key"),
        }
        _atomic_json(self.question_dir / f"{question_id}.json", payload)
        self._refresh_pending_file()
        return payload

    def wait(self, question_id, *, timeout=300.0, poll_interval=0.1):
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive or None")
        response_path = self.response_dir / f"{question_id}.json"
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while deadline is None or time.monotonic() < deadline:
            if response_path.exists():
                return self._consume_response(response_path)
            if not self.is_present():
                if self.trace is not None:
                    self.trace.emit(
                        "operator.question.deferred",
                        summary="Human question remains pending while operator is absent",
                        phase="waiting_human",
                        status="pending",
                        data={"question_id": question_id, "reason": "operator_absent"},
                    )
                return None
            time.sleep(max(0.02, float(poll_interval)))
        if self.trace is not None:
            self.trace.emit(
                "operator.timeout",
                summary="Human question wait ended; question remains answerable",
                phase="waiting_human",
                status="pending",
                data={"question_id": question_id},
            )
        return None

    def ask(self, question, *, timeout=300.0, **kwargs):
        pending = self.post(question, **kwargs)
        if not self.is_present():
            return None
        return self.wait(pending["question_id"], timeout=timeout)

    def _refresh_pending_file(self):
        pending = self.list(status="pending", limit=500)
        if pending:
            _atomic_json(self.pending_file, pending[0])
        else:
            self.pending_file.unlink(missing_ok=True)

    def _consume_response(self, response_path):
        response_path = Path(response_path)
        response = _load_json(response_path)
        if response is None:
            return None
        response_path.unlink(missing_ok=True)
        question_id = response.get("question_id")
        question = self.get(question_id)
        if question is not None:
            question["status"] = "consumed"
            question["answer"] = response.get("answer")
            question["choice"] = response.get("choice")
            question["answered_unix"] = response.get("submitted_unix") or time.time()
            question["consumed_unix"] = time.time()
            _atomic_json(self.question_dir / f"{question_id}.json", question)
        self._refresh_pending_file()
        if self.trace is not None:
            self.trace.emit(
                "operator.answer",
                summary="Human teaching received",
                phase="human_answer",
                status="ok",
                data={
                    "question_id": question_id,
                    "answer": response.get("answer"),
                    "choice": response.get("choice"),
                },
            )
        return response

    def consume_responses(self, *, limit=20):
        if not self.response_dir.exists():
            return []
        paths = sorted(
            self.response_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
        )[:max(0, int(limit))]
        values = []
        for path in paths:
            question = self.get(path.stem)
            response = self._consume_response(path)
            if response is not None:
                values.append({"question": question, "response": response})
        return values

    @staticmethod
    def submit_response(
        question_id,
        answer,
        *,
        choice=None,
        response_dir=None,
        question_dir=None,
        pending_file=None,
    ):
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("question_id must be non-empty")
        if not isinstance(answer, str):
            raise ValueError("answer must be a string")
        target_dir = Path(response_dir) if response_dir is not None else DEFAULT_RESPONSE_DIR
        if question_dir is not None:
            questions = Path(question_dir)
        elif response_dir is not None:
            questions = Path(response_dir).parent / "questions"
        else:
            questions = DEFAULT_QUESTION_DIR
        question_path = questions / f"{question_id}.json"
        question = _load_json(question_path)
        if question is None:
            raise ValueError("operator question not found")
        if question.get("status") not in {"pending", "answered"}:
            raise ValueError("operator question is already closed")
        response = {
            "schema_version": 1,
            "question_id": question_id,
            "answer": answer,
            "choice": choice,
            "submitted_unix": time.time(),
        }
        question["status"] = "answered"
        question["answer"] = answer
        question["choice"] = choice
        question["answered_unix"] = response["submitted_unix"]
        _atomic_json(question_path, question)
        _atomic_json(target_dir / f"{question_id}.json", response)
        if pending_file is not None:
            pending_path = Path(pending_file)
        elif response_dir is not None:
            pending_path = Path(response_dir).parent / "pending.json"
        else:
            pending_path = DEFAULT_PENDING_FILE
        remaining = []
        if questions.exists():
            for path in questions.glob("*.json"):
                value = _load_json(path)
                if value is not None and value.get("status") == "pending":
                    remaining.append(value)
        remaining.sort(key=lambda item: (float(item.get("created_unix", 0)), str(item.get("question_id", ""))))
        if remaining:
            _atomic_json(pending_path, remaining[0])
        else:
            pending_path.unlink(missing_ok=True)
        return response


def load_pending_question(path=None):
    source = Path(path) if path is not None else DEFAULT_PENDING_FILE
    if not source.exists():
        return None
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None

