"""Small, dependency-free visual grid comparison helpers.

The caller supplies the board bounds and grid shape.  This module deliberately
does not try to understand game semantics; it only turns each cell into a
stable center coordinate plus a strict visual signature/match candidate.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat


_NORMALIZED_SIZE = (32, 32)


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
                cells.append({
                    "id": f"r{row}c{column}",
                    "row": row,
                    "column": column,
                    "center": [round(left + cell_width / 2, 3), round(top + cell_height / 2, 3)],
                    "visual_signature": _signature(crop),
                    "perceptual_hash": _perceptual_hash(crop),
                    "content_score": round(sum(ImageStat.Stat(crop).stddev) / 3, 3),
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
