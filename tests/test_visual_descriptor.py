import unittest

from PIL import Image

from phone_harness.visual_descriptor import (
    descriptor_from_image,
    descriptor_similarity,
    rank_descriptors,
    sprite_descriptor_from_image,
)


class VisualDescriptorTests(unittest.TestCase):
    def test_identical_images_match_exactly(self):
        image = Image.new("RGB", (64, 64), (20, 100, 180))
        descriptor = descriptor_from_image(image)
        result = descriptor_similarity(descriptor, descriptor)
        self.assertEqual(result["score"], 1.0)

    def test_different_images_score_lower(self):
        first = descriptor_from_image(Image.new("RGB", (64, 64), (20, 100, 180)))
        second = descriptor_from_image(Image.new("RGB", (64, 64), (180, 40, 30)))
        result = descriptor_similarity(first, second)
        self.assertLess(result["score"], 0.9)

    def test_rank_descriptors_returns_best_candidates_without_domain_semantics(self):
        query = descriptor_from_image(Image.new("RGB", (64, 64), (20, 100, 180)))
        candidates = {
            "same": descriptor_from_image(Image.new("RGB", (64, 64), (20, 100, 180))),
            "near": descriptor_from_image(Image.new("RGB", (64, 64), (30, 100, 170))),
            "far": descriptor_from_image(Image.new("RGB", (64, 64), (180, 40, 30))),
        }
        ranked = rank_descriptors(query, candidates, limit=2)
        self.assertEqual([item["key"] for item in ranked], ["same", "near"])
        self.assertEqual(ranked[0]["score"], 1.0)

    def test_sprite_descriptor_reduces_pale_background_difference(self):
        first = Image.new("RGB", (100, 100), (240, 235, 210))
        second = Image.new("RGB", (100, 100), (220, 240, 250))
        for image in (first, second):
            for x in range(25, 75):
                for y in range(25, 75):
                    image.putpixel((x, y), (220, 40, 80))
        raw = descriptor_similarity(
            descriptor_from_image(first), descriptor_from_image(second)
        )["score"]
        sprite = descriptor_similarity(
            sprite_descriptor_from_image(first), sprite_descriptor_from_image(second)
        )["score"]
        self.assertGreater(sprite, raw)
        self.assertGreater(sprite, 0.99)

    def test_sprite_descriptor_preserves_foreground_aspect_ratio(self):
        tall = Image.new("RGB", (100, 100), (240, 240, 240))
        wide = Image.new("RGB", (100, 100), (240, 240, 240))
        for x in range(42, 58):
            for y in range(15, 85):
                tall.putpixel((x, y), (220, 40, 80))
        for x in range(15, 85):
            for y in range(42, 58):
                wide.putpixel((x, y), (220, 40, 80))
        similarity = descriptor_similarity(
            sprite_descriptor_from_image(tall),
            sprite_descriptor_from_image(wide),
        )["score"]
        self.assertLess(similarity, 0.95)


if __name__ == "__main__":
    unittest.main()
