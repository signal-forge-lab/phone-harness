"""Lazy screen-frame sharing and low-cost visual derivatives.

One :class:`ScreenFrame` represents one physical screenshot.  OCR, region
inspection, coarse visual reasoning and optional MCP image output can derive
their own images from that frame without asking the device for another capture.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from PIL import Image


CaptureFn = Callable[..., tuple[str, dict]]


class CaptureSource(Protocol):
    """Pluggable source for one current phone frame.

    The current implementation is a one-shot CoreDevice PNG screenshot.  A
    future persistent DisplayService stream can implement the same tiny seam
    and return its latest decoded frame without changing OCR/visual consumers.
    """

    name: str

    def capture(self) -> "ScreenFrame | tuple[str, dict]": ...


class CallableCaptureSource:
    def __init__(self, capture: CaptureFn, *, name: str = "still-png"):
        self._capture = capture
        self.name = name

    def capture(self) -> tuple[str, dict]:
        return self._capture()


class BgraCaptureSource:
    """Adapter for a future persistent decoded-frame provider.

    ``latest_frame`` returns ``(bgra, width, height, window)``.  The raw pixels
    stay in memory; no full-resolution PNG is created unless a downstream
    caller explicitly requests one.
    """

    def __init__(self, latest_frame, *, name: str = "decoded-stream"):
        self._latest_frame = latest_frame
        self.name = name

    def capture(self) -> "ScreenFrame":
        bgra, width, height, window = self._latest_frame()
        return ScreenFrame.from_bgra(bgra, width=width, height=height, window=window)


def _region_key(region):
    if region is None:
        return None
    return tuple(float(region[key]) for key in ("x", "y", "w", "h"))


@dataclass
class ScreenFrame:
    """One captured screen plus lazily-created reusable derivatives."""

    path: Path | None
    window: dict
    width: int
    height: int
    capture_ms: float
    captured_at: float = field(default_factory=time.monotonic)
    _source_image: Image.Image | None = field(default=None, init=False, repr=False)
    _derived: dict[tuple, Path] = field(default_factory=dict, init=False, repr=False)
    _owned: set[Path] = field(default_factory=set, init=False, repr=False)

    @classmethod
    def from_path(cls, path, *, window=None):
        source = Path(path)
        with Image.open(source) as image:
            width, height = image.size
        resolved_window = dict(window) if window is not None else {
            "x": 0,
            "y": 0,
            "w": width,
            "h": height,
        }
        return cls(
            path=source,
            window=resolved_window,
            width=width,
            height=height,
            capture_ms=0.0,
        )

    @classmethod
    def from_bgra(cls, bgra, *, width, height, window=None):
        width = int(width)
        height = int(height)
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")
        raw = bytes(bgra)
        expected = width * height * 4
        if len(raw) != expected:
            raise ValueError(f"BGRA frame must contain exactly {expected} bytes")
        image = Image.frombytes("RGBA", (width, height), raw, "raw", "BGRA").convert("RGB")
        resolved_window = dict(window) if window is not None else {
            "x": 0,
            "y": 0,
            "w": width,
            "h": height,
        }
        frame = cls(
            path=None,
            window=resolved_window,
            width=width,
            height=height,
            capture_ms=0.0,
        )
        frame._source_image = image
        return frame

    def variant(
        self,
        *,
        region=None,
        max_long_edge: int | None = None,
        grayscale: bool = False,
        image_format: str = "PNG",
        quality: int = 40,
    ) -> Path:
        """Return a cached crop/resize/encoding derived from this frame.

        ``region`` is expressed in absolute phone-screen coordinates.  A JPEG
        variant is intended for coarse visual processing, while PNG remains the
        lossless choice for OCR and public screenshot output.
        """
        if max_long_edge is not None and max_long_edge <= 0:
            raise ValueError("max_long_edge must be positive")
        normalized_format = image_format.upper()
        if normalized_format not in {"PNG", "JPEG"}:
            raise ValueError("image_format must be PNG or JPEG")
        if not 1 <= int(quality) <= 95:
            raise ValueError("quality must be between 1 and 95")
        if (
            region is None
            and max_long_edge is None
            and not grayscale
            and normalized_format == "PNG"
            and self.path is not None
            and self.path.suffix.lower() == ".png"
        ):
            # The still-screen backend already produced the exact lossless PNG
            # requested by the caller. Avoid a pointless Pillow decode/encode
            # round-trip when the original frame can be shared directly.
            return self.path
        key = (
            _region_key(region),
            max_long_edge,
            bool(grayscale),
            normalized_format,
            int(quality),
        )
        cached = self._derived.get(key)
        if cached is not None and cached.exists():
            return cached

        image = self._source_copy()
        try:
            if region is not None:
                left, top, right, bottom = self._crop_box(region)
                cropped = image.crop((left, top, right, bottom))
                image.close()
                image = cropped
            if max_long_edge is not None and max(image.size) > max_long_edge:
                scale = max_long_edge / max(image.size)
                target = (
                    max(1, round(image.width * scale)),
                    max(1, round(image.height * scale)),
                )
                resized = image.resize(target, Image.Resampling.LANCZOS)
                image.close()
                image = resized
            converted = image.convert("L" if grayscale else "RGB")
            image.close()
            image = converted

            suffix = ".jpg" if normalized_format == "JPEG" else ".png"
            fd, name = tempfile.mkstemp(prefix="phone-harness-frame-", suffix=suffix)
            os.close(fd)
            output = Path(name)
            if normalized_format == "JPEG":
                image.save(output, format="JPEG", quality=int(quality), optimize=True)
            else:
                image.save(output, format="PNG", compress_level=1)
        finally:
            image.close()

        self._derived[key] = output
        self._owned.add(output)
        return output

    def export(self, output, *, region=None) -> Path:
        """Write a lossless full-resolution frame/region to ``output``."""
        destination = Path(output)
        if region is None and self.path is not None:
            shutil.copyfile(self.path, destination)
            return destination
        source = self.variant(region=region, image_format="PNG")
        shutil.copyfile(source, destination)
        return destination

    def close(self) -> None:
        if self._source_image is not None:
            self._source_image.close()
            self._source_image = None
        for path in self._owned:
            path.unlink(missing_ok=True)
        self._owned.clear()
        self._derived.clear()

    def _source_copy(self):
        if self._source_image is not None:
            return self._source_image.copy()
        if self.path is None:
            raise RuntimeError("screen frame has no source pixels")
        with Image.open(self.path) as source:
            return source.copy()

    def _crop_box(self, region):
        for key in ("x", "y", "w", "h"):
            if key not in region:
                raise ValueError("region must contain x, y, w and h")
        x, y, w, h = (float(region[key]) for key in ("x", "y", "w", "h"))
        win_x = float(self.window["x"])
        win_y = float(self.window["y"])
        win_w = float(self.window["w"])
        win_h = float(self.window["h"])
        if w <= 0 or h <= 0:
            raise ValueError("region width and height must be positive")
        if x < win_x or y < win_y or x + w > win_x + win_w or y + h > win_y + win_h:
            raise ValueError("region must fit inside the captured phone screen")
        sx = self.width / win_w
        sy = self.height / win_h
        return (
            round((x - win_x) * sx),
            round((y - win_y) * sy),
            round((x + w - win_x) * sx),
            round((y + h - win_y) * sy),
        )


class FrameBroker:
    """Capture exactly one frame on demand; derived images stay frame-local."""

    def __init__(self, capture: CaptureFn | CaptureSource):
        self._source = (
            CallableCaptureSource(capture)
            if callable(capture)
            else capture
        )

    def capture(self) -> ScreenFrame:
        started = time.perf_counter()
        captured = self._source.capture()
        capture_ms = round((time.perf_counter() - started) * 1000, 3)
        if isinstance(captured, ScreenFrame):
            captured.capture_ms = capture_ms
            return captured
        path, window = captured
        with Image.open(path) as image:
            width, height = image.size
        return ScreenFrame(
            path=Path(path),
            window=dict(window),
            width=width,
            height=height,
            capture_ms=capture_ms,
        )

    @property
    def source_name(self) -> str:
        return getattr(self._source, "name", "unknown")
