"""Evaluate the scheduler on 30 labelled counterfactual scenarios.

This intentionally does not claim LLM agreement rates or actual historical
benefits. Run with: python scripts/evaluate.py
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data import DataRepository
from backend.scenarios import ScenarioRepository
from backend.solver import deterministic_fallback, generate_candidates_with_status, validate_plan


def vary_scenario(base: dict, variant: int) -> tuple[dict, str]:
    """Make parameter changes, not random-seed changes masquerading as cases."""
    scenario = copy.deepcopy(base)
    if variant == 0:
        label = "original scenario parameters"
    elif variant == 1:
        for runway in scenario["resources"]["runways"]:
            runway["separation"] += 1
        label = "increase required movement separation by one minute"
    elif variant == 2:
        scenario["resources"]["handling_teams"] = max(1, scenario["resources"]["handling_teams"] - 1)
        for flight in scenario["flights"]:
            flight["turnaround"] += 5
        label = "one fewer handling team, minimum one; handling tasks five minutes longer"
    elif variant == 3:
        for stand in scenario["resources"]["stands"]:
            stand["available_from"] += 5
        label = "all stands become available five minutes later"
    elif variant == 4:
        for flight in scenario["flights"]:
            flight["waiting_limit"] = max(0, flight["waiting_limit"] - 5)
            flight["latest_arrival"] = min(flight["latest_arrival"], flight["reference_arrival"] + flight["waiting_limit"])
        label = "each aircraft has five fewer minutes of permissible additional wait"
    else:
        raise ValueError("Variant must be in 0..4")
    scenario["id"] = f"{base['id']}-evaluation-{variant}"
    scenario.setdefault("assumptions", []).append({"id": "evaluation-variation", "label": label, "value": variant, "unit": "variation", "source": "simulated"})
    return scenario, label


def summarise(results: list[dict]) -> dict:
    comparable = [r for r in results if r["selected_metrics"] is not None and r["baseline_metrics"] is not None]
    return {
        "scenario_count": len(results),
        "physically_feasible_count": sum(r["physical_feasible"] for r in results),
        "fully_validated_count": sum(r["fully_validated"] for r in results),
        "approval_blocked_count": sum(r["approval_blocked"] for r in results),
        "proven_infeasible_count": sum(r["search_status"] == "infeasible" for r in results),
        "search_timeout_count": sum(r["search_status"] == "search_timeout" for r in results),
        "physical_constraint_violations_in_selected_plans": sum(r["physical_violations"] for r in results),
        "comparable_case_count": len(comparable),
        "candidate_vs_fcfs_total_wait_difference": sum(r["selected_metrics"]["airborne_wait"] - r["baseline_metrics"]["airborne_wait"] for r in comparable),
        "candidate_vs_fcfs_total_surface_wait_difference": sum(r["selected_metrics"].get("surface_wait", 0) - r["baseline_metrics"].get("surface_wait", 0) for r in comparable),
        "candidate_vs_fcfs_total_delay_difference": sum(r["selected_metrics"]["total_delay"] - r["baseline_metrics"]["total_delay"] for r in comparable),
        "difference_definition": "candidate minus FCFS, simulation minutes; negative is lower; only comparable validated cases",
    }


def evaluate(repository: ScenarioRepository, max_candidates: int = 3, time_limit: float = 1.0) -> dict:
    results = []
    for definition in repository.list_scenarios():
        base = repository.get_scenario(definition["id"])
        for variant in range(5):
            scenario, label = vary_scenario(base, variant)
            search = generate_candidates_with_status(scenario, max_candidates=max_candidates, time_limit=time_limit)
            selected = search["plans"][0] if search["plans"] else None
            validation = validate_plan(scenario, selected) if selected else {"valid": False, "checks": []}
            physical_failures = [c for c in validation["checks"] if not c["passed"] and not c["id"].startswith("approval:")]
            blocked = any(not c["passed"] and c["id"].startswith("approval:") for c in validation["checks"])
            baseline = deterministic_fallback(scenario)
            results.append({
                "scenario_id": scenario["id"], "family": definition["id"], "variant": variant,
                "variation": label, "flight_count": len(scenario["flights"]),
                "search_status": search["status"], "search_seconds": search["elapsed_seconds"],
                "candidate_count": len(search["plans"]), "physical_feasible": selected is not None and not physical_failures,
                "fully_validated": validation["valid"], "approval_blocked": blocked,
                "physical_violations": len(physical_failures),
                "selected_plan_id": selected["id"] if selected else None,
                "selected_metrics": selected["metrics"] if selected and validation["valid"] else None,
                "baseline_metrics": baseline["metrics"] if baseline else None,
                "failed_checks": [c for c in validation["checks"] if not c["passed"]],
            })
    return {
        "evaluation_kind": "solver_only_counterfactual",
        "disclosure": "No Claude requests were made. These are simulated scheduling outcomes, not historical delay reductions. Candidate selection uses the scheduler's first result, not agent negotiation.",
        "llm_negotiation": {"executed": False, "consensus_rate": None, "negotiation_latency": None},
        "source_checksums": repository.data.checksums,
        "search_limits": {"max_candidates": max_candidates, "seconds_per_candidate_search": time_limit, "fcfs_seconds": 10},
        "summary": summarise(results), "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "evaluation.json")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "coordination.sqlite3")
    parser.add_argument("--time-limit", type=float, default=1.0)
    parser.add_argument("--max-candidates", type=int, default=3)
    args = parser.parse_args()
    if args.time_limit <= 0 or not 1 <= args.max_candidates <= 12:
        parser.error("time-limit must be positive and max-candidates must be in 1..12")
    data = DataRepository(ROOT, args.database)
    report = evaluate(ScenarioRepository(data), args.max_candidates, args.time_limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), **report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
