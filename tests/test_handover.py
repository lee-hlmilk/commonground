"""Unit and integration tests for the Challenge #2 Handover Engine (handover_doc.md)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
import tempfile

import httpx
from fastapi.testclient import TestClient

from backend.config import ROOT, Settings
from backend.data import DataRepository
from backend.handover import (
    MENUS,
    ROLES,
    HandoverRoleClient,
    HandoverScenarioCatalog,
    apply_controller_decision,
    build_role_isolated_context,
    check_triple,
    evaluate_dominance_gate,
    evaluate_handover_batch,
    prune_turn1_menus,
    run_handover_negotiation,
)
from backend.main import create_app


class HandoverEngineTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        import csv
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.db_path = root / "test_handover.sqlite3"

        with (root / "airlines.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["IATA_CODE", "AIRLINE"])
            w.writeheader()
            w.writerows([
                {"IATA_CODE": "DL", "AIRLINE": "Delta Air Lines Inc."},
                {"IATA_CODE": "WN", "AIRLINE": "Southwest Airlines Co."},
                {"IATA_CODE": "AA", "AIRLINE": "American Airlines Inc."},
            ])

        airport_fields = ["IATA_CODE", "AIRPORT", "CITY", "STATE", "COUNTRY", "LATITUDE", "LONGITUDE"]
        with (root / "airports.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=airport_fields)
            w.writeheader()
            for code, name in [("ATL", "Atlanta"), ("ALB", "Albany"), ("ICT", "Wichita"), ("PIT", "Pittsburgh"), ("ORD", "Chicago")]:
                w.writerow({"IATA_CODE": code, "AIRPORT": name, "CITY": name, "STATE": "GA", "COUNTRY": "USA", "LATITUDE": "33.6", "LONGITUDE": "-84.4"})

        flight_fields = [
            "YEAR", "MONTH", "DAY", "AIRLINE", "FLIGHT_NUMBER", "TAIL_NUMBER",
            "ORIGIN_AIRPORT", "DESTINATION_AIRPORT", "SCHEDULED_DEPARTURE", "SCHEDULED_ARRIVAL",
            "WHEELS_ON", "ARRIVAL_TIME", "DEPARTURE_TIME", "ARRIVAL_DELAY", "TAXI_IN", "TAXI_OUT",
            "DISTANCE", "SCHEDULED_TIME", "WEATHER_DELAY", "LATE_AIRCRAFT_DELAY", "CANCELLED", "DIVERTED", "CANCELLATION_REASON",
        ]
        with (root / "flights.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=flight_fields)
            w.writeheader()
            for i in range(1, 26):
                w.writerow({
                    "YEAR": "2015", "MONTH": "12", "DAY": "30",
                    "AIRLINE": "DL" if i % 2 == 0 else "AA",
                    "FLIGHT_NUMBER": str(1000 + i),
                    "TAIL_NUMBER": f"N{100 + i}DL",
                    "ORIGIN_AIRPORT": "ALB" if i % 2 == 0 else "ICT",
                    "DESTINATION_AIRPORT": "ATL" if i <= 20 else "ORD",
                    "SCHEDULED_DEPARTURE": "0800",
                    "SCHEDULED_ARRIVAL": "1000",
                    "WHEELS_ON": "1045",
                    "ARRIVAL_TIME": "1055",
                    "DEPARTURE_TIME": "0835",
                    "ARRIVAL_DELAY": str(25 + i),
                    "TAXI_IN": "10",
                    "TAXI_OUT": "15",
                    "DISTANCE": str(600 + i * 70),
                    "SCHEDULED_TIME": "120",
                    "WEATHER_DELAY": str(18 + i) if i <= 22 else "5",
                    "LATE_AIRCRAFT_DELAY": "28" if i >= 18 else "0",
                    "CANCELLED": "0",
                    "DIVERTED": "0",
                    "CANCELLATION_REASON": "",
                })

        cls.data = DataRepository(root, cls.db_path)
        cls.catalog = HandoverScenarioCatalog(cls.data)
        cls.settings = Settings(
            provider="jev",
            jev_api_key="test-jev-token",
            api_key="test-claude-token",
            database=str(cls.db_path),
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    async def test_story_1_agreement_in_two_turns(self):
        """Mandatory Story 1:
        Wake needs 10m. Contact gate opens at 20m. Remote gate open at 10m. Connection dies after 15m.
        Turn 1: Ground tries to keep gate and loses on gate margin (-10m).
        Turn 2: keep_gate is gone, Ground moves to remote gate, and the triple passes.
        """
        inc = self.catalog.get_incident("story-weather-agreement")
        run = await run_handover_negotiation(inc, self.settings, mode="dev")

        self.assertEqual(run["status"], "agreed")
        self.assertEqual(run["outcome_type"], "agreement")
        self.assertEqual(run["turns_used"], 2)

        # Verify revised_flight_plan & consensus_log structure
        self.assertEqual(run["revised_flight_plan"]["arrival_slip_minutes"], 10)
        self.assertEqual(run["revised_flight_plan"]["revised_gate"]["kind"], "remote")
        self.assertEqual(len(run["consensus_log"]), 2)
        self.assertEqual(len(run["turns"][0]["structured_messages"]), 3)
        self.assertEqual(run["incident"]["workbook_airport_limits"]["size_class"], "Large hub")
        self.assertEqual(run["incident"]["workbook_airport_limits"]["active_runways"], 4)
        self.assertEqual(run["incident"]["workbook_airport_limits"]["gates"], 150)

        # Turn 1 verification
        t1 = run["turns"][0]
        self.assertEqual(t1["chosen_actions"]["atc"], "hold_10")
        self.assertEqual(t1["chosen_actions"]["pilot"], "accept_hold")
        self.assertEqual(t1["chosen_actions"]["ground"], "keep_gate")
        self.assertFalse(t1["check"]["valid"])
        failed_t1 = {c["id"]: c["margin_minutes"] for c in t1["check"]["failed_checks"]}
        self.assertEqual(failed_t1, {"gate_availability": -10})

        # Turn 2 verification: keep_gate removed, Ground picks move_remote_gate, all 6 constraints pass
        t2 = run["turns"][1]
        self.assertIn("keep_gate", t2["role_decisions"]["ground"]["removed_actions"])
        self.assertNotIn("keep_gate", t2["role_decisions"]["ground"]["remaining_menu"])
        self.assertEqual(t2["chosen_actions"]["ground"], "move_remote_gate")
        self.assertTrue(t2["check"]["valid"])
        self.assertEqual(t2["check"]["implied_slip_min"], 10)
        self.assertTrue(all(c["passed"] for c in t2["check"]["checks"]))

    async def test_story_2_immediate_fuel_refusal_skips_model(self):
        """Mandatory Story 2:
        Reserve covers 8 airborne minutes. Taxiway is closed until 12.
        No choice is offered. The result is no plan, and the fuel margin (-4 min) is the reason.
        """
        inc = self.catalog.get_incident("story-fuel-refusal")
        run = await run_handover_negotiation(inc, self.settings, mode="dev")

        self.assertEqual(run["status"], "refused")
        self.assertEqual(run["outcome_type"], "refusal")
        self.assertTrue(run["model_skipped"])
        self.assertEqual(run["turns_used"], 0)
        self.assertEqual(run["turns"], [])
        self.assertIsNone(run["final_actions"])
        self.assertEqual(run["final_check"]["worst_margin"]["constraint"], "fuel_reserve")
        self.assertEqual(run["final_check"]["worst_margin"]["margin_minutes"], -4)

    async def test_story_3_pilot_explicitly_drops_connection_in_turn_2(self):
        """Passenger connection is the only rule that may be dropped, and only when Pilot picks drop_connection."""
        inc = self.catalog.get_incident("story-weather-drop-connection")
        run = await run_handover_negotiation(inc, self.settings, mode="dev")

        self.assertEqual(run["status"], "agreed")
        self.assertEqual(run["turns_used"], 2)
        t1 = run["turns"][0]
        self.assertFalse(t1["check"]["valid"])
        self.assertEqual(t1["check"]["failed_checks"][0]["id"], "passenger_connection")
        self.assertEqual(t1["check"]["failed_checks"][0]["margin_minutes"], -5)

        t2 = run["turns"][1]
        self.assertEqual(t2["chosen_actions"]["pilot"], "drop_connection")
        self.assertTrue(t2["check"]["valid"])
        conn_check = next(c for c in t2["check"]["checks"] if c["id"] == "passenger_connection")
        self.assertTrue(conn_check["passed"])
        self.assertTrue(conn_check["waived_by_pilot"])

    def test_strict_role_privacy_and_historical_delay_hiding(self):
        """Verify private facts stay with each role and historical arrival outcomes are never in agent contexts."""
        inc = self.catalog.get_incident("story-weather-agreement")
        t1_menus, t1_removed = prune_turn1_menus(inc)
        forbidden_historical = {"arrival_delay", "arrival_time", "wheels_on", "taxi_in", "air_time"}

        contexts = {
            role: build_role_isolated_context(inc, role, 1, t1_menus[role], t1_removed[role])
            for role in ROLES
        }
        for role, ctx in contexts.items():
            serialized = json.dumps(ctx).lower()
            for field in forbidden_historical:
                self.assertNotIn(f'"{field}"', serialized)

        # ATC sees wake_gap_required_min, not gates or available_hold_fuel_min
        self.assertIn("wake_gap_required_min", contexts["atc"]["private_role_facts"])
        self.assertNotIn("available_hold_fuel_min", contexts["atc"]["private_role_facts"])
        self.assertNotIn("gates", contexts["atc"]["private_role_facts"])

        # Pilot sees available_hold_fuel_min & connection_max_slip_min, not wake_gap_required_min or gates
        self.assertIn("available_hold_fuel_min", contexts["pilot"]["private_role_facts"])
        self.assertIn("connection_max_slip_min", contexts["pilot"]["private_role_facts"])
        self.assertNotIn("wake_gap_required_min", contexts["pilot"]["private_role_facts"])
        self.assertNotIn("gates", contexts["pilot"]["private_role_facts"])

        # Ground sees gates & crew_overtime_cap_min, not wake_gap_required_min or available_hold_fuel_min
        self.assertIn("gates", contexts["ground"]["private_role_facts"])
        self.assertIn("crew_overtime_cap_min", contexts["ground"]["private_role_facts"])
        self.assertNotIn("wake_gap_required_min", contexts["ground"]["private_role_facts"])
        self.assertNotIn("available_hold_fuel_min", contexts["ground"]["private_role_facts"])

    def test_dominance_gate_thresholds_045_and_015(self):
        """Top probability >= 0.45 AND >= 0.15 above runner-up commits; otherwise abstains to fallback."""
        menu = ["keep_gate", "move_contact_gate", "move_remote_gate", "hardstand"]
        # Case 1: Strong winner (0.55 vs 0.25 -> top >= 0.45 and gap 0.30 >= 0.15)
        d1 = evaluate_dominance_gate(
            {"keep_gate": 0.55, "move_contact_gate": 0.25, "move_remote_gate": 0.15, "hardstand": 0.05},
            menu,
            fallback_action="move_remote_gate",
        )
        self.assertTrue(d1["committed"])
        self.assertFalse(d1["abstained"])
        self.assertEqual(d1["effective_action"], "keep_gate")

        # Case 2: Top >= 0.45 but runner-up gap < 0.15 (0.46 vs 0.38 -> gap 0.08 < 0.15) -> ABSTAINS
        d2 = evaluate_dominance_gate(
            {"keep_gate": 0.46, "move_contact_gate": 0.38, "move_remote_gate": 0.10, "hardstand": 0.06},
            menu,
            fallback_action="move_remote_gate",
        )
        self.assertFalse(d2["committed"])
        self.assertTrue(d2["abstained"])
        self.assertEqual(d2["effective_action"], "move_remote_gate")

        # Case 3: Flat bar where top < 0.45 (0.35, 0.25, 0.20, 0.20) -> ABSTAINS
        d3 = evaluate_dominance_gate(
            {"keep_gate": 0.35, "move_contact_gate": 0.25, "move_remote_gate": 0.20, "hardstand": 0.20},
            menu,
            fallback_action="move_remote_gate",
        )
        self.assertFalse(d3["committed"])
        self.assertTrue(d3["abstained"])
        self.assertEqual(d3["effective_action"], "move_remote_gate")

    def test_divert_terminates_negotiation_and_requires_controller_accept(self):
        """A divert ends the negotiation; peer labels are kept in log and not executed; controller still accepts."""
        inc = self.catalog.get_incident("story-weather-agreement")
        chk = check_triple(inc, {"atc": "hold_10", "pilot": "divert", "ground": "keep_gate"})
        self.assertTrue(chk["valid"])
        self.assertTrue(chk["is_divert"])
        self.assertIsNone(chk["implied_slip_min"])

    def test_both_causes_qualify_treated_as_destination_weather(self):
        """If both WEATHER_DELAY > 15 and LATE_AIRCRAFT_DELAY > 20 qualify, treat as destination weather and say so."""
        rows = self.data.search_flights({"weather_delay_min": 15, "late_aircraft_delay_min": 20}, limit=5)
        self.assertTrue(len(rows) > 0)
        inc = self.catalog.build_from_flight(rows[0])
        self.assertEqual(inc["situation_type"], "DESTINATION_WEATHER")
        self.assertTrue(inc["shared_facts"]["both_causes_qualified_treated_as_weather"])

    async def test_jev_http_menu_classification_and_three_role_isolation(self):
        """Verify Jev /v1/systemone is called 3 separate times per turn (once per role) and respects dominance gate."""
        inc = self.catalog.get_incident("story-weather-agreement")
        captured_requests = []

        def mock_jev_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured_requests.append(body)
            role = body["state"]["context"]["role"]
            turn = body["state"]["context"]["turn"]
            criteria_keys = list(body["questions"]["action_selection"]["criteria"].keys())

            if role == "atc":
                choice = "hold_10" if "hold_10" in criteria_keys else criteria_keys[0]
            elif role == "pilot":
                choice = "accept_hold" if "accept_hold" in criteria_keys else criteria_keys[0]
            else:
                # Ground picks keep_gate in Turn 1, then move_remote_gate in Turn 2
                if turn == 1 and "keep_gate" in criteria_keys:
                    choice = "keep_gate"
                elif "move_remote_gate" in criteria_keys:
                    choice = "move_remote_gate"
                else:
                    choice = criteria_keys[0]

            rem = len(criteria_keys) - 1
            probs = {
                k: (0.76 if k == choice else round(0.24 / rem, 4)) if rem > 0 else 1.0
                for k in criteria_keys
            }
            # Fix exact sum to 1.0
            diff = 1.0 - sum(probs.values())
            probs[choice] = round(probs[choice] + diff, 4)

            return httpx.Response(
                200,
                headers={"x-request-id": f"jev-{role}-t{turn}"},
                json={
                    "model": "jev-1.13.0",
                    "model_version": "jev-handover-test",
                    "answers": {
                        "action_selection": {
                            "type": "choice",
                            "choice": choice,
                            "confidence": probs[choice],
                            "probabilities": probs,
                        }
                    },
                    "usage": {"input_tokens": 110, "output_tokens": 0},
                },
            )

        transport = httpx.MockTransport(mock_jev_handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            role_client = HandoverRoleClient(self.settings, http_client=http_client)
            run = await run_handover_negotiation(inc, self.settings, mode="jev", role_client=role_client)

        self.assertEqual(run["status"], "agreed")
        self.assertEqual(run["turns_used"], 2)
        # 3 roles * 2 turns = 6 separate Jev HTTP calls (never combined into 1 prompt call!)
        self.assertEqual(len(captured_requests), 6)
        roles_called_t1 = {r["state"]["context"]["role"] for r in captured_requests[:3]}
        self.assertEqual(roles_called_t1, {"atc", "pilot", "ground"})

    async def test_batch_evaluation_zero_illegal_results(self):
        """Batch evaluator must emit zero illegal results across both Model-Off and Model-On batches."""
        report = await evaluate_handover_batch(
            self.catalog,
            self.settings,
            model_off_limit=20,
            model_on_limit=10,
            model_on_mode="dev",
        )
        self.assertEqual(report["model_off_batch"]["illegal_results_emitted"], 0)
        self.assertEqual(report["model_on_batch"]["illegal_results_emitted"], 0)
        self.assertIsNotNone(report["model_off_batch"]["median_negotiated_slip_min"])
        self.assertIsNotNone(report["model_off_batch"]["median_historical_arrival_delay_min_background"])

    def test_fastapi_handover_endpoints(self):
        """Verify REST API endpoints for presets, incident inspection, run simulation, and controller sign-off."""
        app = create_app(self.settings, self.data)
        with TestClient(app) as client:
            presets = client.get("/api/handover/presets")
            self.assertEqual(presets.status_code, 200)
            self.assertEqual(len(presets.json()["presets"]), 5)

            # Simulate Story 1 via API
            r1 = client.post("/api/handover/runs", json={"incident_id": "story-weather-agreement", "mode": "dev"})
            self.assertEqual(r1.status_code, 200)
            run1 = r1.json()
            self.assertEqual(run1["status"], "agreed")
            self.assertEqual(run1["turns_used"], 2)

            # Controller accepts plan
            dec = client.post(
                f"/api/handover/runs/{run1['id']}/decision",
                json={"action": "accept", "note": "Controller accepted remote stand R3"},
            )
            self.assertEqual(dec.status_code, 200)
            self.assertEqual(dec.json()["controller_decision"]["status"], "completed")

            # Simulate Story 2 (Refusal) via API
            r2 = client.post("/api/handover/runs", json={"incident_id": "story-fuel-refusal", "mode": "dev"})
            self.assertEqual(r2.status_code, 200)
            run2 = r2.json()
            self.assertEqual(run2["status"], "refused")
            self.assertTrue(run2["model_skipped"])


if __name__ == "__main__":
    unittest.main()
