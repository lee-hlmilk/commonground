import asyncio
from contextlib import asynccontextmanager
import copy
import json
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .config import ROOT, Settings
from .coordination import Coordinator
from .data import DataRepository
from .scenarios import ScenarioRepository
from .solver import validate_plan
from .store import RunStore, now


class RunInput(BaseModel):
    scenario_id: str = 'atl-three-flight-clash'
    mode: Literal['live', 'fallback', 'replay'] = 'live'
    replay_source_id: str | None = None


class ScenarioInput(BaseModel):
    flight_ids: list[str] = Field(min_length=1, max_length=8)
    name: str | None = Field(default=None, max_length=100)


class DecisionInput(BaseModel):
    proposal_id: str
    scenario_version: int
    decision: Literal['approve', 'reject']


class DisruptionInput(BaseModel):
    type: Literal['stand_unavailable', 'runway_closure', 'approval_update']
    stand_id: str | None = None
    available_from: int | None = Field(default=None, ge=0, le=120)
    start: int | None = Field(default=None, ge=0, le=120)
    end: int | None = Field(default=None, ge=1, le=120)
    approval_id: str | None = None
    status: Literal['confirmed', 'pending', 'denied'] | None = None

    @model_validator(mode='after')
    def required_fields(self):
        required = {'stand_unavailable': ['stand_id', 'available_from'],
                    'runway_closure': ['start', 'end'], 'approval_update': ['approval_id', 'status']}
        if any(getattr(self, f) is None for f in required[self.type]):
            raise ValueError('Disruption is missing required fields.')
        return self


def create_app(settings=None, repository=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.data = repository or await asyncio.to_thread(
            DataRepository, ROOT, settings.database_path(),
            {name: os.getenv(name.upper() + '_CSV', './' + name + '.csv') for name in ('flights', 'airports', 'airlines')})
        app.state.scenarios = ScenarioRepository(app.state.data)
        app.state.custom = {}
        app.state.store = RunStore(settings.database_path())
        app.state.custom.update({s['id']: s for s in app.state.store.scenarios()})
        app.state.coordinator = Coordinator(app.state.store, settings)
        # Rehydrate custom scenarios from persisted runs after restart.
        for run in app.state.store.list():
            if run['scenario']['id'].startswith('custom-'):
                app.state.custom.setdefault(run['scenario']['id'], run['scenario'])
        yield
        tasks = list(app.state.coordinator.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title='Common Ground · airport coordination', lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin],
                       allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'Last-Event-ID'])

    def get_run(run_id):
        run = app.state.store.get(run_id)
        if not run:
            raise HTTPException(404, 'Run not found.')
        return run

    def get_scenario(scenario_id):
        if scenario_id in app.state.custom:
            return copy.deepcopy(app.state.custom[scenario_id])
        try:
            return app.state.scenarios.get_scenario(scenario_id)
        except KeyError:
            raise HTTPException(404, 'Scenario not found.')
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get('/api/health')
    def health():
        return dict(status='ok', provider=settings.provider, provider_label=settings.provider_label,
                    provider_configured=bool(settings.selected_api_key.strip()),
                    fallback_provider='claude' if settings.provider == 'jev' else None,
                    fallback_configured=bool(settings.api_key.strip()) if settings.provider == 'jev' else False,
                    confidence_threshold=settings.jev_min_confidence if settings.provider == 'jev' else None,
                    claude_configured=bool(settings.api_key.strip()), model=settings.selected_model,
                    default_scenario=os.getenv('DEFAULT_SCENARIO', 'atl-three-flight-clash'))

    @app.get('/api/data/summary')
    def summary():
        return app.state.data.summary()

    @app.get('/api/flights')
    def flights(airport: str | None = None, date: str | None = None, airline: str | None = None,
                weather_delay_min: float | None = None, late_aircraft_delay_min: float | None = None,
                limit: int = Query(50, ge=1, le=200)):
        filters = {k: v for k, v in dict(destination=airport, date=date, airline=airline,
                    weather_delay_min=weather_delay_min, late_aircraft_delay_min=late_aircraft_delay_min).items() if v is not None}
        return dict(flights=app.state.data.search_flights(filters, limit=limit))

    @app.get('/api/scenarios')
    def scenarios():
        return dict(scenarios=app.state.scenarios.list_scenarios() + [
            {k: s[k] for k in ('id', 'name', 'description', 'airport')} | {'flight_count': len(s['flights'])}
            for s in app.state.custom.values()])

    @app.get('/api/scenarios/{scenario_id}')
    def scenario(scenario_id: str):
        return get_scenario(scenario_id)

    @app.post('/api/scenarios')
    def build_scenario(body: ScenarioInput):
        try:
            s = app.state.scenarios.build_scenario(body.flight_ids, body.name)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        app.state.custom[s['id']] = s
        app.state.store.save_scenario(s)
        return s

    @app.get('/api/runs')
    def runs():
        return dict(runs=[dict(id=r['id'], status=r['status'], mode=r['mode'], created_at=r['created_at'],
                               name=r['scenario']['name'], consensus=r['consensus']) for r in app.state.store.list()])

    @app.post('/api/runs')
    async def start_run(body: RunInput):
        if body.replay_source_id:
            run = copy.deepcopy(get_run(body.replay_source_id))
            if run['status'] == 'negotiating':
                raise HTTPException(409, 'Wait until the source run finishes before replaying it.')
            run['recording_origin'] = dict(run_id=run['id'], mode=run['mode'], label=run.get('label'))
            run.update(id=uuid4().hex, mode='recorded_replay', replay_source_id=body.replay_source_id,
                       label='Recorded replay · saved messages, no new Claude calls', created_at=now())
            app.state.store.save(run)
            return run
        if body.mode == 'live' and not settings.selected_api_key.strip():
            variable = 'JEV_API_KEY (or TYPESAFE_API_KEY)' if settings.provider == 'jev' else 'ANTHROPIC_API_KEY'
            raise HTTPException(400, f'Add {variable} to .env and restart the backend. You can use illustrative replay or fixed-rule recovery now.')
        return app.state.coordinator.create(get_scenario(body.scenario_id), body.mode)

    @app.get('/api/runs/{run_id}')
    def run(run_id: str):
        return get_run(run_id)

    @app.get('/api/runs/{run_id}/events')
    async def events(run_id: str, request: Request):
        get_run(run_id)
        try:
            after = max(0, int(request.headers.get('last-event-id', '0')))
        except ValueError:
            raise HTTPException(400, 'Invalid event cursor.')

        async def stream():
            cursor = after
            idle = 0
            while not await request.is_disconnected():
                batch = app.state.store.events(run_id, cursor)
                for event_id, payload in batch:
                    cursor = event_id
                    yield f'id: {event_id}\nevent: update\ndata: {json.dumps(payload)}\n\n'
                idle += 1
                if idle % 30 == 0:
                    yield ': keepalive\n\n'
                await asyncio.sleep(.3)
        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.post('/api/runs/{run_id}/disruptions')
    async def disruption(run_id: str, body: DisruptionInput):
        try:
            return app.state.coordinator.disrupt(get_run(run_id), body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post('/api/runs/{run_id}/decision')
    async def decision(run_id: str, body: DecisionInput):
        run = get_run(run_id)
        if run['mode'] == 'recorded_replay':
            raise HTTPException(409, 'Recorded replays are read-only.')
        proposal = run.get('proposal')
        if run['status'] not in ('awaiting_approval', 'fallback') or not proposal:
            raise HTTPException(409, 'There is no current recommendation awaiting a decision.')
        if body.scenario_version != run['scenario']['version'] or body.proposal_id != proposal['id']:
            raise HTTPException(409, 'This proposal is stale. Review the current version.')
        checks = validate_plan(run['scenario'], proposal)
        if body.decision == 'approve' and not checks['valid']:
            raise HTTPException(409, 'The proposal no longer passes all constraints and approvals.')
        run.update(status='approved' if body.decision == 'approve' else 'rejected', checks=checks['checks'],
                   decision=dict(by='Duty manager', at=now(), decision=body.decision,
                                 proposal_id=proposal['id'], scenario_version=body.scenario_version))
        if body.decision == 'approve':
            run['applied_plan'] = copy.deepcopy(proposal)
        app.state.store.save(run)
        return run

    @app.get('/api/runs/{run_id}/export')
    def export(run_id: str):
        return JSONResponse(get_run(run_id), headers={'Content-Disposition': f'attachment; filename="coordination-{run_id}.json"'})

    dist = ROOT / 'frontend' / 'dist'
    if dist.is_dir():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

        @app.get('/')
        def index():
            return FileResponse(dist / 'index.html')
    return app


app = create_app()


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('backend.main:app', host=os.getenv('API_HOST', '127.0.0.1'),
                port=int(os.getenv('API_PORT', '8000')), log_level=os.getenv('LOG_LEVEL', 'info').lower())
