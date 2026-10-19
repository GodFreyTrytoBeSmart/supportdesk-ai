"""Transport-level provider tests; these never call a real model service."""

import json
from dataclasses import replace

import httpx
import pytest

from app.config import Settings
from app.intelligence import Intelligence, rule_classify
from app.knowledge import KnowledgeBase
from app.models import Intent
from app.privacy import redact_sensitive


def provider(handler, **overrides):
    settings = replace(
        Settings(),
        llm_provider="openai_compatible",
        llm_base_url="http://model.test/v1",
        llm_model="configured-test-model",
        llm_api_key="server-only-key",
        **overrides,
    )
    return Intelligence(
        settings, client=httpx.Client(transport=httpx.MockTransport(handler)), sleeper=lambda _: None
    )


def completion(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def valid_classification(intent="BILLING_HISTORY", confidence=0.94, requires_authentication=True):
    return json.dumps(
        {"intent": intent, "confidence": confidence, "requires_authentication": requires_authentication}
    )


def test_mock_does_not_need_a_model_or_key():
    result = Intelligence(Settings()).classify("What is my subscription status?")
    assert result.classification.intent == Intent.SUBSCRIPTION_STATUS
    assert result.classification.requires_authentication is True
    assert result.source == "mock"


@pytest.mark.parametrize(
    "message",
    [
        "I want a refund",
        "Cancel and refund my subscription",
        "A suspicious charge appeared",
        "You charged me twice",
        "I don't recognize that payment",
        "I never authorized that charge",
        "There is a charge I do not recognize",
        "I was billed twice",
        "I want to dispute a charge",
    ],
)
def test_financial_policy_override_never_calls_model(message):
    def unexpected(_):
        pytest.fail("Financial policy override must happen before the model")

    result = provider(unexpected).classify(message)
    assert result.classification.intent == Intent.REFUND_REQUEST
    assert result.source == "policy_override"


@pytest.mark.parametrize(
    "message",
    [
        "Show my subscription status and billing history",
        "Show my receipts and cancel my subscription",
        "When does my plan renew and show transactions?",
    ],
)
def test_multi_intent_requires_clarification_before_model(message):
    def unexpected(_):
        pytest.fail("Ambiguous private requests must not reach the model")

    result = provider(unexpected).classify(message)
    assert result.classification.intent == Intent.UNKNOWN
    assert result.source == "policy_override"


def test_model_auth_flag_cannot_disable_authentication():
    result = provider(lambda _: completion(valid_classification(requires_authentication=False))).classify(
        "Show payments"
    )
    assert result.classification.requires_authentication is True
    assert result.source == "llm"


def test_model_auth_flag_cannot_make_public_instruction_private():
    result = provider(lambda _: completion(valid_classification("CANCEL_SUBSCRIPTION"))).classify(
        "How can I cancel?"
    )
    assert result.classification.requires_authentication is False


@pytest.mark.parametrize(
    "content,reason",
    [
        ("not JSON", "invalid_model_json"),
        ('```json\n{"intent":"UNKNOWN"}\n```', "invalid_model_json"),
        (
            '{"intent":"UNSUPPORTED","confidence":0.9,"requires_authentication":true}',
            "invalid_classification_schema",
        ),
        (
            '{"intent":"BILLING_HISTORY","confidence":1.5,"requires_authentication":true}',
            "invalid_classification_schema",
        ),
        (
            '{"intent":"BILLING_HISTORY","confidence":"0.9","requires_authentication":true}',
            "invalid_classification_schema",
        ),
        (
            '{"intent":"BILLING_HISTORY","confidence":true,"requires_authentication":true}',
            "invalid_classification_schema",
        ),
        (
            '{"intent":"BILLING_HISTORY","confidence":0.9,"requires_authentication":"false"}',
            "invalid_classification_schema",
        ),
        (
            '{"intent":"BILLING_HISTORY","confidence":0.9,"requires_authentication":true,"user_id":"blair"}',
            "invalid_classification_schema",
        ),
        ('["BILLING_HISTORY"]', "invalid_classification_schema"),
        (valid_classification(confidence=0.3), "low_confidence"),
    ],
)
def test_invalid_or_uncertain_model_output_falls_back_without_retry(content, reason):
    calls = []

    def handler(request):
        calls.append(request)
        return completion(content)

    result = provider(handler).classify("Show my transactions")
    assert result.classification.intent == Intent.BILLING_HISTORY
    assert result.source == "mock_fallback"
    assert result.fallback_reason == reason
    assert len(calls) == 1


def test_timeout_has_bounded_retries_and_safe_fallback():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("Secret provider response must not be reflected", request=request)

    result = provider(handler, llm_max_attempts=2).classify("Show my transactions")
    assert len(calls) == 2
    assert result.fallback_reason == "model_timeout"
    assert result.classification.intent == Intent.BILLING_HISTORY


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_status_then_success(status):
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(status, json={"error": "upstream"})
            if len(calls) == 1
            else completion(valid_classification())
        )

    intelligence = provider(handler)
    intelligence.sleeper = sleeps.append
    result = intelligence.classify("Show my transactions")
    assert result.source == "llm"
    assert len(calls) == 2
    assert sleeps == [0.2]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_nonretryable_status_including_unsupported_json_mode(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "json_object unsupported"})

    result = provider(handler).classify("Show my transactions")
    assert result.source == "mock_fallback"
    assert result.fallback_reason == "model_http_error"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "envelope", [{"choices": []}, {"choices": [{"message": {"content": None}}]}, {"error": "bad shape"}]
)
def test_invalid_provider_envelope(envelope):
    result = provider(lambda _: httpx.Response(200, json=envelope)).classify("Show my transactions")
    assert result.fallback_reason == "invalid_provider_response"


def test_transport_connection_failure_is_bounded():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("model is down", request=request)

    result = provider(handler).classify("Show my transactions")
    assert result.fallback_reason == "model_unavailable"
    assert len(calls) == 2


def test_request_is_real_chat_completions_json_and_redacts_user_secrets():
    seen = []

    def handler(request):
        seen.append(request)
        return completion(valid_classification())

    provider(handler).classify(
        "Show payments: alice@example.test 4242 4242 4242 4242 Bearer private-token api_key=private-key"
    )
    request = seen[0]
    payload = json.loads(request.content)
    assert str(request.url) == "http://model.test/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer server-only-key"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["model"] == "configured-test-model"
    assert "tools" not in payload
    context = payload["messages"][1]["content"]
    for secret in ("alice@example.test", "4242", "private-token", "private-key", "server-only-key"):
        assert secret not in context


def test_redaction_is_best_effort_and_handles_common_secret_forms():
    text = redact_sensitive(
        "a@example.com 4242424242424242 sk-proj-abcdefgh12345 password=hunter2 access_token=abc"
    )
    assert "example.com" not in text and "4242" not in text and "hunter2" not in text
    assert "abcdefgh" not in text and "=abc" not in text


def test_public_answer_is_built_from_existing_articles():
    article = KnowledgeBase().get("privacy")
    intelligence = provider(lambda _: completion('{"article_ids":["privacy"]}'))
    answer, fallback = intelligence.public_answer("What is your privacy policy?", [article])
    assert answer == f"{article.title}: {article.content}"
    assert fallback is None


@pytest.mark.parametrize(
    "content,reason",
    [
        ('{"article_ids":["invented_refund_guarantee"]}', "unsupported_article_id"),
        ('{"article_ids":["privacy","privacy"]}', "unsupported_article_id"),
        ('{"article_ids":["privacy"],"answer":"Refunds are guaranteed"}', "invalid_article_selection"),
        ('{"article_ids":[]}', "invalid_article_selection"),
        ('{"article_ids":[123]}', "invalid_article_selection"),
        ("not json", "invalid_model_json"),
    ],
)
def test_untrusted_public_answer_never_adds_policy_text(content, reason):
    article = KnowledgeBase().get("privacy")
    answer, fallback = provider(lambda _: completion(content)).public_answer("Privacy?", [article])
    assert answer == f"{article.title}: {article.content}"
    assert fallback == reason
    assert "guarantee" not in answer


def test_missing_evidence_does_not_call_model():
    def unexpected(_):
        pytest.fail("Do not request a policy answer without evidence")

    answer, fallback = provider(unexpected).public_answer("What is the weather?", [])
    assert "do not have" in answer
    assert fallback is None


def test_retrieval_is_bounded_specific_and_can_abstain():
    knowledge = KnowledgeBase()
    assert knowledge.search("cancel via Google Play")[0].id == "cancel_google_play"
    assert knowledge.search("What is your privacy policy?")[0].id == "privacy"
    assert knowledge.search("zebras quantum mechanics") == []
    assert knowledge.search("cancel", limit=0) == []
    assert len(knowledge.search("cancel", limit=2)) == 2
    assert knowledge.get("not_an_article") is None


def test_unknown_rules_and_public_cancellation():
    assert rule_classify("Hello").intent == Intent.UNKNOWN
    result = rule_classify("Cancel my subscription")
    assert result.intent == Intent.CANCEL_SUBSCRIPTION
    assert result.requires_authentication is False
