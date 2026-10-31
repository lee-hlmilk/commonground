"""Safety checks are deliberately tested with altered schedules."""
import copy
import importlib.util
import unittest

from backend.solver import calculate_metrics, deterministic_fallback, generate_candidates_with_status, validate_plan


def scenario():
    return {
        "id": "test", "version": 1, "horizon": 120,
        "flights": [
            {"id": "A", "callsign": "Alpha", "reference_arrival": 10, "latest_arrival": 40, "waiting_limit": 30, "taxi_in": 5, "taxi_out": 5, "turnaround": 20, "scheduled_departure": 45, "compatible_stands": ["S1", "S2"]},
            {"id": "B", "callsign": "Bravo", "reference_arrival": 11, "latest_arrival": 40, "waiting_limit": 29, "taxi_in": 5, "taxi_out": 5, "turnaround": 20, "scheduled_departure": 46, "compatible_stands": ["S1", "S2"]},
        ],
        "resources": {"runways": [{"id": "R", "separation": 3, "closures": []}], "stands": [{"id": "S1", "available_from": 0}, {"id": "S2", "available_from": 0}], "handling_teams": 2},
        "approvals": [],
    }


def feasible_plan(s):
    assignments = [
        {"flight_id": "A", "arrival": 10, "in_block": 15, "off_block": 35, "departure": 40, "stand_id": "S1", "runway_id": "R"},
        {"flight_id": "B", "arrival": 13, "in_block": 18, "off_block": 38, "departure": 43, "stand_id": "S2", "runway_id": "R"},
    ]
    return {"id": "test-plan", "scenario_version": 1, "assignments": assignments, "metrics": calculate_metrics(s, assignments), "valid": True}


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.s = scenario()
        self.p = feasible_plan(self.s)

    def assertRejected(self, check_id):
        result = validate_plan(self.s, self.p)
        self.assertFalse(result["valid"])
        self.assertTrue(any(check_id in c["id"] and not c["passed"] for c in result["checks"]))

    def test_feasible_plan(self):
        self.assertTrue(validate_plan(self.s, self.p)["valid"])

    def test_overlap_cannot_hide_behind_valid_flag(self):
        self.p["assignments"][1]["stand_id"] = "S1"
        self.assertRejected("stand:A:B")

    def test_runway_separation(self):
        self.p["assignments"][1].update(arrival=11, in_block=16)
        self.assertRejected("separation")

    def test_closure(self):
        self.s["resources"]["runways"][0]["closures"] = [{"start": 10, "end": 20}]
        self.assertRejected("closure")

    def test_waiting_limit(self):
        self.s["flights"][1]["waiting_limit"] = 1
        self.assertRejected("waiting")

    def test_missing_approval(self):
        self.s["approvals"] = [{"id": "dispatch", "label": "Dispatch release", "status": "pending"}]
        self.assertRejected("approval:dispatch")

    def test_stale_version(self):
        self.s["version"] = 2
        self.assertRejected("scenario_version")

    def test_handling_capacity(self):
        self.s["resources"]["handling_teams"] = 1
        self.assertRejected("handling_capacity")

    def test_duplicate_flight(self):
        self.p["assignments"][1]["flight_id"] = "A"
        self.assertRejected("flight_coverage")

    def test_false_metric(self):
        self.p["metrics"]["airborne_wait"] = -100
        self.assertRejected("metrics")

    def test_invalid_integer(self):
        self.p["assignments"][0]["arrival"] = True
        self.assertRejected("assignment_values")

    def test_malformed_resource_is_rejected_without_exception(self):
        self.p["assignments"][0]["stand_id"] = ["S1"]
        self.assertRejected("assignment_values")

    def test_surface_holding_limit(self):
        self.s["flights"][0]["max_surface_wait"] = 2
        self.p["assignments"][0]["in_block"] += 3
        self.assertRejected("taxi")

    def test_surface_holding_capacity(self):
        for f in self.s["flights"]:
            f["max_surface_wait"] = 10
        self.s["resources"]["surface_holding_capacity"] = 1
        self.p["assignments"][0].update(in_block=22, off_block=42, departure=47)
        self.p["assignments"][1].update(in_block=23, off_block=43, departure=50)
        self.p["metrics"] = calculate_metrics(self.s, self.p["assignments"])
        self.assertRejected("surface_holding_capacity")


@unittest.skipUnless(importlib.util.find_spec("ortools"), "OR-Tools not installed")
class SchedulerTests(unittest.TestCase):
    def test_candidates_are_distinct_and_safe(self):
        s = scenario()
        result = generate_candidates_with_status(s, max_candidates=4, time_limit=3)
        self.assertEqual(len(result["plans"]), 4)
        self.assertEqual(len({p["id"] for p in result["plans"]}), 4)
        self.assertTrue(all(validate_plan(s, p)["valid"] for p in result["plans"]))

    def test_impossible_wait_is_proven_infeasible(self):
        s = scenario()
        s["resources"]["runways"][0]["closures"] = [{"start": 0, "end": 50}]
        result = generate_candidates_with_status(s, time_limit=3)
        self.assertEqual(result["status"], "infeasible")
        self.assertEqual(result["plans"], [])

    def test_pending_approval_keeps_physical_options_but_blocks_fallback(self):
        s = scenario()
        s["approvals"] = [{"id": "dispatch", "status": "pending"}]
        result = generate_candidates_with_status(s, max_candidates=1, time_limit=3)
        self.assertEqual(len(result["plans"]), 1)
        self.assertFalse(result["plans"][0]["valid"])
        self.assertIsNone(deterministic_fallback(s))

    def test_fallback_has_no_consensus(self):
        s = scenario()
        p = deterministic_fallback(s)
        self.assertIsNotNone(p)
        self.assertFalse(p["consensus"])
        self.assertTrue(validate_plan(s, p)["valid"])

    def test_variable_flight_count(self):
        s = scenario()
        s["flights"] = s["flights"][:1]
        self.assertEqual(len(generate_candidates_with_status(s, max_candidates=1)["plans"][0]["assignments"]), 1)

    def test_surface_holding_resolves_stand_wait_without_airborne_delay(self):
        s = scenario()
        s["flights"] = s["flights"][:1]
        s["flights"][0].update(waiting_limit=0, latest_arrival=10, max_surface_wait=20)
        s["resources"]["stands"] = [{"id": "S1", "available_from": 30}]
        s["resources"]["surface_holding_capacity"] = 1
        s["flights"][0]["compatible_stands"] = ["S1"]
        p = generate_candidates_with_status(s, max_candidates=1)["plans"][0]
        self.assertTrue(p["valid"])
        self.assertEqual(p["assignments"][0]["arrival"], 10)
        self.assertEqual(p["assignments"][0]["in_block"], 30)
        self.assertEqual(p["metrics"]["surface_wait"], 15)
        self.assertEqual(p["metrics"]["airborne_wait"], 0)

    def test_zero_surface_capacity_prevents_waiting_for_gate(self):
        s = scenario()
        s["flights"] = s["flights"][:1]
        s["flights"][0].update(waiting_limit=0, latest_arrival=10, max_surface_wait=20, compatible_stands=["S1"])
        s["resources"]["stands"] = [{"id": "S1", "available_from": 30}]
        s["resources"]["surface_holding_capacity"] = 0
        self.assertEqual(generate_candidates_with_status(s, max_candidates=1)["status"], "infeasible")


if __name__ == "__main__":
    unittest.main()
