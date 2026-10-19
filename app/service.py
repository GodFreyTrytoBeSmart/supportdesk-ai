"""Explicit workflow: classification suggests; deterministic code authorizes and acts."""

import re

from app.auth import AuthContext
from app.db import Database
from app.intelligence import Intelligence
from app.knowledge import KnowledgeBase
from app.models import AUTH_INTENTS, ChatRequest, ChatResponse, DebugInfo, Intent, Source
from app.tools import SupportTools, get_help_articles, record_response


class SupportService:
    def __init__(self, db: Database, intelligence: Intelligence):
        self.db = db
        self.intelligence = intelligence
        self.knowledge = KnowledgeBase()

    def handle(self, request: ChatRequest, context: AuthContext) -> ChatResponse:
        result = self.intelligence.classify(request.message)
        intent = result.classification.intent
        tools = SupportTools(self.db, context)
        needs_human = request.request_human or bool(
            re.search(
                r"\bhuman\b|support agent|still (?:not working|broken)|already tried|unresolved"
                r"|can(?:not|'t) cancel|cancellation failed|no cancel button",
                request.message.lower(),
            )
        )
        requires_auth = intent in AUTH_INTENTS or needs_human
        data, articles, ticket = {}, [], None
        case_status = "answered"
        selected_tool, routing = "none", "clarify"
        fallback = result.fallback_reason
        execution = "ok"

        if requires_auth and not context.user_id:
            case_status = "needs_authentication"
            routing = "authentication_required"
            message = "Please select a demo customer before I access private records or create a support ticket. Public help articles are available in Guest mode."
        elif intent == Intent.REFUND_REQUEST or needs_human:
            selected_tool, routing = "create_support_ticket", "human_review"
            matches = set(re.findall(r"\btx_[a-z0-9_]{1,60}\b", request.message))
            transaction_id = request.transaction_id or (next(iter(matches)) if len(matches) == 1 else None)
            ticket, duplicate = tools.create_support_ticket(
                intent, request.message, transaction_id, event_id=request.event_id
            )
            case_status = "escalated"
            data = {"deduplicated": duplicate}
            execution = "duplicate_reused" if duplicate else "ticket_created"
            message = (
                f"{'Existing' if duplicate else 'New'} support ticket {ticket.id} is open for human review. "
                "No refund, cancellation, or other financial change has been performed. "
                + (
                    "The transaction was verified against your account."
                    if ticket.transaction_id
                    else "No transaction is linked. A reviewer can request more details; do not send card details or passwords."
                )
            )
        elif intent == Intent.SUBSCRIPTION_STATUS:
            selected_tool, routing = "get_my_subscription", "authenticated_account_lookup"
            subscription = tools.get_my_subscription()
            data = {"subscription": subscription}
            if subscription:
                message = (
                    f"Your synthetic {subscription['plan']} subscription is {subscription['status']}. "
                    f"Purchase channel: {subscription['channel']}. "
                    + (
                        f"Recorded renewal date: {subscription['renewal_date']}."
                        if subscription["renewal_date"]
                        else "There is no scheduled renewal in this demo record."
                    )
                )
            else:
                message = "No subscription is recorded for your account. You can request human help if this looks incorrect."
        elif intent == Intent.BILLING_HISTORY:
            selected_tool, routing = "get_my_transactions", "authenticated_account_lookup"
            transactions = tools.get_my_transactions()
            data = {"transactions": transactions}
            lines = [
                f"{row['id']}: {row['currency']} {row['amount_cents'] // 100}.{row['amount_cents'] % 100:02d} · {row['status']} · {row['created_at'][:10]}"
                for row in transactions
            ]
            message = (
                "Your synthetic billing history:\n\n" + "\n\n".join(lines)
                if lines
                else "There are no transactions recorded for your account."
            )
        elif intent == Intent.CANCEL_SUBSCRIPTION:
            routing = "cancellation_guidance"
            selected_tool = "get_help_articles"
            subscription = tools.get_my_subscription() if context.user_id else None
            article = self.knowledge.get(f"cancel_{subscription['channel']}") if subscription else None
            if context.user_id and (not subscription or not article):
                requires_auth = True
                ticket, duplicate = tools.create_support_ticket(
                    intent, request.message, event_id=request.event_id
                )
                case_status, routing = "escalated", "missing_channel_human_review"
                selected_tool = "get_my_subscription + create_support_ticket"
                data = {"deduplicated": duplicate}
                execution = "duplicate_reused" if duplicate else "ticket_created"
                message = (
                    f"I could not verify cancellation instructions for your purchase channel. Support ticket {ticket.id} "
                    "is open for human review. No cancellation or financial change has been performed."
                )
            elif subscription:
                selected_tool = "get_my_subscription + get_help_articles"
                articles = [article] if article else []
                message = (
                    f"Your recorded purchase channel is {subscription['channel']}. "
                    + (
                        article.content
                        if article
                        else "Channel instructions are unavailable. Request human help to continue."
                    )
                    + " No cancellation has been executed by this assistant."
                )
                if subscription["status"] == "canceled":
                    message = "Your demo subscription is already marked canceled. " + message
            else:
                articles = [
                    self.knowledge.get(article_id)
                    for article_id in ("cancel_web", "cancel_app_store", "cancel_google_play")
                ]
                articles = [article for article in articles if article]
                message = (
                    "Cancellation depends on where you purchased. Select a demo customer for the recorded channel, "
                    "or use the matching instructions below. If you cannot identify the channel or these steps fail, request human help.\n\n"
                    + "\n\n".join(f"{article.title}: {article.content}" for article in articles)
                )
        elif intent in {Intent.GENERAL_FAQ, Intent.TECHNICAL_ISSUE}:
            selected_tool, routing = "get_help_articles", "public_knowledge"
            articles = get_help_articles(request.message)
            message, answer_fallback = self.intelligence.public_answer(request.message, articles)
            fallback = fallback or answer_fallback
            if not articles:
                case_status, routing = "needs_clarification", "no_supported_answer"
            if intent == Intent.TECHNICAL_ISSUE:
                message += "\n\nIf this remains unresolved, select a demo customer and request human help."
        else:
            case_status = "needs_clarification"
            message = (
                "I can help with public FAQs, subscription status, billing history, cancellation instructions, refunds, or technical issues. "
                "Please choose one topic or request human help. Multiple account requests are handled one at a time."
            )

        # No generated model text can set ticket status or claim delivery/resolution.
        response_id, receipt = record_response(self.db, context)
        return ChatResponse(
            response_id=response_id,
            receipt_token=receipt,
            message=message,
            intent=intent,
            confidence=result.classification.confidence,
            requires_authentication=requires_auth,
            sources=[Source(id=a.id, title=a.title, source=a.source) for a in articles],
            data=data,
            ticket=ticket,
            case_status=case_status,
            debug=DebugInfo(
                classification_source=result.source,
                fallback_reason=fallback,
                selected_tool=selected_tool,
                routing_decision=routing,
                execution_result=execution,
            ),
        )
