"""Private JSONL bridge between the Node MCP process and one PhoneRuntime."""

from __future__ import annotations

import base64
import contextlib
import json
import sys
from pathlib import Path

from phone_harness import helpers
from phone_harness.runtime import PhoneRuntime, PhoneRuntimeError


with contextlib.redirect_stdout(sys.stderr):
    RUNTIME = PhoneRuntime()


def _handle(method: str, params: dict) -> dict:
    with contextlib.redirect_stdout(sys.stderr):
        if method == "status":
            return RUNTIME.status()
        if method == "observe":
            result = RUNTIME.observe(force=bool(params.get("force", False)))
            if params.get("include_image") is True:
                path = Path(helpers.screenshot())
                result = {
                    **result,
                    "_image": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                    },
                }
            return result
        if method == "act":
            return RUNTIME.act(
                params.get("actions"),
                observation_id=params.get("observation_id"),
            )
    raise PhoneRuntimeError("INVALID_REQUEST", "Unsupported phone runtime method", phase="preflight")


def _response(request: dict) -> dict:
    request_id = request.get("id")
    try:
        method = request.get("method")
        params = request.get("params", {})
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            raise ValueError("request id must be an integer")
        if not isinstance(method, str) or not isinstance(params, dict):
            raise ValueError("invalid request")
        return {"id": request_id, "ok": True, "result": _handle(method, params)}
    except PhoneRuntimeError as exc:
        return {"id": request_id, "ok": False, "error": exc.to_dict()["error"]}
    except Exception:
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


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
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
        sys.stdout.write(json.dumps(response, ensure_ascii=True, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
