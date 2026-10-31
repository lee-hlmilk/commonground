# Jev primary decisions with Claude escalation

Common Ground uses TypeSafe's Jev System One API as its default primary decision provider. Jev evaluates shared state against typed questions and returns named answers, probabilities and token usage through `POST /v1/systemone`. This integration uses the official endpoint, not a chat-completions service. [TypeSafe API reference](https://docs.typesafe.ai/api/system-one)

When a participant's Jev confidence is below the application threshold, that participant response is routed to Claude. Confident Jev decisions remain Jev decisions, including a confident inability to commit. The route does not weaken operational checks.

## Configure

Copy `.env.example` to `.env` if you have not already created it. Set:

```dotenv
DECISION_PROVIDER=jev
JEV_API_KEY=your_key_here
JEV_BASE_URL=https://api.typesafe.ai/v1
JEV_MODEL=jev-latest
JEV_TIMEOUT_SECONDS=25
JEV_MAX_RETRIES=1
JEV_MIN_CONFIDENCE=0.7
ANTHROPIC_API_KEY=your_claude_key_here
```

Configure both Jev and Claude keys for the complete confidence-based route, and leave the remaining `ANTHROPIC_*` settings intact. For standalone Claude coordination, set `DECISION_PROVIDER=claude`. Restart the backend after any `.env` change. If `JEV_API_KEY` is empty, the application accepts `TYPESAFE_API_KEY` as an alias. Keys remain on the backend; never place them in frontend configuration or an exported scenario.

The default `jev-latest` is a model alias. Available model names can be discovered through TypeSafe's `GET /v1/models`; the evaluation response identifies the model that answered. Pin an available version when comparing repeated evaluations. [Official API reference](https://docs.typesafe.ai/api/system-one)

## How Jev participates

Each ATC, captain and ground participant still receives an independent role context. The application sends state and bounded questions rather than asking Jev to generate prose:

1. **Round one:** A Choice question selects a preferred candidate or review. A Noul question for each candidate evaluates whether that participant can accept it.
2. **Revision:** Existing code combines participants' acceptable options and preferences, then validates the revised plan.
3. **Turn two:** A Choice question selects acceptance, inability to commit, or escalation for the exact proposal version. The context includes role-specific checks, individual aircraft outcomes, and the explicit required-approval list. An empty list means no external dependency; a checked unanimous agreement is applied automatically inside the simulation.
4. **Turn three, when needed:** Reconcile objections against those checked facts and any revised proposal. A prior Jev `ESCALATE` can receive a Claude review in this distinct final turn, labelled `final_turn_escalation_review`. The earlier escalation and probabilities remain in the log. A known `UNABLE`, pending approval, or failed constraint does not authorise this review route. Unanimous explicit acceptance is still required for consensus; remaining objections produce a fallback or unresolved result. There is no fourth turn.

At each round, the route applies separately to each participant response. A below-threshold Jev response invokes Claude using the same role-specific operational context. Other participants can continue to use confident Jev decisions.

Application code validates answer names, types, choices, probability distributions, token counts and current constraints before using a decision. It cannot turn an unsafe proposal into an acceptable one merely because a model selected it. A deterministic failed check is not a reason to ask Claude for permission to bypass it.

## Communication and confidence

Confident Jev decisions are converted into short professional statements using deterministic templates. The dashboard labels these messages **Jev decision · template-rendered**. They are neither Jev-authored explanations nor Claude output.

When confidence is below threshold, Claude generates the operational message. The record identifies the actual responding Claude model and retains the primary Jev decision probabilities and routing reason. An uncertain Jev selection must not be presented as the final participant commitment.

`JEV_MIN_CONFIDENCE=0.7` is an initial application routing setting for this prototype. It is not an aviation safety margin, a validated calibration result, or proof of correctness. Below-threshold confidence triggers Claude escalation. Malformed responses remain errors, and high-confidence `UNABLE` decisions are not rerouted to seek a more agreeable answer. Hard constraints and unanimous explicit acceptance remain mandatory for automatic application regardless of probabilities or which model answers.

The API's Noul value represents the probability of a yes/true answer, while Choice identifies a supplied option with a probability distribution. [TypeSafe response contract](https://api.typesafe.ai/openapi.json)

## Failure and verification

Missing Jev credentials prevent live Jev coordination and produce a configuration message. Authentication, HTTP validation, malformed response and exhausted timeout/retry failures remain explicit and use the existing fixed-rule fallback or unresolved outcome; they do not trigger the confidence-based Claude route.

If Jev confidence is below threshold but the Claude key is absent, the application reports that missing escalation credential and offers its deterministic fallback rather than inventing a response. A failed Claude escalation also remains explicit. The route never fabricates Jev agreement or an operational clearance.

Illustrative replay continues to use authored fixtures and makes no Jev call. Saved runs replay recorded decisions without a new provider request. Mock-transport tests exercise the API contract and failure handling; they do not establish a successful live Jev negotiation. Verify live access using your own key before claiming live evaluation results.
