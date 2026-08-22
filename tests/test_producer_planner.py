import unittest

from phone_harness.merge_planner import MergeToken, MergeTransition
from phone_harness.order_planner import OrderDemand, analyze_order_demand
from phone_harness.producer_planner import (
    ProducerOutput,
    ProducerSpec,
    plan_producer_burst,
)


class ProducerPlannerTests(unittest.TestCase):
    def setUp(self):
        self.transitions = [
            MergeTransition("shrimp", 1, "crab", 2),
            MergeTransition("crab", 2, "sturgeon", 3),
            MergeTransition("shell", 1, "pearl", 2),
        ]

    def test_prefers_output_that_completes_a_chain_merge(self):
        items = [
            MergeToken("a", "shrimp", 1),
            MergeToken("b", "crab", 2),
        ]
        result = plan_producer_burst(
            items,
            [
                ProducerSpec("fishing", (ProducerOutput("shrimp", 1),), outputs_complete=True),
                ProducerSpec("shells", (ProducerOutput("shell", 1),), outputs_complete=True),
            ],
            self.transitions,
            free_cells=10,
            energy=20,
        )

        self.assertEqual(result.producer_id, "fishing")
        # shrimp emission enables shrimp->crab and then crab->sturgeon.
        self.assertEqual(result.expected_merge_gain, 2.0)

    def test_deterministic_producer_can_use_entire_local_budget(self):
        result = plan_producer_burst(
            [],
            [ProducerSpec("fishing", (ProducerOutput("shrimp", 1),), outputs_complete=True)],
            self.transitions,
            free_cells=17,
            energy=12,
            max_emissions=20,
        )
        self.assertEqual(result.emissions, 12)
        self.assertFalse(result.uncertain_output)

    def test_uncertain_producer_uses_smaller_internal_burst(self):
        result = plan_producer_burst(
            [],
            [ProducerSpec("mixed", (
                ProducerOutput("shrimp", 1),
                ProducerOutput("shell", 1),
            ))],
            self.transitions,
            free_cells=30,
            energy=30,
            max_emissions=20,
            uncertain_burst_size=5,
        )
        self.assertEqual(result.emissions, 5)
        self.assertTrue(result.uncertain_output)

    def test_capacity_is_bounded_by_free_cells(self):
        result = plan_producer_burst(
            [],
            [ProducerSpec("fishing", (ProducerOutput("shrimp", 1),), outputs_complete=True)],
            self.transitions,
            free_cells=3,
            energy=100,
        )
        self.assertEqual(result.emissions, 3)

    def test_observed_single_output_is_still_uncertain_until_catalog_is_complete(self):
        result = plan_producer_burst(
            [],
            [ProducerSpec("fishing", (ProducerOutput("shrimp", 1),))],
            self.transitions,
            free_cells=20,
            energy=20,
            uncertain_burst_size=6,
        )
        self.assertEqual(result.emissions, 6)
        self.assertTrue(result.uncertain_output)

    def test_order_target_overrides_unrelated_merge_gain(self):
        items = [MergeToken("a", "shell", 1)]
        order = analyze_order_demand(
            items,
            self.transitions,
            [OrderDemand("crab", 2)],
        )
        result = plan_producer_burst(
            items,
            [
                ProducerSpec("fishing", (ProducerOutput("shrimp", 1),), outputs_complete=True),
                ProducerSpec("shells", (ProducerOutput("shell", 1),), outputs_complete=True),
            ],
            self.transitions,
            free_cells=10,
            energy=10,
            order_analysis=order,
        )
        self.assertEqual(result.producer_id, "fishing")
        self.assertEqual(result.reason, "highest_order_demand_score")

    def test_complete_catalog_limits_burst_to_direct_order_deficit(self):
        order = analyze_order_demand(
            [],
            self.transitions,
            [OrderDemand("crab", 2)],
        )
        result = plan_producer_burst(
            [],
            [ProducerSpec("fishing", (ProducerOutput("shrimp", 1),), outputs_complete=True)],
            self.transitions,
            free_cells=20,
            energy=20,
            order_analysis=order,
        )
        self.assertEqual(result.emissions, 2)


if __name__ == "__main__":
    unittest.main()
