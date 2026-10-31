import unittest

from scripts.evaluate import summarise, vary_scenario


class EvaluationTests(unittest.TestCase):
    def test_variations_do_not_change_the_original(self):
        original = {"id": "demo", "flights": [{"reference_arrival": 10, "latest_arrival": 30, "waiting_limit": 20, "turnaround": 30}], "resources": {"runways": [{"separation": 3}], "handling_teams": 2, "stands": [{"available_from": 0}]}}
        tighter, label = vary_scenario(original, 4)
        self.assertEqual(tighter["flights"][0]["waiting_limit"], 15)
        self.assertEqual(original["flights"][0]["waiting_limit"], 20)
        resource, _ = vary_scenario(original, 2)
        self.assertEqual(resource["resources"]["handling_teams"], 1)
        self.assertEqual(resource["flights"][0]["turnaround"], 35)

    def test_no_missing_baseline_is_counted_as_zero_delay(self):
        result = {"selected_metrics": {"airborne_wait": 9, "total_delay": 15}, "baseline_metrics": None, "physical_feasible": True, "fully_validated": True, "approval_blocked": False, "search_status": "feasible", "physical_violations": 0}
        summary = summarise([result])
        self.assertEqual(summary["comparable_case_count"], 0)
        self.assertEqual(summary["candidate_vs_fcfs_total_delay_difference"], 0)

    def test_unknown_is_not_called_infeasible(self):
        result = {"selected_metrics": None, "baseline_metrics": None, "physical_feasible": False, "fully_validated": False, "approval_blocked": False, "search_status": "search_timeout", "physical_violations": 0}
        summary = summarise([result])
        self.assertEqual(summary["proven_infeasible_count"], 0)
        self.assertEqual(summary["search_timeout_count"], 1)


if __name__ == "__main__":
    unittest.main()
