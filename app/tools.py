"""Narrow backend tools; identity comes only from a validated AuthContext."""

import hashlib
import json
import secrets
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.auth import AuthContext, digest
from app.db import Database, utc_now
from app.models import Intent, Ticket


def ticket_from_row(row) -> Ticket:
    return Ticket(
        id=row["id"],
        customer_ref=row["user_id"],
        category=row["category"],
        description=row["description"],
        transaction_id=row["transaction_id"],
        status=row["status"],
        created_at=row["created_at"],
        escalation_reason=row["escalation_reason"],
    )


class SupportTools:
    def __init__(self, db: Database, context: AuthContext):
        self.db = db
        self.context = context

    def get_my_subscription(self) -> dict | None:
        user_id = self.context.require_customer()
        with self.db.transaction() as conn:
            row = conn.execute(
                """SELECT id, plan, status, channel, renewal_date, amount_cents, currency
                                  FROM subscriptions WHERE user_id = ?""",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_my_transactions(self) -> list[dict]:
        user_id = self.context.require_customer()
        with self.db.transaction() as conn:
            rows = conn.execute(
                """SELECT id, amount_cents, currency, status, created_at
                                   FROM transactions WHERE user_id = ? ORDER BY created_at DESC, id""",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_my_tickets(self) -> list[Ticket]:
        user_id = self.context.require_customer()
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM tickets WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
        return [ticket_from_row(row) for row in rows]

    def create_support_ticket(
        self, category: Intent, description: str, transaction_id: str | None = None, *, event_id: UUID
    ) -> tuple[Ticket, bool]:
        # The model does not supply identity, SQL, status or escalation reason.
        from app.privacy import redact_sensitive

        user_id = self.context.require_customer()
        fingerprint = hashlib.sha256(
            json.dumps(
                {"category": category.value, "description": description, "transaction_id": transaction_id},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        reason = (
            "Financial request requires human review; no refund was executed."
            if category == Intent.REFUND_REQUEST
            else "Customer requested human help or standard guidance was insufficient."
        )
        with self.db.transaction(write=True) as conn:
            prior = conn.execute(
                "SELECT * FROM tickets WHERE user_id = ? AND event_id = ?", (user_id, str(event_id))
            ).fetchone()
            if prior:
                if not secrets.compare_digest(prior["fingerprint"], fingerprint):
                    raise HTTPException(409, "This event ID was already used for a different submission.")
                return ticket_from_row(prior), True
            if (
                transaction_id
                and not conn.execute(
                    "SELECT 1 FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id)
                ).fetchone()
            ):
                # Same response for nonexistent IDs and other customers' IDs.
                raise HTTPException(404, "Transaction not found for this account.")
            ticket_id = f"tkt_{uuid4().hex}"
            conn.execute(
                "INSERT INTO tickets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticket_id,
                    user_id,
                    category.value,
                    redact_sensitive(description)[:500],
                    transaction_id,
                    "open",
                    utc_now(),
                    reason,
                    str(event_id),
                    fingerprint,
                ),
            )
            row = conn.execute(
                "SELECT * FROM tickets WHERE id = ? AND user_id = ?", (ticket_id, user_id)
            ).fetchone()
        return ticket_from_row(row), False


def get_help_articles(query: str):
    from app.knowledge import KnowledgeBase

    return KnowledgeBase().search(query)


def record_response(db: Database, context: AuthContext) -> tuple[str, str]:
    response_id, receipt = f"rsp_{uuid4().hex}", secrets.token_urlsafe(32)
    with db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO responses VALUES (?, ?, ?, ?, ?, ?)",
            (response_id, context.session_hash, digest(receipt), "generated", utc_now(), None),
        )
    return response_id, receipt


def acknowledge(db: Database, context: AuthContext, response_id: str, receipt: str) -> dict:
    with db.transaction(write=True) as conn:
        row = conn.execute("SELECT * FROM responses WHERE id = ?", (response_id,)).fetchone()
        if (
            not row
            or row["session_hash"] != context.session_hash
            or not secrets.compare_digest(row["receipt_hash"], digest(receipt))
        ):
            raise HTTPException(404, "Response receipt not found.")
        conn.execute(
            "UPDATE responses SET delivery_status = 'delivered', delivered_at = COALESCE(delivered_at, ?) WHERE id = ?",
            (utc_now(), response_id),
        )
    return {"response_id": response_id, "delivery_status": "delivered"}
