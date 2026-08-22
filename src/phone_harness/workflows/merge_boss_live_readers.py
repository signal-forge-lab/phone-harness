"""Real-image readers for the Merge Boss perception adapter.

This module is intentionally Merge Boss-specific. Generic image reduction and
descriptor ranking live outside it; this adapter applies game roles and the
7x9 board semantics.
"""

from __future__ import annotations

import time
import math
from pathlib import Path

from PIL import Image

from phone_harness.visual_descriptor import (
    descriptor_from_image,
    rank_descriptors,
    sprite_descriptor_from_image,
)
from phone_harness.visual_grid import (
    describe_grid_cells,
    rank_grid_pairs,
    rank_grid_pairs_coarse_to_fine,
    select_non_overlapping_pairs,
)
from phone_harness.workflows.merge_boss import (
    MergeBossFastWorkflow,
    MergeBossLayout,
    producer_badge_scores,
)
from phone_harness.workflows.merge_boss_catalog import DEFAULT_CATALOG_PATH, MergeBossCatalog
from phone_harness.workflows.merge_boss_perception import MergeBossBoardReadout
from phone_harness.workflows.merge_boss_perception import (
    MergeBossOrderCardReadout,
    MergeBossOrderPageReadout,
    stable_order_id,
)
from phone_harness.workflows.merge_boss_strategy import MergeBossBoardItem, MergeBossProducer


def _confident_match(ranked, *, min_score, min_margin):
    if not ranked or ranked[0]["score"] < min_score:
        return None
    if len(ranked) > 1 and ranked[0]["score"] - ranked[1]["score"] < min_margin:
        return None
    return ranked[0]


def _filter_known_semantic_mismatches(pairs, items):
    """Reject visually-similar pairs when both cells are semantically known and differ.

    Human Teaching established a useful fallback for merge anomalies: if no
    bubble/cash/other spawn explains the missing free-cell gain, verify the
    attempted items' levels.  The cheapest version of that check is available
    before acting whenever both cells already have catalog-backed identity and
    level.  Keep unknown visual identities eligible so this does not reduce
    recall for not-yet-learned items.
    """
    by_cell = {item.cell_id: item for item in items}
    kept = []
    rejected = []
    for pair in pairs:
        left, right = pair
        first = by_cell.get(left)
        second = by_cell.get(right)
        if first is None or second is None:
            kept.append(pair)
            continue
        first_known = int(getattr(first, "level", 0) or 0) > 0 and not str(first.identity).startswith("visual:")
        second_known = int(getattr(second, "level", 0) or 0) > 0 and not str(second.identity).startswith("visual:")
        if first_known and second_known and (
            first.identity != second.identity or int(first.level) != int(second.level)
        ):
            rejected.append(pair)
            continue
        kept.append(pair)
    return tuple(kept), tuple(rejected)


def order_item_descriptor(image, marker_center, geometry=None):
    """Return the sprite descriptor immediately above-left of one order marker.

    Merge Boss draws the blue ``i`` / green check at the lower-right of the
    requested item tile.  Keeping this crop game-specific avoids baking card
    geometry into the generic visual descriptor module.
    """
    x, y = (float(marker_center[0]), float(marker_center[1]))
    scale_x = float((geometry or {}).get("_scale_x", 1.0))
    scale_y = float((geometry or {}).get("_scale_y", 1.0))
    crop = image.crop((
        x - 115 * scale_x,
        y - 115 * scale_y,
        x - 5 * scale_x,
        y - 5 * scale_y,
    ))
    try:
        return sprite_descriptor_from_image(crop)
    finally:
        crop.close()


def _opencv():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - installed with Windows OCR stack
        raise RuntimeError("Merge Boss image readers require OpenCV") from exc
    return cv2, np


def _order_anchor_centers(image, geometry):
    """Find full customer-card anchors from the green circular-arrow control."""
    cv2, np = _opencv()
    rgb = np.asarray(image.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    band = geometry["anchor_band"]
    top = int(band["y"])
    bottom = top + int(band["h"])
    roi = hsv[top:bottom]
    mask = cv2.inRange(
        roi,
        np.array((35, 80, 80), np.uint8),
        np.array((85, 255, 255), np.uint8),
    )
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    components = []
    scale_x = float(geometry.get("_scale_x", 1.0))
    scale_y = float(geometry.get("_scale_y", 1.0))
    area_scale = scale_x * scale_y
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        if not (
            300 * area_scale <= area <= 1200 * area_scale
            and 20 * scale_x <= width <= 50 * scale_x
            and 35 * scale_y <= height <= 50 * scale_y
        ):
            continue
        cx, cy = centroids[index]
        components.append((float(cx), float(cy + top), area))
    components.sort(key=lambda item: item[0])

    anchors = []
    used = set()
    for first in range(len(components)):
        if first in used:
            continue
        best = None
        for second in range(first + 1, len(components)):
            if second in used:
                continue
            dx = components[second][0] - components[first][0]
            if dx > 46 * scale_x:
                break
            if (
                20 * scale_x <= dx <= 46 * scale_x
                and abs(components[second][1] - components[first][1]) <= 15 * scale_y
            ):
                best = second
                break
        if best is None:
            continue
        used.add(first)
        used.add(best)
        anchors.append((components[first][0] + components[best][0]) / 2)

    minimum, maximum = (float(value) for value in geometry["full_anchor_x"])
    return tuple(round(anchor, 3) for anchor in anchors if minimum <= anchor <= maximum)


def _order_marker_state(hsv, x, y, geometry):
    cv2, np = _opencv()
    radius = int(geometry["state_radius"])
    x = int(round(x))
    y = int(round(y))
    region = hsv[
        max(0, y - radius): min(hsv.shape[0], y + radius + 1),
        max(0, x - radius): min(hsv.shape[1], x + radius + 1),
    ]
    if region.size == 0:
        return None
    blue = cv2.inRange(
        region,
        np.array((85, 90, 80), np.uint8),
        np.array((125, 255, 255), np.uint8),
    )
    green = cv2.inRange(
        region,
        np.array((35, 80, 80), np.uint8),
        np.array((85, 255, 255), np.uint8),
    )
    blue_fraction = float((blue > 0).mean())
    green_fraction = float((green > 0).mean())
    if blue_fraction >= float(geometry["blue_min"]):
        return "missing"
    if green_fraction >= float(geometry["green_min"]):
        return "present"
    return None


def _order_customer_base(page_index, anchors, geometry):
    """Map one snap page to logical customer slots without using order content."""
    per_page = int(geometry.get("customers_per_full_page", 2))
    base = int(page_index) * per_page
    if page_index <= 0 or not anchors:
        return base
    reference = tuple(float(value) for value in geometry.get("full_page_anchor_reference", ()))
    if not reference:
        return base
    shift = abs(float(anchors[0]) - reference[0])
    if shift >= float(geometry.get("partial_end_shift_min", 40)):
        base -= int(geometry.get("partial_end_overlap", 1))
    return max(0, base)


class MergeBossOrderImageReader:
    """Read visible full customer cards without accessibility/OCR."""

    def __init__(self, *, catalog_path=None):
        self.catalog_path = Path(catalog_path) if catalog_path is not None else DEFAULT_CATALOG_PATH

    def __call__(self, runtime, calibration, page_index):
        started = time.perf_counter()
        if calibration.order_marker_geometry is None:
            raise RuntimeError("Merge Boss order marker geometry is not calibrated")
        observation = runtime.observe(force=True, mode="visual", image_profile="full")
        catalog = MergeBossCatalog.load(self.catalog_path)
        cv2, np = _opencv()
        cards = []
        with Image.open(Path(observation["_image_path"])) as source:
            image = source.convert("RGB")
            live_calibration = calibration.scaled_to_screen(image.size)
            geometry = live_calibration.order_marker_geometry
            hsv = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2HSV)
            anchors = _order_anchor_centers(image, geometry)
            customer_base = _order_customer_base(page_index, anchors, geometry)
            marker_y = float(geometry["marker_y"])
            for card_index, anchor in enumerate(anchors):
                side_offsets = tuple(float(value) for value in geometry["two_item_offsets"])
                side_states = tuple(
                    _order_marker_state(hsv, anchor + offset, marker_y, geometry)
                    for offset in side_offsets
                )
                if all(state is not None for state in side_states):
                    slots = tuple(
                        (anchor + offset, marker_y, state)
                        for offset, state in zip(side_offsets, side_states)
                    )
                else:
                    middle_x = anchor + float(geometry["one_item_offset"])
                    middle_state = _order_marker_state(hsv, middle_x, marker_y, geometry)
                    if middle_state is None:
                        # A partially-visible card or unexpected overlay is not
                        # trustworthy enough to become planner demand.
                        continue
                    slots = ((middle_x, marker_y, middle_state),)

                demands = []
                unknown_demand = False
                for marker_x, marker_y_value, state in slots:
                    if state != "missing":
                        continue
                    descriptor = order_item_descriptor(
                        image,
                        (marker_x, marker_y_value),
                        geometry,
                    )
                    ranked = catalog.rank_order_items(descriptor, limit=2)
                    matched = _confident_match(
                        ranked,
                        min_score=float(geometry["order_match_min_score"]),
                        min_margin=float(geometry["order_match_min_margin"]),
                    )
                    if matched is None:
                        # One unfamiliar customer must not block all board play.
                        # Omit this whole card from the planner snapshot until its
                        # item visual is learned; treating it as an empty/complete
                        # order would be unsafe, while failing the entire page
                        # prevents unrelated merges, production and known orders.
                        unknown_demand = True
                        break
                    demands.append((matched["identity"], int(matched["level"])))

                if unknown_demand:
                    continue

                # Customer position is authoritative for de-duplication. Two
                # different customers may legitimately request the same item.
                customer_key = f"customer-slot:{customer_base + card_index}"
                order_id = stable_order_id(customer_key, ())
                refresh_y = float(geometry["anchor_band"]["y"]) + float(geometry["anchor_band"]["h"]) / 2.0
                cards.append(MergeBossOrderCardReadout(
                    order_id,
                    tuple(demands),
                    complete_center=None,
                    all_items_present=all(state == "present" for _, _, state in slots),
                    refresh_center=(float(anchor), refresh_y),
                ))

        result = MergeBossOrderPageReadout(
            observation_id=observation["observation_id"],
            cards=tuple(cards),
        )
        trace = getattr(runtime, "trace", None)
        if trace is not None:
            trace.emit(
                "merge_boss.order_page",
                summary=f"Order page {page_index}: {len(cards)} customer(s)",
                phase="analysis",
                status="ok",
                data={
                    "page_index": int(page_index),
                    "observation_id": observation["observation_id"],
                    "customer_count": len(cards),
                    "missing_item_count": sum(len(card.items) for card in cards),
                    "ready_customer_count": sum(1 for card in cards if card.all_items_present),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
        return result


def prepare_order_scan(runtime, calibration):
    """Move the customer strip to its left edge with bounded safe drags."""
    screen_size = (
        runtime.retained_screen_size()
        if hasattr(runtime, "retained_screen_size")
        else None
    )
    if screen_size is None:
        observation = runtime.observe(force=True, mode="visual", image_profile="glance")
        with Image.open(Path(observation["_image_path"])) as source:
            screen_size = source.size
    action = calibration.scaled_to_screen(screen_size).order_scroll_backward_action
    if action is None:
        raise RuntimeError("Merge Boss backward order scroll is not calibrated")
    # Current live layout has exactly three snap positions. Two reverse drags
    # reach the left edge from any of them; an extra drag at the edge is a no-op.
    runtime.act([dict(action), dict(action)])


def scroll_orders_forward(runtime, calibration):
    screen_size = (
        runtime.retained_screen_size()
        if hasattr(runtime, "retained_screen_size")
        else None
    )
    if screen_size is None:
        observation = runtime.observe(force=True, mode="visual", image_profile="glance")
        with Image.open(Path(observation["_image_path"])) as source:
            screen_size = source.size
    action = calibration.scaled_to_screen(screen_size).order_scroll_forward_action
    if action is None:
        return False
    runtime.act([dict(action)])
    return True


def default_merge_boss_readers(*, catalog_path=None):
    """Construct the fast cached-knowledge real-device readers."""
    from phone_harness.workflows.merge_boss_perception import MergeBossPerceptionReaders

    return MergeBossPerceptionReaders(
        read_board=MergeBossBoardImageReader(catalog_path=catalog_path),
        read_visible_orders=MergeBossOrderImageReader(catalog_path=catalog_path),
        prepare_order_scan=prepare_order_scan,
        scroll_orders_forward=scroll_orders_forward,
        # Unknown semantics still fail closed and can be learned interactively.
        read_item_family_hint=None,
        read_producer_output_hint=None,
    )


class MergeBossBoardImageReader:
    def __init__(
        self,
        *,
        catalog_path=None,
        item_min_score=0.93,
        producer_min_score=0.89,
        min_margin=0.015,
        producer_min_margin=0.05,
        content_threshold=6.0,
        producer_badge_threshold=0.025,
        max_relaxed_checks=3,
    ):
        self.catalog_path = Path(catalog_path) if catalog_path is not None else DEFAULT_CATALOG_PATH
        self.item_min_score = float(item_min_score)
        self.producer_min_score = float(producer_min_score)
        self.min_margin = float(min_margin)
        self.producer_min_margin = float(producer_min_margin)
        self.content_threshold = float(content_threshold)
        self.producer_badge_threshold = float(producer_badge_threshold)
        if (
            isinstance(max_relaxed_checks, bool)
            or not isinstance(max_relaxed_checks, int)
            or not 0 <= max_relaxed_checks <= 8
        ):
            raise ValueError("max_relaxed_checks must be an integer between 0 and 8")
        self.max_relaxed_checks = max_relaxed_checks
        # Producer artwork animates, so a high-confidence template match can
        # transiently disappear for one frame. The reader instance is reused
        # across autonomous workflow calls and is discarded after an operator
        # Pause/replan, making a session-local union safe and much more stable
        # than treating every frame as a fresh producer layout.
        self._stable_producer_cells = set()

    @staticmethod
    def _jam_recovery_ranked_grid(image_path, calibration):
        """Use exhaustive full-resolution comparison only for a full-board jam.

        Normal turns intentionally keep the coarse-to-fine ~10% comparison
        budget. A full board with no strict merge is different: stopping is a
        broken game policy, so pay the one-off O(n^2) cost before asking a
        human or refreshing orders.
        """
        return rank_grid_pairs(
            image_path,
            rows=9,
            columns=7,
            bounds=calibration.board_bounds,
            inset=0.20,
        )

    @staticmethod
    def _jam_probe_pairs(ranked_grid, producer_cells):
        """Broaden candidate generation only when exhaustive jam search needs it.

        These are *not* merge-authoritative thresholds. Every returned pair is
        still verified through the in-game name/level panel before a merge can
        be planned. The broader gate exists to catch animated/special items
        such as cash bundles that are visually obvious but sit just below the
        normal relaxed descriptor threshold.
        """
        return [
            pair
            for pair in ranked_grid["pairs"]
            if not any(cell in producer_cells for cell in pair["cells"])
            and pair["score"] >= 0.86
            and pair["color_similarity"] >= 0.88
            and pair["edge_similarity"] >= 0.72
        ]

    @staticmethod
    def _template_producer_cells(image_path, calibration):
        if not calibration.producer_badge_templates or calibration.producer_badge_roi is None:
            return None
        roi = calibration.producer_badge_roi
        x0 = float(roi["x0"])
        y0 = float(roi["y0"])
        x1 = float(roi["x1"])
        y1 = float(roi["y1"])
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("producer badge ROI must use normalized cell fractions")
        bounds = calibration.board_bounds
        cell_width = float(bounds["w"]) / 7
        cell_height = float(bounds["h"]) / 9
        templates = {
            str(index): descriptor
            for index, descriptor in enumerate(calibration.producer_badge_templates)
        }
        found = set()
        with Image.open(Path(image_path)) as source:
            image = source.convert("RGB")
            for row in range(9):
                for column in range(7):
                    left = float(bounds["x"]) + column * cell_width
                    top = float(bounds["y"]) + row * cell_height
                    crop = image.crop((
                        left + cell_width * x0,
                        top + cell_height * y0,
                        left + cell_width * x1,
                        top + cell_height * y1,
                    ))
                    try:
                        descriptor = descriptor_from_image(crop)
                    finally:
                        crop.close()
                    ranked = rank_descriptors(descriptor, templates, limit=1)
                    if ranked and ranked[0]["score"] >= calibration.producer_badge_match_threshold:
                        found.add(f"r{row}c{column}")
        return found

    def __call__(self, runtime, calibration):
        started = time.perf_counter()
        if calibration.board_bounds is None:
            raise RuntimeError("Merge Boss board bounds are not calibrated")
        observation = runtime.observe(force=True, mode="visual", image_profile="full")
        image_path = observation["_image_path"]
        with Image.open(Path(image_path)) as source:
            screen_size = source.size
        live_calibration = calibration.scaled_to_screen(screen_size)
        cells = describe_grid_cells(
            image_path,
            rows=9,
            columns=7,
            bounds=live_calibration.board_bounds,
            inset=0.14,
        )["cells"]
        occupancy_cells = describe_grid_cells(
            image_path,
            rows=9,
            columns=7,
            bounds=live_calibration.board_bounds,
            inset=0.32,
        )["cells"]
        occupancy_scores = {
            cell["id"]: float(cell["content_score"])
            for cell in occupancy_cells
        }
        occupancy_by_id = {cell["id"]: cell for cell in occupancy_cells}
        empty_reference = live_calibration.empty_cell_reference_rgb
        empty_tolerance = float(live_calibration.empty_cell_color_tolerance)

        def is_empty_cell(cell_id):
            cell = occupancy_by_id.get(cell_id)
            if cell is None:
                return False
            score = float(cell.get("content_score", float("inf")))
            if empty_reference is None:
                return score < self.content_threshold
            mean_rgb = cell.get("mean_rgb")
            if not isinstance(mean_rgb, (list, tuple)) or len(mean_rgb) != 3:
                return score < self.content_threshold
            color_distance = math.sqrt(sum(
                (float(mean_rgb[index]) - float(empty_reference[index])) ** 2
                for index in range(3)
            ))
            # A true empty tile is both visually quiet near its center and
            # close to the calibrated board-background color. This rejects
            # flat-color item centers while ignoring producer glow that leaks
            # into the outer part of a neighboring empty cell.
            return score < self.content_threshold and color_distance <= empty_tolerance

        if empty_reference is None:
            # Legacy/unprofiled calibration: preserve the previous wider-crop
            # occupancy behavior. A deep-center variance test alone is unsafe
            # because flat-color real items can have almost no local variance.
            empty_cell_ids = {
                cell["id"]
                for cell in cells
                if float(cell.get("content_score", float("inf"))) < self.content_threshold
            }
        else:
            empty_cell_ids = {
                cell["id"] for cell in occupancy_cells if is_empty_cell(cell["id"])
            }
        ranked_grid = rank_grid_pairs_coarse_to_fine(
            image_path,
            rows=9,
            columns=7,
            bounds=live_calibration.board_bounds,
            inset=0.20,
            candidate_neighbors=4,
        )
        ranked_grid["occupancy_scores"] = occupancy_scores
        default_live_layout = MergeBossLayout().scaled_to_screen(screen_size)
        layout = MergeBossLayout(
            board_bounds=dict(live_calibration.board_bounds),
            info_region=dict(default_live_layout.info_region),
            reference_screen_size=tuple(screen_size),
        )
        catalog = MergeBossCatalog.load(self.catalog_path)
        template_producer_cells = self._template_producer_cells(image_path, live_calibration)
        producer_fallback_badge_count = None
        producer_fallback_visual_match_count = None
        if template_producer_cells is None:
            # The old color-only fallback is intentionally not trusted once a
            # producer visual catalog exists. On a real dense board it marked
            # 33/63 cells as producers because yellow/orange item artwork also
            # occupies the upper-right ROI. Fuse localized badge evidence with
            # an independent full-cell producer match instead. If no producer
            # visuals have been learned yet, preserve the original bootstrap
            # behavior so initial calibration remains possible.
            badge_scores = producer_badge_scores(image_path, layout=layout)
            badge_color_cells = {
                cell_id for cell_id, score in badge_scores.items()
                if score >= self.producer_badge_threshold
            }
            producer_fallback_badge_count = len(badge_color_cells)
            by_cell = {cell["id"]: cell for cell in cells}
            has_producer_visual_catalog = bool(
                cells
                and catalog.rank_visual_producers(
                    cells[0]["visual_descriptor"],
                    limit=1,
                )
            )
            if has_producer_visual_catalog:
                producer_cells = set()
                for cell_id in badge_color_cells:
                    ranked = catalog.rank_visual_producers(
                        by_cell[cell_id]["visual_descriptor"],
                        limit=2,
                    )
                    if _confident_match(
                        ranked,
                        min_score=self.producer_min_score,
                        min_margin=self.producer_min_margin,
                    ) is not None:
                        producer_cells.add(cell_id)
                producer_fallback_visual_match_count = len(producer_cells)
            else:
                producer_cells = badge_color_cells
                producer_fallback_visual_match_count = len(producer_cells)
            template_candidate_count = None
        else:
            template_candidate_count = len(template_producer_cells)
            producer_cells = template_producer_cells
        producer_cells = set(producer_cells)
        detected_producer_count = len(producer_cells)
        if self._stable_producer_cells:
            producer_cells.update(self._stable_producer_cells)
        self._stable_producer_cells.update(producer_cells)
        if len(producer_cells) < 2:
            raise RuntimeError("Merge Boss board not recognized: producer badges missing")

        merge_workflow = MergeBossFastWorkflow(runtime=runtime, layout=layout)
        strict_pairs, relaxed_pairs = merge_workflow._candidate_pairs(
            ranked_grid,
            producer_cells,
            content_threshold=self.content_threshold,
            empty_cells=empty_cell_ids,
        )
        provisional_free_cells = sum(
            1
            for cell in cells
            if cell["id"] not in producer_cells
            and cell["id"] in empty_cell_ids
        )
        jam_recovery_used = False
        jam_probe_candidate_count = 0
        # A full board is never a terminal state in Merge Boss. If the fast
        # shortlist has no strict merge, escalate *before* any panel taps so
        # the original full-resolution frame is still alive. This keeps the
        # normal path fast while making jam recovery deliberately thorough.
        if provisional_free_cells <= 0 and not strict_pairs:
            ranked_grid = self._jam_recovery_ranked_grid(image_path, live_calibration)
            ranked_grid["occupancy_scores"] = occupancy_scores
            jam_recovery_used = True
            strict_pairs, relaxed_pairs = merge_workflow._candidate_pairs(
                ranked_grid,
                producer_cells,
                content_threshold=self.content_threshold,
                empty_cells=empty_cell_ids,
            )
            if not strict_pairs and not relaxed_pairs:
                relaxed_pairs = self._jam_probe_pairs(ranked_grid, producer_cells)
                jam_probe_candidate_count = len(relaxed_pairs)
        visual_merge_pairs = tuple(
            tuple(pair["cells"])
            for pair in select_non_overlapping_pairs(
                strict_pairs,
                excluded_cells=producer_cells,
                limit=16,
            )
        )
        relaxed_checks = 0
        relaxed_confirmed = 0
        current_observation_id = observation["observation_id"]
        # The durable Merge Boss evidence already shows animated same-item
        # frames with good color similarity but edge similarity around 0.90.
        # When the fast strict path finds nothing, confirm only a few top
        # relaxed candidates through the in-game information panel rather than
        # blindly merging on appearance. This preserves semantic safety while
        # keeping the expensive confirmation off the normal path.
        relaxed_check_limit = 8 if jam_recovery_used else self.max_relaxed_checks
        if not visual_merge_pairs and relaxed_check_limit and relaxed_pairs:
            candidates = select_non_overlapping_pairs(
                relaxed_pairs,
                excluded_cells=producer_cells,
                limit=relaxed_check_limit,
            )
            confirmed = []
            current_observation = {"observation_id": current_observation_id}
            for pair in candidates:
                relaxed_checks += 1
                ok, evidence = merge_workflow._confirm_pair_by_panel(
                    current_observation,
                    ranked_grid,
                    pair,
                )
                current_observation_id = evidence["observation_id"]
                current_observation = {"observation_id": current_observation_id}
                if ok:
                    confirmed.append(tuple(pair["cells"]))
            if confirmed:
                visual_merge_pairs = tuple(confirmed)
                relaxed_confirmed = len(confirmed)

        items = []
        producers = []
        centers = {}
        free_cells = 0
        for cell in cells:
            cell_id = cell["id"]
            centers[cell_id] = tuple(cell["center"])
            if cell_id in producer_cells:
                ranked = catalog.rank_visual_producers(cell["visual_descriptor"], limit=2)
                matched = _confident_match(
                    ranked,
                    min_score=self.producer_min_score,
                    min_margin=self.producer_min_margin,
                )
                if matched is None:
                    producers.append(MergeBossProducer(
                        cell_id,
                        f"visual-producer:{cell['visual_signature']}",
                        0,
                    ))
                else:
                    producers.append(MergeBossProducer(
                        cell_id,
                        matched["identity"],
                        matched["level"],
                    ))
                continue
            if cell_id in empty_cell_ids:
                free_cells += 1
                continue
            ranked = catalog.rank_sprite_items(cell["sprite_descriptor"], limit=2)
            if not ranked:
                ranked = catalog.rank_visual_items(cell["visual_descriptor"], limit=2)
            matched = _confident_match(
                ranked,
                min_score=self.item_min_score,
                min_margin=self.min_margin,
            )
            if matched is None:
                items.append(MergeBossBoardItem(
                    cell_id,
                    f"visual:{cell['visual_signature']}",
                    0,
                ))
            else:
                items.append(MergeBossBoardItem(
                    cell_id,
                    matched["identity"],
                    matched["level"],
                ))

        visual_merge_pairs, semantic_mismatch_pairs = _filter_known_semantic_mismatches(
            visual_merge_pairs,
            items,
        )

        result = MergeBossBoardReadout(
            observation_id=current_observation_id,
            items=tuple(items),
            producers=tuple(producers),
            free_cells=free_cells,
            energy=None,
            cell_centers=centers,
            selected_producer_cell=None,
            visual_merge_pairs=visual_merge_pairs,
            preview_path=str(image_path),
            screen_size=tuple(int(value) for value in screen_size),
        )
        trace = getattr(runtime, "trace", None)
        if trace is not None:
            trace.emit(
                "merge_boss.board",
                summary=f"Board: {len(items)} items, {free_cells} free, {len(visual_merge_pairs)} merge pair(s)",
                phase="analysis",
                status="ok",
                data={
                    "observation_id": observation["observation_id"],
                    "item_count": len(items),
                    "known_item_count": sum(1 for item in items if not str(item.identity).startswith("visual:")),
                    "producer_count": len(producers),
                    "known_producer_count": sum(1 for producer in producers if not str(producer.identity).startswith("visual-producer:")),
                    "producer_detected_this_frame": detected_producer_count,
                    "producer_stable_cell_count": len(self._stable_producer_cells),
                    "producer_template_candidate_count": template_candidate_count,
                    "producer_fallback_badge_candidate_count": producer_fallback_badge_count,
                    "producer_fallback_visual_match_count": producer_fallback_visual_match_count,
                    "free_cells": free_cells,
                    "visual_merge_pair_count": len(visual_merge_pairs),
                    "semantic_mismatch_pair_count": len(semantic_mismatch_pairs),
                    "semantic_mismatch_pairs": [list(pair) for pair in semantic_mismatch_pairs],
                    "relaxed_merge_candidate_count": len(relaxed_pairs),
                    "relaxed_confirmation_checks": relaxed_checks,
                    "relaxed_confirmed_count": relaxed_confirmed,
                    "jam_recovery_used": jam_recovery_used,
                    "jam_probe_candidate_count": jam_probe_candidate_count,
                    "detail_comparison_fraction": ranked_grid.get("detail_comparison_fraction"),
                    "occupancy_inset": 0.32,
                    "empty_reference_rgb": (
                        list(empty_reference) if empty_reference is not None else None
                    ),
                    "empty_cell_count": len(empty_cell_ids),
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
        return result
