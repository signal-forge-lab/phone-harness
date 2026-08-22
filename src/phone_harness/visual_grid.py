"""Small, dependency-free visual grid comparison helpers.

The caller supplies the board bounds and grid shape.  This module deliberately
does not try to understand game semantics; it only turns each cell into a
stable center coordinate plus a strict visual signature/match candidate.
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat

from phone_harness.visual_descriptor import descriptor_from_image, sprite_descriptor_from_image


_NORMALIZED_SIZE = (32, 32)
_COARSE_SIZE = (8, 8)


def _normalized_crop(image, box, inset):
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    dx = width * inset
    dy = height * inset
    crop = image.crop((left + dx, top + dy, right - dx, bottom - dy)).convert("RGB")
    return crop.resize(_NORMALIZED_SIZE, Image.Resampling.LANCZOS)


def _similarity(first, second, first_edges, second_edges):
    diff = ImageChops.difference(first, second)
    color = 1.0 - sum(ImageStat.Stat(diff).mean) / (3 * 255)
    edge_diff = ImageChops.difference(first_edges, second_edges)
    edge = 1.0 - ImageStat.Stat(edge_diff).mean[0] / 255
    return color, edge


def _coarse_payload(image):
    small = image.resize(_COARSE_SIZE, Image.Resampling.LANCZOS)
    return bytes((value // 16) * 16 for value in small.tobytes())


def _coarse_color_similarity(first, second):
    if len(first) != len(second):
        raise ValueError("coarse visual payload sizes must match")
    if not first:
        return 0.0
    return 1.0 - sum(abs(a - b) for a, b in zip(first, second)) / (len(first) * 255)


def _signature(image):
    # Quantization removes tiny capture noise while preserving sprite identity.
    quantized = bytes((value // 8) * 8 for value in image.tobytes())
    return hashlib.sha256(quantized).hexdigest()[:16]


def _perceptual_hash(image):
    small = image.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
    values = list(small.tobytes())
    mean = sum(values) / len(values)
    bits = 0
    for value in values:
        bits = (bits << 1) | int(value >= mean)
    return f"{bits:016x}"


def _prepare_grid(image_path, *, rows, columns, bounds, inset):
    if not isinstance(rows, int) or rows < 1 or not isinstance(columns, int) or columns < 1:
        raise ValueError("rows and columns must be positive integers")
    if rows * columns > 400:
        raise ValueError("visual grid may contain at most 400 cells")
    if not 0 <= inset < 0.5:
        raise ValueError("inset must be between 0 and 0.5")

    with Image.open(Path(image_path)) as source:
        image = source.convert("RGB")
        x = float(bounds["x"])
        y = float(bounds["y"])
        width = float(bounds["w"])
        height = float(bounds["h"])
        if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > image.width or y + height > image.height:
            raise ValueError("grid bounds must fit inside the screenshot")

        cell_width = width / columns
        cell_height = height / rows
        cells = []
        normalized = []
        edges = []
        for row in range(rows):
            for column in range(columns):
                left = x + column * cell_width
                top = y + row * cell_height
                right = left + cell_width
                bottom = top + cell_height
                crop = _normalized_crop(image, (left, top, right, bottom), inset)
                stats = ImageStat.Stat(crop)
                cells.append({
                    "id": f"r{row}c{column}",
                    "row": row,
                    "column": column,
                    "center": [round(left + cell_width / 2, 3), round(top + cell_height / 2, 3)],
                    "visual_signature": _signature(crop),
                    "perceptual_hash": _perceptual_hash(crop),
                    "content_score": round(sum(stats.stddev) / 3, 3),
                    "mean_rgb": [round(float(value), 3) for value in stats.mean[:3]],
                })
                normalized.append(crop)
                edges.append(crop.convert("L").filter(ImageFilter.FIND_EDGES))

    return {"x": x, "y": y, "w": width, "h": height}, cells, normalized, edges


def analyze_grid(
    image_path,
    *,
    rows,
    columns,
    bounds,
    inset=0.14,
    exact_threshold=0.995,
    min_content_score=6.0,
):
    """Analyze a regular visual grid and return strict same-looking groups.

    ``exact_groups`` are deliberately conservative: both color and edge
    similarity must exceed ``exact_threshold``.  They are visual candidates,
    not a claim that two objects have the same game semantics or level.
    """
    if not 0 < exact_threshold <= 1:
        raise ValueError("exact_threshold must be in (0, 1]")
    normalized_bounds, cells, normalized, edges = _prepare_grid(
        image_path,
        rows=rows,
        columns=columns,
        bounds=bounds,
        inset=inset,
    )

    adjacency = [set() for _ in cells]
    exact_pairs = []
    for first in range(len(cells)):
        if cells[first]["content_score"] < min_content_score:
            continue
        for second in range(first + 1, len(cells)):
            if cells[second]["content_score"] < min_content_score:
                continue
            color, edge = _similarity(normalized[first], normalized[second], edges[first], edges[second])
            if color >= exact_threshold and edge >= exact_threshold:
                adjacency[first].add(second)
                adjacency[second].add(first)
                exact_pairs.append({
                    "cells": [cells[first]["id"], cells[second]["id"]],
                    "color_similarity": round(color, 6),
                    "edge_similarity": round(edge, 6),
                })

    exact_groups = []
    visited = set()
    for start in range(len(cells)):
        if start in visited or not adjacency[start]:
            continue
        stack = [start]
        component = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current])
        visited.update(component)
        if all(second in adjacency[first] for first in component for second in component if first != second):
            exact_groups.append([cells[index]["id"] for index in sorted(component)])

    return {
        "rows": rows,
        "columns": columns,
        "bounds": normalized_bounds,
        "cells": cells,
        "exact_groups": exact_groups,
        "exact_pairs": exact_pairs,
        "exact_threshold": exact_threshold,
    }


def rank_grid_pairs(
    image_path,
    *,
    rows,
    columns,
    bounds,
    inset=0.14,
    min_content_score=6.0,
):
    """Rank non-empty cell pairs by visual similarity without declaring identity.

    This is intentionally a candidate API. Animated sprites can have lower edge
    similarity even when they are the same semantic item, so callers may use the
    individual color/edge scores to choose a bounded confirmation strategy.
    """
    normalized_bounds, cells, normalized, edges = _prepare_grid(
        image_path,
        rows=rows,
        columns=columns,
        bounds=bounds,
        inset=inset,
    )
    pairs = []
    for first in range(len(cells)):
        if cells[first]["content_score"] < min_content_score:
            continue
        for second in range(first + 1, len(cells)):
            if cells[second]["content_score"] < min_content_score:
                continue
            color, edge = _similarity(
                normalized[first], normalized[second], edges[first], edges[second]
            )
            score = color * 0.75 + edge * 0.25
            pairs.append({
                "cells": [cells[first]["id"], cells[second]["id"]],
                "score": round(score, 6),
                "color_similarity": round(color, 6),
                "edge_similarity": round(edge, 6),
            })
    pairs.sort(
        key=lambda item: (
            item["score"], item["color_similarity"], item["edge_similarity"]
        ),
        reverse=True,
    )
    return {
        "rows": rows,
        "columns": columns,
        "bounds": normalized_bounds,
        "cells": cells,
        "pairs": pairs,
    }


def rank_grid_pairs_coarse_to_fine(
    image_path,
    *,
    rows,
    columns,
    bounds,
    inset=0.14,
    min_content_score=6.0,
    candidate_neighbors=4,
):
    """Rank grid pairs with cheap 8x8 shortlist and 32x32 confirmation.

    The coarse stage is candidate generation only. Returned color/edge scores
    always come from the same 32x32 comparison used by ``rank_grid_pairs``.
    """
    if not isinstance(candidate_neighbors, int) or candidate_neighbors < 1:
        raise ValueError("candidate_neighbors must be a positive integer")
    normalized_bounds, cells, normalized, edges = _prepare_grid(
        image_path,
        rows=rows,
        columns=columns,
        bounds=bounds,
        inset=inset,
    )
    try:
        coarse = [_coarse_payload(image) for image in normalized]
        active = [
            index for index, cell in enumerate(cells)
            if cell["content_score"] >= min_content_score
        ]
        candidate_pairs = set()
        for first in active:
            ranked = sorted(
                (
                    (_coarse_color_similarity(coarse[first], coarse[second]), second)
                    for second in active if second != first
                ),
                reverse=True,
            )
            for _score, second in ranked[:candidate_neighbors]:
                candidate_pairs.add(tuple(sorted((first, second))))

        pairs = []
        for first, second in candidate_pairs:
            color, edge = _similarity(
                normalized[first], normalized[second], edges[first], edges[second]
            )
            pairs.append({
                "cells": [cells[first]["id"], cells[second]["id"]],
                "score": round(color * 0.75 + edge * 0.25, 6),
                "color_similarity": round(color, 6),
                "edge_similarity": round(edge, 6),
            })
        pairs.sort(
            key=lambda item: (
                item["score"], item["color_similarity"], item["edge_similarity"]
            ),
            reverse=True,
        )
        full_pair_count = len(active) * (len(active) - 1) // 2
        return {
            "rows": rows,
            "columns": columns,
            "bounds": normalized_bounds,
            "cells": cells,
            "pairs": pairs,
            "candidate_pair_count": len(candidate_pairs),
            "full_pair_count": full_pair_count,
            "detail_comparison_fraction": (
                round(len(candidate_pairs) / full_pair_count, 6)
                if full_pair_count else 0.0
            ),
        }
    finally:
        for image in normalized:
            image.close()
        for edge in edges:
            edge.close()


def select_non_overlapping_pairs(pairs, *, excluded_cells=(), limit=None):
    """Greedily keep ranked pairs that do not reuse a cell.

    This helper only understands opaque cell ids. The caller owns any meaning
    attached to the cells or pair scores.
    """
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
    ):
        raise ValueError("limit must be a non-negative integer or None")
    excluded = set(excluded_cells)
    used = set(excluded)
    selected = []
    for pair in pairs:
        cells = tuple(pair["cells"])
        if len(cells) != 2:
            raise ValueError("pairs must contain exactly two cell ids")
        if any(cell in used for cell in cells):
            continue
        selected.append(pair)
        used.update(cells)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def describe_grid_cells(
    image_path,
    *,
    rows,
    columns,
    bounds,
    inset=0.14,
):
    """Return compact per-cell visual descriptors from one screenshot decode."""
    normalized_bounds, cells, normalized, _edges = _prepare_grid(
        image_path,
        rows=rows,
        columns=columns,
        bounds=bounds,
        inset=inset,
    )
    try:
        described = [
            {
                **cell,
                "visual_descriptor": descriptor_from_image(image),
                "sprite_descriptor": sprite_descriptor_from_image(image),
            }
            for cell, image in zip(cells, normalized)
        ]
    finally:
        for image in normalized:
            image.close()
        for edge in _edges:
            edge.close()
    return {
        "rows": rows,
        "columns": columns,
        "bounds": normalized_bounds,
        "cells": described,
    }


def analyze_grid_coarse_to_fine(
    detail_path,
    coarse_path,
    *,
    rows,
    columns,
    detail_bounds,
    coarse_bounds,
    inset=0.14,
    exact_threshold=0.995,
    candidate_neighbors=2,
    min_content_score=6.0,
):
    """Shortlist visually-near cells on a coarse image, confirm on detail.

    The coarse image is allowed to be heavily resized/compressed. Its only job
    is high-recall candidate generation: for every cell, retain the closest
    ``candidate_neighbors`` cells. All final same-looking decisions use the
    detail image and ``exact_threshold``. This keeps compression artifacts out
    of the final identity decision while avoiding an O(n^2) detail comparison.
    """
    if not isinstance(candidate_neighbors, int) or candidate_neighbors < 1:
        raise ValueError("candidate_neighbors must be a positive integer")
    if not 0 < exact_threshold <= 1:
        raise ValueError("exact_threshold must be in (0, 1]")

    _, coarse_cells, coarse_images, coarse_edges = _prepare_grid(
        coarse_path,
        rows=rows,
        columns=columns,
        bounds=coarse_bounds,
        inset=inset,
    )
    normalized_bounds, detail_cells, detail_images, detail_edges = _prepare_grid(
        detail_path,
        rows=rows,
        columns=columns,
        bounds=detail_bounds,
        inset=inset,
    )

    candidate_pairs = set()
    for first in range(len(coarse_cells)):
        ranked = []
        for second in range(len(coarse_cells)):
            if first == second:
                continue
            color, edge = _similarity(
                coarse_images[first],
                coarse_images[second],
                coarse_edges[first],
                coarse_edges[second],
            )
            ranked.append((min(color, edge), second))
        for _, second in sorted(ranked, reverse=True)[:candidate_neighbors]:
            candidate_pairs.add(tuple(sorted((first, second))))

    adjacency = [set() for _ in detail_cells]
    exact_pairs = []
    detail_comparisons = 0
    for first, second in sorted(candidate_pairs):
        if (
            detail_cells[first]["content_score"] < min_content_score
            or detail_cells[second]["content_score"] < min_content_score
        ):
            continue
        detail_comparisons += 1
        color, edge = _similarity(
            detail_images[first],
            detail_images[second],
            detail_edges[first],
            detail_edges[second],
        )
        if color < exact_threshold or edge < exact_threshold:
            continue
        adjacency[first].add(second)
        adjacency[second].add(first)
        exact_pairs.append({
            "cells": [detail_cells[first]["id"], detail_cells[second]["id"]],
            "color_similarity": round(color, 6),
            "edge_similarity": round(edge, 6),
        })

    exact_groups = []
    visited = set()
    for start in range(len(detail_cells)):
        if start in visited or not adjacency[start]:
            continue
        stack = [start]
        component = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current])
        visited.update(component)
        if all(second in adjacency[first] for first in component for second in component if first != second):
            exact_groups.append([detail_cells[index]["id"] for index in sorted(component)])

    full_pair_count = len(detail_cells) * (len(detail_cells) - 1) // 2
    return {
        "rows": rows,
        "columns": columns,
        "bounds": normalized_bounds,
        "cells": detail_cells,
        "exact_groups": exact_groups,
        "exact_pairs": exact_pairs,
        "exact_threshold": exact_threshold,
        "candidate_neighbors": candidate_neighbors,
        "candidate_pair_count": len(candidate_pairs),
        "detail_comparison_count": detail_comparisons,
        "full_pair_count": full_pair_count,
        "detail_comparison_fraction": round(detail_comparisons / full_pair_count, 6)
        if full_pair_count else 0.0,
    }


def compare_grid_frames(
    before_path,
    after_path,
    *,
    rows,
    columns,
    bounds,
    inset=0.14,
    stable_threshold=0.995,
):
    """Return cell-level visual changes without inferring action success."""
    if not 0 < stable_threshold <= 1:
        raise ValueError("stable_threshold must be in (0, 1]")
    normalized_bounds, before_cells, before_images, before_edges = _prepare_grid(
        before_path, rows=rows, columns=columns, bounds=bounds, inset=inset
    )
    _, after_cells, after_images, after_edges = _prepare_grid(
        after_path, rows=rows, columns=columns, bounds=bounds, inset=inset
    )

    cells = []
    changed_cells = []
    unchanged_cells = []
    for index, before_cell in enumerate(before_cells):
        color, edge = _similarity(
            before_images[index], after_images[index], before_edges[index], after_edges[index]
        )
        changed = color < stable_threshold or edge < stable_threshold
        cell_id = before_cell["id"]
        cells.append({
            "id": cell_id,
            "row": before_cell["row"],
            "column": before_cell["column"],
            "center": before_cell["center"],
            "before_signature": before_cell["visual_signature"],
            "after_signature": after_cells[index]["visual_signature"],
            "color_similarity": round(color, 6),
            "edge_similarity": round(edge, 6),
            "changed": changed,
        })
        (changed_cells if changed else unchanged_cells).append(cell_id)

    return {
        "rows": rows,
        "columns": columns,
        "bounds": normalized_bounds,
        "cells": cells,
        "changed_cells": changed_cells,
        "unchanged_cells": unchanged_cells,
        "stable_threshold": stable_threshold,
    }


def compare_grid_temporal(
    before_paths,
    after_paths,
    *,
    rows,
    columns,
    bounds,
    inset=0.14,
    material_margin=0.04,
):
    """Compare post-action cells against their pre-action ambient variation.

    The baseline requires at least two pre-action frames. For each cell, the
    lowest pairwise similarity inside that baseline is treated as the cell's
    observed ambient variation floor. A post-action cell is a material change
    only when even its best match to any baseline frame falls below that floor
    by ``material_margin``. This keeps existing animation/bling from being
    confused with an action-induced state transition.

    This function only reports visual evidence; callers still own app/game
    semantics and success criteria.
    """
    before_paths = list(before_paths)
    after_paths = list(after_paths)
    if len(before_paths) < 2:
        raise ValueError("temporal comparison requires at least two baseline frames")
    if not after_paths:
        raise ValueError("temporal comparison requires at least one post-action frame")
    if not 0 <= material_margin < 1:
        raise ValueError("material_margin must be in [0, 1)")

    prepared_before = [
        _prepare_grid(path, rows=rows, columns=columns, bounds=bounds, inset=inset)
        for path in before_paths
    ]
    prepared_after = [
        _prepare_grid(path, rows=rows, columns=columns, bounds=bounds, inset=inset)
        for path in after_paths
    ]

    normalized_bounds = prepared_before[0][0]
    reference_cells = prepared_before[0][1]
    cells = []
    material_changed_cells = []
    ambient_only_cells = []

    for index, reference in enumerate(reference_cells):
        baseline_scores = []
        for first, second in itertools.combinations(prepared_before, 2):
            color, edge = _similarity(
                first[2][index], second[2][index], first[3][index], second[3][index]
            )
            baseline_scores.append(min(color, edge))
        ambient_floor = min(baseline_scores)

        post_scores = []
        for before in prepared_before:
            for after in prepared_after:
                color, edge = _similarity(
                    before[2][index], after[2][index], before[3][index], after[3][index]
                )
                post_scores.append(min(color, edge))
        post_best = max(post_scores)
        material_threshold = max(0.0, ambient_floor - material_margin)
        changed = post_best < material_threshold
        cell_id = reference["id"]
        cells.append({
            "id": cell_id,
            "row": reference["row"],
            "column": reference["column"],
            "center": reference["center"],
            "ambient_floor_similarity": round(ambient_floor, 6),
            "post_best_similarity": round(post_best, 6),
            "material_threshold": round(material_threshold, 6),
            "material_changed": changed,
        })
        (material_changed_cells if changed else ambient_only_cells).append(cell_id)

    return {
        "rows": rows,
        "columns": columns,
        "bounds": normalized_bounds,
        "baseline_frame_count": len(before_paths),
        "post_frame_count": len(after_paths),
        "material_margin": material_margin,
        "cells": cells,
        "material_changed_cells": material_changed_cells,
        "ambient_only_cells": ambient_only_cells,
    }
