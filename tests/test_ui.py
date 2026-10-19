"""User-visible isolation, delivery, and safe retry checks without real networking."""

import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

from streamlit.testing.v1 import AppTest

UI = Path(__file__).resolve().parents[1] / "ui" / "streamlit_app.py"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class DemoApi:
    def __init__(self):
        self.tokens = {}
        self.calls = []
        self.fail_chat_once = False
        self.unavailable = False
        self.expired = False

    def __call__(self, request, timeout):
        if self.unavailable:
            raise URLError("connection refused")
        path = request.full_url.split("8000", 1)[-1]
        body = json.loads(request.data) if request.data else None
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        persona = self.tokens.get(token, "guest")
        self.calls.append((request.method, path, body, persona))
        if path == "/health":
            result = {"status": "ok", "provider": "mock"}
        elif path == "/demo/sessions" and request.method == "POST":
            persona = body["persona"]
            token = f"session-{persona}-{len(self.calls)}"
            self.tokens[token] = persona
            result = {
                "access_token": token,
                "customer": None if persona == "guest" else {"id": persona, "name": persona.title()},
            }
        elif path == "/demo/sessions" and request.method == "DELETE":
            self.tokens.pop(token, None)
            result = {}
        elif self.expired:
            raise HTTPError(request.full_url, 401, "expired", {}, io.BytesIO(b"{}"))
        elif path == "/me/subscription":
            result = {
                "subscription": {
                    "id": f"sub-{persona}",
                    "plan": f"{persona.title()} plan",
                    "status": "active",
                    "channel": "web",
                    "renewal_date": "2026-11-01",
                    "amount_cents": 1299,
                    "currency": "USD",
                }
            }
        elif path == "/me/tickets":
            result = {"tickets": []}
        elif path == "/chat":
            if self.fail_chat_once:
                self.fail_chat_once = False
                raise URLError("lost response")
            result = {
                "response_id": body["event_id"],
                "receipt_token": "receipt-secret",
                "message": f"Help for {persona}: {body['message']}",
                "intent": "GENERAL_FAQ",
                "requires_authentication": False,
                "confidence": 0.9,
                "sources": [],
                "data": {},
                "ticket": None,
                "delivery_status": "generated",
                "case_status": "answered",
                "debug": {
                    "classification_source": "mock",
                    "selected_tool": "get_help_articles",
                    "routing_decision": "public_help",
                    "execution_result": "ok",
                },
            }
        elif path.endswith("/ack"):
            result = {"response_id": path.split("/")[2], "delivery_status": "delivered"}
        else:
            raise AssertionError(f"Unexpected request: {request.method} {path}")
        return FakeResponse(result)


def launch(monkeypatch):
    backend = DemoApi()
    monkeypatch.setattr("urllib.request.urlopen", backend)
    monkeypatch.setenv("API_URL", "http://127.0.0.1:8000")
    return AppTest.from_file(str(UI), default_timeout=10).run(), backend


def find_button(app, label):
    return next(button for button in app.button if button.label == label)


def test_guest_and_account_switch_clear_private_chat(monkeypatch):
    app, backend = launch(monkeypatch)
    assert not app.exception
    assert not any(path.startswith("/me/") for _, path, _, _ in backend.calls)
    app.selectbox[0].select("alex").run()
    app.chat_input[0].set_value("Show my account").run()
    assert app.session_state["messages"]
    old_token = app.session_state["session_token"]
    app.selectbox[0].select("blair").run()
    assert not app.exception
    assert app.session_state["messages"] == []
    assert app.session_state["subscription"]["plan"] == "Blair plan"
    assert old_token not in backend.tokens
    assert app.session_state["transaction_id_input"] == ""
    assert not app.session_state["human_request_input"]


def test_delivery_requires_explicit_acknowledgement(monkeypatch):
    app, backend = launch(monkeypatch)
    app.chat_input[0].set_value("How do I sign in?").run()
    assert not app.exception
    assert not any(path.endswith("/ack") for _, path, _, _ in backend.calls)
    assert not app.session_state["messages"][0]["delivered"]
    find_button(app, "Mark as received").click().run()
    assert not app.exception
    assert app.session_state["messages"][0]["delivered"]
    assert sum(path.endswith("/ack") for _, path, _, _ in backend.calls) == 1


def test_transport_failure_retry_reuses_exact_submission(monkeypatch):
    app, backend = launch(monkeypatch)
    backend.fail_chat_once = True
    app.chat_input[0].set_value("I need a refund").run()
    first = [body for _, path, body, _ in backend.calls if path == "/chat"][0]
    find_button(app, "Retry this message").click().run()
    submissions = [body for _, path, body, _ in backend.calls if path == "/chat"]
    assert submissions == [first, first]
    assert len(app.session_state["messages"]) == 1
    assert app.session_state["messages"][0]["response"]
    assert not app.exception


def test_backend_down_is_recoverable_without_traceback(monkeypatch):
    backend = DemoApi()
    backend.unavailable = True
    monkeypatch.setattr("urllib.request.urlopen", backend)
    app = AppTest.from_file(str(UI), default_timeout=10).run()
    assert not app.exception
    assert app.error
    assert find_button(app, "Retry connection")
    backend.unavailable = False
    find_button(app, "Retry connection").click().run()
    assert not app.exception
    assert app.chat_input


def test_expired_session_clears_account_and_pending_chat(monkeypatch):
    app, backend = launch(monkeypatch)
    app.selectbox[0].select("alex").run()
    backend.expired = True
    app.chat_input[0].set_value("Show my billing history").run()
    assert not app.exception
    assert app.session_state["messages"] == []
    assert app.session_state["customer"] is None
    assert app.session_state["subscription"] is None
    assert app.session_state["session_token"] is None
    assert find_button(app, "Start a fresh demo session")


def test_reviewer_requires_explicit_key_and_can_lock(monkeypatch):
    backend = DemoApi()
    reviewer_calls = []

    def review_api(request, timeout):
        if request.full_url.endswith("/review/tickets"):
            key = request.headers.get("X-reviewer-key")
            reviewer_calls.append(key)
            if key != "review-secret":
                raise HTTPError(request.full_url, 403, "denied", {}, io.BytesIO(b"{}"))
            return FakeResponse(
                {
                    "tickets": [
                        {
                            "id": "tkt_example",
                            "customer_ref": "usr_alex",
                            "category": "REFUND_REQUEST",
                            "description": "Please review this charge",
                            "status": "open",
                            "transaction_id": "tx_alex_001",
                            "created_at": "2026-10-18",
                            "escalation_reason": "Financial review required",
                        }
                    ]
                }
            )
        return backend(request, timeout)

    monkeypatch.setattr("urllib.request.urlopen", review_api)
    monkeypatch.setenv("REVIEWER_API_KEY", "must-not-be-read-by-ui")
    page = UI.parent / "pages" / "1_Human_Review.py"
    app = AppTest.from_file(str(page), default_timeout=10).run()
    assert not app.exception
    assert reviewer_calls == []
    app.text_input[0].set_value("wrong-key")
    find_button(app, "Unlock review queue").click().run()
    assert app.error
    assert "reviewer_key_active" not in app.session_state
    app.text_input[0].set_value("review-secret")
    find_button(app, "Unlock review queue").click().run()
    assert not app.exception
    assert len(app.session_state["reviewer_tickets"]) == 1
    assert reviewer_calls[0] == "wrong-key"
    assert all(key == "review-secret" for key in reviewer_calls[1:])
    find_button(app, "Lock review queue").click().run()
    assert "reviewer_tickets" not in app.session_state
    assert "reviewer_key_active" not in app.session_state
