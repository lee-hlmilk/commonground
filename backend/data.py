"""Read-only CSV ingestion and provenance-preserving historical flight lookup."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any


TIME_FIELDS = {"SCHEDULED_DEPARTURE", "DEPARTURE_TIME", "WHEELS_OFF", "WHEELS_ON", "SCHEDULED_ARRIVAL", "ARRIVAL_TIME"}
IDENTIFIER_FIELDS = {"AIRLINE", "FLIGHT_NUMBER", "TAIL_NUMBER", "ORIGIN_AIRPORT", "DESTINATION_AIRPORT", "CANCELLATION_REASON"}
REQUIRED_FLIGHT_FIELDS = {"YEAR", "MONTH", "DAY", "AIRLINE", "FLIGHT_NUMBER", "TAIL_NUMBER", "ORIGIN_AIRPORT", "DESTINATION_AIRPORT", "SCHEDULED_ARRIVAL", "WHEELS_ON", "ARRIVAL_TIME", "WEATHER_DELAY", "LATE_AIRCRAFT_DELAY", "CANCELLED", "DIVERTED"}


def parse_hhmm(value: str | int | None) -> int | None:
    """Parse airport-local clock values; 2400 means next-day midnight."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError(f"Invalid HHMM value: {text}")
    number = int(text)
    if number == 2400:
        return 1440
    hours, minutes = divmod(number, 100)
    if hours > 23 or minutes > 59:
        raise ValueError(f"Invalid HHMM value: {text}")
    return hours * 60 + minutes


def numeric(value: str | None) -> int | float | None:
    if value is None or not value.strip():
        return None
    parsed = float(value)
    return int(parsed) if parsed.is_integer() else parsed


class DataRepository:
    """Import immutable source snapshots once and retrieve only selected records.

    The SQLite cache is keyed by checksums rather than file modification times.
    No source records are updated by the simulation.
    """

    def __init__(self, root: str | Path, db_path: str | Path, paths: dict[str, str | Path] | None = None):
        self.root = Path(root).resolve()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths = {name: self.root / f"{name}.csv" for name in ("flights", "airlines", "airports")}
        if paths:
            self.paths.update({name: Path(value) if Path(value).is_absolute() else self.root / value for name, value in paths.items()})
        self.checksums = {name: self._checksum(path) for name, path in self.paths.items()}
        self._initialise()

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _read_lookup(path: Path, key: str = "IATA_CODE") -> dict[str, dict[str, str]]:
        with path.open(newline="", encoding="utf-8-sig") as source:
            result = {}
            for row in csv.DictReader(source):
                if not row.get(key):
                    raise ValueError(f"Missing {key} in {path.name}")
                if row[key] in result:
                    raise ValueError(f"Duplicate {key} {row[key]} in {path.name}")
                result[row[key]] = row
            return result

    def _initialise(self) -> None:
        self.airlines = self._read_lookup(self.paths["airlines"])
        self.airports = self._read_lookup(self.paths["airports"])
        signature = json.dumps(self.checksums, sort_keys=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS data_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS data_flights (
                    id TEXT PRIMARY KEY, record INTEGER NOT NULL, date TEXT NOT NULL,
                    airline TEXT NOT NULL, flight_number TEXT NOT NULL, tail_number TEXT,
                    origin TEXT NOT NULL, destination TEXT NOT NULL,
                    scheduled_arrival INTEGER, wheels_on INTEGER,
                    weather_delay REAL, late_aircraft_delay REAL,
                    qualifies INTEGER NOT NULL, raw TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS data_flights_destination_date ON data_flights(destination,date,scheduled_arrival);
                CREATE INDEX IF NOT EXISTS data_flights_tail_date ON data_flights(tail_number,date);
                CREATE INDEX IF NOT EXISTS data_flights_airline ON data_flights(airline);
            """)
            previous = db.execute("SELECT value FROM data_metadata WHERE key='signature'").fetchone()
            if previous and previous["value"] == signature:
                return
            db.execute("DELETE FROM data_flights")
            missing_airlines, missing_airports = set(), set()
            missing = {}
            month_counts = {}
            counts = {"flight_rows": 0, "qualifying_rows": 0, "weather_gt15": 0, "late_aircraft_gt20": 0, "cancelled": 0, "diverted": 0}
            dates = []
            batch = []
            with self.paths["flights"].open(newline="", encoding="utf-8-sig") as source:
                reader = csv.DictReader(source)
                if not REQUIRED_FLIGHT_FIELDS.issubset(reader.fieldnames or []):
                    raise ValueError("Flight CSV is missing required fields: " + ", ".join(sorted(REQUIRED_FLIGHT_FIELDS - set(reader.fieldnames or []))))
                for record, row in enumerate(reader, 1):
                    day = date(int(row["YEAR"]), int(row["MONTH"]), int(row["DAY"])).isoformat()
                    dates.append(day)
                    weather = numeric(row["WEATHER_DELAY"])
                    late = numeric(row["LATE_AIRCRAFT_DELAY"])
                    qualifies = (weather is not None and weather > 15) or (late is not None and late > 20)
                    scheduled, landing = parse_hhmm(row["SCHEDULED_ARRIVAL"]), parse_hhmm(row["WHEELS_ON"])
                    for key, value in row.items():
                        if value == "":
                            missing[key] = missing.get(key, 0) + 1
                        elif key in TIME_FIELDS:
                            parse_hhmm(value)
                        elif key not in IDENTIFIER_FIELDS:
                            numeric(value)
                    if row["AIRLINE"] not in self.airlines:
                        missing_airlines.add(row["AIRLINE"])
                    missing_airports.update(code for code in (row["ORIGIN_AIRPORT"], row["DESTINATION_AIRPORT"]) if code not in self.airports)
                    counts["flight_rows"] += 1
                    counts["qualifying_rows"] += int(qualifies)
                    counts["weather_gt15"] += int(weather is not None and weather > 15)
                    counts["late_aircraft_gt20"] += int(late is not None and late > 20)
                    counts["cancelled"] += int(row["CANCELLED"] == "1")
                    counts["diverted"] += int(row["DIVERTED"] == "1")
                    month_counts[row["MONTH"]] = month_counts.get(row["MONTH"], 0) + 1
                    batch.append((f"flight-{record}",record,day,row["AIRLINE"],row["FLIGHT_NUMBER"],row["TAIL_NUMBER"],row["ORIGIN_AIRPORT"],row["DESTINATION_AIRPORT"],scheduled,landing,weather,late,int(qualifies),json.dumps(row,separators=(",", ":"))))
                    if len(batch) >= 5000:
                        db.executemany("INSERT INTO data_flights VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                        batch.clear()
                if batch:
                    db.executemany("INSERT INTO data_flights VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
            duplicates = db.execute("""SELECT COUNT(*) FROM (SELECT date,airline,flight_number,origin,destination,json_extract(raw,'$.SCHEDULED_DEPARTURE') FROM data_flights GROUP BY date,airline,flight_number,origin,destination,json_extract(raw,'$.SCHEDULED_DEPARTURE') HAVING COUNT(*)>1)""").fetchone()[0]
            summary = {**counts, "airlines":len(self.airlines), "airports":len(self.airports), "min_date":min(dates) if dates else None, "max_date":max(dates) if dates else None, "month_counts":month_counts, "missing_months":[month for month in range(1,13) if str(month) not in month_counts], "missing_values":missing,"unmapped_airlines":sorted(missing_airlines),"unmapped_airports":sorted(missing_airports),"duplicate_identity_groups":duplicates,"checksums":self.checksums,"coverage_note":"A disruption-focused subset, not complete airport traffic or independently verified original provenance."}
            db.execute("INSERT OR REPLACE INTO data_metadata VALUES ('summary',?)", (json.dumps(summary),))
            db.execute("INSERT OR REPLACE INTO data_metadata VALUES ('signature',?)", (signature,))

    def summary(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT value FROM data_metadata WHERE key='summary'").fetchone()
            return json.loads(row[0])

    def network(self) -> dict[str, Any]:
        """Aggregate the qualifying records into airports and origin-destination routes."""
        cached = getattr(self, "_network", None)
        if cached is not None:
            return cached
        with self._connect() as db:
            rows = db.execute("""SELECT origin, destination, COUNT(*) AS flights,
                SUM(weather_delay > 15) AS weather, SUM(late_aircraft_delay > 20) AS late_aircraft,
                AVG(COALESCE(weather_delay,0) + COALESCE(late_aircraft_delay,0)) AS mean_delay
                FROM data_flights WHERE qualifies=1 GROUP BY origin, destination""").fetchall()
        airports: dict[str, dict[str, Any]] = {}
        routes = []
        for row in rows:
            for code, direction in ((row["origin"], "outbound"), (row["destination"], "inbound")):
                if code not in airports:
                    info = self.airport(code)
                    if info.get("latitude") is None or info.get("longitude") is None:
                        continue
                    airports[code] = {**info, "inbound": 0, "outbound": 0, "weather_inbound": 0}
                airports[code][direction] += row["flights"]
            if row["destination"] in airports:
                airports[row["destination"]]["weather_inbound"] += row["weather"] or 0
            if row["origin"] in airports and row["destination"] in airports:
                routes.append({"origin": row["origin"], "destination": row["destination"], "flights": row["flights"],
                               "weather": row["weather"] or 0, "late_aircraft": row["late_aircraft"] or 0,
                               "mean_delay": round(row["mean_delay"] or 0, 1)})
        routes.sort(key=lambda route: -route["flights"])
        self._network = {"airports": sorted(airports.values(), key=lambda a: -(a["inbound"] + a["outbound"])),
                         "routes": routes,
                         "note": "Counts cover the supplied qualifying delayed-flight subset only, not total airport traffic."}
        return self._network

    def airport_profile(self, code: str, limit: int = 14) -> dict[str, Any]:
        """Busiest qualifying arrival dates and origins for one destination airport."""
        with self._connect() as db:
            dates = db.execute("""SELECT date, COUNT(*) AS flights, SUM(weather_delay > 15) AS weather,
                SUM(late_aircraft_delay > 20) AS late_aircraft, MAX(weather_delay) AS max_weather
                FROM data_flights WHERE destination=? AND qualifies=1 GROUP BY date
                ORDER BY weather DESC, flights DESC LIMIT ?""", (code, limit)).fetchall()
            origins = db.execute("""SELECT origin, COUNT(*) AS flights FROM data_flights
                WHERE destination=? AND qualifies=1 GROUP BY origin ORDER BY flights DESC LIMIT 8""", (code,)).fetchall()
            months = db.execute("""SELECT substr(date,6,2) AS month, COUNT(*) AS flights, SUM(weather_delay > 15) AS weather
                FROM data_flights WHERE destination=? AND qualifies=1 GROUP BY month ORDER BY month""", (code,)).fetchall()
        return {"airport": self.airport(code),
                "dates": [dict(row) for row in dates],
                "origins": [{**dict(row), "name": self.airport(row["origin"])["name"]} for row in origins],
                "months": [dict(row) for row in months]}

    def airport(self, code: str) -> dict[str, Any]:
        row = self.airports.get(code)
        if not row:
            return {"code":code,"name":code,"unmapped":True}
        return {"code":code,"name":row["AIRPORT"],"city":row.get("CITY"),"state":row.get("STATE"),"country":row.get("COUNTRY"),"latitude":numeric(row.get("LATITUDE")),"longitude":numeric(row.get("LONGITUDE"))}

    def _enrich(self, stored: sqlite3.Row) -> dict[str, Any]:
        row = json.loads(stored["raw"])
        carrier = row["AIRLINE"]
        historical = {key.lower(): (value if key in TIME_FIELDS | IDENTIFIER_FIELDS else numeric(value)) for key,value in row.items()}
        record_checksum = hashlib.sha256(json.dumps(row,sort_keys=True,separators=(",", ":")).encode("utf-8")).hexdigest()
        return {"id":stored["id"],"date":stored["date"],"callsign":carrier+row["FLIGHT_NUMBER"],"airline":{"code":carrier,"name":self.airlines.get(carrier,{}).get("AIRLINE",carrier)},"origin":self.airport(row["ORIGIN_AIRPORT"]),"destination":self.airport(row["DESTINATION_AIRPORT"]),"tail_number":row["TAIL_NUMBER"],"historical":historical,"qualifies":bool(stored["qualifies"]),"source":{"file":self.paths["flights"].name,"record":stored["record"],"line":stored["record"]+1,"checksum":self.checksums["flights"],"record_checksum":record_checksum}}

    def get_flight(self, flight_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM data_flights WHERE id=?", (flight_id,)).fetchone()
            return self._enrich(row) if row else None

    def search_flights(self, filters: dict[str, Any] | None = None, limit: int = 50) -> list[dict[str, Any]]:
        filters = filters or {}
        where, params = [], []
        aliases = {"date":"date","airport":"destination","destination":"destination","origin":"origin","airline":"airline","tail_number":"tail_number","flight_number":"flight_number"}
        for key,column in aliases.items():
            if filters.get(key) is not None and filters[key] != "":
                where.append(f"{column}=?")
                params.append(str(filters[key]))
        if filters.get("qualifying", True):
            where.append("qualifies=1")
        for key,column in (("weather_delay_min","weather_delay"),("late_aircraft_delay_min","late_aircraft_delay")):
            if filters.get(key) is not None:
                where.append(f"{column}>?")
                params.append(float(filters[key]))
        limit = max(1,min(int(limit),500))
        sql = "SELECT * FROM data_flights" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY date,destination,scheduled_arrival,record LIMIT ?"
        with self._connect() as db:
            return [self._enrich(row) for row in db.execute(sql, [*params,limit])]

    def rotation_candidates(self, flight: dict[str, Any]) -> list[dict[str, Any]]:
        """Plausible same-tail next departures, never certified complete rotations."""
        tail = flight.get("tail_number")
        if not tail:
            return []
        gate = parse_hhmm(flight["historical"].get("arrival_time"))
        if gate is None or gate >= 1440:
            return []
        candidates = self.search_flights({"tail_number":tail,"origin":flight["destination"]["code"],"date":flight["date"],"qualifying":False},limit=500)
        result = []
        for candidate in candidates:
            departure = parse_hhmm(candidate["historical"].get("departure_time"))
            if departure is not None and gate < departure < 1440:
                result.append({"flight_id":candidate["id"],"callsign":candidate["callsign"],"tail_number":tail,"destination":candidate["destination"],"scheduled_departure":candidate["historical"].get("scheduled_departure"),"departure_time":candidate["historical"].get("departure_time"),"source":candidate["source"],"status":"candidate","uncertainty":"Filtered data may omit intervening legs; this is not a certified aircraft rotation."})
        return sorted(result,key=lambda candidate:parse_hhmm(candidate["departure_time"]))
