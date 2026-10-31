"""Scenario definitions combine real records with explicit simulation assumptions."""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from .data import DataRepository, parse_hhmm


CATALOGUE = [
    ("atl-three-flight-clash", "Three arrivals, one recovery plan", "Three real weather-delayed ATL flights compete for simulated runway and stand availability."),
    ("equipment-failure", "Pushback equipment unavailable", "A failed tug delays stand release and the subsequent turnaround sequence."),
    ("missing-approval", "Remote stand awaiting approval", "An external airport approval remains pending; no participant may invent its release."),
    ("runway-restriction", "Extended runway restriction", "A longer temporary closure compresses the arrival sequence."),
    ("ramp-weather", "Ramp work suspended", "Simulated unsafe ramp weather defers stand availability and ground handling."),
    ("infeasible", "No safe arrival window", "A deliberate conflict between runway closure and aircraft waiting limits requires escalation."),
]


class ScenarioRepository:
    def __init__(self, data: DataRepository):
        self.data = data

    def list_scenarios(self) -> list[dict[str, Any]]:
        return [{"id":identifier,"name":name,"description":description,"flight_count":3,"airport":"ATL","date":"2015-12-30"} for identifier,name,description in CATALOGUE]

    def _demo_flights(self) -> list[dict[str, Any]]:
        chosen = []
        for airline,number,origin,tail in (("DL","2289","ALB","N950AT"),("DL","2458","ICT","N968AT"),("WN","2793","PIT","N386SW")):
            found = self.data.search_flights({"date":"2015-12-30","destination":"ATL","origin":origin,"airline":airline,"flight_number":number,"tail_number":tail})
            if len(found) != 1:
                raise ValueError(f"Expected exactly one source record for {airline}{number}; found {len(found)}")
            chosen.append(found[0])
        return chosen

    def get_scenario(self, scenario_id: str) -> dict[str, Any]:
        definition = next((entry for entry in CATALOGUE if entry[0] == scenario_id),None)
        if definition is None:
            raise KeyError(scenario_id)
        scenario = self._build(self._demo_flights(),"10:15",scenario_id,definition[1],definition[2])
        if scenario_id == "equipment-failure":
            scenario["resources"]["stands"][0]["available_from"] = 48
            scenario["assumptions"].append(self._assumption("tug-failure","Failed tug delays B2 release",48,"minutes from scenario start"))
        elif scenario_id == "missing-approval":
            scenario["approvals"] = [{"id":"remote-stand-approval","label":"Airport operations approval for remote stand R1","status":"pending"}]
            scenario["assumptions"].append(self._assumption("external-approval","Required external approval remains pending","pending","status"))
        elif scenario_id == "runway-restriction":
            scenario["resources"]["runways"][0]["closures"] = [{"start":0,"end":27}]
            scenario["assumptions"].append(self._assumption("extended-closure","Runway closed until",27,"minutes from scenario start"))
        elif scenario_id == "ramp-weather":
            for stand in scenario["resources"]["stands"]:
                stand["available_from"] = max(stand["available_from"],35)
            scenario["assumptions"].append(self._assumption("ramp-suspension","Ramp work unavailable until",35,"minutes from scenario start"))
        elif scenario_id == "infeasible":
            scenario["resources"]["runways"][0]["closures"] = [{"start":0,"end":90}]
            scenario["assumptions"].append(self._assumption("no-window","Runway closure exceeds all arrival limits",90,"minutes from scenario start"))
        self._sync_assumptions(scenario)
        return scenario

    @staticmethod
    def _assumption(identifier: str, label: str, value: Any, unit: str, source: str = "simulated") -> dict[str, Any]:
        return {"id":identifier,"label":label,"value":value,"unit":unit,"source":source}

    @staticmethod
    def _sync_assumptions(scenario: dict[str, Any]) -> None:
        """Keep the visible assumptions aligned with each variant's actual inputs."""
        values = {"runway-closure":scenario["resources"]["runways"][0]["closures"],"handling-teams":scenario["resources"]["handling_teams"],"stand-availability":{stand["id"]:stand["available_from"] for stand in scenario["resources"]["stands"]}}
        for assumption in scenario["assumptions"]:
            if assumption["id"] in values:
                assumption["value"] = values[assumption["id"]]

    def _build(self, flights: list[dict[str, Any]], clock_start: str, identifier: str, name: str, description: str) -> dict[str, Any]:
        start = int(clock_start[:2])*60+int(clock_start[3:])
        working = []
        assumptions = [
            self._assumption("runway-closure","Simulated runway closure",[{"start":10,"end":20}],"minutes from scenario start"),
            self._assumption("runway-separation","Required spacing between runway movements",3,"minutes"),
            self._assumption("handling-teams","Available concurrent handling teams",2,"teams"),
            self._assumption("surface-holding-capacity","Authorised simulated surface holding location capacity",2,"aircraft"),
            self._assumption("surface-holding-authority","Scenario airport authority permits bounded waiting at a designated surface holding location","authorised in simulation only","authorisation"),
            self._assumption("stand-availability","Stand earliest availability",{"B2":30,"C2":0,"R1":0},"minutes from scenario start"),
            self._assumption("scope","Background traffic is represented by restrictions; source data is not complete airport traffic","bounded simulation","scope"),
        ]
        for index,source in enumerate(flights):
            flight = copy.deepcopy(source)
            historical = flight["historical"]
            landing = parse_hhmm(historical.get("wheels_on"))
            if landing is None or landing >= 1440:
                raise ValueError(f"{flight['callsign']} has an ambiguous or unavailable landing day")
            gate_arrival = parse_hhmm(historical.get("arrival_time"))
            if gate_arrival is None or gate_arrival < landing:
                raise ValueError(f"{flight['callsign']} has an ambiguous destination-day rollover")
            reference = landing-start
            if reference < 0 or reference > 90:
                raise ValueError("Selected landing reference times must fit a 90-minute common-airport window")
            waiting = 20 if index == 0 else 25
            rotations = self.data.rotation_candidates(source)
            next_leg = rotations[0] if rotations else None
            scheduled_departure = reference+45
            if next_leg:
                candidate_time = parse_hhmm(next_leg["scheduled_departure"])
                if candidate_time is not None:
                    scheduled_departure = candidate_time-start
            flight.update({"reference_arrival":reference,"latest_arrival":reference+waiting,"waiting_limit":waiting,"max_surface_wait":20,"taxi_in":8,"taxi_out":10,"turnaround":30,"scheduled_departure":scheduled_departure,"compatible_stands":["B2","C2","R1"],"connection_deadline":reference+60,"next_leg":next_leg})
            working.append(flight)
            assumptions.extend([
                self._assumption(f"{flight['id']}-reference","Reference arrival for "+flight["callsign"],reference,"minutes from scenario start","historical-derived"),
                self._assumption(f"{flight['id']}-waiting","Maximum additional airborne wait for "+flight["callsign"],waiting,"minutes"),
                self._assumption(f"{flight['id']}-surface-wait","Maximum wait at authorised simulated surface holding location for "+flight["callsign"],20,"minutes"),
                self._assumption(f"{flight['id']}-turnaround","Minimum handling duration for "+flight["callsign"],30,"minutes"),
                self._assumption(f"{flight['id']}-taxi-in","Scenario taxi-in duration for "+flight["callsign"],8,"minutes"),
                self._assumption(f"{flight['id']}-taxi-out","Scenario taxi-out duration for "+flight["callsign"],10,"minutes"),
                self._assumption(f"{flight['id']}-connection","Illustrative connection deadline for "+flight["callsign"],reference+60,"minutes from scenario start"),
                self._assumption(f"{flight['id']}-compatibility","Illustrative stand compatibility for "+flight["callsign"],["B2","C2","R1"],"stand IDs"),
                self._assumption(f"{flight['id']}-departure-target","Illustrative onward departure target for "+flight["callsign"]+(" from an unconfirmed same-tail candidate" if next_leg else " with no observed candidate"),scheduled_departure,"minutes from scenario start","historical-derived" if next_leg else "simulated"),
            ])
        return {"id":identifier,"name":name,"description":description,"airport":flights[0]["destination"],"date":flights[0]["date"],"clock_start":clock_start,"version":1,"horizon":120,"flights":working,"resources":{"runways":[{"id":"RWY-1","separation":3,"closures":[{"start":10,"end":20}]}],"stands":[{"id":"B2","available_from":30},{"id":"C2","available_from":0},{"id":"R1","available_from":0}],"handling_teams":2,"surface_holding_capacity":2},"approvals":[],"assumptions":assumptions,"provenance":{"flight_file":self.data.paths["flights"].name,"checksums":self.data.checksums,"source_records":[flight["source"] for flight in flights],"mode":"counterfactual","reference_note":"Recorded landing times are historical-derived reference estimates used to initialise a counterfactual simulation, not forecasts available to historical controllers.","rotation_note":"Same-tail onward flights are candidates only; intervening records may be absent.","coverage_note":"Filtered delayed flights do not establish full airport demand or normal airport capacity."}}

    def build_scenario(self, flight_ids: list[str], name: str | None = None) -> dict[str, Any]:
        if not flight_ids or len(flight_ids) > 8:
            raise ValueError("Choose between one and eight flights")
        if len(set(flight_ids)) != len(flight_ids):
            raise ValueError("Choose distinct flight records")
        flights = [self.data.get_flight(identifier) for identifier in flight_ids]
        if any(flight is None for flight in flights):
            raise ValueError("A selected flight record does not exist")
        if len({(flight["date"],flight["destination"]["code"]) for flight in flights}) != 1:
            raise ValueError("Selected flights must arrive at the same airport on the same date")
        if any(not flight["qualifies"] for flight in flights):
            raise ValueError("Every selected flight must satisfy WEATHER_DELAY > 15 or LATE_AIRCRAFT_DELAY > 20")
        if any(flight["historical"].get("cancelled") or flight["historical"].get("diverted") for flight in flights):
            raise ValueError("Cancelled or diverted flights need a dedicated supported scenario")
        tails = [flight["tail_number"] for flight in flights if flight["tail_number"]]
        if len(tails) != len(set(tails)):
            raise ValueError("A concurrent arrival scenario cannot contain the same aircraft twice")
        landings = [parse_hhmm(flight["historical"].get("wheels_on")) for flight in flights]
        if any(value is None or value >= 1440 for value in landings):
            raise ValueError("Landing times must have an unambiguous same-day clock")
        if max(landings)-min(landings)>90:
            raise ValueError("Selected landings must fit within 90 minutes; midnight rollover is not guessed")
        start = max(0,min(landings)-10)
        clock = f"{start//60:02d}:{start%60:02d}"
        identifier = "custom-"+hashlib.sha256("|".join(flight_ids).encode()).hexdigest()[:12]
        return self._build(flights,clock,identifier,name or "Custom airport recovery","A counterfactual recovery scenario built from selected historical records.")
