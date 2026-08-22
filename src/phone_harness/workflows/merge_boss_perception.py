"""Calibratable real-device perception boundary for Merge Boss.

Planning and control are phone-independent. This adapter owns the remaining
screen-specific seam: customer-strip enumeration, board semantic bindings,
producer/item hint learning, and conversion into planner snapshots.

Concrete image/OCR readers are injected so calibration can evolve without
putting AliExpress-specific rules into PhoneRuntime or the generic planners.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from phone_harness.scroll_strip import enumerate_scroll_strip
from phone_harness.semantic_slots import parse_semantic_slots, semantic_slots_from_records
from phone_harness.workflows.merge_boss_catalog import (
    DEFAULT_CATALOG_PATH,
    MergeBossCatalog,
    record_item_family_hint,
    record_producer_output_hint,
)
from phone_harness.workflows.merge_boss_control import (
    MergeBossBindings,
    MergeBossSnapshot,
)
from phone_harness.workflows.merge_boss_strategy import MergeBossOrder


PENDING_REAL_DEVICE_CHECKS = (
    "calibrate the full horizontally-scrollable customer/order strip",
    "verify reliable identity+level extraction for one/two requested items per customer",
    "map each visible Complete/完成 control to its customer order after scrolling",
    "verify board-item upper-left i hint opens a complete level-1 through level-10 item-family list",
    "verify order-item upper-left i hint exposes the same item-family list",
    "extract and persist newly encountered item-family/type chains so future turns do not reopen known hints",
    "verify producer selection then upper-left i icon discovery in the description panel",
    "extract each producer's full possible-output list from the i information view",
    "verify energy extraction and selected-producer state",
    "bind recognized board item identity+level to 7x9 cell ids without full-screen OCR on every loop",
)


DEFAULT_CALIBRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "game-operations"
    / "aliexpress-merge-boss"
    / "calibration.json"
)


@dataclass(frozen=True)
class MergeBossPerceptionCalibration:
    """Values that must be verified against the live game layout."""

    calibrated: bool = False
    screen_size: tuple[int, int] | None = None
    board_bounds: dict | None = None
    order_strip_bounds: dict | None = None
    order_scroll_forward_action: dict | None = None
    order_scroll_backward_action: dict | None = None
    order_marker_geometry: dict | None = None
    item_family_hint_slots: tuple[dict, ...] = ()
    producer_hint_slots: tuple[dict, ...] = ()
    producer_badge_roi: dict | None = None
    producer_badge_templates: tuple[dict, ...] = ()
    producer_badge_match_threshold: float = 0.95
    empty_cell_reference_rgb: tuple[float, float, float] | None = None
    empty_cell_color_tolerance: float = 24.0
    max_order_pages: int = 8
    stagnant_order_pages: int = 2

    @classmethod
    def load(cls, path=None):
        source = Path(path) if path is not None else DEFAULT_CALIBRATION_PATH
        data = json.loads(source.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            raise ValueError("unsupported Merge Boss perception calibration version")
        screen = data.get("screen_size")
        empty_rgb = data.get("empty_cell_reference_rgb")
        return cls(
            calibrated=bool(data.get("calibrated", False)),
            screen_size=tuple(screen) if screen is not None else None,
            board_bounds=data.get("board_bounds"),
            order_strip_bounds=data.get("order_strip_bounds"),
            order_scroll_forward_action=data.get("order_scroll_forward_action"),
            order_scroll_backward_action=data.get("order_scroll_backward_action"),
            order_marker_geometry=data.get("order_marker_geometry"),
            item_family_hint_slots=tuple(data.get("item_family_hint_slots", [])),
            producer_hint_slots=tuple(data.get("producer_hint_slots", [])),
            producer_badge_roi=data.get("producer_badge_roi"),
            producer_badge_templates=tuple(data.get("producer_badge_templates", [])),
            producer_badge_match_threshold=float(data.get("producer_badge_match_threshold", 0.95)),
            empty_cell_reference_rgb=(
                tuple(float(value) for value in empty_rgb)
                if isinstance(empty_rgb, (list, tuple)) and len(empty_rgb) == 3
                else None
            ),
            empty_cell_color_tolerance=float(data.get("empty_cell_color_tolerance", 24.0)),
            max_order_pages=int(data.get("max_order_pages", 8)),
            stagnant_order_pages=int(data.get("stagnant_order_pages", 2)),
        )

    def to_dict(self):
        return {
            "version": 1,
            "calibrated": self.calibrated,
            "screen_size": list(self.screen_size) if self.screen_size is not None else None,
            "board_bounds": self.board_bounds,
            "order_strip_bounds": self.order_strip_bounds,
            "order_scroll_forward_action": self.order_scroll_forward_action,
            "order_scroll_backward_action": self.order_scroll_backward_action,
            "order_marker_geometry": self.order_marker_geometry,
            "item_family_hint_slots": list(self.item_family_hint_slots),
            "producer_hint_slots": list(self.producer_hint_slots),
            "producer_badge_roi": self.producer_badge_roi,
            "producer_badge_templates": list(self.producer_badge_templates),
            "producer_badge_match_threshold": self.producer_badge_match_threshold,
            "empty_cell_reference_rgb": (
                list(self.empty_cell_reference_rgb)
                if self.empty_cell_reference_rgb is not None
                else None
            ),
            "empty_cell_color_tolerance": self.empty_cell_color_tolerance,
            "max_order_pages": self.max_order_pages,
            "stagnant_order_pages": self.stagnant_order_pages,
        }

    @staticmethod
    def _scaled_rect(value, scale_x, scale_y):
        if value is None:
            return None
        result = dict(value)
        for key in ("x", "w"):
            if key in result:
                result[key] = float(result[key]) * scale_x
        for key in ("y", "h"):
            if key in result:
                result[key] = float(result[key]) * scale_y
        return result

    @staticmethod
    def _scaled_action(value, scale_x, scale_y):
        if value is None:
            return None
        result = dict(value)
        for key in ("x", "x1", "x2"):
            if key in result:
                result[key] = float(result[key]) * scale_x
        for key in ("y", "y1", "y2"):
            if key in result:
                result[key] = float(result[key]) * scale_y
        return result

    @staticmethod
    def _scaled_marker_geometry(value, scale_x, scale_y):
        if value is None:
            return None
        result = dict(value)
        if isinstance(result.get("anchor_band"), dict):
            result["anchor_band"] = MergeBossPerceptionCalibration._scaled_rect(
                result["anchor_band"], scale_x, scale_y
            )
        for key in ("full_anchor_x", "two_item_offsets", "full_page_anchor_reference"):
            if isinstance(result.get(key), (list, tuple)):
                result[key] = [float(item) * scale_x for item in result[key]]
        for key in ("one_item_offset", "partial_end_shift_min"):
            if key in result:
                result[key] = float(result[key]) * scale_x
        if "marker_y" in result:
            result["marker_y"] = float(result["marker_y"]) * scale_y
        if "state_radius" in result:
            result["state_radius"] = float(result["state_radius"]) * min(scale_x, scale_y)
        # Runtime-only metadata lets image readers scale component/crop gates
        # without changing the persisted calibration schema.
        result["_scale_x"] = scale_x
        result["_scale_y"] = scale_y
        return result

    def scaled_to_screen(self, screen_size):
        """Bind reference calibration geometry to one live device frame.

        ``calibration.json`` remains a reference-device profile.  Durable
        semantics therefore never identify an action by one device's pixels;
        pixels are produced only here at runtime for the current frame size.
        """
        if screen_size is None or self.screen_size is None:
            return self
        width, height = (float(screen_size[0]), float(screen_size[1]))
        ref_width, ref_height = (float(self.screen_size[0]), float(self.screen_size[1]))
        if width <= 0 or height <= 0 or ref_width <= 0 or ref_height <= 0:
            raise ValueError("screen sizes must be positive")
        scale_x = width / ref_width
        scale_y = height / ref_height
        if abs(scale_x - 1.0) < 1e-9 and abs(scale_y - 1.0) < 1e-9:
            return self

        def scale_slots(slots):
            scaled = []
            for slot in slots:
                item = dict(slot)
                if isinstance(item.get("bounds"), dict):
                    item["bounds"] = self._scaled_rect(item["bounds"], scale_x, scale_y)
                scaled.append(item)
            return tuple(scaled)

        return MergeBossPerceptionCalibration(
            calibrated=self.calibrated,
            screen_size=(int(round(width)), int(round(height))),
            board_bounds=self._scaled_rect(self.board_bounds, scale_x, scale_y),
            order_strip_bounds=self._scaled_rect(self.order_strip_bounds, scale_x, scale_y),
            order_scroll_forward_action=self._scaled_action(
                self.order_scroll_forward_action, scale_x, scale_y
            ),
            order_scroll_backward_action=self._scaled_action(
                self.order_scroll_backward_action, scale_x, scale_y
            ),
            order_marker_geometry=self._scaled_marker_geometry(
                self.order_marker_geometry, scale_x, scale_y
            ),
            item_family_hint_slots=scale_slots(self.item_family_hint_slots),
            producer_hint_slots=scale_slots(self.producer_hint_slots),
            producer_badge_roi=self.producer_badge_roi,
            producer_badge_templates=self.producer_badge_templates,
            producer_badge_match_threshold=self.producer_badge_match_threshold,
            empty_cell_reference_rgb=self.empty_cell_reference_rgb,
            empty_cell_color_tolerance=self.empty_cell_color_tolerance,
            max_order_pages=self.max_order_pages,
            stagnant_order_pages=self.stagnant_order_pages,
        )

    def save(self, path=None):
        target = Path(path) if path is not None else DEFAULT_CALIBRATION_PATH
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        return target

    def item_family_slots(self):
        return semantic_slots_from_records(self.item_family_hint_slots)

    def producer_slots(self):
        return semantic_slots_from_records(self.producer_hint_slots)

    def readiness_errors(self):
        errors = []
        if not self.calibrated:
            errors.append("live_geometry_not_calibrated")
        if self.screen_size is None:
            errors.append("screen_size_missing")
        if self.board_bounds is None:
            errors.append("board_bounds_missing")
        if self.order_strip_bounds is None:
            errors.append("order_strip_bounds_missing")
        if self.order_scroll_forward_action is None:
            errors.append("order_scroll_forward_missing")
        if self.order_scroll_backward_action is None:
            errors.append("order_scroll_backward_missing")
        if not 1 <= int(self.max_order_pages) <= 64:
            errors.append("max_order_pages_invalid")
        if not 1 <= int(self.stagnant_order_pages) <= 16:
            errors.append("stagnant_order_pages_invalid")
        return errors


@dataclass(frozen=True)
class MergeBossBoardReadout:
    observation_id: int
    items: tuple
    producers: tuple
    free_cells: int
    energy: int | None
    cell_centers: dict[str, tuple[float, float]]
    selected_producer_cell: str | None = None
    visual_merge_pairs: tuple[tuple[str, str], ...] = ()
    preview_path: str | None = None
    screen_size: tuple[int, int] | None = None


@dataclass(frozen=True)
class MergeBossOrderCardReadout:
    order_id: str
    items: tuple[tuple[str, int], ...]
    complete_center: tuple[float, float] | None = None
    all_items_present: bool = False
    refresh_center: tuple[float, float] | None = None


@dataclass(frozen=True)
class MergeBossOrderPageReadout:
    observation_id: int
    cards: tuple[MergeBossOrderCardReadout, ...]


@dataclass(frozen=True)
class MergeBossItemFamilyHintReadout:
    levels: tuple[dict, ...]
    family_id: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class MergeBossProducerHintReadout:
    outputs: tuple[dict, ...]
    complete: bool = True


@dataclass(frozen=True)
class MergeBossPerceptionReaders:
    """Injected readers used after live calibration.

    Reader signatures:

    - ``read_board(runtime, calibration) -> MergeBossBoardReadout``
    - ``read_visible_orders(runtime, calibration, page_index) -> MergeBossOrderPageReadout``
    - optional ``prepare_order_scan(runtime, calibration)``
    - optional ``scroll_orders_forward(runtime, calibration) -> bool``
    """

    read_board: object
    read_visible_orders: object
    prepare_order_scan: object | None = None
    scroll_orders_forward: object | None = None
    read_item_family_hint: object | None = None
    read_producer_output_hint: object | None = None


class MergeBossLivePerception:
    """Build planner snapshots from calibrated, injected screen readers."""

    def __init__(
        self,
        runtime,
        *,
        calibration=None,
        readers=None,
        catalog_path=None,
    ):
        self.runtime = runtime
        self.calibration = calibration or MergeBossPerceptionCalibration.load()
        self.catalog_path = catalog_path or DEFAULT_CATALOG_PATH
        if readers is None:
            from phone_harness.workflows.merge_boss_live_readers import default_merge_boss_readers
            readers = default_merge_boss_readers(catalog_path=self.catalog_path)
        self.readers = readers
        self._cached_order_scan = None
        # Runtime-only memory for visual pairs that repeatedly behaved unlike
        # a valid merge.  This is deliberately transient: it prevents one
        # confusing animated/look-alike pair from dominating play, while a
        # future frame can reconsider it after a short cooldown.
        self._merge_anomaly_counts = {}
        self._merge_pair_cooldowns = {}

    def record_merge_anomaly(self, pending, snapshot, anomaly, *, spawn_evidence=None):
        """Learn a short-lived reject for one repeatedly non-progressing pair.

        A single missing free-cell gain is not enough: bubbles and green cash
        can legitimately explain it.  Only a single-step anomaly with no new
        occupied cell and no independently observed spawn contributes to the
        counter.  Two repeats put the cell pair on a three-snapshot cooldown.
        """
        if spawn_evidence is not None or not isinstance(anomaly, dict):
            return ()
        steps = tuple(anomaly.get("steps") or ())
        if len(steps) != 1 or anomaly.get("new_cells"):
            return ()
        if int(anomaly.get("actual_free_gain", 0)) > 0:
            return ()
        pair = tuple(sorted(tuple(steps[0])))
        count = int(self._merge_anomaly_counts.get(pair, 0)) + 1
        self._merge_anomaly_counts[pair] = count
        if count < 2:
            return ()
        self._merge_pair_cooldowns[pair] = 3
        self._merge_anomaly_counts[pair] = 0
        trace = getattr(self.runtime, "trace", None)
        if trace is not None:
            trace.emit(
                "merge_boss.merge.suspect_pair",
                summary="Repeated non-progressing visual pair temporarily suppressed",
                phase="learning",
                status="observed",
                data={"pair": list(pair), "cooldown_snapshots": 3},
                preview_path=snapshot.bindings.preview_path,
            )
        return (pair,)

    def _apply_merge_pair_cooldowns(self, pairs):
        if not self._merge_pair_cooldowns:
            return tuple(pairs)
        kept = []
        active = {}
        for pair in pairs:
            key = tuple(sorted(tuple(pair)))
            remaining = int(self._merge_pair_cooldowns.get(key, 0))
            if remaining > 0:
                active[key] = remaining - 1
                continue
            kept.append(tuple(pair))
        # Retain only still-active cooldowns; pairs not currently visible also
        # age by one snapshot so this never becomes durable hidden state.
        for key, remaining in self._merge_pair_cooldowns.items():
            if key in active:
                continue
            if remaining > 1:
                active[key] = remaining - 1
        self._merge_pair_cooldowns = active
        return tuple(kept)

    def _missing(self):
        missing = list(self.calibration.readiness_errors())
        if self.readers is None:
            missing.append("screen_readers_not_configured")
        else:
            if not callable(self.readers.read_board):
                missing.append("board_reader_not_configured")
            if not callable(self.readers.read_visible_orders):
                missing.append("order_reader_not_configured")
        return missing

    def _learning_missing(self):
        missing = []
        if self.readers is None or not callable(self.readers.read_item_family_hint):
            missing.append("item_family_hint_reader_not_configured")
        if self.readers is None or not callable(self.readers.read_producer_output_hint):
            missing.append("producer_hint_reader_not_configured")
        return missing

    def status(self):
        missing = self._missing()
        if missing:
            return {
                "ready": False,
                "reason": "real_device_perception_calibration_required",
                "missing": missing,
                "pending_checks": list(PENDING_REAL_DEVICE_CHECKS),
            }
        learning_missing = self._learning_missing()
        return {
            "ready": True,
            "reason": "calibrated_cached_knowledge_path",
            "learning_ready": not learning_missing,
            "learning_missing": learning_missing,
        }

    def _scroll_orders_forward(self):
        if self.readers is not None and callable(self.readers.scroll_orders_forward):
            return bool(self.readers.scroll_orders_forward(self.runtime, self.calibration))
        action = self.calibration.order_scroll_forward_action
        if action is None:
            return False
        self.runtime.act([dict(action)])
        return True

    def _enumerate_orders(self):
        if self.readers is None:
            raise RuntimeError("Merge Boss screen readers are not configured")
        if callable(self.readers.prepare_order_scan):
            self.readers.prepare_order_scan(self.runtime, self.calibration)

        state = {"page_index": 0, "last_observation_id": None}

        def capture_items():
            page = self.readers.read_visible_orders(
                self.runtime,
                self.calibration,
                state["page_index"],
            )
            if not isinstance(page, MergeBossOrderPageReadout):
                raise TypeError("order reader must return MergeBossOrderPageReadout")
            state["last_observation_id"] = page.observation_id
            return [
                {
                    "order_id": card.order_id,
                    "card": card,
                    "page_index": state["page_index"],
                }
                for card in page.cards
            ]

        def scroll_next():
            if not self._scroll_orders_forward():
                return False
            state["page_index"] += 1
            return True

        result = enumerate_scroll_strip(
            capture_items,
            scroll_next,
            key_fn=lambda item: item["order_id"],
            max_pages=self.calibration.max_order_pages,
            stop_after_stagnant_pages=self.calibration.stagnant_order_pages,
        )
        return result, state

    def invalidate_order_cache(self):
        self._cached_order_scan = None

    def snapshot(self, *, rescan_orders=True):
        status = self.status()
        if not status["ready"]:
            raise RuntimeError("Merge Boss live perception is not calibrated yet")

        cached_order_items = (
            self._cached_order_scan[0].get("items")
            if self._cached_order_scan is not None
            and isinstance(self._cached_order_scan[0], dict)
            else None
        )
        # A modal/transition can temporarily cover the customer strip and
        # yield a syntactically valid zero-order scan. Merge Boss normally has
        # visible customer orders, so never let that transient empty result
        # become a durable cache that idles later turns after the overlay is
        # gone. Non-empty scans keep the original cheap cache path.
        cached_orders_empty = self._cached_order_scan is not None and not cached_order_items
        scanned_orders = bool(
            rescan_orders
            or self._cached_order_scan is None
            or cached_orders_empty
        )
        if scanned_orders:
            self._cached_order_scan = self._enumerate_orders()
        order_result, _order_state = self._cached_order_scan
        trace = getattr(self.runtime, "trace", None)
        if trace is not None:
            trace.emit(
                "merge_boss.order_scan",
                summary=(
                    f"Orders: {len(order_result['items'])} customer(s) / {order_result['page_count']} page(s)"
                    + (" (fresh)" if scanned_orders else " (cache)")
                ),
                phase="analysis",
                status="ok",
                data={
                    "customer_count": len(order_result["items"]),
                    "page_count": order_result["page_count"],
                    "stop": order_result.get("stop"),
                    "cached": not scanned_orders,
                },
            )

        # Read the board last. Order-strip scanning may create fresh
        # observations, so this guarantees the final observation id belongs to
        # the board state whose cell bindings the controller will act on.
        board = self.readers.read_board(self.runtime, self.calibration)
        if not isinstance(board, MergeBossBoardReadout):
            raise TypeError("board reader must return MergeBossBoardReadout")

        cards = [item["card"] for item in order_result["items"]]
        orders = tuple(
            MergeBossOrder(
                card.order_id,
                card.items,
                ready=bool(card.all_items_present or card.complete_center is not None),
            )
            for card in cards
        )
        complete_centers = {
            item["card"].order_id: item["card"].complete_center
            for item in order_result["items"]
            if item["card"].complete_center is not None
        }
        refresh_centers = {
            item["card"].order_id: item["card"].refresh_center
            for item in order_result["items"]
            if item["card"].refresh_center is not None
        }
        page_indices = {
            item["card"].order_id: int(item["page_index"])
            for item in order_result["items"]
        }
        final_page = max(
            (int(item["page_index"]) for item in order_result["items"]),
            default=max(0, order_result["page_count"] - 1),
        )

        live_calibration = (
            self.calibration.scaled_to_screen(board.screen_size)
            if board.screen_size is not None
            else self.calibration
        )
        bindings = MergeBossBindings(
            observation_id=board.observation_id,
            cell_centers=dict(board.cell_centers),
            complete_centers=complete_centers,
            refresh_centers=refresh_centers,
            selected_producer_cell=board.selected_producer_cell,
            order_page_indices=page_indices,
            order_scan_final_page=final_page,
            order_scroll_forward_action=live_calibration.order_scroll_forward_action,
            order_scroll_backward_action=live_calibration.order_scroll_backward_action,
            preview_path=board.preview_path,
        )
        visual_merge_pairs = self._apply_merge_pair_cooldowns(board.visual_merge_pairs)
        snapshot = MergeBossSnapshot(
            items=tuple(board.items),
            orders=orders,
            producers=tuple(board.producers),
            free_cells=int(board.free_cells),
            energy=board.energy,
            bindings=bindings,
            visual_merge_pairs=visual_merge_pairs,
        )
        if trace is not None:
            trace.emit(
                "merge_boss.snapshot",
                summary="Planner snapshot ready",
                phase="analysis",
                status="ok",
                data={
                    "observation_id": board.observation_id,
                    "item_count": len(snapshot.items),
                    "order_count": len(snapshot.orders),
                    "producer_count": len(snapshot.producers),
                    "free_cells": snapshot.free_cells,
                    "energy": snapshot.energy,
                    "visual_merge_pair_count": len(snapshot.visual_merge_pairs),
                    "orders_cached": not scanned_orders,
                },
            )
        return snapshot

    def learn_item_family_hint(
        self,
        levels,
        *,
        source,
        family_id=None,
        display_name=None,
    ):
        return record_item_family_hint(
            levels,
            path=self.catalog_path,
            family_id=family_id,
            display_name=display_name,
            source=source,
        )

    def learn_producer_output_hint(
        self,
        identity,
        level,
        outputs,
        *,
        complete=True,
        source="producer_i",
    ):
        return record_producer_output_hint(
            identity,
            level,
            outputs,
            path=self.catalog_path,
            complete=complete,
            source=source,
        )

    def ensure_item_family(
        self,
        identity,
        level,
        *,
        hint_target,
        source="item_i",
    ):
        """Return a complete family, opening/persisting its hint only if unknown."""
        catalog = MergeBossCatalog.load(self.catalog_path)
        known = catalog.family_for_item(identity, level)
        if known is not None and known.complete:
            return {
                "family_id": known.family_id,
                "learned": False,
                "family": known,
            }
        reader = None if self.readers is None else self.readers.read_item_family_hint
        if not callable(reader):
            raise RuntimeError("item-family hint reader is not configured")
        hint = reader(self.runtime, self.calibration, hint_target)
        if not isinstance(hint, MergeBossItemFamilyHintReadout):
            raise TypeError("item-family hint reader must return MergeBossItemFamilyHintReadout")
        family_id = self.learn_item_family_hint(
            hint.levels,
            source=source,
            family_id=hint.family_id,
            display_name=hint.display_name,
        )
        refreshed = MergeBossCatalog.load(self.catalog_path).family(family_id)
        if refreshed is None:
            raise RuntimeError("persisted item family could not be reloaded")
        return {"family_id": family_id, "learned": True, "family": refreshed}

    def ensure_producer_catalog(
        self,
        identity,
        level,
        *,
        hint_target,
    ):
        """Return producer knowledge, opening its hint only while incomplete."""
        catalog = MergeBossCatalog.load(self.catalog_path)
        known = catalog.producer(identity, level)
        if known is not None and known.spec.outputs_complete:
            return {"learned": False, "producer": known}
        reader = None if self.readers is None else self.readers.read_producer_output_hint
        if not callable(reader):
            raise RuntimeError("producer-output hint reader is not configured")
        hint = reader(self.runtime, self.calibration, hint_target)
        if not isinstance(hint, MergeBossProducerHintReadout):
            raise TypeError("producer hint reader must return MergeBossProducerHintReadout")
        self.learn_producer_output_hint(
            identity,
            level,
            hint.outputs,
            complete=hint.complete,
        )
        refreshed = MergeBossCatalog.load(self.catalog_path).producer(identity, level)
        if refreshed is None:
            raise RuntimeError("persisted producer could not be reloaded")
        return {"learned": True, "producer": refreshed}


def parse_item_family_hint_elements(
    elements,
    slots,
    *,
    family_id=None,
    display_name=None,
):
    """Normalize one calibrated item-family `i` view into a complete hint."""
    records = parse_semantic_slots(elements, slots, ignored_text=("i",))
    if [record["level"] for record in records] != list(range(1, 11)):
        raise ValueError("item-family hint slots must resolve levels 1 through 10 in order")
    return MergeBossItemFamilyHintReadout(
        levels=records,
        family_id=family_id,
        display_name=display_name,
    )


def parse_producer_hint_elements(elements, slots, *, complete=True):
    """Normalize calibrated producer-output slots into a producer hint."""
    records = parse_semantic_slots(elements, slots, ignored_text=("i",))
    return MergeBossProducerHintReadout(outputs=records, complete=complete)


def stable_order_id(customer_key, items):
    """Create a stable local order id from customer visual key + item demand."""
    material = json.dumps(
        {
            "customer": str(customer_key),
            "items": [[str(identity), int(level)] for identity, level in items],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"order-{hashlib.sha256(material).hexdigest()[:12]}"
