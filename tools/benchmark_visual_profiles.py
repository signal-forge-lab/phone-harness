"""Offline visual-quality benchmark for saved phone screenshots."""

from __future__ import annotations

import argparse
import json

from phone_harness.frame import ScreenFrame
from phone_harness.visual_quality import (
    benchmark_grid_candidate_recall,
    benchmark_grid_identity,
    benchmark_visual_profiles,
)


def _region(value):
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("region must be x,y,w,h")
    return dict(zip(("x", "y", "w", "h"), parts))


def _grid(value):
    parts = value.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("grid must be ROWSxCOLUMNS")
    return int(parts[0]), int(parts[1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--region", type=_region)
    parser.add_argument("--grid", type=_grid)
    parser.add_argument("--inset", type=float, default=0.20)
    parser.add_argument("--exact-threshold", type=float, default=0.95)
    args = parser.parse_args()

    frame = ScreenFrame.from_path(args.image)
    try:
        output = {
            "visual_profiles": benchmark_visual_profiles(frame, region=args.region),
        }
        if args.grid is not None:
            if args.region is None:
                parser.error("--grid requires --region")
            rows, columns = args.grid
            output["grid_identity"] = benchmark_grid_identity(
                frame,
                rows=rows,
                columns=columns,
                bounds=args.region,
                inset=args.inset,
                exact_threshold=args.exact_threshold,
            )
            output["grid_candidate_recall"] = benchmark_grid_candidate_recall(
                frame,
                rows=rows,
                columns=columns,
                bounds=args.region,
                inset=args.inset,
                exact_threshold=args.exact_threshold,
            )
        print(json.dumps(output, indent=2, ensure_ascii=False))
    finally:
        frame.close()


if __name__ == "__main__":
    main()
