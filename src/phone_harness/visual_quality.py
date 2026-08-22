"""Candidate coarse-vision profiles and offline preservation benchmarks.

These profiles are intentionally *not* runtime acceptance thresholds.  They are
candidate encodings that make it cheap to discover how little visual detail a
task actually needs before choosing a production default from real evidence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageFilter, ImageStat

from .frame import ScreenFrame
from .visual_grid import _prepare_grid, _similarity as _grid_similarity, analyze_grid


@dataclass(frozen=True)
class VisualProfile:
    name: str
    max_long_edge: int
    image_format: str
    quality: int
    grayscale: bool = False


CANDIDATE_VISUAL_PROFILES = (
    VisualProfile("glance", 256, "JPEG", 28),
    VisualProfile("coarse", 384, "JPEG", 35),
    VisualProfile("balanced", 640, "JPEG", 45),
    VisualProfile("detail", 960, "JPEG", 60),
)

VISUAL_PROFILE_BY_NAME = {
    profile.name: profile for profile in CANDIDATE_VISUAL_PROFILES
}


def _normalized_structure(path, *, edges=False):
    with Image.open(path) as source:
        image = source.convert("L").resize((64, 64), Image.Resampling.LANCZOS)
    return image.filter(ImageFilter.FIND_EDGES) if edges else image


def _similarity(first, second):
    diff = ImageChops.difference(first, second)
    return 1.0 - ImageStat.Stat(diff).mean[0] / 255


def benchmark_visual_profiles(frame: ScreenFrame, *, region=None, profiles=None):
    """Measure byte cost and coarse structure preservation for each profile."""
    profiles = tuple(profiles or CANDIDATE_VISUAL_PROFILES)
    reference = frame.variant(region=region, image_format="PNG")
    reference_bytes = reference.stat().st_size
    reference_low = _normalized_structure(reference)
    reference_edges = _normalized_structure(reference, edges=True)

    results = []
    for profile in profiles:
        started = time.perf_counter()
        candidate = frame.variant(
            region=region,
            max_long_edge=profile.max_long_edge,
            grayscale=profile.grayscale,
            image_format=profile.image_format,
            quality=profile.quality,
        )
        generation_ms = round((time.perf_counter() - started) * 1000, 3)
        with Image.open(candidate) as image:
            width, height = image.size
        candidate_low = _normalized_structure(candidate)
        candidate_edges = _normalized_structure(candidate, edges=True)
        candidate_bytes = candidate.stat().st_size
        results.append({
            "profile": profile.name,
            "max_long_edge": profile.max_long_edge,
            "format": profile.image_format.lower(),
            "quality": profile.quality,
            "width": width,
            "height": height,
            "bytes": candidate_bytes,
            "byte_ratio": round(candidate_bytes / reference_bytes, 6) if reference_bytes else 0.0,
            "generation_ms": generation_ms,
            "low_frequency_similarity": round(_similarity(reference_low, candidate_low), 6),
            "edge_similarity": round(_similarity(reference_edges, candidate_edges), 6),
            "path": str(candidate),
        })
    return {
        "reference_bytes": reference_bytes,
        "reference_path": str(reference),
        "profiles": results,
    }


def benchmark_grid_identity(
    frame: ScreenFrame,
    *,
    rows,
    columns,
    bounds,
    inset=0.20,
    exact_threshold=0.95,
    profiles=None,
):
    """Check whether same-looking grid groups survive coarse encodings.

    The full-resolution crop defines the reference groups.  Each candidate
    profile is analyzed with the same grid/inset/threshold in its own local
    pixel coordinate system.  This measures preservation only; it does not make
    game/app semantic claims.
    """
    profiles = tuple(profiles or CANDIDATE_VISUAL_PROFILES)
    reference_path = frame.variant(region=bounds, image_format="PNG")
    with Image.open(reference_path) as reference_image:
        reference_bounds = {
            "x": 0,
            "y": 0,
            "w": reference_image.width,
            "h": reference_image.height,
        }
    reference = analyze_grid(
        reference_path,
        rows=rows,
        columns=columns,
        bounds=reference_bounds,
        inset=inset,
        exact_threshold=exact_threshold,
    )
    expected_groups = tuple(tuple(group) for group in reference["exact_groups"])

    results = []
    for profile in profiles:
        candidate_path = frame.variant(
            region=bounds,
            max_long_edge=profile.max_long_edge,
            grayscale=profile.grayscale,
            image_format=profile.image_format,
            quality=profile.quality,
        )
        with Image.open(candidate_path) as image:
            local_bounds = {"x": 0, "y": 0, "w": image.width, "h": image.height}
        candidate = analyze_grid(
            candidate_path,
            rows=rows,
            columns=columns,
            bounds=local_bounds,
            inset=inset,
            exact_threshold=exact_threshold,
        )
        groups = tuple(tuple(group) for group in candidate["exact_groups"])
        results.append({
            "profile": profile.name,
            "groups_preserved": groups == expected_groups,
            "group_count": len(groups),
            "groups": [list(group) for group in groups],
        })
    return {
        "reference_groups": [list(group) for group in expected_groups],
        "profiles": results,
    }


def benchmark_grid_candidate_recall(
    frame: ScreenFrame,
    *,
    rows,
    columns,
    bounds,
    inset=0.20,
    exact_threshold=0.95,
    neighbor_counts=(1, 2, 3),
    min_content_score=6.0,
    profiles=None,
):
    """Measure whether coarse per-cell nearest-neighbor search keeps true pairs.

    Full-resolution exact pairs form the reference set.  A coarse profile only
    needs to shortlist those pairs; final confirmation can still use the full
    frame's tiny normalized cell crops.  This is the intended coarse-to-fine
    contract: optimize recall first, not coarse-stage precision.
    """
    profiles = tuple(profiles or CANDIDATE_VISUAL_PROFILES)
    counts = tuple(sorted(set(int(value) for value in neighbor_counts)))
    if not counts or counts[0] < 1:
        raise ValueError("neighbor_counts must contain positive integers")

    reference_path = frame.variant(region=bounds, image_format="PNG")
    with Image.open(reference_path) as reference_image:
        reference_bounds = {
            "x": 0,
            "y": 0,
            "w": reference_image.width,
            "h": reference_image.height,
        }
    reference = analyze_grid(
        reference_path,
        rows=rows,
        columns=columns,
        bounds=reference_bounds,
        inset=inset,
        exact_threshold=exact_threshold,
        min_content_score=min_content_score,
    )
    reference_pairs = {
        tuple(sorted(pair["cells"])) for pair in reference["exact_pairs"]
    }

    profile_results = []
    for profile in profiles:
        candidate_path = frame.variant(
            region=bounds,
            max_long_edge=profile.max_long_edge,
            grayscale=profile.grayscale,
            image_format=profile.image_format,
            quality=profile.quality,
        )
        with Image.open(candidate_path) as image:
            local_bounds = {"x": 0, "y": 0, "w": image.width, "h": image.height}
        _, cells, normalized, edges = _prepare_grid(
            candidate_path,
            rows=rows,
            columns=columns,
            bounds=local_bounds,
            inset=inset,
        )
        valid = [
            index for index, cell in enumerate(cells)
            if cell["content_score"] >= min_content_score
        ]
        rankings = {}
        for first in valid:
            ranked = []
            for second in valid:
                if first == second:
                    continue
                color, edge = _grid_similarity(
                    normalized[first], normalized[second], edges[first], edges[second]
                )
                ranked.append((min(color, edge), second))
            rankings[first] = sorted(ranked, reverse=True)

        total_possible = len(valid) * (len(valid) - 1) // 2
        by_neighbors = []
        for count in counts:
            candidate_pairs = set()
            for first in valid:
                for _, second in rankings[first][:count]:
                    candidate_pairs.add(tuple(sorted((cells[first]["id"], cells[second]["id"]))))
            hits = reference_pairs & candidate_pairs
            recall = 1.0 if not reference_pairs else len(hits) / len(reference_pairs)
            by_neighbors.append({
                "neighbors": count,
                "reference_pair_recall": round(recall, 6),
                "reference_pairs_found": len(hits),
                "reference_pair_count": len(reference_pairs),
                "candidate_pair_count": len(candidate_pairs),
                "candidate_fraction": round(candidate_pairs.__len__() / total_possible, 6)
                if total_possible else 0.0,
            })
        profile_results.append({
            "profile": profile.name,
            "valid_cell_count": len(valid),
            "neighbor_results": by_neighbors,
        })

    return {
        "reference_pairs": [list(pair) for pair in sorted(reference_pairs)],
        "profiles": profile_results,
    }
