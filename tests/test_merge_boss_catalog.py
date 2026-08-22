import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from phone_harness.visual_descriptor import descriptor_from_image

from phone_harness.workflows.merge_boss_catalog import (
    MergeBossCatalog,
    item_family_hint_id,
    record_board_item_visual,
    record_board_producer_visual,
    record_item_family_hint,
    record_item_family_visuals,
    record_order_item_visual,
    record_producer_visual,
    record_producer_output_hint,
)


class MergeBossCatalogTests(unittest.TestCase):
    def test_default_catalog_reuses_learned_transitions_and_completed_producers(self):
        catalog = MergeBossCatalog.load()
        self.assertTrue(any(
            transition.input_identity == "エビ"
            and transition.output_identity == "カニ"
            for transition in catalog.transitions
        ))
        fishing = catalog.producer("プロフェッショナルフィッシングギアボックス", 8)
        self.assertIsNotNone(fishing)
        self.assertTrue(fishing.spec.outputs_complete)
        self.assertEqual(
            [(output.identity, output.level) for output in fishing.spec.outputs],
            [
                ("fishing-supplies-lv1", 1),
                ("fish-plush-lv1", 1),
                ("fishing-supplies-lv2", 2),
                ("fish-plush-lv2", 2),
            ],
        )

        fish_plush = catalog.family("fish-plush")
        self.assertTrue(fish_plush.complete)
        self.assertEqual(fish_plush.generation_producer_levels(), (5, 6, 7, 8))
        self.assertEqual(fish_plush.identity_at(4), "fish-plush-lv4")

        cheering = catalog.family("cheering-spectator")
        self.assertTrue(cheering.complete)
        self.assertEqual(cheering.identity_at(2), "cheering-spectator-lv2")
        self.assertEqual(cheering.generation_producer_levels(), (7, 8))
        self.assertTrue(any(
            transition.input_identity == "cheering-spectator-lv2"
            and transition.output_identity == "cheering-spectator-lv3"
            for transition in catalog.transitions
        ))

        tennis_bag = catalog.producer("マスターテニスバッグ", 8)
        self.assertTrue(tennis_bag.spec.outputs_complete)
        self.assertIsNotNone(tennis_bag.visual_descriptor)
        self.assertEqual(
            [(output.identity, output.level) for output in tennis_bag.spec.outputs],
            [
                ("tennis-goods-lv1", 1),
                ("cheering-spectator-lv1", 1),
                ("tennis-goods-lv2", 2),
                ("cheering-spectator-lv2", 2),
            ],
        )
        producer_rank = catalog.rank_visual_producers(tennis_bag.visual_descriptor, limit=1)
        self.assertEqual(producer_rank[0]["identity"], "マスターテニスバッグ")
        self.assertEqual(producer_rank[0]["level"], 8)
        self.assertEqual(producer_rank[0]["score"], 1.0)

    def test_complete_i_catalog_can_mark_single_output_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 1,
                "merge_transitions": [],
                "producers": [{
                    "identity": "Producer",
                    "level": 3,
                    "outputs_complete": True,
                    "possible_outputs": [{"identity": "A", "level": 1}],
                }],
            }), encoding="utf-8")
            catalog = MergeBossCatalog.load(path)
        self.assertTrue(catalog.producer("Producer", 3).spec.outputs_complete)

    def test_item_family_hint_builds_level_chain_transitions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [{
                    "family_id": "family-a",
                    "display_name": "Family A",
                    "complete": True,
                    "levels": [
                        {"level": level, "identity": f"A{level}"}
                        for level in range(1, 11)
                    ],
                }],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            catalog = MergeBossCatalog.load(path)

        family = catalog.family("family-a")
        self.assertTrue(family.complete)
        self.assertEqual(family.identity_at(10), "A10")
        self.assertEqual(catalog.family_for_item("A4", 4).family_id, "family-a")
        self.assertTrue(any(
            transition.input_identity == "A9"
            and transition.input_level == 9
            and transition.output_identity == "A10"
            and transition.output_level == 10
            for transition in catalog.transitions
        ))
        self.assertFalse(any(
            transition.input_level == 10
            for transition in catalog.transitions
        ))

    def test_complete_item_family_requires_all_ten_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [{
                    "family_id": "incomplete",
                    "complete": True,
                    "levels": [{"level": 1, "identity": "A1"}],
                }],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "levels 1 through 10"):
                MergeBossCatalog.load(path)

    def test_family_preserves_generation_producer_levels_and_descriptors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [{
                    "family_id": "family-generation",
                    "complete": True,
                    "levels": [
                        {"level": level, "identity": f"A{level}"}
                        for level in range(1, 11)
                    ],
                    "generation_producers": [
                        {"level": 4, "visual_descriptor": {"version": 1, "size": [8, 8], "rgb4_b64": "p4"}},
                        {"level": 8, "visual_descriptor": {"version": 1, "size": [8, 8], "rgb4_b64": "p8"}},
                    ],
                }],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            catalog = MergeBossCatalog.load(path)

        family = catalog.family("family-generation")
        self.assertEqual(family.generation_producer_levels(), (4, 8))
        self.assertEqual(family.generation_producer_descriptor(8)["rgb4_b64"], "p8")

    def test_rank_visual_items_keeps_domain_mapping_outside_generic_matcher(self):
        blue = descriptor_from_image(Image.new("RGB", (64, 64), (20, 100, 180)))
        red = descriptor_from_image(Image.new("RGB", (64, 64), (180, 40, 30)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [{
                    "family_id": "visual-family",
                    "complete": False,
                    "levels": [
                        {"level": 1, "identity": "Blue", "visual_descriptor": blue},
                        {"level": 2, "identity": "Red", "visual_descriptor": red},
                    ],
                }],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            catalog = MergeBossCatalog.load(path)

        ranked = catalog.rank_visual_items(blue, limit=1)
        self.assertEqual(ranked[0]["family_id"], "visual-family")
        self.assertEqual(ranked[0]["identity"], "Blue")
        self.assertEqual(ranked[0]["level"], 1)
        self.assertEqual(ranked[0]["score"], 1.0)

    def test_rank_sprite_items_prefers_sprite_descriptor_when_available(self):
        blue = descriptor_from_image(Image.new("RGB", (64, 64), (20, 100, 180)))
        red = descriptor_from_image(Image.new("RGB", (64, 64), (180, 40, 30)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [{
                    "family_id": "sprite-family",
                    "complete": False,
                    "levels": [
                        {"level": 1, "identity": "Blue", "sprite_descriptor": blue},
                        {"level": 2, "identity": "Red", "sprite_descriptor": red},
                    ],
                }],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            catalog = MergeBossCatalog.load(path)
        ranked = catalog.rank_sprite_items(blue, limit=1)
        self.assertEqual(ranked[0]["identity"], "Blue")
        self.assertEqual(ranked[0]["level"], 1)
        self.assertEqual(ranked[0]["score"], 1.0)

    def test_visual_mbv_overlays_sprite_descriptors_without_bloating_catalog(self):
        blue = descriptor_from_image(Image.new("RGB", (64, 64), (20, 100, 180)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [{
                    "family_id": "overlay-family",
                    "complete": False,
                    "levels": [{"level": 1, "identity": "Overlay"}],
                }],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            (root / "visual.mbv").write_text(
                json.dumps({"fmt": "MBV1"}) + "\n" +
                json.dumps({"k": "item", "family": "overlay-family", "level": 1, "d": blue}) + "\n",
                encoding="utf-8",
            )
            catalog = MergeBossCatalog.load(path)
        self.assertEqual(catalog.family("overlay-family").sprite_descriptor_at(1), blue)

    def test_record_item_family_visuals_replaces_only_target_family_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "family.png"
            image = Image.new("RGB", (100, 1000), (240, 240, 240))
            for level in range(1, 11):
                for x in range(20, 80):
                    for y in range((level - 1) * 100 + 20, (level - 1) * 100 + 80):
                        image.putpixel((x, y), ((level * 20) % 255, 40, 180))
            image.save(image_path)
            image.close()
            visual_path = root / "visual.mbv"
            visual_path.write_text(
                json.dumps({"fmt": "MBV1"}) + "\n" +
                json.dumps({"k": "item", "family": "other", "level": 1, "d": {"version": 1, "size": [8, 8], "rgb4_b64": "AA=="}}) + "\n",
                encoding="utf-8",
            )
            slots = tuple(
                {
                    "bounds": {"x": 0, "y": (level - 1) * 100, "w": 100, "h": 100},
                    "expected_level": level,
                }
                for level in range(1, 11)
            )
            descriptors = record_item_family_visuals(
                "target", image_path, slots, path=visual_path
            )
            lines = [json.loads(line) for line in visual_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(descriptors), 10)
        self.assertTrue(any(item.get("family") == "other" for item in lines[1:]))
        self.assertEqual(sum(item.get("family") == "target" for item in lines[1:]), 10)

    def test_record_producer_visual_is_loaded_as_catalog_overlay(self):
        blue = descriptor_from_image(Image.new("RGB", (64, 64), (20, 100, 180)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [{
                    "identity": "P",
                    "level": 8,
                    "outputs_complete": False,
                    "possible_outputs": [],
                }],
            }), encoding="utf-8")
            record_producer_visual("P", 8, blue, path=root / "visual.mbv")
            catalog = MergeBossCatalog.load(catalog_path)
        self.assertEqual(catalog.producer("P", 8).visual_descriptor, blue)

    def test_order_item_visual_can_be_ranked_from_mbv_overlay(self):
        blue = descriptor_from_image(Image.new("RGB", (64, 64), (20, 100, 180)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            record_order_item_visual("A", 3, blue, path=root / "visual.mbv")
            catalog = MergeBossCatalog.load(catalog_path)
        ranked = catalog.rank_order_items(blue, limit=1)
        self.assertEqual(ranked[0]["identity"], "A")
        self.assertEqual(ranked[0]["level"], 3)
        self.assertEqual(ranked[0]["score"], 1.0)

    def test_board_item_visual_augments_sprite_matching_without_replacing_hint_template(self):
        blue = descriptor_from_image(Image.new("RGB", (64, 64), (20, 100, 180)))
        red = descriptor_from_image(Image.new("RGB", (64, 64), (180, 40, 30)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps({
                "version": 2,
                "item_families": [{
                    "family_id": "board-family",
                    "complete": False,
                    "levels": [{"level": 3, "identity": "Known", "sprite_descriptor": blue}],
                }],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            record_board_item_visual("Known", 3, red, path=root / "visual.mbv")
            # Exact duplicate is ignored, but a different future render may coexist.
            record_board_item_visual("Known", 3, red, path=root / "visual.mbv")
            catalog = MergeBossCatalog.load(catalog_path)
            records = [
                json.loads(line)
                for line in (root / "visual.mbv").read_text(encoding="utf-8").splitlines()[1:]
            ]
        ranked_red = catalog.rank_sprite_items(red, limit=1)
        ranked_blue = catalog.rank_sprite_items(blue, limit=1)
        self.assertEqual((ranked_red[0]["identity"], ranked_red[0]["level"]), ("Known", 3))
        self.assertEqual(ranked_red[0]["template_source"], "board-item")
        self.assertEqual(ranked_red[0]["score"], 1.0)
        self.assertEqual((ranked_blue[0]["identity"], ranked_blue[0]["level"]), ("Known", 3))
        self.assertEqual(ranked_blue[0]["score"], 1.0)
        self.assertEqual(sum(item.get("k") == "board-item" for item in records), 1)

    def test_board_producer_visual_augments_producer_matching(self):
        blue = descriptor_from_image(Image.new("RGB", (64, 64), (20, 100, 180)))
        red = descriptor_from_image(Image.new("RGB", (64, 64), (180, 40, 30)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [{
                    "identity": "P",
                    "level": 8,
                    "outputs_complete": True,
                    "possible_outputs": [],
                    "visual_descriptor": blue,
                }],
            }), encoding="utf-8")
            record_board_producer_visual("P", 8, red, path=root / "visual.mbv")
            catalog = MergeBossCatalog.load(catalog_path)
        ranked = catalog.rank_visual_producers(red, limit=1)
        self.assertEqual((ranked[0]["identity"], ranked[0]["level"]), ("P", 8))
        self.assertEqual(ranked[0]["template_source"], "board-producer")
        self.assertEqual(ranked[0]["score"], 1.0)

    def test_record_item_family_hint_preserves_generation_producers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [{
                    "family_id": "f",
                    "display_name": "old",
                    "complete": True,
                    "levels": [{"level": level, "identity": f"A{level}"} for level in range(1, 11)],
                    "generation_producers": [{"level": 8}],
                }],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            record_item_family_hint(
                [{"level": level, "identity": f"A{level}"} for level in range(1, 11)],
                path=path,
                family_id="f",
                display_name="new",
            )
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["item_families"][0]["generation_producers"], [{"level": 8}])

    def test_record_item_family_hint_persists_and_reuses_stable_family_id(self):
        levels = [
            {"level": level, "identity": f"Item-{level}"}
            for level in range(1, 11)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")

            first_id = record_item_family_hint(
                levels,
                path=path,
                display_name="Family",
                source="board_item_i",
            )
            second_id = record_item_family_hint(
                levels,
                path=path,
                display_name="Family",
                source="order_item_i",
            )
            data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(first_id, second_id)
        self.assertEqual(first_id, item_family_hint_id(levels))
        self.assertEqual(len(data["item_families"]), 1)
        self.assertEqual(
            data["item_families"][0]["learned_from"],
            ["board_item_i", "order_item_i"],
        )

    def test_record_item_family_hint_rejects_partial_hint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "levels 1 through 10"):
                record_item_family_hint(
                    [{"level": 1, "identity": "OnlyOne"}],
                    path=path,
                )

    def test_item_family_visual_descriptor_metadata_round_trips(self):
        levels = [
            {
                "level": level,
                "identity": f"Item-{level}",
                "visual_descriptor": {
                    "version": 1,
                    "size": [8, 8],
                    "rgb4_b64": "AA==",
                },
            }
            for level in range(1, 11)
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "item_families": [],
                "merge_transitions": [],
                "producers": [],
            }), encoding="utf-8")
            record_item_family_hint(levels, path=path, family_id="family-v")
            data = json.loads(path.read_text(encoding="utf-8"))
            catalog = MergeBossCatalog.load(path)

        self.assertIn("visual_descriptor", data["item_families"][0]["levels"][0])
        self.assertEqual(catalog.family("family-v").descriptor_at(1)["version"], 1)

    def test_producer_i_hint_preserves_observed_output_and_marks_complete_when_consistent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "merge_transitions": [],
                "item_families": [],
                "producers": [{
                    "identity": "Producer",
                    "level": 3,
                    "outputs_complete": False,
                    "possible_outputs": [{
                        "identity": "A", "level": 1,
                        "source": "observed_emission", "observations": 2,
                    }],
                }],
            }, ensure_ascii=False), encoding="utf-8")
            complete = record_producer_output_hint(
                "Producer", 3, [{"identity": "A", "level": 1}], path=path
            )
            catalog = MergeBossCatalog.load(path)

        self.assertTrue(complete)
        self.assertTrue(catalog.producer("Producer", 3).spec.outputs_complete)

    def test_conflicting_complete_producer_hint_stays_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps({
                "version": 2,
                "merge_transitions": [],
                "item_families": [],
                "producers": [{
                    "identity": "Producer",
                    "level": 3,
                    "outputs_complete": False,
                    "possible_outputs": [{"identity": "Observed", "level": 1}],
                }],
            }, ensure_ascii=False), encoding="utf-8")
            complete = record_producer_output_hint(
                "Producer", 3, [{"identity": "HintOnly", "level": 1}], path=path
            )

        self.assertFalse(complete)


if __name__ == "__main__":
    unittest.main()
