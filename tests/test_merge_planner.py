import unittest

from phone_harness.merge_planner import (
    MergeToken,
    MergeTransition,
    plan_merge_rounds,
)


class MergePlannerTests(unittest.TestCase):
    def test_plans_independent_merges_in_one_round(self):
        result = plan_merge_rounds(
            [
                MergeToken("a", "shrimp", 1),
                MergeToken("b", "shrimp", 1),
                MergeToken("c", "fish", 4),
                MergeToken("d", "fish", 4),
            ],
            [
                MergeTransition("shrimp", 1, "crab", 2),
                MergeTransition("fish", 4, "octopus", 5),
            ],
        )

        self.assertEqual(result["merge_count"], 2)
        self.assertEqual(len(result["rounds"]), 1)
        self.assertEqual(
            {(step.input_identity, step.output_identity) for step in result["rounds"][0]},
            {("shrimp", "crab"), ("fish", "octopus")},
        )

    def test_predicts_chain_merge_created_by_previous_round(self):
        result = plan_merge_rounds(
            [
                MergeToken("a", "shrimp", 1),
                MergeToken("b", "shrimp", 1),
                MergeToken("c", "shrimp", 1),
                MergeToken("d", "shrimp", 1),
                MergeToken("e", "crab", 2),
            ],
            [
                MergeTransition("shrimp", 1, "crab", 2),
                MergeTransition("crab", 2, "sturgeon", 3),
            ],
        )

        self.assertEqual(result["merge_count"], 3)
        self.assertEqual([len(round_) for round_ in result["rounds"]], [2, 1])
        self.assertTrue(all(step.output_level == 2 for step in result["rounds"][0]))
        self.assertEqual(result["rounds"][1][0].input_level, 2)
        self.assertEqual(result["rounds"][1][0].output_level, 3)

    def test_unknown_transition_is_left_unmerged(self):
        result = plan_merge_rounds(
            [MergeToken("a", "mystery", 9), MergeToken("b", "mystery", 9)],
            [],
        )
        self.assertEqual(result["merge_count"], 0)
        self.assertEqual(len(result["final_items"]), 2)

    def test_max_merge_budget_stops_plan_without_overplanning(self):
        result = plan_merge_rounds(
            [
                MergeToken("a", "x", 1),
                MergeToken("b", "x", 1),
                MergeToken("c", "x", 1),
                MergeToken("d", "x", 1),
            ],
            [MergeTransition("x", 1, "y", 2)],
            max_merges=1,
        )
        self.assertEqual(result["merge_count"], 1)
        self.assertEqual(len(result["rounds"]), 1)


if __name__ == "__main__":
    unittest.main()
