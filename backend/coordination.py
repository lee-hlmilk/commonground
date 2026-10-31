"""Bounded human-role coordination over a versioned simulated operation."""
import asyncio
import copy
import time
from uuid import uuid4

from .agents import context_for, envelope, illustrative_reply, participant_client, participants, validate_reply
from .solver import deterministic_fallback, generate_candidates_with_status, validate_plan
from .store import now


class Coordinator:
    def __init__(self, store, settings, client_factory=participant_client):
        self.store, self.settings, self.client_factory = store, settings, client_factory
        self.tasks = {}
        self.started = {}

    def create(self, scenario, mode='live'):
        started = time.monotonic()
        created_at = now()
        run = dict(id=uuid4().hex, status='negotiating', mode=mode, provider=self.settings.provider,
                   provider_policy='jev_then_claude_on_low_confidence_or_final_escalation_review' if self.settings.provider == 'jev' else 'claude_only',
                   scenario=copy.deepcopy(scenario),
                   round=0, max_rounds=self.settings.max_rounds, episode=1, participants=participants(scenario), messages=[], candidates=[],
                   proposal=None, consensus=False, checks=[], metrics={}, created_at=created_at,
                   timing=dict(started_at=created_at, completed_at=None, elapsed_seconds=None),
                   label=('Jev first · Claude on low confidence' if self.settings.provider == 'jev'
                          else 'Live Claude coordination') if mode == 'live' else
                         'Deterministic fallback · no AI consensus' if mode == 'fallback' else
                         'Illustrative replay · authored responses, not Claude output')
        self.store.save(run)
        self.launch(run, started)
        return run

    def launch(self, run, started=None):
        old = self.tasks.get(run['id'])
        if old and not old.done():
            old.cancel()
        self.tasks[run['id']] = asyncio.create_task(self.execute(run, time.monotonic() if started is None else started))

    def save_current(self, run):
        stored = self.store.get(run['id'])
        if not stored or stored['episode'] != run['episode']:
            raise asyncio.CancelledError()
        start = self.started.get((run['id'], run['episode']))
        if run['status'] != 'negotiating' and start is not None and not run.get('timing', {}).get('completed_at'):
            elapsed = round(max(0, time.monotonic() - start), 3)
            run['timing'] = dict(started_at=run.get('timing', {}).get('started_at', run['created_at']),
                                 completed_at=now(), elapsed_seconds=elapsed)
            run.setdefault('metrics', {})['coordination_seconds'] = elapsed
        self.store.save(run)

    @staticmethod
    def choose(candidates, messages):
        """Worst preference rank, summed rank, and deterministic schedule order."""
        def score(p):
            ranks, objections = [], 0
            for m in messages:
                ordered = list(m['acceptable_plan_ids'])
                preferred = m.get('preferred_plan_id')
                if preferred in ordered:
                    ordered.remove(preferred)
                    ordered.insert(0, preferred)
                if p['id'] not in ordered:
                    objections += 1
                    ranks.append(len(candidates) + 1)
                else:
                    ranks.append(ordered.index(p['id']))
            return (objections, max(ranks, default=0), sum(ranks),
                    p['metrics']['airborne_wait'], p['metrics']['total_delay'], p['id'])
        return min(candidates, key=score)

    async def fallback(self, run, reason):
        plan = await asyncio.to_thread(deterministic_fallback, run['scenario'])
        run.update(consensus=False, error=reason, round=run.get('round', 0))
        if plan:
            plan['label'] = next((p['label'] for p in run['candidates'] if p['id'] == plan['id'] and 'label' in p), 'Fallback plan')
            check = validate_plan(run['scenario'], plan)
            plan.update(check)
            run.update(proposal=plan, checks=check['checks'], status='fallback', metrics=copy.deepcopy(plan['metrics']))
        else:
            run.update(status='unresolved', proposal=None)
            if run['candidates']:
                run['checks'] = validate_plan(run['scenario'], run['candidates'][0])['checks']
                failed = [c for c in run['checks'] if not c['passed']]
                if failed:
                    run['error'] = reason + ' ' + ' '.join(c['label'] + ': ' + c['detail'] for c in failed[:2])
        self.save_current(run)

    def reconcile(self, run, messages):
        """Revise against declared alternatives without rewriting refusals as consent."""
        previous = run['proposal']
        refused = any(m['type'] == 'UNABLE' for m in messages)
        preferences = [m for m in run['messages'] if m['round'] == 1]
        common = [p for p in run['candidates'] if p['valid'] and all(
            p['id'] in m['acceptable_plan_ids'] for m in preferences)]
        alternatives = [p for p in common if not refused or p['id'] != previous['id']]
        if alternatives:
            run['proposal'] = copy.deepcopy(self.choose(alternatives, preferences))
        check = validate_plan(run['scenario'], run['proposal'])
        run['proposal'].update(check)
        run['checks'] = check['checks']
        run.setdefault('proposal_history', []).append(dict(round=2, proposal=copy.deepcopy(previous),
            next_proposal_id=run['proposal']['id'], objections=copy.deepcopy([m for m in messages if m['type'] != 'ACCEPT'])))
        self.save_current(run)

    async def execute(self, run, started=None):
        client = None
        started = time.monotonic() if started is None else started
        execution_key = (run['id'], run['episode'])
        self.started[execution_key] = started
        try:
            result = await asyncio.to_thread(generate_candidates_with_status, run['scenario'], 12, 10)
            run['candidates'] = result['plans']
            for index, plan in enumerate(run['candidates']):
                plan['label'] = f'Plan {chr(65 + index)}'
            run['search'] = {k: v for k, v in result.items() if k != 'plans'}
            self.save_current(run)
            if not run['candidates']:
                run['status'] = 'unresolved'
                run['error'] = ('No feasible schedule exists within the modelled constraints.' if result['status'] == 'infeasible'
                                else 'No plan found within the bounded search budget. This does not prove infeasibility.')
                self.save_current(run)
                return
            baseline = await asyncio.to_thread(deterministic_fallback, run['scenario'])
            if baseline:
                baseline['label'] = next((p['label'] for p in run['candidates'] if p['id'] == baseline['id']), 'Baseline plan')
            run['baseline'] = baseline
            self.save_current(run)
            if run['mode'] == 'fallback':
                await self.fallback(run, 'Fixed-rule recovery requested. No agent consensus is claimed.')
                return
            if run['mode'] == 'live':
                client = self.client_factory(self.settings)
            async with asyncio.timeout(self.settings.deadline):
                for round_number in range(1, self.settings.max_rounds + 1):
                    run['round'] = round_number
                    self.save_current(run)
                    snapshot = copy.deepcopy(run['messages'])

                    async def respond(person):
                        context = context_for(person, run['scenario'], run['candidates'], snapshot,
                                              run['proposal'], round_number)
                        context['max_rounds'] = self.settings.max_rounds
                        if round_number == 3:
                            previous = next((m for m in reversed(snapshot) if m['sender'] == person['id'] and m['round'] == 2), None)
                            context['reconciliation'] = dict(final_turn=True, previous_reply=previous,
                                proposal_changed=bool(previous and previous['proposal_id'] != run['proposal']['id']),
                                instruction='Resolve the recorded objection against the explicit checked facts. Give a final ACCEPT, UNABLE or ESCALATE for this exact proposal. Never invent a release or override a failed check.')
                        if client:
                            reply = await client.respond(context)
                        else:
                            reply = validate_reply(illustrative_reply(person, context), context)
                            reply['fixture'] = True
                        return envelope(person, reply, context, run['participants'])

                    jobs = [asyncio.create_task(respond(p)) for p in run['participants']]
                    round_messages = []
                    try:
                        for future in asyncio.as_completed(jobs):
                            message = await future
                            message['timestamp'] = now()
                            round_messages.append(message)
                            run['messages'].append(message)
                            self.save_current(run)
                    finally:
                        for job in jobs:
                            if not job.done():
                                job.cancel()
                        await asyncio.gather(*jobs, return_exceptions=True)
                    if round_number == 1:
                        run['proposal'] = copy.deepcopy(self.choose(run['candidates'], round_messages))
                        check = validate_plan(run['scenario'], run['proposal'])
                        run['proposal'].update(check)
                        run['checks'] = check['checks']
                        self.save_current(run)
                    else:
                        check = validate_plan(run['scenario'], run['proposal'])
                        agreement = all(m['type'] == 'ACCEPT' and m['proposal_id'] == run['proposal']['id']
                                        and m['scenario_version'] == run['scenario']['version'] for m in round_messages)
                        if agreement and len(round_messages) == len(run['participants']) and check['valid']:
                            run['proposal'].update(check)
                            run.update(status='applied', consensus=True, checks=check['checks'],
                                       metrics=copy.deepcopy(run['proposal']['metrics']),
                                       applied_plan=copy.deepcopy(run['proposal']),
                                       decision=dict(by='Multi-agent coordinator', at=now(), decision='auto_apply',
                                                     proposal_id=run['proposal']['id'], scenario_version=run['scenario']['version']))
                            break
                        if round_number < self.settings.max_rounds and check['valid']:
                            self.reconcile(run, round_messages)
                        else:
                            await self.fallback(run, f'No complete, validated agreement within {round_number} turns.')
                            break
                if run['status'] == 'negotiating':
                    await self.fallback(run, 'Negotiation round limit reached without final commitments.')
            if not run.get('timing', {}).get('completed_at'):
                self.save_current(run)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Provider errors may contain request data. Persist only safe categories.
            reason = {
                'AuthenticationError': 'Claude authentication failed. Check ANTHROPIC_API_KEY in .env.',
                'NotFoundError': 'The configured Claude model is unavailable to this key. Check ANTHROPIC_MODEL.',
                'RateLimitError': 'Claude rate limit reached after the bounded retry.',
                'TimeoutError': 'The negotiation deadline expired.',
                'APITimeoutError': 'Claude response timed out after the bounded retry.',
                'ValueError': 'An operational response failed validation; it cannot count as agreement.',
            }.get(type(exc).__name__, 'Coordination could not complete (' + type(exc).__name__ + ').')
            if self.settings.provider == 'jev':
                from .jev import JevAPIError
                if isinstance(exc, JevAPIError):
                    reason = exc.safe_message
                    if getattr(exc, 'handoff', None):
                        run.setdefault('provider_handoffs', []).append(exc.handoff)
            try:
                await self.fallback(run, reason)
            except asyncio.CancelledError:
                raise
            except Exception:
                run.update(status='unresolved', consensus=False, error=reason + ' No validated fallback is available.')
                self.save_current(run)
        finally:
            try:
                if client:
                    await client.close()
            finally:
                self.started.pop(execution_key, None)

    def disrupt(self, run, event):
        if run['mode'] == 'recorded_replay':
            raise ValueError('A recorded replay is read-only. Start a new scenario to change it.')
        scenario = run['scenario']
        if event['type'] == 'stand_unavailable':
            stand = next((s for s in scenario['resources']['stands'] if s['id'] == event.get('stand_id')), None)
            if stand is None:
                raise ValueError('Unknown stand.')
            stand['available_from'] = event['available_from']
        elif event['type'] == 'runway_closure':
            if event['end'] <= event['start']:
                raise ValueError('Closure end must be later than start.')
            scenario['resources']['runways'][0]['closures'].append(dict(start=event['start'], end=event['end']))
        elif event['type'] == 'approval_update':
            approval = next((a for a in scenario['approvals'] if a['id'] == event.get('approval_id')), None)
            if approval is None:
                raise ValueError('Unknown external approval.')
            approval['status'] = event['status']
        else:
            raise ValueError('Unsupported disruption.')
        scenario['version'] += 1
        scenario['assumptions'].append(dict(id='event-' + uuid4().hex[:8], label='Duty-manager scenario update',
                                             value=event, unit='event', source='simulated'))
        archived = dict(episode=run['episode'], version=scenario['version'] - 1,
                        messages=run['messages'], proposal=run['proposal'], status=run['status'])
        for key in ('applied_plan', 'decision', 'baseline', 'search', 'metrics', 'provider_handoffs', 'proposal_history', 'timing'):
            if key in run:
                archived[key] = run.pop(key)
        run.setdefault('episodes', []).append(archived)
        run.update(episode=run['episode'] + 1, round=0, max_rounds=self.settings.max_rounds, status='negotiating', consensus=False,
                   messages=[], candidates=[], proposal=None, checks=[], metrics={},
                   timing=dict(started_at=now(), completed_at=None, elapsed_seconds=None))
        run.pop('error', None)
        run['last_disruption'] = dict(event, timestamp=now())
        self.store.save(run)
        self.launch(run)
        return run
