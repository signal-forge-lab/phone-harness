import unittest

from phone_harness.merge_planner import MergeToken, MergeTransition
from phone_harness.order_planner import (
    OrderDemand,
    OrderRequest,
    analyze_order_demand,
    plan_completable_orders,
    plan_order_merge_rounds,
    score_producer_output_for_orders,
)


class OrderPlannerTests(unittest.TestCase):
    def setUp(self):
        self.transitions = [
            MergeTransition("shrimp", 1, "crab", 2),
            MergeTransition("crab", 2, "sturgeon", 3),
        ]

    def test_existing_requested_item_is_reserved_instead_of_merged_up(self):
        analysis = analyze_order_demand(
            [
                MergeToken("a", "crab", 2),
                MergeToken("b", "crab", 2),
            ],
            self.transitions,
            [OrderDemand("crab", 2)],
        )
        self.assertEqual(analysis["reserved"][("crab", 2)], 1)
        self.assertTrue(analysis["complete"])
        self.assertEqual(analysis["remaining_inventory"][("crab", 2)], 1)

    def test_missing_high_level_order_expands_to_lower_level_requirement(self):
        analysis = analyze_order_demand(
            [],
            self.transitions,
            [OrderDemand("sturgeon", 3)],
        )
        self.assertEqual(analysis["required"][("sturgeon", 3)], 1)
        self.assertEqual(analysis["required"][("crab", 2)], 2)
        self.assertEqual(analysis["required"][("shrimp", 1)], 4)
        self.assertEqual(analysis["deficit"][("sturgeon", 3)], 1)
        self.assertEqual(analysis["deficit"][("crab", 2)], 2)
        self.assertEqual(analysis["deficit"][("shrimp", 1)], 4)

    def test_existing_lower_inventory_reduces_production_requirement(self):
        analysis = analyze_order_demand(
            [MergeToken("a", "shrimp", 1), MergeToken("b", "shrimp", 1)],
            self.transitions,
            [OrderDemand("crab", 2)],
        )
        # Both shrimp are already present, so no additional shrimp production is needed.
        self.assertEqual(analysis["required"][("shrimp", 1)], 2)
        self.assertEqual(analysis["remaining_inventory"][("shrimp", 1)], 0)
        self.assertEqual(analysis["deficit"][("shrimp", 1)], 0)

    def test_direct_order_output_scores_above_precursor(self):
        analysis = analyze_order_demand(
            [], self.transitions, [OrderDemand("crab", 2)]
        )
        self.assertGreater(
            score_producer_output_for_orders("crab", 2, analysis),
            score_producer_output_for_orders("shrimp", 1, analysis),
        )

    def test_unrelated_output_has_zero_order_score(self):
        analysis = analyze_order_demand(
            [], self.transitions, [OrderDemand("crab", 2)]
        )
        self.assertEqual(score_producer_output_for_orders("other", 1, analysis), 0.0)

    def test_order_merge_plan_does_not_overmerge_when_one_target_is_needed(self):
        result = plan_order_merge_rounds(
            [
                MergeToken("a", "shrimp", 1),
                MergeToken("b", "shrimp", 1),
                MergeToken("c", "shrimp", 1),
                MergeToken("d", "shrimp", 1),
            ],
            self.transitions,
            [OrderDemand("crab", 2)],
        )
        self.assertEqual(result["merge_count"], 1)
        identities = [(item.identity, item.level) for item in result["final_items"]]
        self.assertEqual(identities.count(("crab", 2)), 1)
        self.assertEqual(identities.count(("shrimp", 1)), 2)

    def test_order_merge_plan_predicts_dependency_rounds(self):
        result = plan_order_merge_rounds(
            [
                MergeToken("a", "shrimp", 1),
                MergeToken("b", "shrimp", 1),
                MergeToken("c", "shrimp", 1),
                MergeToken("d", "shrimp", 1),
            ],
            self.transitions,
            [OrderDemand("sturgeon", 3)],
        )
        self.assertEqual([len(round_) for round_ in result["rounds"]], [2, 1])
        self.assertEqual(result["merge_count"], 3)
        self.assertEqual(
            [(item.identity, item.level) for item in result["final_items"]],
            [("sturgeon", 3)],
        )

    def test_order_merge_plan_reserves_exact_requested_item(self):
        result = plan_order_merge_rounds(
            [
                MergeToken("a", "crab", 2),
                MergeToken("b", "crab", 2),
            ],
            self.transitions,
            [OrderDemand("crab", 2)],
        )
        self.assertEqual(result["merge_count"], 0)
        self.assertTrue(result["order_analysis"]["complete"])

    def test_completable_orders_do_not_double_spend_inventory(self):
        result = plan_completable_orders(
            [MergeToken("a", "crab", 2)],
            [
                OrderRequest("first", (OrderDemand("crab", 2),)),
                OrderRequest("second", (OrderDemand("crab", 2),)),
            ],
        )
        self.assertEqual(result["order_ids"], ["first"])
        self.assertEqual(result["consumed"][("crab", 2)], 1)

    def test_two_item_order_requires_both_items_before_delivery(self):
        result = plan_completable_orders(
            [MergeToken("a", "crab", 2)],
            [OrderRequest("customer", (
                OrderDemand("crab", 2),
                OrderDemand("sturgeon", 3),
            ))],
        )
        self.assertEqual(result["order_ids"], [])


if __name__ == "__main__":
    unittest.main()
