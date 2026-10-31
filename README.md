# Common Ground

A human-centred airport recovery simulator for Autonomous SG Challenge 2. Independent participants represent ATC, each aircraft's captain, and a ground-operations coordinator. Jev makes the initial typed decisions; an uncertain participant response is routed to Claude for operational communication. Confident Jev decisions use labelled message templates. Participants exchange evidence-backed proposals and limitations, then commit to one checked plan that is applied automatically inside the simulation.

## Start the prototype

Python 3.11+ and Node.js 20.19+ are required. From the project folder in PowerShell:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Jev is the default primary provider, with Claude used when a participant's Jev confidence is below the configured threshold. Configure both keys in `.env`:

```dotenv
DECISION_PROVIDER=jev
JEV_API_KEY=your_key_here
JEV_MODEL=jev-latest
JEV_MIN_CONFIDENCE=0.7
ANTHROPIC_API_KEY=your_claude_key_here
```

`TYPESAFE_API_KEY` is also accepted when `JEV_API_KEY` is empty. Set `DECISION_PROVIDER=claude` for standalone Claude coordination. The Jev-to-Claude route is per participant response, not a replacement of all participants. See [Jev configuration and decision behaviour](docs/JEV.md).

Then start the server:

```powershell
.venv/Scripts/python.exe -m backend.main
```

Open **http://localhost:8000**. Initial CSV import creates an indexed cache and can take a little longer than later starts. Restart the backend after changing `.env`; it never sends your key to the browser. Local `.env` values take precedence over inherited terminal variables. Set `ANTHROPIC_MODEL` to a structured-output-capable Claude model available to your account if the default is unavailable.

Without a key for the selected provider, **Illustrative replay** and **deterministic fallback** work. The illustrative replay uses authored fixture responses, not recorded model output. It exercises the same routing, schema validation, constraints and approval workflow. A saved run can also be opened as a read-only recorded replay without new API calls.

For frontend development, leave the backend running and use `npm --prefix frontend run dev`, then open http://localhost:5173. The development server proxies `/api` to the backend.

## A three-minute demo

1. Open **Three arrivals, one recovery plan**. Inspect the historical reference times and the separately labelled runway, stand, holding and staffing assumptions.
2. Start live coordination using the selected provider (or the explicitly labelled illustrative replay). Each captain has a separate context; the ATC and ground roles cannot speak for them.
3. Follow turn-one proposals, turn-two commitments, and a final reconciliation turn when needed. Inspect operational checks and the recommended arrival/stand assignments.
4. Once all participants agree and the final checks pass, the simulation records and applies the plan automatically.
5. Change stand availability. This invalidates old commitments and the applied plan, archives the previous episode, and coordinates again.
6. Select **Extended runway restriction** or **Ramp work suspended** to compare recovery decisions under different resource restrictions.

## Historical data and reusable scenarios

The supplied files contain 358,774 qualifying delayed flights, 14 airlines and 322 airports. October is absent and no cancellations/diversions are present. This is a filtered historical population, not complete airport traffic. All airline and airport codes map successfully. See [data and modelling notes](docs/DATA.md).

The default demo uses DL2289, DL2458 and WN2793 arriving at ATL on 30 December 2015. Recorded landing times initialise counterfactual reference arrival estimates; they are not claimed to have been forecasts available to historical controllers. Additional closures, waiting limitations, stands, handling resources and passenger consequences are simulated.

Use **Flight records** to filter the source data and select one to eight flights at the same airport/date within a 90-minute landing window. Build a scenario from those records. Two captains remain separate even when employed by the same airline. Ambiguous arrival-day rollover is rejected rather than guessed. Scenario definitions live in `backend/scenarios.py`; additional scenarios reuse the same engine, messages and checks.

Supported modules include shared runway movement/closure checks, stand compatibility/occupancy, bounded authorised surface holding, handling team capacity, turnaround duration, aircraft waiting limits, and external approvals. Recovery scenarios also reserve a simplified shared taxi corridor for one moving aircraft at a time (landing/taxi-in, holding-pad exit, and pushback/taxi-out), with two minutes of stand clearance after off-block. These conservative simulation assumptions are listed in the evidence panel; the drawing is not a surveyed airport taxiway network. Aircraft performance and dispatch decisions are not modelled beyond explicit scenario inputs.

Changing scenarios resets the replay source. Saved recordings are offered only for their own scenario; older recordings without surface-clearance checks retain their messages but do not animate aircraft. The tug-failure scenario restricts DL2458 to the affected stand B2, so the tug delay changes the recovery rather than being bypassed through any free stand. Pending external approval and infeasible arrival windows keep aircraft in holding and show the blocking reason.

## How coordination works

- Each participant receives shared flight references, role-specific facts, checked candidate schedules and relevant messages. Private aircraft limitations originate in that captain's context and can be communicated by the captain.
- One turn means one independent response per involved participant. Five participants produce at most fifteen logical responses across three turns, stopping early when everyone commits. SDK transport retries do not add negotiation turns. `NEGOTIATION_MAX_ROUNDS=3` sets the bound (one to three supported).
- Turn one collects acceptable candidates and preferences. A deterministic selector minimises objections, worst preference rank and total preference rank, then operational costs. Turn two requests explicit acceptance of the exact proposal/version. If a checked proposal remains unresolved, turn three reconciles objections against role-specific check evidence and explicit approval status; an alternative already declared acceptable can replace a refused proposal. Prior refusals remain in the audit record. No fourth turn is allowed.
- Claude uses schema-constrained JSON via the official Anthropic SDK. Local validation rejects unknown evidence, unknown plans, incomplete output and unsafe acceptance. Natural-language wording remains an LLM explanation, not an operational proof; the checks and evidence are the decision basis.
- Jev receives typed Choice and Noul questions for each independent participant. Confident decisions use labelled professional message templates. Below-threshold confidence routes that participant's response to Claude; the log retains the Jev probabilities and identifies the Claude model that answered. A confident refusal is preserved, not sent to Claude to seek acceptance. Probability thresholds do not replace operational checks. See [Jev integration](docs/JEV.md).
- On turn three only, a prior Jev `ESCALATE` can receive an independent Claude review for the same participant when all operational checks and required approvals pass. This is labelled `final_turn_escalation_review`, with the original escalation retained. A known `UNABLE` or failed check does not trigger this route. If participants still do not agree, the checked fallback or unresolved outcome is reported without claiming consensus.
- OR-Tools generates up to twelve options under a ten-second search budget. Independent Python checks revalidate schedules and displayed metrics. Search timeout is distinct from proven model infeasibility.
- A fixed FCFS arrival-order scheduling policy supplies fallback under the same constraints. A fallback never counts as negotiated consensus. Unresolved safety or approval conditions escalate.
- Unanimous commitments and final operational checks are required for automatic application. The audit record identifies the coordinator and exact proposal/version. Fallbacks remain unapplied previews. No live operational clearance is issued.
- New disruptions invalidate old versions. SQLite stores ordered events, evidence snapshots, source checksums, proposals and decisions. The SSE feed reconnects using event IDs. Server restart marks unfinished coordination interrupted instead of replaying billable requests.

The scheduler's metrics are **additional simulated airborne waiting**, **surface waiting**, **departure delay against the scenario target**, staff overtime and illustrative connection exposure. They do not establish fuel savings or reductions in actual 2015 delay. Possible same-tail onward legs are uncertain because intervening flights may be absent.

## Verification

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
npm --prefix frontend run build
npm --prefix frontend run test:presentation
npm --prefix frontend run test:decision-analysis
npm --prefix frontend run test:simulation
.venv/Scripts/python.exe -m scripts.evaluate
# Browser smoke test (backend running; uses an installed Chrome/Edge, screenshots go to tmp/screenshots)
$env:CHROME_EXECUTABLE="C:\Program Files\Google\Chrome\Application\chrome.exe"; node frontend/scripts/smoke.cjs
```

## Interface

- **Network globe** — every qualifying delayed route in the supplied records, coloured by weather share. Click an airport for its monthly profile, busiest weather-disruption days, top delayed origins, prepared scenarios, and a builder that turns 1–8 real flights into a new scenario.
- **Recovery room** — a 3D airport rendered from the scenario: runway (closures shown live), taxiway, contact and remote stands (availability and occupancy), authorised surface holding, holding stack, handling-team vehicles, ATC tower and ground ops. Aircraft fly the exact minutes of the displayed plan and approach from the real bearing of their origin airport. Agent messages are drawn as transmissions between tower, aircraft and ground ops.
- **Timeline** — scrub or play the plan; lanes show permitted arrival windows, airborne and surface waiting, turnarounds, runway movements with separation, stand availability and closures. Preview the fixed-rule baseline or any solver option in the scene; only the checked proposal accepted by every participant is applied automatically.
- **Agent discussion and decision** — stable Plan A / Plan B labels connect messages to previewable options, including older recorded messages that used internal IDs. A fact-based verbal summary explains the landing order, holding requirements, comparison with first-come-first-served, and approval status. Raw model records remain in expandable evidence details; checked consensus plans are applied automatically, with no approval buttons.
- **Critical choices** — explanations use each run's actual runway closure, stand compatibility/opening times, handling-team reservations, arrival-limit margins, and recorded participant preferences. Per-aircraft consequences expose redistributed waiting even when aggregate totals tie. A checked alternative can be previewed without changing the applied plan. Illustrative replay preferences follow each role's operational outcomes (captain-specific waiting, ATC airborne delay, ground waiting/overtime) instead of a fixed candidate order.

The evaluator writes `data/evaluation.json`: five variations of four scenario families, identical starting conditions for candidates and FCFS, feasible/infeasible/approval-blocked outcomes, independently checked violations, and metrics only when comparisons exist. It does **not** fabricate provider consensus or latency results when no live requests were made. Use exported live run histories for those observations.

Decision runtime covers planning, all agent turns, provider retries/reviews, final checks and any fallback. The timer stops at the outcome, including automatic application and excluding simulation playback. Replanning starts a new timer and archives the previous duration. Recorded replays retain the original duration; older runs without timing show “Not recorded”.

## API

Interactive documentation is at `/docs` while the server is running.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Configuration presence (not a live credential test) |
| `GET /api/data/summary`, `/api/flights` | Dataset diagnostics and filtered source records |
| `GET /api/network`, `/api/airports/{code}` | Route/airport aggregates of qualifying records; per-airport dates, months and origins |
| `GET /api/scenarios`, `/api/scenarios/{id}` | Scenario catalogue and definitions |
| `POST /api/scenarios` | Build from selected `flight_ids` |
| `GET /api/runs`, `POST /api/runs` | Saved runs / start live, fallback, illustrative or saved replay |
| `GET /api/runs/{id}`, `/events`, `/export` | Snapshot, SSE updates, audit download |
| `POST /api/runs/{id}/disruptions` | Stand availability, runway closure or external approval update |

This is a local coordination prototype. It has no multi-user authentication or production airport integration. Keep the default loopback binding.
