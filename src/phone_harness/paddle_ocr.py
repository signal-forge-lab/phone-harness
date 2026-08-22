"""Local OCR for Windows screenshots using PaddleOCR / PP-OCRv6 medium."""

import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from PIL import Image


_MODEL = None
LOG = logging.getLogger(__name__)
_MAX_INFERENCE_LONG_EDGE = 1600


def image_size(path):
    with Image.open(path) as image:
        return image.size


@contextmanager
def _inference_image(path):
    source = Path(path)
    temporary = None
    with Image.open(source) as image:
        width, height = image.size
        long_edge = max(width, height)
        if long_edge <= _MAX_INFERENCE_LONG_EDGE:
            yield source
            return

        scale = _MAX_INFERENCE_LONG_EDGE / long_edge
        target = (max(1, round(width * scale)), max(1, round(height * scale)))
        resized = image.convert("RGB").resize(target, Image.Resampling.LANCZOS)
        fd, temporary_name = tempfile.mkstemp(prefix="phone-harness-ocr-", suffix=".png")
        os.close(fd)
        temporary = Path(temporary_name)
        resized.save(temporary, format="PNG", compress_level=1)

    try:
        yield temporary
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _model():
    global _MODEL
    if _MODEL is None:
        from paddleocr import PaddleOCR

        _MODEL = PaddleOCR(
            text_detection_model_name="PP-OCRv6_medium_det",
            text_recognition_model_name="PP-OCRv6_medium_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="cpu",
        )
    return _MODEL


def normalize_result(result, image_px, window):
    """Normalize one PaddleOCR result to phone-harness tap-ready boxes."""
    texts = result.get("rec_texts", [])
    scores = result.get("rec_scores", [])
    polys = result.get("rec_polys", [])
    img_w, img_h = image_px
    sx, sy = window["w"] / img_w, window["h"] / img_h
    out = []
    for text, score, poly in zip(texts, scores, polys):
        text = str(text).strip()
        if not text:
            continue
        points = [(float(point[0]), float(point[1])) for point in poly]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
        screen_poly = [
            [round(window["x"] + x * sx, 1), round(window["y"] + y * sy, 1)]
            for x, y in points
        ]
        x = round(window["x"] + (left + right) / 2 * sx, 1)
        y = round(window["y"] + (top + bottom) / 2 * sy, 1)
        width = round((right - left) * sx, 1)
        height = round((bottom - top) * sy, 1)
        bbox_x = round(window["x"] + left * sx, 1)
        bbox_y = round(window["y"] + top * sy, 1)
        out.append({
            "text": text,
            "confidence": round(float(score), 3),
            "polygon": screen_poly,
            "bbox": {"x": bbox_x, "y": bbox_y, "w": width, "h": height},
            "center": {"x": x, "y": y},
            "x": x,
            "y": y,
            "w": width,
            "h": height,
        })
    return out


def recognize(path, window):
    started = time.perf_counter()
    boxes = []
    with _inference_image(path) as inference_path:
        image_px = image_size(inference_path)
        for result in _model().predict(str(inference_path)):
            boxes.extend(normalize_result(result, image_px, window))
    LOG.debug("operation=ocr duration=%.3f result=pass boxes=%d", time.perf_counter() - started, len(boxes))
    return boxes
