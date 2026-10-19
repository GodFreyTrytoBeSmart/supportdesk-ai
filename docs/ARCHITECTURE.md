# Architecture and trust boundaries

SupportDesk AI is a local, single-backend support workflow. The Streamlit interface is an HTTP client. FastAPI owns validation, identity, routing, tools, and persisted state. SQLite holds synthetic account and workflow data. The optional model is an untrusted inference dependency with no database connection or arbitrary tool execution.

## Request flow

```mermaid
flowchart TD
    Customer[Customer or guest] --> UI[Streamlit chat]
    UI --> Input[FastAPI input validation]
    Input --> Session[Validate bearer token and derive identity]
    Session --> Classify[Rule or real-provider classification]
    Classify --> Validate[Validate structured result and apply policy]
    Validate --> Route{Deterministic route}
    Route -->|Public FAQ or technical help| Retrieve[Lexical retrieval over reviewed demo articles]
    Retrieve --> Select[Optional model selects allowed article IDs]
    Select --> Public[Render stored evidence]
    Route -->|Private account lookup| Scoped[Customer-scoped read tool]
    Scoped --> DB[(SQLite)]
    Route -->|Refund or unresolved case| Ticket[Verify optional transaction ownership]
    Ticket --> Dedupe[Atomic event-id ticket deduplication]
    Dedupe --> DB
    Route -->|Missing identity| Login[Ask for demo sign-in]
    Route -->|Unknown or ambiguous| Clarify[Ask for one supported topic]
    Public --> Output[Validated response and generated receipt]
    Scoped --> Output
    Dedupe --> Output
    Login --> Output
    Clarify --> Output
    Output --> UI
    UI -->|User clicks Mark as received| Ack[Session plus receipt-token validation]
    Ack --> Delivered[Persist delivered state]
    Delivered --> DB
    Input --> Logs[Allowlisted request logs and process counters]
```

A routine cancellation follows the known subscription channel and returns instructions. A guest receives public channel choices. If an authenticated customer's purchase channel is missing or unsupported, or the standard process fails, the workflow can create a human-review ticket. No cancellation is executed.

## Who controls each value?

| Value or decision | Trusted source | Never trusted for this purpose |
|---|---|---|
| Current customer | Validated server-issued session | Chat text, LLM output, arbitrary `user_id` JSON |
| Tool allowed for a route | Backend policy code | Model-proposed executable code or SQL |
| Transaction ownership | Database query scoped to authenticated customer | A transaction ID's format or presence in chat |
| Refund/cancellation execution | Not implemented | Assistant statements or ticket creation |
| Ticket status and escalation reason | Backend-defined values | Caller-supplied status or model prose |
| Public policy answer | Reviewed local synthetic articles | Invented or unsupported model claims |
| Delivery state | Matching session and explicit receipt acknowledgement | Successful text generation |
| Reviewer access | Separate configured key | Customer selector, customer token, frontend-only state |

A valid transaction ID is merely an identifier. An opaque session token is a credential. Both require server-side checks before they authorize a read or write.

## Data model

```mermaid
erDiagram
    USERS ||--o| SUBSCRIPTIONS : owns
    USERS ||--o{ TRANSACTIONS : owns
    USERS ||--o{ SESSIONS : authenticates
    USERS ||--o{ TICKETS : submits
    TRANSACTIONS o|--o{ TICKETS : may_reference
    USERS {
        string id PK
        string persona UK
        string name
    }
    SUBSCRIPTIONS {
        string id PK
        string user_id FK
        string plan
        string status
        string channel
        string renewal_date
        int amount_cents
        string currency
    }
    TRANSACTIONS {
        string id PK
        string user_id FK
        int amount_cents
        string currency
        string status
        string created_at
    }
    SESSIONS {
        string token_hash PK
        string user_id FK
        float expires_at
        string created_at
    }
    TICKETS {
        string id PK
        string user_id FK
        string category
        string description
        string transaction_id FK
        string status
        string created_at
        string escalation_reason
        string event_id
        string fingerprint
    }
    RESPONSES {
        string id PK
        string session_hash
        string receipt_hash
        string delivery_status
        string created_at
        string delivered_at
    }
```

Guest sessions have no customer foreign key. `RESPONSES.session_hash` binds a receipt to a session but is not a foreign key that prevents session revocation. Tickets enforce uniqueness on `(user_id, event_id)`. IDs are not access credentials; authorization is separate.

Private record queries use `WHERE user_id = ?` and parameters supplied from `AuthContext`. Ticket creation checks transaction ownership inside the same short write transaction used for deduplication and insertion. SQLite foreign keys, WAL mode, and short connection lifetimes support this local workflow; they do not make it a distributed data system.

## Retry and delivery sequence

```mermaid
sequenceDiagram
    participant U as Streamlit
    participant A as FastAPI
    participant D as SQLite
    U->>A: Refund request, bearer token, event E
    A->>D: Verify ownership; create ticket for customer plus E
    D-->>A: Ticket T
    A--xU: Response lost in transport
    U->>A: Retry same event E and same payload
    A->>D: Look up customer plus E and compare fingerprint
    D-->>A: Existing ticket T
    A-->>U: Ticket T, deduplicated=true, generated receipt
    Note over U: Render the response
    U->>A: Explicit acknowledgement with response ID and receipt secret
    A->>D: Verify same session and receipt hash; mark delivered
    A-->>U: delivered
    Note over U,D: No step marks the problem resolved or executes a refund
```

Deduplication covers ticket events. Chat responses are not fully replayed: a retry may generate another response ID and receipt. New UUIDs are different events even if their text is identical. An event ID reused with changed ticket fields returns `409`.

## Failure behavior

| Failure | Implemented behavior |
|---|---|
| No session on a public question | Public help remains available |
| No customer identity on a private route | Ask for demo sign-in; no private read |
| Invalid, revoked, or expired bearer token | `401`; UI clears private state |
| Cross-account or nonexistent transaction linkage | Generic not-found response; no linked ticket |
| Invalid model JSON/types/schema | Deterministic fallback with sanitized diagnostic reason |
| Low model confidence | Deterministic fallback; no permission change |
| Model timeout, network fault, `429`, or `5xx` | Bounded retry/backoff, then fallback |
| Provider `4xx` such as unsupported JSON mode | No pointless retry; deterministic fallback |
| No relevant evidence | Clarification or human-support guidance; no invented policy |
| SQLite error | Sanitized `503`; same event may be retried |
| Response lost after ticket insertion | UI retains original event/payload; retry reuses ticket |
| Backend unreachable | UI shows a recoverable setup/retry message |
| Reviewer key missing/incorrect | Review disabled/denied; no customer-token bypass |
| Wrong receipt secret or different session | Receipt not found; no delivery state change |

## Observability and deployment boundaries

`app/observability.py` logs allowlisted request ID, route template, status, and elapsed time. It keeps process-local request totals, mean/max request latency, intent/case counts, fallback counts, ticket creation/reuse, and database-error counts. The reviewer-protected `/review/metrics` endpoint exposes the aggregate snapshot. Counters reset when the backend process restarts; they are not durable business analytics or latency percentiles.

Default Uvicorn access logs are disabled by the documented launcher/commands to avoid query-string logging. Request validation errors omit echoed raw input. Tokens, free-text messages, retrieved account records, and provider response bodies are not intended log fields.

All services bind to loopback for the demo. No external helpdesk, payment platform, public hosting, or real identity provider is connected. See [DECISIONS.md](DECISIONS.md) for the production changes and [VERIFICATION.md](VERIFICATION.md) for what was actually exercised.
