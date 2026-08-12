"""Long-lived phone runtime intended to be hosted by a future MCP server."""

from __future__ import annotations

import copy
import sys
import time

from . import helpers


RUNTIME_CONTRACT_VERSION = 1


class PhoneRuntime:
    """Small stable interface over observation, batched actions and status."""

    def __init__(self, observation_ttl=0.25):
        self.observation_ttl = float(observation_ttl)
        self._observation = None
        self._observed_at = 0.0

    def status(self):
        state = helpers.connection_state()
        result = {"contract_version": RUNTIME_CONTRACT_VERSION, "connection_state": state}
        if sys.platform == "win32":
            from . import windows

            result["transport_mode"] = windows.transport_mode()
            if result["transport_mode"] in {"wifi", "auto"}:
                result["tunneld"] = windows.tunneld_status()
            if state == "ready":
                result["active_transport"] = windows.active_transport()
        return result

    def observe(self, force=False):
        now = time.monotonic()
        cached = (
            not force
            and self._observation is not None
            and now - self._observed_at <= self.observation_ttl
        )
        if not cached:
            elements = helpers.elements()
            source = elements[0].get("source", "ocr") if elements else "none"
            self._observation = {"source": source, "elements": elements}
            self._observed_at = now
        assert self._observation is not None
        result = copy.deepcopy(self._observation)
        result["contract_version"] = RUNTIME_CONTRACT_VERSION
        result["cached"] = cached
        return result

    def invalidate(self):
        self._observation = None
        self._observed_at = 0.0

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
            raise RuntimeError(f"no visible text matches {text!r}")
        if index is None:
            if len(hits) != 1:
                raise RuntimeError(f"visible text {text!r} is ambiguous ({len(hits)} matches)")
            return hits[0]
        if not isinstance(index, int) or index < 0 or index >= len(hits):
            raise ValueError(f"tap_text index {index!r} is outside {len(hits)} matches")
        return hits[index]

    @staticmethod
    def _number(action, key, default=None):
        value = action.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{action.get('op')} requires numeric {key}")
        return value

    def _prepare(self, actions):
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
                    observation = self.observe()
                assert observation is not None
                target = self._match(
                    observation["elements"],
                    action.get("text"),
                    exact,
                    index,
                )
                prepared.append((op, action, target))
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

    def act(self, actions):
        """Execute one batch prepared from the current observed screen.

        All ``tap_text`` targets are resolved before the first mutation. Split
        workflows at an observe boundary when an action changes to a new screen.
        """
        prepared = self._prepare(actions)
        if sys.platform == "win32":
            from . import windows

            wda_batchable = all(
                op in {"tap_text", "type_text", "swipe"}
                and (op != "tap_text" or target.get("source") == "accessibility")
                for op, _action, target in prepared
            )
            if prepared and wda_batchable and windows.wda_runtime_batch_supported():
                wda_actions = []
                results = []
                for op, action, target in prepared:
                    if op == "tap_text":
                        wda_actions.append(windows._wda_action_for_accessibility(target))
                        result = {"text": target["text"], "source": "accessibility"}
                    elif op == "type_text":
                        wda_actions.append({"op": "type", "text": action["text"]})
                        result = None
                    else:
                        wda_actions.append(
                            windows._wda_action_for_swipe(
                                action["direction"],
                                action.get("distance", 0.4),
                            )
                        )
                        result = None
                    results.append({"op": op, "result": result})
                windows.run_wda_runtime_batch(wda_actions)
                self.invalidate()
                return {
                    "contract_version": RUNTIME_CONTRACT_VERSION,
                    "count": len(results),
                    "results": results,
                }

        if (
            sys.platform == "win32"
            and prepared
            and all(op == "tap_text" and target.get("source") == "accessibility" for op, _action, target in prepared)
        ):
            from . import windows

            targets = [target for _op, _action, target in prepared]
            windows.tap_accessibility_batch(targets)
            results = [
                {"op": "tap_text", "result": {"text": target["text"], "source": "accessibility"}}
                for target in targets
            ]
            self.invalidate()
            return {
                "contract_version": RUNTIME_CONTRACT_VERSION,
                "count": len(results),
                "results": results,
            }

        results = []
        for op, action, target in prepared:
            if op == "tap_text":
                if sys.platform == "win32" and target.get("source") == "accessibility":
                    from . import windows

                    windows.tap_accessibility(target)
                else:
                    helpers.tap(target["x"], target["y"])
                result = {"text": target["text"], "source": target.get("source", "ocr")}
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
            results.append({"op": op, "result": result})
        self.invalidate()
        return {
            "contract_version": RUNTIME_CONTRACT_VERSION,
            "count": len(results),
            "results": results,
        }
