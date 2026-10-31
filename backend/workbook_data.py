"""Challenge Workbook Reference Data & Operational Rules.

Contains:
1. Synthetic Airport Planning Limits from the challenge workbook (scaled from flights.csv):
   - Large hub (4 runways, 150 gates, 90 max ops/hr, 45m min turnaround, 3 spare gates)
   - Medium hub (3 runways, 70 gates, 60 max ops/hr, 35m min turnaround, 3 spare gates)
   - Small hub (2 runways, 30 gates, 36 max ops/hr, 35m min turnaround, 2 spare gates)
   - Non-hub (1 runway, 8 gates, 20 max ops/hr, 35m min turnaround, 2 spare gates)
2. Simplified FAA JO 7110.65 Wake Separation Rules (nm and seconds).
3. Operational Domain Rules for Pilot (30m final fuel reserve, 45m default arrival fuel,
   5% contingency fuel, 2,400 kg/hr narrowbody vs 6,500 kg/hr widebody holding burn,
   13h max crew duty) and Ground (8h shift, 1.5x overtime rate, 20m gate change notice).
4. Explicit documentation of stated demo deltas per workbook rule:
   "Use the limits in this workbook. Any limit you change must be stated in your demo."
"""
from __future__ import annotations

from typing import Any

# Airport hub classifications and delayed departure counts from the Challenge Workbook
LARGE_HUBS: dict[str, int] = {
    "ORD": 25274, "ATL": 17606, "DFW": 17484, "DEN": 15480, "LAX": 14854,
}

MEDIUM_HUBS: dict[str, int] = {
    "SFO": 12182, "LAS": 10104, "LGA": 9575, "IAH": 9354, "MCO": 8711,
    "PHX": 8238, "EWR": 7330, "BOS": 6488, "BWI": 6330, "MDW": 6293,
    "JFK": 5993, "DTW": 5174, "MSP": 5060, "MIA": 5013, "SAN": 4960,
    "DAL": 4908, "TPA": 4904, "CLT": 4885, "DCA": 4858, "SEA": 4623,
    "FLL": 4531, "HOU": 4120, "PHL": 4095,
}

SMALL_HUBS: dict[str, int] = {
    "SLC": 3687, "STL": 3558, "BNA": 3453, "OAK": 3314, "AUS": 3008,
    "SJC": 2918, "SMF": 2884, "MSY": 2852, "MCI": 2653, "RDU": 2281,
    "PDX": 2167, "IAD": 2166, "SNA": 2166, "MKE": 2028, "RSW": 1971,
    "SAT": 1966, "PBI": 1896, "CLE": 1868, "SJU": 1753, "CMH": 1726,
    "IND": 1582, "ABQ": 1489, "ONT": 1450, "BUR": 1381, "CVG": 1343,
    "OKC": 1298, "PIT": 1218, "HNL": 1170, "JAX": 1169, "OMA": 1075,
    "TUL": 1071, "BDL": 1062, "RNO": 1000, "BUF": 983, "MEM": 961,
    "OGG": 949, "ELP": 883, "TUS": 881, "XNA": 854, "BHM": 832,
    "LIT": 799, "RIC": 790, "BOI": 784,
}

HUB_SIZE_TEMPLATES: dict[str, dict[str, Any]] = {
    "Large hub": {
        "size_class": "Large hub",
        "active_runways": 4,
        "gates": 150,
        "max_runway_ops_per_hour": 90,
        "min_turnaround_min": 45,
        "spare_gates_at_any_time": 3,
    },
    "Medium hub": {
        "size_class": "Medium hub",
        "active_runways": 3,
        "gates": 70,
        "max_runway_ops_per_hour": 60,
        "min_turnaround_min": 35,
        "spare_gates_at_any_time": 3,
    },
    "Small hub": {
        "size_class": "Small hub",
        "active_runways": 2,
        "gates": 30,
        "max_runway_ops_per_hour": 36,
        "min_turnaround_min": 35,
        "spare_gates_at_any_time": 2,
    },
    "Non-hub": {
        "size_class": "Non-hub",
        "active_runways": 1,
        "gates": 8,
        "max_runway_ops_per_hour": 20,
        "min_turnaround_min": 35,
        "spare_gates_at_any_time": 2,
    },
}

# Simplified FAA JO 7110.65 wake rules from Challenge Workbook
FAA_JO_7110_65_WAKE_TABLE: list[dict[str, Any]] = [
    {"leader": "Super (A380)", "follower": "Heavy", "min_separation_nm": 6, "min_time_s": 180},
    {"leader": "Super (A380)", "follower": "Large", "min_separation_nm": 7, "min_time_s": 180},
    {"leader": "Super (A380)", "follower": "Small", "min_separation_nm": 8, "min_time_s": 180},
    {"leader": "Heavy", "follower": "Heavy", "min_separation_nm": 4, "min_time_s": 120},
    {"leader": "Heavy", "follower": "Large", "min_separation_nm": 5, "min_time_s": 120},
    {"leader": "Heavy", "follower": "Small", "min_separation_nm": 6, "min_time_s": 120},
    {"leader": "Large", "follower": "Small", "min_separation_nm": 4, "min_time_s": 60},
    {"leader": "Any other pair", "follower": "Any", "min_separation_nm": 3, "min_time_s": 60},
]

# Workbook operational rules
WORKBOOK_OPERATIONAL_RULES: dict[str, Any] = {
    "final_fuel_reserve_min": 30,
    "contingency_fuel_fraction": 0.05,
    "holding_fuel_burn_kg_per_hour": {
        "narrowbody": 2400,  # A320/737 class (40 kg/min)
        "widebody": 6500,    # A330/777 class (108.33 kg/min)
    },
    "assumed_arrival_holding_fuel_min": 45,  # 30m final reserve + 15m spare holding fuel unless stated otherwise
    "default_spare_holding_fuel_above_reserve_min": 15,
    "flight_crew_max_duty_hours": 13.0,      # Simplified FAA Part 117
    "ground_crew_shift_hours": 8.0,          # Overtime costs 1.5x after 8 hours
    "ground_crew_overtime_multiplier": 1.5,
    "gate_change_notice_min": 20,            # At least 20 minutes before arrival
}


def get_airport_workbook_limits(iata_code: str) -> dict[str, Any]:
    """Return the Challenge Workbook synthetic planning limits for an airport."""
    code = (iata_code or "").strip().upper()
    if code in LARGE_HUBS:
        base = dict(HUB_SIZE_TEMPLATES["Large hub"], delayed_departures_in_flights_csv=LARGE_HUBS[code])
    elif code in MEDIUM_HUBS:
        base = dict(HUB_SIZE_TEMPLATES["Medium hub"], delayed_departures_in_flights_csv=MEDIUM_HUBS[code])
    elif code in SMALL_HUBS:
        base = dict(HUB_SIZE_TEMPLATES["Small hub"], delayed_departures_in_flights_csv=SMALL_HUBS[code])
    else:
        base = dict(HUB_SIZE_TEMPLATES["Non-hub"], delayed_departures_in_flights_csv=None)
    return {"iata_code": code, **base}


def get_faa_wake_rule(leader: str, follower: str) -> dict[str, Any]:
    """Look up FAA JO 7110.65 wake separation (nm and seconds) for leader -> follower."""
    norm_follower = "Small" if follower == "Medium" else follower
    for row in FAA_JO_7110_65_WAKE_TABLE:
        if row["leader"] == leader and row["follower"] == norm_follower:
            return dict(row)
    return {"leader": leader, "follower": follower, "min_separation_nm": 3, "min_time_s": 60}


def compute_fuel_metrics(
    aircraft_category: str,
    scheduled_elapsed_min: int,
    available_hold_fuel_min: int,
    airborne_hold_min: int,
) -> dict[str, Any]:
    """Compute fuel metrics in both minutes and kilograms using the Challenge Workbook rules."""
    is_widebody = aircraft_category in ("Heavy", "Super (A380)")
    body_class = "widebody (A330/777 class)" if is_widebody else "narrowbody (A320/737 class)"
    burn_rate_kg_hr = (
        WORKBOOK_OPERATIONAL_RULES["holding_fuel_burn_kg_per_hour"]["widebody"]
        if is_widebody
        else WORKBOOK_OPERATIONAL_RULES["holding_fuel_burn_kg_per_hour"]["narrowbody"]
    )
    burn_rate_kg_min = burn_rate_kg_hr / 60.0
    reserve_min = WORKBOOK_OPERATIONAL_RULES["final_fuel_reserve_min"]
    total_arrival_fuel_min = reserve_min + available_hold_fuel_min
    trip_fuel_est_kg = round(scheduled_elapsed_min * burn_rate_kg_min * 1.15)
    contingency_fuel_kg = round(trip_fuel_est_kg * WORKBOOK_OPERATIONAL_RULES["contingency_fuel_fraction"])
    reserve_fuel_kg = round(reserve_min * burn_rate_kg_min)
    spare_fuel_kg = round(available_hold_fuel_min * burn_rate_kg_min)
    hold_burn_kg = round(airborne_hold_min * burn_rate_kg_min)
    remaining_spare_min = available_hold_fuel_min - airborne_hold_min
    remaining_spare_kg = round(remaining_spare_min * burn_rate_kg_min)

    return {
        "aircraft_body_class": body_class,
        "holding_burn_rate_kg_per_hour": burn_rate_kg_hr,
        "holding_burn_rate_kg_per_min": round(burn_rate_kg_min, 2),
        "final_reserve_required_min": reserve_min,
        "final_reserve_required_kg": reserve_fuel_kg,
        "contingency_fuel_5pct_kg": contingency_fuel_kg,
        "total_arrival_holding_fuel_min": total_arrival_fuel_min,
        "workbook_default_arrival_fuel_min": WORKBOOK_OPERATIONAL_RULES["assumed_arrival_holding_fuel_min"],
        "custom_fuel_stated_for_scenario": total_arrival_fuel_min != WORKBOOK_OPERATIONAL_RULES["assumed_arrival_holding_fuel_min"],
        "available_spare_hold_fuel_min": available_hold_fuel_min,
        "available_spare_hold_fuel_kg": spare_fuel_kg,
        "planned_airborne_hold_min": airborne_hold_min,
        "planned_airborne_hold_burn_kg": hold_burn_kg,
        "remaining_spare_fuel_above_reserve_min": remaining_spare_min,
        "remaining_spare_fuel_above_reserve_kg": remaining_spare_kg,
    }
