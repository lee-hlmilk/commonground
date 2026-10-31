"""Bounded scheduling and independent, non-LLM operational checks.

Times are integer minutes from the scenario clock. The supplied separation,
turnaround and aircraft limits belong to the simulation, not aviation guidance.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from itertools import combinations
from typing import Any


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def calculate_metrics(scenario: dict, assignments: list[dict]) -> dict:
    flights = {f["id"]: f for f in scenario["flights"]}
    shift_end = scenario["resources"].get("staff_shift_end", scenario.get("horizon", 120))
    return {
        "airborne_wait": sum(max(0, a["arrival"] - flights[a["flight_id"]]["reference_arrival"]) for a in assignments),
        "surface_wait": sum(max(0, a["in_block"] - a["arrival"] - flights[a["flight_id"]]["taxi_in"]) for a in assignments),
        "total_delay": sum(max(0, a["departure"] - flights[a["flight_id"]]["scheduled_departure"]) for a in assignments),
        "overtime": sum(max(0, a["off_block"] - shift_end) for a in assignments),
        "connection_exposure": sum(a["in_block"] > flights[a["flight_id"]]["connection_deadline"] for a in assignments if flights[a["flight_id"]].get("connection_deadline") is not None),
    }


def validate_plan(scenario: dict, plan: dict) -> dict:
    """Recompute checks from assignments; never trust an attached valid flag."""
    checks: list[dict] = []

    def check(key: str, label: str, passed: bool, detail: str) -> None:
        checks.append({"id": key, "label": label, "passed": bool(passed), "detail": detail})

    flights = {f["id"]: f for f in scenario.get("flights", [])}
    resources = scenario.get("resources", {})
    stands = {s["id"]: s for s in resources.get("stands", [])}
    runways = {r["id"]: r for r in resources.get("runways", [])}
    horizon = scenario.get("horizon", 120)
    check("scenario_version", "Current scenario version", _integer(plan.get("scenario_version")) and plan.get("scenario_version") == scenario.get("version"), "Commitments apply only to this exact scenario version.")
    assignments = plan.get("assignments")
    shape_ok = isinstance(assignments, list) and all(isinstance(a, dict) for a in assignments)
    check("assignment_shape", "Structured assignments", shape_ok, "Assignments must be a list of flight schedules.")
    if not shape_ok:
        return {"valid": False, "checks": checks}
    identifiers = [a.get("flight_id") for a in assignments]
    exact = len(identifiers) == len(flights) and len(set(str(i) for i in identifiers)) == len(identifiers) and set(str(i) for i in identifiers) == set(flights)
    check("flight_coverage", "Exactly one assignment per flight", exact, "No aircraft may be omitted, duplicated, or invented.")
    required_times = ("arrival", "in_block", "off_block", "departure")
    typed = all(all(_integer(a.get(k)) for k in required_times) and all(isinstance(a.get(k), str) for k in ("stand_id", "runway_id", "flight_id")) and a.get("stand_id") in stands and a.get("runway_id") in runways and a.get("flight_id") in flights for a in assignments)
    check("assignment_values", "Known resources and integer-minute times", typed, "Every flight, stand and runway must exist in the scenario.")
    if not typed or not exact:
        return {"valid": False, "checks": checks}

    for a in assignments:
        f = flights[a["flight_id"]]
        label = f.get("callsign", f["id"])
        prefix = f"flight:{f['id']}"
        limit = min(f["latest_arrival"], f["reference_arrival"] + f["waiting_limit"])
        check(prefix + ":waiting", f"{label}: waiting limit", f["reference_arrival"] <= a["arrival"] <= limit, f"Arrival {a['arrival']}; feasible window {f['reference_arrival']}–{limit} minutes.")
        taxi_complete = a["arrival"] + f["taxi_in"]
        max_surface_wait = f.get("max_surface_wait", 0)
        check(prefix + ":taxi", f"{label}: taxi and authorised surface holding", taxi_complete <= a["in_block"] <= taxi_complete + max_surface_wait, f"Gate arrival follows {f['taxi_in']} minutes taxi; up to {max_surface_wait} minutes additional wait is authorised in the simulated holding area.")
        check(prefix + ":turnaround", f"{label}: turnaround duration", a["off_block"] >= a["in_block"] + f["turnaround"], f"At least {f['turnaround']} minutes of handling are required.")
        check(prefix + ":departure", f"{label}: departure readiness", a["departure"] >= a["off_block"] + f["taxi_out"], "Departure cannot precede readiness and taxi-out.")
        check(prefix + ":horizon", f"{label}: simulation horizon", all(0 <= a[k] <= horizon for k in required_times), f"The scenario covers minutes 0–{horizon}.")
        check(prefix + ":stand", f"{label}: compatible and available stand", a["stand_id"] in f["compatible_stands"] and a["in_block"] >= stands[a["stand_id"]].get("available_from", 0), f"Stand {a['stand_id']} must be suitable and ready before entry.")

    for a, b in combinations(assignments, 2):
        overlap = a["stand_id"] == b["stand_id"] and a["in_block"] < b["off_block"] and b["in_block"] < a["off_block"]
        check(f"stand:{a['flight_id']}:{b['flight_id']}", "Non-overlapping stand occupancy", not overlap, f"{a['flight_id']} / {b['flight_id']}: stand occupation ends at off-block.")

    for rid, runway in runways.items():
        sep = runway["separation"]
        movements = sorted((a[k], a["flight_id"], k) for a in assignments if a["runway_id"] == rid for k in ("arrival", "departure"))
        for left, right in zip(movements, movements[1:]):
            check(f"separation:{rid}:{left[1]}:{left[2]}:{right[1]}:{right[2]}", f"{rid}: aircraft separation", right[0] - left[0] >= sep, f"{left[1]} {left[2]} at {left[0]}; {right[1]} {right[2]} at {right[0]}; minimum {sep} minutes.")
        for t, fid, kind in movements:
            closed = any(t < c["end"] and c["start"] < t + sep for c in runway.get("closures", []))
            check(f"closure:{rid}:{fid}:{kind}", f"{rid}: available for {fid} {kind}", not closed, f"Movement and its separation interval [{t}, {t + sep}) must not overlap a closure.")

    team_capacity = resources.get("handling_teams", 1)
    max_teams = 0
    for t in sorted({a[k] for a in assignments for k in ("in_block", "off_block")}):
        max_teams = max(max_teams, sum(a["in_block"] <= t < a["off_block"] for a in assignments))
    check("handling_capacity", "Handling team capacity", max_teams <= team_capacity, f"Peak demand {max_teams}; available teams {team_capacity}.")
    holding_capacity = resources.get("surface_holding_capacity", len(flights))
    holding_intervals = [(a["arrival"] + flights[a["flight_id"]]["taxi_in"], a["in_block"]) for a in assignments]
    holding_peak = max((sum(start <= t < end for start, end in holding_intervals) for t in {t for pair in holding_intervals for t in pair}), default=0)
    check("surface_holding_capacity", "Authorised surface holding capacity", holding_peak <= holding_capacity, f"Peak surface holding demand {holding_peak}; authorised positions {holding_capacity}. Holding is distinct from airborne delay.")
    for approval in scenario.get("approvals", []):
        check(f"approval:{approval['id']}", approval.get("label", approval["id"]), approval.get("status") == "confirmed", f"External approval status: {approval.get('status', 'missing')}.")

    metrics = calculate_metrics(scenario, assignments)
    if "metrics" in plan:
        check("metrics", "Recomputed operational metrics", plan["metrics"] == metrics, "Displayed metrics must match the actual assignments.")
    return {"valid": all(c["passed"] for c in checks), "checks": checks}


def _plan(scenario: dict, assignments: list[dict], kind: str = "candidate") -> dict:
    assignments.sort(key=lambda a: a["flight_id"])
    payload = json.dumps({"version": scenario["version"], "assignments": assignments}, sort_keys=True, separators=(",", ":"))
    plan = {"id": "plan-" + hashlib.sha256(payload.encode()).hexdigest()[:12], "scenario_version": scenario["version"], "assignments": assignments, "metrics": calculate_metrics(scenario, assignments), "kind": kind}
    plan.update(validate_plan(scenario, plan))
    return plan


def _build_model(scenario: dict, fcfs: bool = False):
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:
        raise RuntimeError("Scheduling requires OR-Tools. Install requirements.txt in the backend environment.") from exc
    model = cp_model.CpModel()
    horizon = int(scenario.get("horizon", 120))
    resources = scenario["resources"]
    flights = scenario["flights"]
    stands = resources["stands"]
    runways = resources["runways"]
    if not flights or not stands or not runways:
        raise ValueError("A scenario needs flights, stands, and at least one runway.")
    vars_by_id = {}
    stand_intervals = {s["id"]: [] for s in stands}
    runway_intervals = {r["id"]: [] for r in runways}
    handling = []
    surface_holding = []
    waits, delays, overtime = [], [], []
    shift_end = resources.get("staff_shift_end", horizon)
    signatures = []
    for index, flight in enumerate(flights):
        fid = flight["id"]
        low = max(0, flight["reference_arrival"])
        high = min(horizon, flight["latest_arrival"], flight["reference_arrival"] + flight["waiting_limit"])
        arrival = model.new_int_var(0, horizon, f"{fid}_arrival")
        model.add(arrival >= low)
        model.add(arrival <= high)
        in_block = model.new_int_var(0, horizon, f"{fid}_in_block")
        off_block = model.new_int_var(0, horizon, f"{fid}_off_block")
        departure = model.new_int_var(0, horizon, f"{fid}_departure")
        duration = model.new_int_var(flight["turnaround"], horizon, f"{fid}_handling_duration")
        taxi_complete = model.new_int_var(0, horizon + flight["taxi_in"], f"{fid}_taxi_complete")
        surface_duration = model.new_int_var(0, flight.get("max_surface_wait", 0), f"{fid}_surface_wait")
        model.add(taxi_complete == arrival + flight["taxi_in"])
        model.add(in_block == taxi_complete + surface_duration)
        surface_holding.append(model.new_interval_var(taxi_complete, surface_duration, in_block, f"{fid}_surface_holding"))
        model.add(off_block == in_block + duration)
        model.add(departure >= off_block + flight["taxi_out"])
        handling.append(model.new_interval_var(in_block, duration, off_block, f"{fid}_handling"))
        stand_choice = model.new_int_var(0, len(stands) - 1, f"{fid}_stand")
        stand_flags = []
        for s_index, stand in enumerate(stands):
            use = model.new_bool_var(f"{fid}_uses_{stand['id']}")
            model.add(stand_choice == s_index).only_enforce_if(use)
            model.add(stand_choice != s_index).only_enforce_if(use.Not())
            if stand["id"] not in flight["compatible_stands"]:
                model.add(use == 0)
            model.add(in_block >= stand.get("available_from", 0)).only_enforce_if(use)
            stand_flags.append(use)
            stand_intervals[stand["id"]].append(model.new_optional_interval_var(in_block, duration, off_block, use, f"{fid}_stand_{s_index}"))
        model.add_exactly_one(stand_flags)
        runway_choice = model.new_int_var(0, len(runways) - 1, f"{fid}_runway")
        runway_flags = []
        for r_index, runway in enumerate(runways):
            use = model.new_bool_var(f"{fid}_runway_{r_index}")
            model.add(runway_choice == r_index).only_enforce_if(use)
            model.add(runway_choice != r_index).only_enforce_if(use.Not())
            runway_flags.append(use)
            for name, start in (("arrival", arrival), ("departure", departure)):
                runway_intervals[runway["id"]].append(model.new_optional_fixed_size_interval_var(start, runway["separation"], use, f"{fid}_{name}_occupancy_{r_index}"))
        model.add_exactly_one(runway_flags)
        delay = model.new_int_var(0, horizon + abs(flight["scheduled_departure"]), f"{fid}_delay")
        model.add_max_equality(delay, [0, departure - flight["scheduled_departure"]])
        extra = model.new_int_var(0, horizon + abs(shift_end), f"{fid}_overtime")
        model.add_max_equality(extra, [0, off_block - shift_end])
        waits.append(arrival - flight["reference_arrival"])
        delays.append(delay)
        overtime.append(extra)
        vars_by_id[fid] = {"arrival": arrival, "in_block": in_block, "off_block": off_block, "departure": departure, "stand": stand_choice, "runway": runway_choice}
        signatures.extend([arrival, stand_choice, runway_choice])
    for intervals in stand_intervals.values():
        model.add_no_overlap(intervals)
    for runway in runways:
        for index, closure in enumerate(runway.get("closures", [])):
            if closure["end"] > closure["start"]:
                runway_intervals[runway["id"]].append(model.new_fixed_size_interval_var(closure["start"], closure["end"] - closure["start"], f"closure_{runway['id']}_{index}"))
        model.add_no_overlap(runway_intervals[runway["id"]])
    model.add_cumulative(handling, [1] * len(handling), resources.get("handling_teams", 1))
    model.add_cumulative(surface_holding, [1] * len(surface_holding), resources.get("surface_holding_capacity", len(flights)))
    if fcfs:
        ordered = sorted(flights, key=lambda f: (f["reference_arrival"], f["id"]))
        for left, right in zip(ordered, ordered[1:]):
            model.add(vars_by_id[left["id"]]["arrival"] <= vars_by_id[right["id"]]["arrival"])
    # Integer weights make the priorities lexicographic over their full bounds.
    overtime_bound = len(flights) * (horizon + abs(shift_end))
    delay_bound = sum(horizon + abs(f["scheduled_departure"]) for f in flights)
    delay_weight = overtime_bound + 1
    wait_weight = delay_bound * delay_weight + overtime_bound + 1
    model.minimize(sum(waits) * wait_weight + sum(delays) * delay_weight + sum(overtime))
    return cp_model, model, vars_by_id, signatures


def generate_candidates_with_status(scenario: dict, max_candidates: int = 12, time_limit: float = 10, *, fcfs: bool = False) -> dict:
    """Return bounded candidate search diagnostics without claiming optimality."""
    started = time.monotonic()
    try:
        cp_model, model, variables, signatures = _build_model(scenario, fcfs)
    except (ValueError, KeyError, TypeError) as exc:
        return {"plans": [], "status": "invalid_scenario", "detail": str(exc), "elapsed_seconds": time.monotonic() - started}
    # The first OR-Tools import can be slow on Windows. Give the actual search
    # its stated budget, rather than consuming it while loading native code.
    search_started = time.monotonic()
    plans = []
    status = "search_timeout"
    while len(plans) < max(0, min(int(max_candidates), 12)):
        remaining = time_limit - (time.monotonic() - search_started)
        if remaining <= 0:
            status = "feasible" if plans else "search_timeout"
            break
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        solver.parameters.max_time_in_seconds = remaining
        result = solver.solve(model)
        if result in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            assignments = []
            for f in scenario["flights"]:
                v = variables[f["id"]]
                a = {"flight_id": f["id"], **{k: solver.value(v[k]) for k in ("arrival", "in_block", "off_block", "departure")}}
                a["stand_id"] = scenario["resources"]["stands"][solver.value(v["stand"])]["id"]
                a["runway_id"] = scenario["resources"]["runways"][solver.value(v["runway"])]["id"]
                assignments.append(a)
            plans.append(_plan(scenario, assignments, "fallback" if fcfs else "candidate"))
            model.add_forbidden_assignments(signatures, [[solver.value(v) for v in signatures]])
            status = "feasible"
        elif result == cp_model.INFEASIBLE:
            status = "exhausted" if plans else "infeasible"
            break
        elif result == cp_model.MODEL_INVALID:
            status = "invalid_scenario"
            break
        else:
            status = "feasible" if plans else "search_timeout"
            break
    return {"plans": plans, "status": status, "elapsed_seconds": round(time.monotonic() - started, 3)}


def generate_candidates(scenario: dict, max_candidates: int = 12, time_limit: float = 10) -> list[dict]:
    return generate_candidates_with_status(scenario, max_candidates, time_limit)["plans"]


def deterministic_fallback(scenario: dict) -> dict | None:
    """Fixed FCFS arrival-order scheduling, without any agent acceptance."""
    result = generate_candidates_with_status(scenario, max_candidates=1, time_limit=10, fcfs=True)
    if not result["plans"]:
        return None
    plan = result["plans"][0]
    if not validate_plan(scenario, plan)["valid"]:
        return None
    plan["consensus"] = False
    return plan
