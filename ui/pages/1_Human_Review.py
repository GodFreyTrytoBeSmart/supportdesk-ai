"""Separate, explicitly unlocked view of the minimal human-review queue."""

import streamlit as st

from ui.streamlit_app import ApiError, api


def lock_review() -> None:
    st.session_state.pop("reviewer_key_active", None)
    st.session_state.pop("reviewer_key_input", None)
    st.session_state.pop("reviewer_tickets", None)


def main() -> None:
    st.set_page_config(page_title="Human Review · SupportDesk AI", page_icon="📥", layout="centered")
    st.title("Human review")
    st.caption("Independent portfolio demo · synthetic support tickets only")
    st.info(
        "Reviewer access is separate from the customer selector. This queue records requests for investigation; it does not issue refunds or resolve cases."
    )

    if st.session_state.get("reviewer_notice"):
        st.warning(st.session_state.pop("reviewer_notice"))
    if not st.session_state.get("reviewer_key_active"):
        with st.form("reviewer_unlock"):
            key = st.text_input("Reviewer key", type="password", key="reviewer_key_input")
            unlock = st.form_submit_button("Unlock review queue")
        st.caption(
            "Disabled by default. The local operator must configure REVIEWER_API_KEY on the backend and enter that same key here."
        )
        if unlock:
            if not key:
                st.warning("Enter the reviewer key to continue.")
                return
            try:
                result = api("GET", "/review/tickets", reviewer_key=key)
            except ApiError as exc:
                if exc.status == 403:
                    st.error("Reviewer access was denied. Check the configured key.")
                elif exc.status == 503:
                    st.error(
                        "The review queue is unavailable. Check that reviewer access is enabled and the backend is healthy."
                    )
                else:
                    st.error(str(exc))
                return
            st.session_state.reviewer_key_active = key
            st.session_state.reviewer_tickets = result.get("tickets", [])
            st.session_state.pop("reviewer_key_input", None)
            st.rerun()
        return

    controls = st.columns(2)
    with controls[0]:
        st.button("Refresh queue", use_container_width=True)
    with controls[1]:
        if st.button("Lock review queue", use_container_width=True):
            lock_review()
            st.rerun()
    try:
        result = api("GET", "/review/tickets", reviewer_key=st.session_state.reviewer_key_active)
        st.session_state.reviewer_tickets = result.get("tickets", [])
    except ApiError:
        lock_review()
        st.session_state.reviewer_notice = (
            "Reviewer access ended or the queue is unavailable. Unlock again to continue."
        )
        st.rerun()
    st.caption(
        "Up to the 100 most recent tickets. Descriptions receive best-effort redaction; use synthetic data only."
    )
    tickets = st.session_state.get("reviewer_tickets", [])
    if not tickets:
        st.success("No tickets in the review queue yet.")
    for ticket in tickets:
        with st.container(border=True):
            st.markdown(f"**{ticket.get('id', 'Ticket')}** · {ticket.get('status', 'open')}")
            st.caption(f"Customer reference: {ticket.get('customer_ref', '')} · {ticket.get('category', '')}")
            st.write(ticket.get("description", ""))
            if ticket.get("transaction_id"):
                st.caption(f"Verified transaction reference: {ticket['transaction_id']}")
            st.caption(f"Created: {ticket.get('created_at', '')}")
            st.write(ticket.get("escalation_reason", ""))


if __name__ == "__main__":
    main()
