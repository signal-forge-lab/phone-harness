"""Compact visual descriptors for low-resolution template learning/matching."""

from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat


_SIZE = (8, 8)


def _normalized(image):
    return image.convert("RGB").resize(_SIZE, Image.Resampling.LANCZOS)


def _quantized_bytes(image):
    normalized = _normalized(image)
    # Four bits/channel are enough for coarse sprite identity and make the
    # descriptor small enough to persist in JSON.
    return bytes((value // 16) * 16 for value in normalized.tobytes())


def descriptor_from_image(image):
    payload = _quantized_bytes(image)
    return {
        "version": 1,
        "size": list(_SIZE),
        "rgb4_b64": base64.b64encode(payload).decode("ascii"),
    }


def sprite_descriptor_from_image(
    image,
    *,
    saturation_threshold=110,
    value_threshold=70,
    background=128,
    padding=3,
):
    """Build a compact descriptor from saturated foreground pixels only.

    This is intended for the same colorful sprite rendered on different pale
    UI backgrounds. It is domain-agnostic: callers decide what the sprite means.
    """
    if not 0 <= saturation_threshold <= 255 or not 0 <= value_threshold <= 255:
        raise ValueError("sprite HSV thresholds must be between 0 and 255")
    if not 0 <= background <= 255:
        raise ValueError("sprite background must be between 0 and 255")
    if isinstance(padding, bool) or not isinstance(padding, int) or padding < 0:
        raise ValueError("padding must be a non-negative integer")

    rgb = image.convert("RGB")
    hsv = rgb.convert("HSV")
    width, height = rgb.size
    foreground = []
    hsv_data = getattr(hsv, "get_flattened_data", hsv.getdata)
    for index, (_hue, saturation, value) in enumerate(hsv_data()):
        if saturation >= saturation_threshold and value >= value_threshold:
            foreground.append((index % width, index // width))
    if len(foreground) < 20:
        return descriptor_from_image(rgb)

    xs = [point[0] for point in foreground]
    ys = [point[1] for point in foreground]
    left = max(0, min(xs) - padding)
    top = max(0, min(ys) - padding)
    right = min(width, max(xs) + padding + 1)
    bottom = min(height, max(ys) + padding + 1)
    rgb = rgb.crop((left, top, right, bottom))
    hsv = rgb.convert("HSV")
    neutral = Image.new("RGB", rgb.size, (background, background, background))
    rgb_data = getattr(rgb, "get_flattened_data", rgb.getdata)
    hsv_data = getattr(hsv, "get_flattened_data", hsv.getdata)
    neutral_data = getattr(neutral, "get_flattened_data", neutral.getdata)
    source_pixels = list(rgb_data())
    hsv_pixels = list(hsv_data())
    output_pixels = list(neutral_data())
    for index, (_hue, saturation, value) in enumerate(hsv_pixels):
        if saturation >= saturation_threshold and value >= value_threshold:
            output_pixels[index] = source_pixels[index]
    neutral.putdata(output_pixels)
    # Preserve the foreground aspect ratio. Stretching every tight foreground
    # crop to a square destroys useful shape evidence (for example a tall
    # tripod vs. a wide camera). Letterbox it on a neutral square instead.
    side = max(neutral.size)
    square = Image.new("RGB", (side, side), (background, background, background))
    square.paste(
        neutral,
        ((side - neutral.width) // 2, (side - neutral.height) // 2),
    )
    return descriptor_from_image(square)


def descriptor_from_path(path, bounds=None):
    with Image.open(Path(path)) as source:
        image = source.convert("RGB")
        if bounds is not None:
            x = float(bounds["x"])
            y = float(bounds["y"])
            w = float(bounds["w"])
            h = float(bounds["h"])
            image = image.crop((x, y, x + w, y + h))
        return descriptor_from_image(image)


def _descriptor_image(descriptor):
    if descriptor.get("version") != 1:
        raise ValueError("unsupported visual descriptor version")
    size = tuple(descriptor.get("size") or ())
    if size != _SIZE:
        raise ValueError("unsupported visual descriptor size")
    payload = base64.b64decode(descriptor["rgb4_b64"])
    expected = _SIZE[0] * _SIZE[1] * 3
    if len(payload) != expected:
        raise ValueError("invalid visual descriptor payload")
    return Image.frombytes("RGB", _SIZE, payload)


def descriptor_similarity(first, second):
    first_image = _descriptor_image(first)
    second_image = _descriptor_image(second)
    color_diff = ImageChops.difference(first_image, second_image)
    color = 1.0 - sum(ImageStat.Stat(color_diff).mean) / (3 * 255)
    first_edge = first_image.convert("L").filter(ImageFilter.FIND_EDGES)
    second_edge = second_image.convert("L").filter(ImageFilter.FIND_EDGES)
    edge_diff = ImageChops.difference(first_edge, second_edge)
    edge = 1.0 - ImageStat.Stat(edge_diff).mean[0] / 255
    return {
        "score": color * 0.8 + edge * 0.2,
        "color_similarity": color,
        "edge_similarity": edge,
    }


def rank_descriptors(query, candidates, *, limit=None):
    """Rank opaque candidate keys by visual descriptor similarity.

    This helper deliberately has no knowledge of games, item families or
    screen geometry. Domain adapters decide what each key means.
    """
    if not hasattr(candidates, "items"):
        raise TypeError("candidates must be a mapping")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
    ):
        raise ValueError("limit must be a non-negative integer or None")
    ranked = []
    for key, descriptor in candidates.items():
        similarity = descriptor_similarity(query, descriptor)
        ranked.append({"key": key, **similarity})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked if limit is None else ranked[:limit]
