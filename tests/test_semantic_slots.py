import unittest

from phone_harness.semantic_slots import (
    SemanticSlot,
    parse_identity_level,
    parse_level,
    semantic_slots_from_records,
)


class SemanticSlotTests(unittest.TestCase):
    def test_parse_level_accepts_common_level_spellings(self):
        self.assertEqual(parse_level("Lv.4"), 4)
        self.assertEqual(parse_level("Lvl 10"), 10)
        self.assertEqual(parse_level("Level: 7"), 7)

    def test_expected_slot_level_allows_identity_when_level_text_is_missing(self):
        slot = SemanticSlot({"x": 0, "y": 0, "w": 100, "h": 100}, expected_level=3)
        result = parse_identity_level([
            {"text": "チョウザメ", "x": 10, "y": 10, "w": 50, "h": 20},
        ], slot)
        self.assertEqual(result, {"identity": "チョウザメ", "level": 3})

    def test_recognized_level_mismatch_fails_closed(self):
        slot = SemanticSlot({"x": 0, "y": 0, "w": 100, "h": 100}, expected_level=3)
        with self.assertRaisesRegex(ValueError, "expected level 3"):
            parse_identity_level([
                {"text": "Item Lv.4", "x": 10, "y": 10, "w": 50, "h": 20},
            ], slot)

    def test_slot_records_round_trip_into_semantic_slots(self):
        slots = semantic_slots_from_records(({
            "bounds": {"x": 1, "y": 2, "w": 3, "h": 4},
            "expected_level": 7,
        },))
        self.assertEqual(slots[0].bounds["x"], 1)
        self.assertEqual(slots[0].expected_level, 7)

    def test_element_xy_are_center_coordinates(self):
        slot = SemanticSlot({"x": 0, "y": 0, "w": 100, "h": 100}, expected_level=2)
        result = parse_identity_level([
            {"text": "Item", "x": 90, "y": 90, "w": 40, "h": 40},
        ], slot)
        self.assertEqual(result, {"identity": "Item", "level": 2})


if __name__ == "__main__":
    unittest.main()
