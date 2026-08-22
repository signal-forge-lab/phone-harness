"""Private JSONL bridge between the Node MCP process and one PhoneRuntime."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import sys
import time
from pathlib import Path

from phone_harness.runtime import PhoneRuntime, PhoneRuntimeError


with contextlib.redirect_stdout(sys.stderr):
    RUNTIME = PhoneRuntime()


def _handle(method: str, params: dict) -> dict:
    with contextlib.redirect_stdout(sys.stderr):
        # Observability must never become a prerequisite for phone control.
        # A trace filesystem issue should reduce monitor fidelity, not turn an
        # otherwise successful status/observe/action into BRIDGE_UNAVAILABLE.
        try:
            RUNTIME.trace_host_request(method, params)
        except Exception:
            pass
        if method == "status":
            return RUNTIME.status()
        if method == "observe":
            include_image = params.get("include_image") is True or params.get("mode") == "visual"
            result = RUNTIME.observe(
                force=bool(params.get("force", False)),
                include_text_content=bool(params.get("include_text_content", False)),
                region=params.get("region"),
                include_image=include_image,
                mode=params.get("mode", "semantic"),
                image_profile=params.get("image_profile", "full"),
                reuse_observation_id=params.get("reuse_observation_id"),
            )
            image_path = result.pop("_image_path", None)
            image_mime_type = result.pop("_image_mime_type", None)
            if include_image:
                if not isinstance(image_path, str):
                    raise RuntimeError("phone runtime did not retain an observation image")
                if image_mime_type not in {"image/png", "image/jpeg"}:
                    raise RuntimeError("phone runtime returned unsupported image mime type")
                path = Path(image_path)
                result = {
                    **result,
                    "_image": {
                        "mime_type": image_mime_type,
                        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                    },
                }
            return result
        if method == "act":
            return RUNTIME.act(
                params.get("actions"),
                observation_id=params.get("observation_id"),
            )
        if method == "workflow":
            return RUNTIME.run_workflow(
                params.get("name"),
                options=params.get("options"),
            )
        if method == "decision":
            return RUNTIME.trace_decision(params)
        if method == "host_activity":
            return RUNTIME.mark_host_activity(
                params.get("state"),
                note=params.get("note"),
            )
        if method == "operator_question":
            return RUNTIME.ask_operator(
                params.get("question"),
                choices=params.get("choices") or (),
                context=params.get("context"),
                confidence=params.get("confidence"),
                impact=params.get("impact", "medium"),
                timeout=params.get("timeout", 300),
                preview_path=params.get("preview_path"),
            )
        if method == "teaching_next":
            result = RUNTIME.next_human_teaching()
            image_path = result.pop("_image_path", None)
            image_mime_type = result.pop("_image_mime_type", None)
            if image_path:
                if image_mime_type not in {"image/png", "image/jpeg", "image/webp"}:
                    raise RuntimeError("human teaching returned unsupported image mime type")
                path = Path(image_path)
                result["_image"] = {
                    "mime_type": image_mime_type,
                    "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                }
            return result
        if method == "teaching_handle":
            return RUNTIME.handle_human_teaching(
                params.get("message_id"),
                reply=params.get("reply"),
                learned=bool(params.get("learned", False)),
            )
    raise PhoneRuntimeError("INVALID_REQUEST", "Unsupported phone runtime method", phase="preflight")


def _response(request: dict) -> dict:
    request_id = request.get("id")
    started = time.perf_counter()
    try:
        method = request.get("method")
        params = request.get("params", {})
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            raise ValueError("request id must be an integer")
        if not isinstance(method, str) or not isinstance(params, dict):
            raise ValueError("invalid request")
        result = _handle(method, params)
        try:
            RUNTIME.trace.emit(
                "host.request.complete",
                summary=f"Host request completed: {method}",
                phase="host",
                status="completed",
                data={
                    "request_id": request_id,
                    "method": method,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
        except Exception:
            pass
        return {"id": request_id, "ok": True, "result": result}
    except PhoneRuntimeError as exc:
        try:
            RUNTIME.trace.emit(
                "host.request.failed",
                summary="Host request failed",
                phase="host",
                status="failed",
                data={
                    "request_id": request_id,
                    "error_code": exc.code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
        except Exception:
            pass
        return {"id": request_id, "ok": False, "error": exc.to_dict()["error"]}
    except Exception as exc:
        try:
            RUNTIME.trace.emit(
                "host.request.failed",
                summary="Host request failed",
                phase="host",
                status="failed",
                data={
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
            RUNTIME.trace.emit(
                "bridge.exception",
                summary=f"Unhandled bridge exception: {type(exc).__name__}",
                phase="bridge",
                status="error",
                data={
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        except Exception:
            pass
        return {
            "id": request_id,
            "ok": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Phone runtime bridge failed",
                "retryable": False,
                "phase": "bridge",
            },
        }


def _serialized_response(response: dict) -> str:
    try:
        return json.dumps(response, ensure_ascii=True, separators=(",", ":"))
    except Exception as exc:
        try:
            RUNTIME.trace.emit(
                "bridge.serialization_error",
                summary=f"Bridge response serialization failed: {type(exc).__name__}",
                phase="bridge",
                status="error",
                data={
                    "request_id": response.get("id"),
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:300],
                },
            )
        except Exception:
            pass
        fallback = {
            "id": response.get("id"),
            "ok": False,
            "error": {
                "code": "SERIALIZATION_ERROR",
                "message": "Phone runtime result could not be serialized",
                "retryable": False,
                "phase": "bridge",
            },
        }
        return json.dumps(fallback, ensure_ascii=True, separators=(",", ":"))


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.flush()
    try:
        with os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1) as protocol_stdout:
            os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
            for raw in sys.stdin:
                try:
                    request = json.loads(raw)
                    if not isinstance(request, dict):
                        raise ValueError
                    response = _response(request)
                except Exception:
                    response = {
                        "id": None,
                        "ok": False,
                        "error": {
                            "code": "INVALID_BRIDGE_REQUEST",
                            "message": "Invalid bridge request",
                            "retryable": False,
                            "phase": "bridge",
                        },
                    }
                protocol_stdout.write(_serialized_response(response) + "\n")
    finally:
        with contextlib.redirect_stdout(sys.stderr):
            RUNTIME.close()


if __name__ == "__main__":
    main()
