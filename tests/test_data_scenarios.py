"""Data boundaries and reuse checks independent of Claude or the full CSV."""

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from backend.data import DataRepository, parse_hhmm
from backend.scenarios import ScenarioRepository


class TimeParsingTests(unittest.TestCase):
    def test_hhmm_and_explicit_next_midnight(self):
        self.assertEqual(parse_hhmm("0007"), 7)
        self.assertEqual(parse_hhmm("1045"), 645)
        self.assertEqual(parse_hhmm("2400"), 1440)
        self.assertIsNone(parse_hhmm(""))
        for invalid in ("2360", "2500", "12:30", "-1"):
            with self.assertRaises(ValueError):
                parse_hhmm(invalid)


class DataScenarioTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self._write("airlines", ["IATA_CODE", "AIRLINE"], [{"IATA_CODE":"DL","AIRLINE":"Delta Air Lines Inc."},{"IATA_CODE":"WN","AIRLINE":"Southwest Airlines Co."}])
        airport_fields = ["IATA_CODE","AIRPORT","CITY","STATE","COUNTRY","LATITUDE","LONGITUDE"]
        self._write("airports",airport_fields,[{"IATA_CODE":code,"AIRPORT":name,"CITY":name,"STATE":"GA","COUNTRY":"USA","LATITUDE":"33.6","LONGITUDE":"-84.4"} for code,name in [("ATL","Atlanta"),("ALB","Albany"),("ICT","Wichita"),("PIT","Pittsburgh")]])
        self.fields = ["YEAR","MONTH","DAY","AIRLINE","FLIGHT_NUMBER","TAIL_NUMBER","ORIGIN_AIRPORT","DESTINATION_AIRPORT","SCHEDULED_DEPARTURE","SCHEDULED_ARRIVAL","WHEELS_ON","ARRIVAL_TIME","DEPARTURE_TIME","ARRIVAL_DELAY","TAXI_IN","TAXI_OUT","WEATHER_DELAY","LATE_AIRCRAFT_DELAY","CANCELLED","DIVERTED","CANCELLATION_REASON"]
        self.rows = []
        for airline,number,tail,origin,scheduled,landing,arrival,delay in [("DL","2289","N950AT","ALB","0904","1045","1058","114"),("DL","2458","N968AT","ICT","0904","1027","1033","89"),("WN","2793","N386SW","PIT","0915","1028","1044","89")]:
            self.rows.append(dict(YEAR="2015",MONTH="12",DAY="30",AIRLINE=airline,FLIGHT_NUMBER=number,TAIL_NUMBER=tail,ORIGIN_AIRPORT=origin,DESTINATION_AIRPORT="ATL",SCHEDULED_DEPARTURE="0800",SCHEDULED_ARRIVAL=scheduled,WHEELS_ON=landing,ARRIVAL_TIME=arrival,DEPARTURE_TIME="0900",ARRIVAL_DELAY=delay,TAXI_IN="8",TAXI_OUT="17",WEATHER_DELAY=delay,LATE_AIRCRAFT_DELAY="0",CANCELLED="0",DIVERTED="0",CANCELLATION_REASON=""))
        self.rows.append({**self.rows[0],"FLIGHT_NUMBER":"999","TAIL_NUMBER":"N999","WEATHER_DELAY":"15","LATE_AIRCRAFT_DELAY":"20"})
        self._write("flights",self.fields,self.rows)
        self.data = DataRepository(self.root,self.root/"cache.sqlite3")
        self.scenarios = ScenarioRepository(self.data)

    def tearDown(self):
        self.directory.cleanup()

    def _write(self,name,fields,rows):
        with (self.root/f"{name}.csv").open("w",newline="",encoding="utf-8") as stream:
            writer=csv.DictWriter(stream,fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_import_mapping_qualification_and_provenance(self):
        summary = self.data.summary()
        self.assertEqual(summary["flight_rows"],4)
        self.assertEqual(summary["qualifying_rows"],3)
        self.assertEqual(summary["unmapped_airports"],[])
        result=self.data.search_flights({"destination":"ATL","date":"2015-12-30"})
        self.assertEqual(len(result),3)
        flight=self.data.get_flight("flight-1")
        self.assertEqual(flight["airline"]["name"],"Delta Air Lines Inc.")
        self.assertEqual(flight["origin"]["name"],"Albany")
        self.assertEqual(flight["source"]["record"],1)
        self.assertEqual(flight["source"]["line"],2)
        self.assertEqual(len(flight["source"]["checksum"]),64)
        expected_record_hash=hashlib.sha256(json.dumps(self.rows[0],sort_keys=True,separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(flight["source"]["record_checksum"],expected_record_hash)
        self.assertEqual(flight["source"]["checksum"],hashlib.sha256((self.root/"flights.csv").read_bytes()).hexdigest())
        self.assertEqual(flight["historical"]["late_aircraft_delay"],0)
        self.assertIsNone(self.data.get_flight("unknown"))
        self.assertFalse(self.data.get_flight("flight-4")["qualifies"])

    def test_network_and_airport_profile_count_only_qualifying_records(self):
        network = self.data.network()
        self.assertEqual(sorted((r["origin"], r["flights"]) for r in network["routes"]), [("ALB", 1), ("ICT", 1), ("PIT", 1)])
        atl = next(a for a in network["airports"] if a["code"] == "ATL")
        self.assertEqual((atl["inbound"], atl["outbound"], atl["weather_inbound"]), (3, 0, 3))
        self.assertIsInstance(atl["latitude"], float)
        profile = self.data.airport_profile("ATL")
        self.assertEqual(profile["dates"], [{"date": "2015-12-30", "flights": 3, "weather": 3, "late_aircraft": 0, "max_weather": 114.0}])
        self.assertEqual(profile["months"], [{"month": "12", "flights": 3, "weather": 3}])
        self.assertEqual(profile["airport"]["name"], "Atlanta")

    def test_cache_reuse_and_invalidated_checksum(self):
        second=DataRepository(self.root,self.root/"cache.sqlite3")
        self.assertEqual(second.summary()["flight_rows"],4)
        self.rows.pop()
        self._write("flights",self.fields,self.rows)
        refreshed=DataRepository(self.root,self.root/"cache.sqlite3")
        self.assertEqual(refreshed.summary()["flight_rows"],3)
        self.assertNotEqual(refreshed.checksums["flights"],self.data.checksums["flights"])

    def test_default_uses_source_identity_and_labels_assumptions(self):
        scenario=self.scenarios.get_scenario("atl-three-flight-clash")
        self.assertEqual([flight["reference_arrival"] for flight in scenario["flights"]],[30,12,13])
        self.assertEqual(scenario["provenance"]["mode"],"counterfactual")
        self.assertEqual(scenario["resources"]["handling_teams"],2)
        self.assertEqual(scenario["resources"]["surface_holding_capacity"],2)
        self.assertTrue(all(flight["max_surface_wait"]==20 for flight in scenario["flights"]))
        surface_assumptions=[assumption for assumption in scenario["assumptions"] if "surface" in assumption["id"]]
        self.assertGreaterEqual(len(surface_assumptions),5)
        self.assertTrue(all(assumption["source"]=="simulated" for assumption in surface_assumptions))
        self.assertTrue(all("source" in assumption for assumption in scenario["assumptions"]))
        self.assertTrue(all(flight["next_leg"] is None for flight in scenario["flights"]))

    def test_same_engine_input_contract_for_variants(self):
        equipment=self.scenarios.get_scenario("equipment-failure")
        weather=self.scenarios.get_scenario("ramp-weather")
        self.assertEqual(equipment["resources"]["stands"][0]["available_from"],48)
        self.assertEqual(equipment["flights"][1]["compatible_stands"],["B2"])
        self.assertTrue(all(stand["available_from"]>=35 for stand in weather["resources"]["stands"]))
        self.assertEqual(len(self.scenarios.list_scenarios()), 4)
        for removed in ('missing-approval', 'infeasible'):
            with self.assertRaises(KeyError):
                self.scenarios.get_scenario(removed)
        with self.assertRaises(KeyError):
            self.scenarios.get_scenario("unsupported")

    def test_builder_variable_participant_counts_and_no_silent_invalid_data(self):
        one=self.scenarios.build_scenario(["flight-2"])
        self.assertEqual(len(one["flights"]),1)
        self.assertEqual(one["clock_start"],"10:17")
        two=self.scenarios.build_scenario(["flight-2","flight-3"],name="Two captains")
        self.assertEqual(len(two["flights"]),2)
        self.assertEqual(two["name"],"Two captains")
        for invalid in ([],["flight-1","flight-1"],["flight-4"],["unknown"]):
            with self.assertRaises(ValueError):
                self.scenarios.build_scenario(invalid)


if __name__ == "__main__":
    unittest.main()
