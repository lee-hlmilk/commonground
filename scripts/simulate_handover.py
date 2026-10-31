"""CLI simulator and batch evaluator for the Challenge #2 Multi-Agent ATM Framework.

Examples:
  # Run all 5 preset stories (Agreement, Fuel Refusal, Drop Connection, Turn 3 Fallback, Late Inbound):
  .venv/bin/python -m scripts.simulate_handover

  # Simulate a specific preset or 2015 flight row in dev, jev, claude, jev_claude_cascade, or fallback mode:
  .venv/bin/python -m scripts.simulate_handover --incident story-weather-agreement --mode dev

  # Override scenario parameters interactively from the CLI:
  .venv/bin/python -m scripts.simulate_handover --incident story-weather-agreement --wake-gap 15 --fuel 12 --taxiway 10

  # Run batch evaluation (Model Off vs Model On) and save to data/handover_evaluation.json:
  .venv/bin/python -m scripts.simulate_handover --batch --off-limit 100 --on-limit 25
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import Settings
from backend.data import DataRepository, numeric
from backend.handover import (
    ROLES,
    HandoverScenarioCatalog,
    apply_controller_decision,
    evaluate_handover_batch,
    run_handover_negotiation,
)


class FastCSVDataAdapter:
    """Instant streaming CSV adapter when SQLite cache is not yet pre-indexed."""

    def __init__(self, root: Path):
        import csv
        import hashlib
        from datetime import date
        self.root = root
        self.paths = {name: root / f"{name}.csv" for name in ("flights", "airlines", "airports")}
        self.checksums = {name: "streaming-sha256" for name in self.paths}
        self.airlines = DataRepository._read_lookup(self.paths["airlines"])
        self.airports = DataRepository._read_lookup(self.paths["airports"])
        self._rows: list[dict] = []
        self._by_id: dict[str, dict] = {}
        with self.paths["flights"].open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, 1):
                if row.get("CANCELLED") == "1" or row.get("DIVERTED") == "1":
                    continue
                wx = numeric(row.get("WEATHER_DELAY")) or 0
                late = numeric(row.get("LATE_AIRCRAFT_DELAY")) or 0
                if not (wx > 15 or late > 20):
                    continue
                day = date(int(row["YEAR"]), int(row["MONTH"]), int(row["DAY"])).isoformat()
                carrier = row["AIRLINE"]
                hist = {k.lower(): (v if k in ("AIRLINE", "FLIGHT_NUMBER", "TAIL_NUMBER", "ORIGIN_AIRPORT", "DESTINATION_AIRPORT", "SCHEDULED_DEPARTURE", "SCHEDULED_ARRIVAL", "WHEELS_ON", "ARRIVAL_TIME", "DEPARTURE_TIME") else numeric(v)) for k, v in row.items()}
                fid = f"flight-{idx}"
                enriched = {
                    "id": fid,
                    "date": day,
                    "callsign": carrier + row["FLIGHT_NUMBER"],
                    "airline": {"code": carrier, "name": self.airlines.get(carrier, {}).get("AIRLINE", carrier)},
                    "origin": self._airport(row["ORIGIN_AIRPORT"]),
                    "destination": self._airport(row["DESTINATION_AIRPORT"]),
                    "tail_number": row["TAIL_NUMBER"],
                    "historical": hist,
                    "qualifies": True,
                    "source": {"file": "flights.csv", "record": idx, "line": idx + 1},
                }
                self._rows.append(enriched)
                self._by_id[fid] = enriched
                if len(self._rows) >= 60:
                    break

    def _airport(self, code: str) -> dict:
        r = self.airports.get(code, {})
        return {"code": code, "name": r.get("AIRPORT", code), "city": r.get("CITY"), "state": r.get("STATE"), "country": r.get("COUNTRY")}

    def get_flight(self, flight_id: str) -> dict | None:
        return self._by_id.get(flight_id)

    def search_flights(self, filters: dict | None = None, limit: int = 50) -> list[dict]:
        filters = filters or {}
        out = []
        for r in self._rows:
            if filters.get("destination") and r["destination"]["code"] != filters["destination"]:
                continue
            if filters.get("date") and r["date"] != filters["date"]:
                continue
            if filters.get("weather_delay_min") is not None and (r["historical"].get("weather_delay") or 0) <= float(filters["weather_delay_min"]):
                continue
            if filters.get("late_aircraft_delay_min") is not None and (r["historical"].get("late_aircraft_delay") or 0) <= float(filters["late_aircraft_delay_min"]):
                continue
            out.append(r)
            if len(out) >= limit:
                break
        return out


def print_run_trace(run: dict) -> None:
    inc = run["incident"]
    sf = inc["shared_facts"]
    wb = inc["workbook_airport_limits"]
    rfp = run["revised_flight_plan"]
    print("=" * 96)
    print(f"INCIDENT : {inc['name']} [{inc['id']}]")
    print(f"SITUATION: {inc['situation_type']} | {sf['cause_minute_label']}")
    print(
        f"FLIGHT   : {sf['callsign']} ({sf['origin']['code']} -> {sf['destination']['code']}) "
        f"| Sched Arr: {sf['scheduled_arrival']} | Dist: {sf['distance_miles']} mi ({sf['aircraft_category']})"
    )
    print(
        f"AIRPORT  : {wb['iata_code']} ({wb['size_class']}) | Runways: {wb['active_runways']} "
        f"(max {wb['max_runway_ops_per_hour']} ops/hr) | Gates: {wb['gates']} ({wb['spare_gates_at_any_time']} spare)"
    )
    print(
        f"MODE     : {run['mode']} | STATUS: {run['status'].upper()} "
        f"| TURNS USED: {run['turns_used']} / {run['max_turns']}"
    )
    print("-" * 96)

    if run.get("model_skipped"):
        print("PRE-CHECK SAFETY REFUSAL (Model Skipped):")
        print(f"  {run['summary_banner']}")
        for c in run["final_check"]["checks"]:
            status = "PASS" if c["passed"] else "FAIL"
            print(f"  [{status}] {c['label']:42s} | margin = {c['margin_minutes']:+3d} min | {c['detail']}")
    else:
        for t in run["turns"]:
            fb_reason = f" | Trigger: {t['fallback_trigger_reason']}" if t.get("fallback_trigger_reason") else ""
            print(f"\n--- TURN {t['turn']} ({t['kind']}{fb_reason}) ---")
            role_decisions = t.get("role_decisions", {})
            for msg in t["structured_messages"]:
                role = msg["sender_role"]
                dom = msg["dominance_evaluation"]
                rd = role_decisions.get(role, {})
                usage = rd.get("usage", {})
                model_str = f"{msg['provider']}:{usage.get('model', 'n/a')}"
                tok_str = f"in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)} {usage.get('latency_seconds', 0.0):.2f}s"
                probs_str = ", ".join(f"{k}:{v:.2f}" for k, v in msg["probabilities"].items())
                commit_badge = "COMMITTED" if dom["committed"] else "ABSTAINED->FALLBACK"
                if t["kind"] == "published_fallback":
                    commit_badge = "TURN3_FALLBACK"
                print(
                    f"  [MSG {msg['message_id']}] {role.upper():6s} ({model_str} | {tok_str}) -> '{msg['selected_action_label']}' "
                    f"[{msg['selected_action_id']}] ({commit_badge} | p_top={dom['top_probability']:.2f}, "
                    f"gap=+{dom['margin_over_runner_up']:.2f}) probs=[{probs_str}]"
                )
                if rd.get("cascade_from"):
                    p_dom = rd.get("primary_jev_dominance")
                    if p_dom:
                        print(f"           [CASCADE] Jev abstained (p_top={p_dom.get('top_probability', 0):.2f}, gap=+{p_dom.get('margin_over_runner_up', 0):.2f}) -> escalated to Claude")
                    else:
                        print(f"           [CASCADE] {rd['cascade_from']} -> escalated to Claude")
                if msg.get("pruned_menu_actions"):
                    print(f"           pruned: {list(msg['pruned_menu_actions'].keys())}")
                print(f"           stmt  : {msg['statement']}")
            chk = t["check"]
            print(
                f"  CHECKER: valid={chk['valid']} | implied_slip=+{chk['implied_slip_min']} min "
                f"| gate={chk['selected_gate']['gate_id'] if chk.get('selected_gate') else 'N/A'}"
            )
            for c in chk["checks"]:
                status = "PASS" if c["passed"] else "FAIL"
                waived = " (WAIVED BY PILOT)" if c.get("waived_by_pilot") else ""
                print(f"    [{status}] {c['label']:42s} | margin = {c['margin_minutes']:+3d} min{waived}")

    print("-" * 96)
    print("REVISED FLIGHT PLAN OUTPUT:")
    slip_str = f"+{rfp['arrival_slip_minutes']} min" if rfp["arrival_slip_minutes"] is not None else "N/A"
    print(
        f"  Disposition : {rfp['flight_plan_disposition']} | "
        f"Sched Arrival: {rfp['scheduled_arrival_hhmm']} -> Revised Arrival: {rfp['revised_arrival_hhmm'] or 'REFUSED/DIVERTED'} "
        f"(Slip: {slip_str})"
    )
    rev_gate = rfp["revised_gate"]["gate_id"] if rfp.get("revised_gate") else "NONE"
    print(
        f"  Gate Plan   : {rfp['original_assigned_gate']} -> {rev_gate} | "
        f"Connection: {rfp['passenger_connection_status']}"
    )
    if rfp.get("fuel_plan"):
        fp = rfp["fuel_plan"]
        print(
            f"  Fuel Plan   : {fp['aircraft_body_class']} ({fp['holding_burn_rate_kg_per_hour']} kg/hr) | "
            f"Hold Burn: {fp['planned_airborne_hold_min']} min ({fp['planned_airborne_hold_burn_kg']} kg) | "
            f"Spare Above 30m Final Reserve: {fp['remaining_spare_fuel_above_reserve_min']:+d} min "
            f"({fp['remaining_spare_fuel_above_reserve_kg']:+d} kg)"
        )
    print(f"SUMMARY  : {run['summary_banner']}")
    print(
        f"EVAL BG  : Historical 2015 ARRIVAL_DELAY = {inc['evaluation_background']['historical_arrival_delay_min']} min "
        "(background context only, withheld from roles)"
    )
    print("=" * 96)


async def async_main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incident", type=str, default=None, help="Preset ID or flight-<record> ID")
    parser.add_argument(
        "--mode",
        choices=("dev", "jev", "claude", "jev_claude_cascade", "fallback"),
        default="dev",
        help="Execution mode for role decisions",
    )
    parser.add_argument("--wake-gap", type=int, default=None, help="Override required wake gap (min)")
    parser.add_argument("--fuel", type=int, default=None, help="Override available holding fuel above 30m reserve (min)")
    parser.add_argument("--taxiway", type=int, default=None, help="Override taxiway closed until (min)")
    parser.add_argument("--conn-limit", type=int, default=None, help="Override max connection slip (min)")
    parser.add_argument("--batch", action="store_true", help="Run Model-Off + Model-On batch evaluation")
    parser.add_argument("--off-limit", type=int, default=100, help="Number of rows for Model-Off batch")
    parser.add_argument("--on-limit", type=int, default=25, help="Number of rows for Model-On batch")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "handover_evaluation.json",
        help="Output path for batch evaluation JSON",
    )
    args = parser.parse_args()

    settings = Settings()
    db_file = settings.database_path()
    data = DataRepository(ROOT, db_file) if db_file.exists() else FastCSVDataAdapter(ROOT)
    catalog = HandoverScenarioCatalog(data)  # type: ignore[arg-type]

    if args.batch:
        report = await evaluate_handover_batch(
            catalog,
            settings,
            model_off_limit=args.off_limit,
            model_on_limit=args.on_limit,
            model_on_mode=args.mode if args.mode != "fallback" else "dev",
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return

    overrides = {
        k: v
        for k, v in {
            "wake_gap_min": args.wake_gap,
            "available_hold_fuel_min": args.fuel,
            "taxiway_closed_until_min": args.taxiway,
            "conn_max_slip_min": args.conn_limit,
        }.items()
        if v is not None
    }

    if args.incident:
        inc = catalog.get_incident(args.incident)
        if overrides:
            inc = catalog.apply_overrides(inc, overrides)
        run = await run_handover_negotiation(inc, settings, mode=args.mode)
        print_run_trace(run)
        return

    for preset in catalog.list_presets():
        inc = catalog.get_incident(preset["id"])
        if overrides:
            inc = catalog.apply_overrides(inc, overrides)
        run = await run_handover_negotiation(inc, settings, mode=args.mode)
        action = "accept_refusal" if run["status"] == "refused" else "accept"
        run = apply_controller_decision(run, decision_action=action, controller_note="Demo controller sign-off")
        print_run_trace(run)
        print()


if __name__ == "__main__":
    asyncio.run(async_main())
