import unittest
import random
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from PIL import Image

from phone_harness.frame import BgraCaptureSource, CallableCaptureSource, FrameBroker, ScreenFrame


class FrameBrokerTests(unittest.TestCase):
    def test_capture_happens_once_and_variants_are_cached(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "screen.png"
            Image.new("RGB", (1200, 2400), "white").save(source)
            capture = MagicMock(return_value=(str(source), {"x": 0, "y": 0, "w": 1200, "h": 2400}))
            frame = FrameBroker(capture).capture()
            try:
                first = frame.variant(max_long_edge=384, image_format="JPEG", quality=35)
                second = frame.variant(max_long_edge=384, image_format="JPEG", quality=35)
                self.assertEqual(first, second)
                with Image.open(first) as image:
                    self.assertEqual(image.size, (192, 384))
                capture.assert_called_once_with()
            finally:
                frame.close()

    def test_full_png_variant_reuses_original_file_without_reencode(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "screen.png"
            Image.new("RGB", (400, 800), "white").save(source)
            frame = ScreenFrame.from_path(source)
            try:
                self.assertEqual(frame.variant(image_format="PNG"), source)
            finally:
                frame.close()
            self.assertTrue(source.exists())

    def test_capture_source_name_is_exposed_for_future_stream_backends(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "screen.png"
            Image.new("RGB", (10, 20), "white").save(source)
            broker = FrameBroker(
                CallableCaptureSource(
                    lambda: (str(source), {"x": 0, "y": 0, "w": 10, "h": 20}),
                    name="fixture-stream",
                )
            )
            frame = broker.capture()
            try:
                self.assertEqual(broker.source_name, "fixture-stream")
            finally:
                frame.close()

    def test_bgra_source_can_make_coarse_jpeg_without_full_png_source(self):
        width, height = 64, 96
        pixel = bytes((10, 20, 30, 255))
        bgra = pixel * (width * height)
        source = BgraCaptureSource(
            lambda: (bgra, width, height, {"x": 0, "y": 0, "w": width, "h": height}),
            name="fixture-decoded-stream",
        )
        broker = FrameBroker(source)
        frame = broker.capture()
        try:
            self.assertIsNone(frame.path)
            coarse = frame.variant(max_long_edge=48, image_format="JPEG", quality=28)
            self.assertTrue(coarse.exists())
            self.assertEqual(coarse.suffix, ".jpg")
            with Image.open(coarse) as image:
                self.assertEqual(image.size, (32, 48))
            self.assertEqual(broker.source_name, "fixture-decoded-stream")
        finally:
            frame.close()

    def test_region_variant_crops_absolute_screen_coordinates_then_resizes(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "screen.png"
            Image.new("RGB", (1000, 2000), "white").save(source)
            frame = FrameBroker(
                lambda: (str(source), {"x": 0, "y": 0, "w": 500, "h": 1000})
            ).capture()
            try:
                variant = frame.variant(
                    region={"x": 100, "y": 200, "w": 200, "h": 300},
                    max_long_edge=300,
                    image_format="PNG",
                )
                with Image.open(variant) as image:
                    self.assertEqual(image.size, (200, 300))
            finally:
                frame.close()

    def test_coarse_jpeg_is_materially_smaller_than_full_png(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "screen.png"
            image = Image.new("RGB", (900, 1800))
            pixels = image.load()
            rng = random.Random(42)
            for y in range(image.height):
                for x in range(image.width):
                    pixels[x, y] = (
                        ((x * 7) + rng.randrange(48)) % 256,
                        ((y * 11) + rng.randrange(48)) % 256,
                        (((x + y) * 5) + rng.randrange(48)) % 256,
                    )
            image.save(source, format="PNG")
            frame = FrameBroker(
                lambda: (str(source), {"x": 0, "y": 0, "w": 900, "h": 1800})
            ).capture()
            try:
                coarse = frame.variant(max_long_edge=384, image_format="JPEG", quality=35)
                self.assertLess(coarse.stat().st_size, source.stat().st_size / 4)
            finally:
                frame.close()
