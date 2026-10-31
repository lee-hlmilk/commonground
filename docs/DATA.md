# Historical data and scenario construction

The supplied CSVs remain unchanged. The application creates a SQLite cache, retaining the original string-valued record and source references. The file structure is consistent with the challenge's 2015 flight dataset; local inspection does not independently establish original publisher provenance.

## Verified coverage

| Measure | Observed value |
| --- | ---: |
| Flight records | 358,774 |
| Records satisfying WEATHER_DELAY > 15 or LATE_AIRCRAFT_DELAY > 20 | 358,774 |
| WEATHER_DELAY > 15 | 42,012 |
| LATE_AIRCRAFT_DELAY > 20 | 326,249 |
| Airline lookup records | 14 |
| Airport lookup records | 322 |
| Unmapped airline / airport references | 0 / 0 |
| Cancelled / diverted records | 0 / 0 |
| Duplicate flight identity groups | 0 |
| Earliest / latest source dates | 2015-01-01 / 2015-12-31 |

October is absent. The two delay-filter counts overlap and must not be added. Cancellation reason is blank on every record, consistent with the absence of cancellations. Treat this file as a disruption-focused subset, not complete airport traffic. Do not use it to infer normal runway capacity, overall delay probability, or the absence of other aircraft.

Monthly record counts: January 34,136; February 34,585; March 32,451; April 27,198; May 32,684; June 44,751; July 41,261; August 34,378; September 18,705; November 22,422; December 36,203.

## Provenance and joins

Flight AIRLINE joins to airline IATA_CODE. Flight ORIGIN_AIRPORT and DESTINATION_AIRPORT each join to airport IATA_CODE. Identifiers remain strings. Lookup fields provide names, cities and coordinates; coordinates do not establish runway orientations or taxi routes.

Each flight ID is `flight-<record number>`, with one-based data-record numbering excluding the header. Source `line` is record + 1 for these supplied one-line CSV records. A full-file SHA-256 identifies the exact snapshot, while `record_checksum` is SHA-256 of the original row dictionary serialized as UTF-8 JSON with sorted keys and compact separators. The record hash preserves original field strings and distinguishes, for example, a blank from zero. A changed source checksum rebuilds the cache. The full raw record remains available internally; Claude receives only the selected operational context.

## Three-flight demonstration

At ATL on 2015-12-30, the default simulation clock starts at 10:15 airport-local time.

| Flight | Source record / line | Tail | Scheduled gate arrival | Recorded landing | Recorded gate arrival | Reference minute |
| --- | --- | --- | --- | --- | --- | ---: |
| DL2289 ALB–ATL | 355768 / 355769 | N950AT | 09:04 | 10:45 | 10:58 | 30 |
| DL2458 ICT–ATL | 355826 / 355827 | N968AT | 09:04 | 10:27 | 10:33 | 12 |
| WN2793 PIT–ATL | 358015 / 358016 | N386SW | 09:15 | 10:28 | 10:44 | 13 |

These records anchor a **counterfactual simulation**. Their recorded landings become historical-derived reference estimates; they are not forecasts known to controllers in 2015. The CSV does not prove an actual three-flight operational clash. The simulated runway closure and stand restrictions create the demonstrator's clash.

Historical weather-delay minutes are attribution fields, not observed weather telemetry. Recorded outcomes remain in a separate historical view and are not targets to be overwritten by the simulation. Evaluate simulated additional delay against an identical simulated baseline, not as a claimed reduction of real historical delay.

## Assumptions and authority

The initial default assumes runway movement spacing of three minutes; a closure from minute 10 through 20; stands B2, C2 and R1; B2 unavailable before minute 30; two concurrent handling teams; eight-minute taxi-in, ten-minute taxi-out and thirty-minute handling; and illustrative per-aircraft waiting limits of 20/25/25 minutes. Stand compatibility, passenger connection deadlines and approvals are also simulated. These values are demonstration inputs, not certified operating limits.

The simulation also explicitly authorises a designated surface holding location with capacity for two aircraft and a maximum surface wait of twenty minutes per aircraft. Arrival at the stand may therefore occur after landing + taxi-in, bounded by that additional surface-wait allowance and simultaneous holding capacity. This separates safe landing from stand availability instead of automatically forcing an aircraft to remain airborne because its gate is unavailable. Neither the existence of this holding location, its approval, nor any actual historical surface holding is established by the CSV. All three are labelled simulation inputs; actual recorded landing and gate times remain unchanged.

Every input is listed in `assumptions` with a source category: `simulated` or `historical-derived`. Variant definitions update those labels alongside actual model inputs. Source observations are preserved under each flight's `historical` object. A missing input is not replaced with a false confirmation.

The ground coordinator cannot invent external approval. The missing-approval variant includes a pending airport-operations approval and must remain unresolved until the appropriate simulation input changes. Equipment failure delays B2 release to minute 48; it does not imply that all airport handling capacity disappears.

## Candidate rotations

Possible onward ATL legs share the arriving aircraft's tail number and have later recorded departure clock times on the same source date:

- DL2289 → candidate DL2006 to MSN.
- DL2458 → candidate DL1513 to JAN.
- WN2793 → candidate WN2793 to MCO.

These are **candidate links**, not confirmed immediate next legs. The filtered dataset may omit intervening flights; aircraft reassignment and date interpretation also require care. Their scheduled departure can supply an explicitly labelled illustrative downstream target. When no candidate exists, the target is a synthetic reference-arrival + 45 minutes. Neither case establishes a validated minimum turnaround or actual causal delay propagation.

## Time and builder boundaries

HHMM parsing treats 2400 as minute 1440 and rejects malformed hours/minutes. Empty values remain missing. WHEELS_ON is landing; ARRIVAL_TIME is gate arrival. Source dates and local clock strings are preserved. Origin-local and destination-local times are never directly subtracted to estimate flight duration.

The generic builder accepts one to eight distinct qualifying flights at the same destination on the same source date, with recorded landings within ninety minutes and distinct known tails. It starts ten minutes before the earliest landing, bounded at local midnight. Ambiguous landing-day values, a gate time before landing, cancelled/diverted records and unknown records are rejected rather than assigning an invented timezone or day. A 2400 landing is recognized by the parser but excluded from automatic same-day scenario construction. More sophisticated multi-day or cross-timezone scenarios require additional explicit dated inputs.

## Reuse and evaluation limits

Equipment failure, extended runway closure, ramp suspension, missing approval and deliberately infeasible scenarios reuse the same state shape. Adding supported restrictions requires scenario configuration. New operational capabilities need a new checked module; a changed prompt alone does not add a safety rule.

Search is bounded. A timeout means no plan was found within budget, not proof that no feasible plan exists. Report consensus, fallback and unresolved outcomes separately. Cancellation/diversion scenarios require supplementary or explicitly simulated inputs because the supplied snapshot contains no examples of either.
