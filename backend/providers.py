"""Jev-first decisions with an explicit, per-response Claude uncertainty handoff."""
import copy
import time

from .agents import ClaudeParticipantClient
from .jev import JevAPIError, JevLowConfidenceError, JevParticipantClient


class JevFirstParticipantClient:
    def __init__(self, settings, primary=None, fallback_factory=ClaudeParticipantClient):
        self.settings = settings
        self.primary = primary or JevParticipantClient(settings)
        self.fallback_factory = fallback_factory
        self.fallback = None

    async def respond(self, context):
        started = time.monotonic()
        reconciliation = context.get('reconciliation', {})
        previous = reconciliation.get('previous_reply') or {}
        if (context['round'] == 3 and previous.get('provider') == 'jev'
                and previous.get('type') == 'ESCALATE'
                and context.get('proposal_assessment', {}).get('all_checks_pass')
                and not context['proposal_assessment'].get('missing_external_approvals')
                and self.settings.api_key.strip()):
            # A distinct final turn resolves the earlier request for review.
            # It retains the prior escalation and never reroutes a known UNABLE.
            if self.fallback is None:
                self.fallback = self.fallback_factory(self.settings)
            review_context = copy.deepcopy(context)
            review_context['decision_review'] = {
                'reason': 'final_turn_escalation_review',
                'instruction': 'Independently resolve this same participant’s earlier escalation using the explicit checked facts. '
                    'A checked unanimous commitment is applied automatically inside the simulation. Preserve any concrete constraint or authority blocker; do not invent approvals.',
            }
            try:
                reply = await self.fallback.respond(review_context)
            except Exception:
                raise JevAPIError('Final-turn Claude review could not complete; no agreement was inferred.') from None
            reply.update(provider='claude', statement_source='claude_generated',
                review={'from': 'jev', 'to': 'claude', 'reason': 'final_turn_escalation_review',
                        'previous_round': previous['round'], 'previous_proposal_id': previous['proposal_id'],
                        'proposal_changed': reconciliation.get('proposal_changed', False)},
                primary_decision=previous.get('decision'), primary_usage=previous.get('usage'),
                cascade_seconds=round(time.monotonic() - started, 3))
            return reply
        try:
            return await self.primary.respond(context)
        except JevLowConfidenceError as uncertainty:
            # Only a validated low-confidence decision authorises this handoff.
            # Auth/network errors and confident UNABLE decisions are not uncertainty.
            if not self.settings.api_key.strip():
                failure = JevAPIError('Jev was below the confidence threshold, but Claude fallback is not configured. Add ANTHROPIC_API_KEY to .env.')
                self._attach_failure(failure, context, uncertainty)
                raise failure from None
            if self.fallback is None:
                self.fallback = self.fallback_factory(self.settings)
            handoff_context = copy.deepcopy(context)
            handoff_context['decision_handoff'] = {
                'from': 'jev', 'reason': 'low_confidence',
                'threshold': self.settings.jev_min_confidence,
                'primary_decision': uncertainty.decision,
                'instruction': 'Independently assess the supplied operational evidence for this same participant and round. '
                               'Jev confidence is not an operational fact or permission to waive a check.',
            }
            try:
                reply = await self.fallback.respond(handoff_context)
            except Exception as exc:
                detail = {
                    'AuthenticationError': 'Claude fallback authentication failed. Check ANTHROPIC_API_KEY in .env.',
                    'NotFoundError': 'The configured Claude fallback model is unavailable. Check ANTHROPIC_MODEL.',
                    'RateLimitError': 'Claude fallback reached its rate limit.',
                    'APITimeoutError': 'Claude fallback timed out.',
                    'ValueError': 'Claude fallback failed operational response validation.',
                }.get(type(exc).__name__, 'Claude fallback could not complete (' + type(exc).__name__ + ').')
                failure = JevAPIError('Jev was below the confidence threshold. ' + detail)
                self._attach_failure(failure, context, uncertainty)
                raise failure from None
            reply.update(provider='claude', statement_source='claude_generated',
                         fallback={'from': 'jev', 'to': 'claude', 'reason': 'low_confidence',
                                   'trigger': getattr(uncertainty, 'reason', 'confidence_threshold'),
                                   'threshold': self.settings.jev_min_confidence},
                         primary_decision=uncertainty.decision, primary_usage=uncertainty.usage,
                         cascade_seconds=round(time.monotonic() - started, 3))
            return reply

    @staticmethod
    def _attach_failure(failure, context, uncertainty):
        failure.handoff = {'participant': context['participant']['id'], 'round': context['round'],
                           'trigger': getattr(uncertainty, 'reason', 'confidence_threshold'),
                           'reason': 'low_confidence', 'primary_decision': uncertainty.decision,
                           'primary_usage': uncertainty.usage}

    async def close(self):
        try:
            await self.primary.close()
        finally:
            if self.fallback is not None:
                await self.fallback.close()
