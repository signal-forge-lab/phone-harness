import unittest

from phone_harness.workflows.merge_boss_catalog import MergeBossCatalog
from phone_harness.workflows.merge_boss_strategy import (
    MergeBossBoardItem,
    MergeBossOrder,
    MergeBossProducer,
    plan_merge_boss_turn,
)


class MergeBossStrategyTests(unittest.TestCase):
    def setUp(self):
        self.catalog = MergeBossCatalog.load()

    def test_delivery_has_priority_over_additional_merging(self):
        result = plan_merge_boss_turn(
            [
                MergeBossBoardItem("r0c0", "カニ", 2),
                MergeBossBoardItem("r0c1", "カニ", 2),
            ],
            [MergeBossOrder("customer-1", (("カニ", 2),))],
            [],
            free_cells=10,
            energy=10,
            catalog=self.catalog,
        )
        self.assertEqual(result["kind"], "deliver")
        self.assertEqual(result["order_ids"], ["customer-1"])

    def test_ui_ready_order_can_deliver_without_recounting_board_inventory(self):
        result = plan_merge_boss_turn(
            [],
            [MergeBossOrder("customer-1", (("Rare", 8),), ready=True)],
            [],
            free_cells=10,
            energy=10,
            catalog=self.catalog,
        )
        self.assertEqual(result["kind"], "deliver")
        self.assertEqual(result["order_ids"], ["customer-1"])
        self.assertEqual(result["reason"], "order_ui_ready")

    def test_one_crab_order_merges_only_one_shrimp_pair(self):
        result = plan_merge_boss_turn(
            [
                MergeBossBoardItem("a", "エビ", 1),
                MergeBossBoardItem("b", "エビ", 1),
                MergeBossBoardItem("c", "エビ", 1),
                MergeBossBoardItem("d", "エビ", 1),
            ],
            [MergeBossOrder("customer", (("カニ", 2),))],
            [],
            free_cells=10,
            catalog=self.catalog,
        )
        self.assertEqual(result["kind"], "merge")
        self.assertEqual(len(result["merges"]), 1)

    def test_sturgeon_order_predicts_two_round_chain(self):
        result = plan_merge_boss_turn(
            [
                MergeBossBoardItem("a", "エビ", 1),
                MergeBossBoardItem("b", "エビ", 1),
                MergeBossBoardItem("c", "エビ", 1),
                MergeBossBoardItem("d", "エビ", 1),
            ],
            [MergeBossOrder("customer", (("チョウザメ", 3),))],
            [],
            free_cells=10,
            catalog=self.catalog,
        )
        self.assertEqual(result["kind"], "merge")
        self.assertEqual([len(round_) for round_ in result["rounds"]], [2, 1])

    def test_partial_fishing_catalog_uses_bounded_local_burst_for_crab_order(self):
        result = plan_merge_boss_turn(
            [],
            [MergeBossOrder("customer", (("カニ", 2),))],
            [MergeBossProducer("r8c0", "プロフェッショナルフィッシングギアボックス", 8)],
            free_cells=20,
            energy=20,
            catalog=self.catalog,
            max_emissions=20,
            uncertain_burst_size=6,
        )
        self.assertEqual(result["kind"], "produce")
        self.assertEqual(result["producer_cell"], "r8c0")
        self.assertEqual(result["emissions"], 6)
        self.assertTrue(result["uncertain_output"])

    def test_unknown_producer_does_not_trigger_blind_production(self):
        result = plan_merge_boss_turn(
            [],
            [MergeBossOrder("customer", (("カニ", 2),))],
            [MergeBossProducer("r8c0", "unknown", 1)],
            free_cells=20,
            energy=20,
            catalog=self.catalog,
        )
        self.assertEqual(result["kind"], "idle")
        self.assertEqual(result["reason"], "no_known_producer_can_advance_orders")

    def test_strict_visual_compaction_precedes_production_when_board_is_nearly_full(self):
        result = plan_merge_boss_turn(
            [],
            [MergeBossOrder("customer", (("カニ", 2),))],
            [MergeBossProducer("r8c0", "プロフェッショナルフィッシングギアボックス", 8)],
            free_cells=4,
            energy=20,
            catalog=self.catalog,
            visual_merge_pairs=(("a", "b"), ("c", "d")),
        )
        self.assertEqual(result["kind"], "merge")
        self.assertEqual(result["reason"], "strict_visual_compaction")
        self.assertEqual(
            [(merge.source_cell, merge.target_cell) for merge in result["merges"]],
            [("a", "b"), ("c", "d")],
        )

    def test_visual_compaction_still_runs_when_space_is_available(self):
        result = plan_merge_boss_turn(
            [],
            [MergeBossOrder("customer", (("カニ", 2),))],
            [MergeBossProducer("r8c0", "プロフェッショナルフィッシングギアボックス", 8)],
            free_cells=12,
            energy=20,
            catalog=self.catalog,
            visual_merge_pairs=(("a", "b"),),
        )
        self.assertEqual(result["kind"], "merge")
        self.assertEqual(result["reason"], "strict_visual_compaction")

    def test_visual_compaction_runs_even_without_visible_orders(self):
        result = plan_merge_boss_turn(
            [],
            [],
            [],
            free_cells=12,
            energy=20,
            catalog=self.catalog,
            visual_merge_pairs=(("a", "b"), ("c", "d")),
        )
        self.assertEqual(result["kind"], "merge")
        self.assertEqual(result["reason"], "strict_visual_compaction")
        self.assertEqual(
            [(merge.source_cell, merge.target_cell) for merge in result["merges"]],
            [("a", "b"), ("c", "d")],
        )

    def test_known_producer_can_spend_energy_when_orders_are_temporarily_unreadable(self):
        producer = next(
            producer
            for producer in self.catalog.producers
            if producer.spec.outputs
        )
        result = plan_merge_boss_turn(
            [],
            [],
            [MergeBossProducer("r8c0", producer.identity, producer.level)],
            free_cells=9,
            energy=20,
            catalog=self.catalog,
            max_emissions=20,
            uncertain_burst_size=6,
        )
        self.assertEqual(result["kind"], "produce")
        self.assertEqual(result["reason"], "bounded_energy_spend_without_visible_orders")
        self.assertEqual(result["emissions"], 6)

    def test_unknown_producer_still_idles_without_orders(self):
        result = plan_merge_boss_turn(
            [],
            [],
            [MergeBossProducer("r8c0", "unknown", 1)],
            free_cells=9,
            energy=20,
            catalog=self.catalog,
        )
        self.assertEqual(result["kind"], "idle")
        self.assertEqual(result["reason"], "no_visible_or_known_orders")

    def test_order_priority_prefers_shorter_distance_from_current_board_inventory(self):
        result = plan_merge_boss_turn(
            [
                MergeBossBoardItem("near-a", "tennis-goods-lv3", 3),
                MergeBossBoardItem("near-b", "tennis-goods-lv3", 3),
                MergeBossBoardItem("far-a", "viewing-equipment-lv5", 5),
                MergeBossBoardItem("far-b", "viewing-equipment-lv5", 5),
            ],
            [
                MergeBossOrder("near-order", (("tennis-goods-lv5", 5),)),
                MergeBossOrder("far-order", (("viewing-equipment-lv9", 9),)),
            ],
            [], free_cells=20, energy=20, catalog=self.catalog,
        )
        self.assertEqual(result["kind"], "merge")
        self.assertEqual(result["priority_order_id"], "near-order")
        self.assertEqual(result["priority_distance"], [2])
        self.assertEqual(result["merges"][0].input_identity, "tennis-goods-lv3")

    def test_directly_useful_producer_for_next_ranked_order_beats_generic_fallback(self):
        result = plan_merge_boss_turn(
            [MergeBossBoardItem("near", "tennis-goods-lv4", 4)],
            [
                MergeBossOrder("near-order", (("tennis-goods-lv5", 5),)),
                MergeBossOrder("romeo-order", (("romeo-juliet-props-lv10", 10),)),
            ],
            [MergeBossProducer("r8c3", "縦型フィルムカメラ", 8)],
            free_cells=20,
            energy=20,
            catalog=self.catalog,
            uncertain_burst_size=2,
        )
        self.assertEqual(result["kind"], "produce")
        self.assertEqual(result["producer_cell"], "r8c3")
        self.assertEqual(result["priority_order_id"], "romeo-order")
        self.assertEqual(result["reason"], "highest_order_demand_score")

    def test_duplicate_same_producer_uses_stable_cell_independent_of_reader_order(self):
        orders = [MergeBossOrder("romeo-order", (("romeo-juliet-props-lv8", 8),))]
        first = plan_merge_boss_turn(
            [],
            orders,
            [
                MergeBossProducer("r8c3", "縦型フィルムカメラ", 8),
                MergeBossProducer("r8c4", "縦型フィルムカメラ", 8),
            ],
            free_cells=20,
            energy=20,
            catalog=self.catalog,
            uncertain_burst_size=2,
        )
        second = plan_merge_boss_turn(
            [],
            orders,
            [
                MergeBossProducer("r8c4", "縦型フィルムカメラ", 8),
                MergeBossProducer("r8c3", "縦型フィルムカメラ", 8),
            ],
            free_cells=20,
            energy=20,
            catalog=self.catalog,
            uncertain_burst_size=2,
        )

        self.assertEqual(first["producer_cell"], "r8c3")
        self.assertEqual(second["producer_cell"], "r8c3")

    def test_unrelated_or_incomplete_producer_can_be_used_as_bounded_exploration(self):
        result = plan_merge_boss_turn(
            [],
            [MergeBossOrder("customer", (("カニ", 2),))],
            [MergeBossProducer("r8c0", "プロフェッショナルフィッシングギアボックス", 8)],
            free_cells=12,
            energy=12,
            catalog=self.catalog,
            uncertain_burst_size=3,
        )
        self.assertEqual(result["kind"], "produce")
        self.assertTrue(result["exploration"])
        self.assertEqual(result["emissions"], 3)

    def test_all_high_level_orders_request_human_refresh_review_before_blind_production(self):
        result = plan_merge_boss_turn(
            [],
            [
                MergeBossOrder("high-a", (("romeo-juliet-props-lv10", 10),)),
                MergeBossOrder("high-b", (("viewing-equipment-lv9", 9),)),
            ],
            [MergeBossProducer("r8c3", "縦型フィルムカメラ", 8)],
            free_cells=12,
            energy=20,
            catalog=self.catalog,
            uncertain_burst_size=2,
        )
        self.assertEqual(result["kind"], "review_refresh")
        self.assertIn(result["order_id"], {"high-a", "high-b"})
        self.assertEqual(result["reason"], "all_visible_orders_extremely_high_level")

    def test_full_board_without_safe_merge_requests_refresh_review_instead_of_idling(self):
        result = plan_merge_boss_turn(
            [],
            [MergeBossOrder("customer", (("カニ", 2),))],
            [],
            free_cells=0,
            energy=31,
            catalog=self.catalog,
            visual_merge_pairs=(),
        )
        self.assertEqual(result["kind"], "review_refresh")
        self.assertEqual(result["order_id"], "customer")
        self.assertEqual(result["reason"], "board_full_no_merge_candidate")

    def test_mixed_low_and_high_orders_do_not_force_refresh_review(self):
        result = plan_merge_boss_turn(
            [],
            [
                MergeBossOrder("low", (("カニ", 2),)),
                MergeBossOrder("high", (("romeo-juliet-props-lv10", 10),)),
            ],
            [MergeBossProducer("r8c0", "プロフェッショナルフィッシングギアボックス", 8)],
            free_cells=12,
            energy=12,
            catalog=self.catalog,
            uncertain_burst_size=3,
        )
        self.assertNotEqual(result["kind"], "review_refresh")


if __name__ == "__main__":
    unittest.main()
