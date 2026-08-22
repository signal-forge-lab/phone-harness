"""Bounded fast-loop workflow for AliExpress Merge Boss.

Game semantics stay here rather than in PhoneRuntime. The workflow uses one
long-lived PhoneRuntime so WDA, FrameBroker and OCR/model state can be reused
without a ChatGPT round-trip between each small action.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from phone_harness import windows
from phone_harness.runtime import PhoneRuntime
from phone_harness.visual_grid import describe_grid_cells, rank_grid_pairs


@dataclass(frozen=True)
class MergeBossLayout:
    rows: int = 9
    columns: int = 7
    reference_screen_size: tuple[int, int] = (1206, 2622)
    board_bounds: dict = field(default_factory=lambda: {
        "x": 35.0,
        "y": 910.0,
        "w": 1135.0,
        "h": 1485.0,
    })
    info_region: dict = field(default_factory=lambda: {
        "x": 20.0,
        "y": 690.0,
        "w": 1165.0,
        "h": 205.0,
    })
    inset: float = 0.20

    def scaled_to_screen(self, screen_size):
        width, height = (float(screen_size[0]), float(screen_size[1]))
        ref_width, ref_height = (
            float(self.reference_screen_size[0]),
            float(self.reference_screen_size[1]),
        )
        scale_x = width / ref_width
        scale_y = height / ref_height

        def rect(value):
            return {
                "x": float(value["x"]) * scale_x,
                "y": float(value["y"]) * scale_y,
                "w": float(value["w"]) * scale_x,
                "h": float(value["h"]) * scale_y,
            }

        return MergeBossLayout(
            rows=self.rows,
            columns=self.columns,
            reference_screen_size=(int(round(width)), int(round(height))),
            board_bounds=rect(self.board_bounds),
            info_region=rect(self.info_region),
            inset=self.inset,
        )


def _cell_index(cell_id: str, columns: int) -> int:
    row_text, column_text = cell_id.split("c", 1)
    return int(row_text[1:]) * columns + int(column_text)


def _normalized_panel(path):
    with Image.open(Path(path)) as source:
        return source.convert("L").resize((128, 32), Image.Resampling.LANCZOS)


def _panel_similarity_images(first, second) -> float:
    try:
        diff = ImageChops.difference(first, second)
        return 1.0 - ImageStat.Stat(diff).mean[0] / 255
    finally:
        diff.close()


def _panel_similarity(first_path, second_path) -> float:
    first = _normalized_panel(first_path)
    second = _normalized_panel(second_path)
    try:
        return _panel_similarity_images(first, second)
    finally:
        first.close()
        second.close()


def producer_badge_scores(image_path, *, layout: MergeBossLayout):
    """Return per-cell yellow/orange upper-right badge evidence.

    This is candidate evidence only. A false candidate is harmless in the fast
    loop because the workflow verifies that a producer tap actually changes an
    empty board cell before continuing.
    """
    with Image.open(Path(image_path)) as source:
        image = source.convert("RGB")
        x = layout.board_bounds["x"]
        y = layout.board_bounds["y"]
        width = layout.board_bounds["w"]
        height = layout.board_bounds["h"]
        cell_width = width / layout.columns
        cell_height = height / layout.rows
        scores = {}
        for row in range(layout.rows):
            for column in range(layout.columns):
                left = x + column * cell_width
                top = y + row * cell_height
                badge = image.crop((
                    left + cell_width * 0.58,
                    top + cell_height * 0.02,
                    left + cell_width * 0.99,
                    top + cell_height * 0.48,
                )).convert("HSV")
                get_pixels = getattr(badge, "get_flattened_data", badge.getdata)
                pixels = list(get_pixels())
                badge.close()
                if not pixels:
                    score = 0.0
                else:
                    yellow = sum(
                        1 for hue, saturation, value in pixels
                        if 8 <= hue <= 52 and saturation >= 115 and value >= 175
                    )
                    score = yellow / len(pixels)
                scores[f"r{row}c{column}"] = round(score, 6)
    return scores


class MergeBossFastWorkflow:
    def __init__(self, runtime=None, *, layout=None):
        self.runtime = runtime or PhoneRuntime()
        self.layout = layout or MergeBossLayout()
        self._live_layout = self.layout

    def _observe_board(self):
        observation = self.runtime.observe(
            force=True,
            mode="visual",
            image_profile="full",
        )
        with Image.open(Path(observation["_image_path"])) as source:
            self._live_layout = self.layout.scaled_to_screen(source.size)
        analysis_started = time.perf_counter()
        ranked = rank_grid_pairs(
            observation["_image_path"],
            rows=self._live_layout.rows,
            columns=self._live_layout.columns,
            bounds=self._live_layout.board_bounds,
            inset=self._live_layout.inset,
        )
        occupancy_cells = describe_grid_cells(
            observation["_image_path"],
            rows=self._live_layout.rows,
            columns=self._live_layout.columns,
            bounds=self._live_layout.board_bounds,
            # Neighboring producer glow spills far into otherwise empty cells.
            # A deeper center crop is a much better occupancy signal while the
            # wider crop remains useful for visual identity/similarity.
            inset=0.27,
        )["cells"]
        ranked["occupancy_scores"] = {
            cell["id"]: float(cell["content_score"])
            for cell in occupancy_cells
        }
        producer_scores = producer_badge_scores(
            observation["_image_path"], layout=self._live_layout
        )
        producers = {
            cell for cell, score in producer_scores.items() if score >= 0.025
        }
        return observation, ranked, producers, producer_scores, round(
            (time.perf_counter() - analysis_started) * 1000, 3
        )

    @staticmethod
    def _aliexpress_foreground():
        try:
            elements = windows.accessibility_elements()
        except RuntimeError:
            return False
        for item in elements:
            if item.get("role") != "XCUIElementTypeApplication":
                continue
            if item.get("text") == "AliExpress" or item.get("name") == "AliExpress":
                return True
        return False

    def _candidate_pairs(
        self,
        ranked,
        producers,
        *,
        content_threshold=6.0,
        empty_cells=None,
    ):
        strict = []
        relaxed = []
        explicit_empty_cells = None if empty_cells is None else set(empty_cells)
        by_cell = {
            cell.get("id"): cell
            for cell in ranked.get("cells", ())
            if isinstance(cell, dict) and cell.get("id")
        }
        occupancy_scores = ranked.get("occupancy_scores") or {
            cell_id: float(cell.get("content_score", float("inf")))
            for cell_id, cell in by_cell.items()
        }
        for pair in ranked["pairs"]:
            if any(cell in producers for cell in pair["cells"]):
                continue
            if explicit_empty_cells is not None and any(
                cell in explicit_empty_cells for cell in pair["cells"]
            ):
                continue
            # Empty board cells share the same pale tile/background and can
            # become extremely strong visual pairs. They are never mergeable.
            if explicit_empty_cells is None and occupancy_scores and any(
                float(occupancy_scores.get(cell, float("inf"))) < float(content_threshold)
                for cell in pair["cells"]
            ):
                continue
            if pair["color_similarity"] >= 0.95 and pair["edge_similarity"] >= 0.95:
                strict.append(pair)
            elif (
                pair["color_similarity"] >= 0.92
                and pair["edge_similarity"] >= 0.79
                and pair["score"] >= 0.90
            ):
                relaxed.append(pair)
        return strict, relaxed

    def _center(self, ranked, cell_id):
        return ranked["cells"][_cell_index(cell_id, self.layout.columns)]["center"]

    def _tap(self, observation_id, center):
        return self.runtime.act(
            [{"op": "tap", "x": center[0], "y": center[1]}],
            observation_id=observation_id,
        )

    def _drag(self, observation_id, first, second):
        return self.runtime.act(
            [{
                "op": "drag",
                "x1": first[0],
                "y1": first[1],
                "x2": second[0],
                "y2": second[1],
                "duration": 0.35,
            }],
            observation_id=observation_id,
        )

    def _confirm_pair_by_panel(self, observation, ranked, pair):
        first = self._center(ranked, pair["cells"][0])
        second = self._center(ranked, pair["cells"][1])
        first_tap = self._tap(observation["observation_id"], first)
        first_panel = self.runtime.observe(
            force=True,
            mode="visual",
            region=self._live_layout.info_region,
            image_profile="coarse",
        )
        # runtime.act() invalidates the current ScreenFrame and deletes its
        # derived temporary JPEGs. Decode the tiny 128x32 comparison image now,
        # before the second tap, rather than copying/re-capturing a file merely
        # to extend its lifetime. This keeps the fast path at two taps and two
        # small region captures while eliminating stale temp-path failures.
        first_panel_image = _normalized_panel(first_panel["_image_path"])
        try:
            second_tap = self._tap(first_panel["observation_id"], second)
            second_panel = self.runtime.observe(
                force=True,
                mode="visual",
                region=self._live_layout.info_region,
                image_profile="coarse",
            )
            second_panel_image = _normalized_panel(second_panel["_image_path"])
            try:
                similarity = _panel_similarity_images(first_panel_image, second_panel_image)
            finally:
                second_panel_image.close()
        finally:
            first_panel_image.close()
        return similarity >= 0.965, {
            "panel_similarity": round(similarity, 6),
            "tap_ms": round(
                first_tap["duration_ms"] + second_tap["duration_ms"], 3
            ),
            "panel_capture_ms": round(
                first_panel.get("duration_ms", 0) + second_panel.get("duration_ms", 0),
                3,
            ),
            "observation_id": second_panel["observation_id"],
            "centers": [first, second],
        }

    @staticmethod
    def _newly_filled_cells(before, after, producers):
        before_by_id = {cell["id"]: cell for cell in before["cells"]}
        filled = []
        for cell in after["cells"]:
            if cell["id"] in producers:
                continue
            previous = before_by_id[cell["id"]]
            if previous["content_score"] < 6.0 <= cell["content_score"]:
                filled.append(cell["id"])
        return filled

    def run_one_merge(self, *, max_producer_taps=4, max_relaxed_checks=3):
        """Run at most one merge and return detailed timings/evidence."""
        if max_producer_taps < 0 or max_producer_taps > 8:
            raise ValueError("max_producer_taps must be between 0 and 8")
        if max_relaxed_checks < 0 or max_relaxed_checks > 8:
            raise ValueError("max_relaxed_checks must be between 0 and 8")

        started = time.perf_counter()
        producer_taps = 0
        relaxed_checks = 0
        history = []
        state = None

        if not self._aliexpress_foreground():
            return {
                "merged": False,
                "reason": "aliexpress_not_foreground",
                "producer_taps": 0,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "history": history,
            }

        while True:
            if hasattr(self.runtime, "pause_checkpoint"):
                pause = self.runtime.pause_checkpoint(boundary="merge_boss_once.cycle")
                if pause["replan_required"]:
                    state = None
            elif hasattr(self.runtime, "wait_if_paused"):
                waited = self.runtime.wait_if_paused(boundary="merge_boss_once.cycle")
                if waited > 0:
                    state = None
            if state is None:
                state = self._observe_board()
            observation, ranked, producers, producer_scores, analysis_ms = state
            state = None
            if len(producers) < 2:
                return {
                    "merged": False,
                    "reason": "merge_boss_board_not_recognized",
                    "producer_taps": producer_taps,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "history": history,
                }
            strict, relaxed = self._candidate_pairs(ranked, producers)
            history.append({
                "capture_ms": observation.get("capture_ms"),
                "observe_ms": observation.get("duration_ms"),
                "grid_analysis_ms": analysis_ms,
                "producer_count": len(producers),
                "strict_candidates": len(strict),
                "relaxed_candidates": len(relaxed),
            })

            if strict:
                pair = strict[0]
                centers = [self._center(ranked, cell) for cell in pair["cells"]]
                action = self._drag(observation["observation_id"], *centers)
                verify = self.runtime.observe(force=True, mode="visual", image_profile="glance")
                return {
                    "merged": True,
                    "pair": pair,
                    "confirmation": "strict_visual",
                    "producer_taps": producer_taps,
                    "drag_ms": action["duration_ms"],
                    "verify_ms": verify["duration_ms"],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "history": history,
                }

            confirmed = None
            for pair in relaxed[: max(0, max_relaxed_checks - relaxed_checks)]:
                relaxed_checks += 1
                ok, evidence = self._confirm_pair_by_panel(observation, ranked, pair)
                if ok:
                    confirmed = (pair, evidence)
                    break
                # Selection changes the frame, so restart candidate generation.
                break
            if confirmed is not None:
                pair, evidence = confirmed
                action = self._drag(
                    evidence["observation_id"], *evidence["centers"]
                )
                verify = self.runtime.observe(force=True, mode="visual", image_profile="glance")
                return {
                    "merged": True,
                    "pair": pair,
                    "confirmation": "visual_panel",
                    "panel_similarity": evidence["panel_similarity"],
                    "producer_taps": producer_taps,
                    "drag_ms": action["duration_ms"],
                    "verify_ms": verify["duration_ms"],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "history": history,
                }
            if relaxed and relaxed_checks < max_relaxed_checks:
                continue

            if producer_taps >= max_producer_taps or not producers:
                return {
                    "merged": False,
                    "reason": "no_confirmed_pair_or_producer_budget_exhausted",
                    "producer_taps": producer_taps,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "history": history,
                }

            # Prefer the strongest producer-badge candidate. A first tap may only
            # select it; the loop confirms emission by looking for a newly-filled
            # non-producer cell and may tap the same producer once more.
            producer = max(producers, key=lambda cell: producer_scores[cell])
            center = self._center(ranked, producer)
            before = ranked
            for _ in range(min(2, max_producer_taps - producer_taps)):
                self._tap(observation["observation_id"], center)
                producer_taps += 1
                state = self._observe_board()
                observation, after, producers, producer_scores, analysis_ms = state
                if self._newly_filled_cells(before, after, producers):
                    break
                before = after
            # Re-enter the main loop using the already-captured post-tap state.

