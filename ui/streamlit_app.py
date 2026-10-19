"""Small customer-facing demo UI; all authorization stays in the API."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import streamlit as st

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")
PERSONAS = {
    "guest": "Guest · public help only",
    "alex": "Alex · demo customer",
    "blair": "Blair · demo customer",
    "casey": "Casey · demo customer",
}
PRIVATE_DEFAULTS = {
    "messages": [],
    "subscription": None,
    "tickets": [],
    "customer": None,
    "account_loaded": False,
    "transaction_id_input": "",
    "human_request_input": False,
}


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def api(
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    token: str | None = None,
    reviewer_key: str | None = None,
) -> dict:
    """Use a bounded request; mutation retries are explicit and preserve event IDs."""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if reviewer_key:
        headers["X-Reviewer-Key"] = reviewer_key
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(f"{API_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=200) as response:
            raw = response.read()
        result = json.loads(raw) if raw else {}
        if not isinstance(result, dict):
            raise ValueError("Expected an object")
        return result
    except HTTPError as exc:
        if exc.code == 401:
            message = "Your demo session has expired. Start a fresh session to continue."
        elif exc.code == 403:
            message = "This session is not allowed to access that information."
        elif exc.code == 404:
            message = (
                "That record was not found for this account. Check the transaction ID if you supplied one."
            )
        elif exc.code == 409:
            message = "This submission conflicts with a previous event. Submit a new message if the details have changed."
        elif exc.code == 422:
            message = "The request could not be validated. Check the message and optional transaction ID."
        elif exc.code == 429:
            message = "The service is busy. Wait briefly, then use Retry."
        else:
            message = "The support service could not complete the request. You can retry safely."
        raise ApiError(message, exc.code) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ApiError(
            "The support service is unavailable. Check that the backend is running, then retry."
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ApiError("The support service returned an unreadable response. Please retry.") from exc


def initialize() -> None:
    defaults = {
        **PRIVATE_DEFAULTS,
        "persona_choice": "guest",
        "session_token": None,
        "pending_revocation": None,
        "session_expired": False,
        "session_notice": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value.copy() if isinstance(value, list) else value


def clear_private_state() -> None:
    for key, value in PRIVATE_DEFAULTS.items():
        if key in {"transaction_id_input", "human_request_input"}:
            # Deletion also works when expiry is discovered after widgets render.
            st.session_state.pop(key, None)
        else:
            st.session_state[key] = value.copy() if isinstance(value, list) else value
    # Streamlit widget state can retain acknowledgement/retry controls after a switch.
    for key in list(st.session_state):
        if key.startswith(("ack_", "retry_", "debug_", "reviewer_")):
            del st.session_state[key]


def select_persona() -> None:
    """Clear data immediately; do not establish the new account until logout succeeds."""
    old_token = st.session_state.session_token
    clear_private_state()
    st.session_state.session_token = None
    if old_token:
        st.session_state.pending_revocation = old_token
    st.session_state.session_expired = False
    st.session_state.session_notice = None


def start_session() -> None:
    if st.session_state.pending_revocation:
        try:
            api("DELETE", "/demo/sessions", token=st.session_state.pending_revocation)
        except ApiError as exc:
            if exc.status != 401:  # An expired token already has no authority.
                raise
        st.session_state.pending_revocation = None
    result = api("POST", "/demo/sessions", {"persona": st.session_state.persona_choice})
    token = result.get("access_token")
    if not isinstance(token, str) or not token:
        raise ApiError("The support service did not return a valid demo session. Please retry.")
    st.session_state.session_token = token
    st.session_state.customer = result.get("customer")
    st.session_state.session_expired = False
    st.session_state.session_notice = None


def expire_session() -> None:
    clear_private_state()
    st.session_state.session_token = None
    st.session_state.session_expired = True
    st.session_state.session_notice = "Your demo session expired. Private information has been cleared."


def load_account() -> None:
    if not st.session_state.customer:
        st.session_state.account_loaded = True
        return
    token = st.session_state.session_token
    subscription = api("GET", "/me/subscription", token=token)
    tickets = api("GET", "/me/tickets", token=token)
    st.session_state.subscription = subscription.get("subscription")
    st.session_state.tickets = tickets.get("tickets", [])
    st.session_state.account_loaded = True


def send_event(event: dict[str, Any]) -> None:
    """Every retry sends exactly the original payload and original event UUID."""
    try:
        result = api("POST", "/chat", event["payload"], token=st.session_state.session_token)
        required = {"response_id", "receipt_token", "message", "intent", "debug"}
        if not required.issubset(result):
            raise ApiError("The support service returned an incomplete response. Please retry.")
        event["response"] = result
        event["error"] = None
        event["delivered"] = False
        st.session_state.account_loaded = False
    except ApiError as exc:
        if exc.status == 401:
            expire_session()
        else:
            event["error"] = str(exc)


def money(amount_cents: int, currency: str) -> str:
    return f"{amount_cents / 100:,.2f} {currency}"


def draw_account() -> None:
    customer = st.session_state.customer
    st.subheader("Your demo account")
    if not customer:
        st.info("Guest access includes public help. Select a demo customer to view account information.")
        return
    st.write(f"**{customer.get('name', 'Demo customer')}**")
    subscription = st.session_state.subscription
    if not subscription:
        st.caption("No subscription is currently available for this account.")
    else:
        st.write(
            f"**{subscription.get('plan', 'Subscription')}** · {subscription.get('status', 'Unknown status')}"
        )
        st.caption(f"Purchased through {str(subscription.get('channel', '')).replace('_', ' ')}")
        if subscription.get("amount_cents") is not None:
            st.write(money(subscription["amount_cents"], subscription.get("currency", "")))
        if subscription.get("renewal_date"):
            st.caption(f"Renewal date: {subscription['renewal_date']}")
    if st.button("Refresh account", use_container_width=True):
        st.session_state.account_loaded = False
        st.rerun()


def draw_tickets() -> None:
    if not st.session_state.customer:
        return
    with st.expander(f"Your support tickets · {len(st.session_state.tickets)}"):
        if not st.session_state.tickets:
            st.caption("No support tickets yet. Refund requests and financial disputes go to human review.")
        for ticket in st.session_state.tickets:
            st.markdown(f"**{ticket.get('id', 'Ticket')}** · {ticket.get('status', 'open')}")
            st.write(ticket.get("description", ""))
            st.caption(f"{ticket.get('category', '')} · {ticket.get('created_at', '')}")
            if ticket.get("transaction_id"):
                st.caption(f"Linked transaction: {ticket['transaction_id']}")
            if ticket.get("escalation_reason"):
                st.caption(f"Human review: {ticket['escalation_reason']}")
            st.divider()


def draw_event(event: dict[str, Any]) -> None:
    with st.chat_message("user"):
        st.write(event["payload"]["message"])
    if event.get("error"):
        with st.chat_message("assistant"):
            st.warning(event["error"])
            if st.button("Retry this message", key=f"retry_{event['id']}"):
                with st.spinner("Retrying the same submission…"):
                    send_event(event)
                st.rerun()
        return
    result = event.get("response")
    if not result:
        return
    with st.chat_message("assistant"):
        st.write(result["message"])
        if result.get("ticket"):
            st.success(f"Sent for human review · Ticket {result['ticket']['id']}")
            st.caption(
                "A ticket records the request. It does not approve a refund or mean a human has answered."
            )
        if result.get("sources"):
            st.caption(
                "Knowledge used: "
                + " · ".join(source.get("title", "Help article") for source in result["sources"])
            )
        if event.get("delivered"):
            st.caption("✓ Receipt acknowledged · this does not mark the case resolved")
        elif st.button(
            "Mark as received",
            key=f"ack_{event['id']}",
            help="Confirm receipt after reading this response; this does not resolve your case.",
        ):
            try:
                ack = api(
                    "POST",
                    f"/responses/{result['response_id']}/ack",
                    {"receipt_token": result["receipt_token"]},
                    token=st.session_state.session_token,
                )
                if ack.get("delivery_status") != "delivered":
                    raise ApiError("The receipt was not confirmed. Please try again.")
                event["delivered"] = True
                st.rerun()
            except ApiError as exc:
                if exc.status == 401:
                    expire_session()
                    st.rerun()
                st.warning(str(exc))
        with st.expander("How this answer was handled"):
            debug = result.get("debug", {})
            st.json(
                {
                    "intent": result.get("intent"),
                    "case_status": result.get("case_status"),
                    "requires_authentication": result.get("requires_authentication"),
                    "confidence_hint": result.get("confidence"),
                    **{
                        key: debug.get(key)
                        for key in (
                            "classification_source",
                            "fallback_reason",
                            "selected_tool",
                            "routing_decision",
                            "execution_result",
                        )
                    },
                }
            )
            st.caption(
                "Confidence is a routing hint, not a calibrated probability. Tokens and private record payloads are excluded from this panel."
            )


def main() -> None:
    st.set_page_config(page_title="SupportDesk AI · Portfolio Demo", page_icon="💬", layout="centered")
    initialize()
    st.title("SupportDesk AI")
    st.caption("Clear answers. Account-aware help. Human review when it matters.")
    st.info(
        "Independent educational portfolio demo. All customer records and policies are synthetic. No affiliation with Nebula, OBRIO, or any subscription service."
    )

    with st.sidebar:
        st.markdown("## Demo workspace")
        st.selectbox(
            "Try an account",
            list(PERSONAS),
            format_func=PERSONAS.get,
            key="persona_choice",
            on_change=select_persona,
        )
        st.caption("This selector simulates sign-in. It is not a production identity system.")
    if st.session_state.session_notice:
        st.warning(st.session_state.session_notice)
    if st.session_state.session_expired:
        if st.button("Start a fresh demo session"):
            st.session_state.session_expired = False
            st.rerun()
        return

    try:
        health = api("GET", "/health")
        with st.sidebar:
            st.caption(f"Backend available · Provider: {health.get('provider', 'configured')}")
        if not st.session_state.session_token:
            start_session()
        if not st.session_state.account_loaded:
            load_account()
    except ApiError as exc:
        if exc.status == 401:
            expire_session()
            st.rerun()
        st.error(str(exc))
        st.code("python scripts/run_demo.py", language="bash")
        st.caption(
            "Run this from the project folder. If using separate processes, start the FastAPI backend before the interface."
        )
        if st.session_state.pending_revocation:
            st.caption(
                "The old account has been cleared from this interface. Switching will finish once the previous session can be revoked."
            )
        if st.button("Retry connection"):
            st.rerun()
        return

    with st.sidebar:
        draw_account()
        st.divider()
        st.text_input(
            "Transaction ID (optional)",
            key="transaction_id_input",
            max_chars=100,
            help="Use an ID from your own billing history when asking about a charge.",
        )
        st.checkbox("Ask for human support", key="human_request_input")
        st.caption("Billing tools are read-only. The assistant cannot issue refunds or cancel subscriptions.")

    if not st.session_state.messages:
        st.markdown("### How can we help?")
        st.write("Try “How do I cancel?”, “Show my billing history”, or “I need a refund for a charge.”")
    for event in list(st.session_state.messages):
        draw_event(event)
    draw_tickets()

    message = st.chat_input("Ask a support question", max_chars=2000)
    if message:
        event_id = str(uuid4())
        payload = {
            "message": message,
            "event_id": event_id,
            "request_human": st.session_state.human_request_input,
        }
        if st.session_state.transaction_id_input.strip():
            payload["transaction_id"] = st.session_state.transaction_id_input.strip()
        event = {"id": event_id, "payload": payload, "response": None, "error": None, "delivered": False}
        st.session_state.messages.append(event)
        with st.spinner("Finding the right help…"):
            send_event(event)
        st.rerun()


if __name__ == "__main__":
    main()
