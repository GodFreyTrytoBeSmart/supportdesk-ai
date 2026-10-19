# SupportDesk AI — portfolio copy

## Short project description

SupportDesk AI is an independent customer-support portfolio demo built with Python, FastAPI, Streamlit, and SQLite. It combines public knowledge retrieval, session-scoped subscription and billing tools, and structured escalation to a human-review queue. A configurable OpenAI-compatible model adapter assists with classification and evidence selection; deterministic backend code controls authorization, routing, and side effects. An offline mock provider makes the full workflow reproducible without credentials.

All customers, transactions, and support policies are synthetic. This project is not an official Nebula or OBRIO product and does not perform financial operations.

## Resume bullets

- Built a customer-support MVP with public knowledge retrieval, authenticated account lookups, and human escalation using FastAPI, Streamlit, and SQLite.
- Enforced server-derived customer identity and transaction ownership in narrow backend tools; kept account records out of model prompts and separated reviewer access from customer sessions.
- Implemented validated model output, bounded inference retries, deterministic fallback, event-based ticket idempotency, and explicit delivery acknowledgements distinct from case resolution.
- Verified the implementation with 102 passing API, adapter, and interface tests; documented synthetic evaluation results, failure cases, architecture decisions, and production limitations.

## Sixty-second spoken introduction

“I built a support workflow where the model has a useful but limited role. It helps interpret the customer's request and select public evidence. The backend decides which tools may run, derives customer identity from a validated session, and checks ownership before accessing account records.

“The assistant can answer public questions, retrieve a customer's subscription or billing history, and create a structured human-review ticket. It gives cancellation instructions for the recorded purchase channel, but it cannot cancel a subscription or issue a refund.

“I focused on failure behavior as much as the happy path: invalid model output falls back predictably, a retried event reuses its ticket, and receipt acknowledgement is separate from resolution. The default demo works offline. The test suite and documentation show what was verified, what remains synthetic, and what a production rollout would require.”

## Evidence and limits

| Evidence | What it supports | What it does not establish |
|---|---|---|
| 102 passing tests: 46 API, 50 intelligence, 6 interface | Recorded authorization, routing, failure, retry, receipt, provider-contract, and UI checks | Security against every attack or compatibility with every deployment |
| 38/40 synthetic English intent examples | Regression behavior of the deterministic classifier, including known misses | Held-out or production classification accuracy; multilingual quality |
| 13/13 expected articles found in the top three | Retrieval hits on the labeled synthetic subset | Semantic correctness of every answer or general retrieval quality |
| Mocked provider transport and schema checks | Handling of validated and malformed output, errors, timeouts, and retries | A completed live external-model inference; none was verified without credentials or a running compatible local model |
| Request logs and reviewer-protected process metrics | Local request timing and aggregate workflow counts | Durable analytics, p95 latency, resolution-rate improvement, or cost savings |

The public persona selector is an authentication simulator, and the optional reviewer key is shared. The queue has no real agent notification, assignment, reply, or closure integration. Tickets are local records, and the application remains a local portfolio MVP.

## Engineering points to discuss

The strongest discussion topics are the distinction between authentication and authorization; the choice to treat model output as untrusted input; source-based public answers; database-enforced event idempotency; and the separate meanings of generation, delivery, and resolution. Explain each with a concrete request from [the demo script](DEMO_SCRIPT.md), then use [the decision guide](DECISIONS.md) for trade-offs and code locations.

For a production plan, prioritize real identity, a real human-support workflow, reviewed knowledge ownership, privacy and retention controls, representative held-out evaluation, and measurements of customer outcomes. Add a vector index, workflow engine, distributed queue, or new financial action only when a demonstrated requirement justifies its added complexity.
