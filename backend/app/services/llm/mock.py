"""Deterministic offline provider.

Not random and not hardcoded per case: it derives its answer from the same
case facts a real model would read. Same input always yields the same output,
which makes the demo and the simulation reproducible.
"""
import json
import re
from typing import TypeVar

from pydantic import BaseModel

from app.models.enums import FailureReason, Strategy
from app.services.llm import LLMProvider

T = TypeVar("T", bound=BaseModel)

# How legible is each failure signal? Ambiguous causes get low confidence,
# which the policy engine converts into a human approval requirement.
CONFIDENCE: dict[str, float] = {
    FailureReason.EXPIRED_CARD: 0.93,
    FailureReason.UPI_TIMEOUT: 0.91,
    FailureReason.TECHNICAL_ERROR: 0.88,
    FailureReason.CARD_DECLINED: 0.84,
    FailureReason.PAYMENT_OVERDUE: 0.81,
    FailureReason.INSUFFICIENT_FUNDS: 0.79,
    FailureReason.PRODUCT_UNCERTAINTY: 0.76,
    FailureReason.PRICE_OBJECTION: 0.74,
    FailureReason.USER_ABANDONED: 0.62,
    FailureReason.UNKNOWN: 0.45,
}

DIAGNOSIS: dict[str, str] = {
    FailureReason.UPI_TIMEOUT: (
        "The UPI collect request expired before the customer authorised it. The customer "
        "reached the payment step, so intent is intact and a fresh attempt should convert."),
    FailureReason.TECHNICAL_ERROR: (
        "The payment attempt failed on a provider-side error rather than a customer "
        "decision. Nothing suggests the customer changed their mind."),
    FailureReason.EXPIRED_CARD: (
        "The saved card on this subscription has expired. This is an instrument problem, "
        "not churn — the customer has not cancelled."),
    FailureReason.CARD_DECLINED: (
        "The issuing bank declined the transaction. An alternative payment method is the "
        "most direct path to completion."),
    FailureReason.INSUFFICIENT_FUNDS: (
        "The account lacked sufficient balance at the time of the attempt. Repeated "
        "retries against the same instrument are unlikely to succeed."),
    FailureReason.PRODUCT_UNCERTAINTY: (
        "The customer engaged repeatedly but did not complete, and raised a product "
        "question. Uncertainty about fit, not price, appears to be the blocker."),
    FailureReason.PRICE_OBJECTION: (
        "Engagement is high but the customer stalls at the cart. Price sensitivity is the "
        "most likely barrier."),
    FailureReason.PAYMENT_OVERDUE: (
        "A high-value invoice is materially overdue despite a clean payment history. This "
        "reads as a process delay rather than an unwillingness to pay."),
    FailureReason.USER_ABANDONED: (
        "The customer left before reaching payment authorisation. Revealed intent is weak."),
    FailureReason.UNKNOWN: (
        "The available signals do not identify a clear cause. Confidence is low and this "
        "case should be reviewed by a human."),
}

BARRIER: dict[str, str] = {
    FailureReason.UPI_TIMEOUT: "Expired payment session",
    FailureReason.TECHNICAL_ERROR: "Provider-side payment failure",
    FailureReason.EXPIRED_CARD: "Expired payment instrument",
    FailureReason.CARD_DECLINED: "Issuer decline",
    FailureReason.INSUFFICIENT_FUNDS: "Insufficient balance",
    FailureReason.PRODUCT_UNCERTAINTY: "Unanswered product question",
    FailureReason.PRICE_OBJECTION: "Price sensitivity",
    FailureReason.PAYMENT_OVERDUE: "Accounts-payable delay",
    FailureReason.USER_ABANDONED: "Low purchase intent",
    FailureReason.UNKNOWN: "Undetermined",
}


def _field(prompt: str, key: str, default: str = "") -> str:
    match = re.search(rf"^{key}:\s*(.+)$", prompt, re.MULTILINE)
    return match.group(1).strip() if match else default


def _int_field(prompt: str, key: str, default: int = 0) -> int:
    raw = _field(prompt, key)
    match = re.search(r"-?\d+", raw)
    return int(match.group()) if match else default


class MockProvider(LLMProvider):
    name = "mock"
    model = "deterministic-v1"

    def _generate(self, system: str, prompt: str, schema: type[T]) -> T:
        reason = _field(prompt, "failure_reason", FailureReason.UNKNOWN)
        if reason not in CONFIDENCE:
            reason = FailureReason.UNKNOWN

        if schema.__name__ == "AnalystOutput":
            engagement = _int_field(prompt, "engagement_score", 50)
            purchases = _int_field(prompt, "previous_purchases", 0)
            attempts = _int_field(prompt, "attempt_count", 1)

            # Reaching payment authorisation is stronger evidence than browsing.
            reached_payment = reason not in (
                FailureReason.USER_ABANDONED, FailureReason.PRODUCT_UNCERTAINTY,
                FailureReason.PRICE_OBJECTION)
            intent = engagement
            intent += 15 if reached_payment else -5
            intent += min(purchases * 3, 12)
            intent -= min(max(attempts - 1, 0) * 4, 16)
            intent = max(0, min(100, intent))

            evidence = [
                f"Failure signal recorded as {reason}.",
                f"{purchases} previous successful purchases on this account.",
                f"Engagement score {engagement}/100.",
            ]
            if attempts > 1:
                evidence.append(f"{attempts} payment attempts already made.")

            payload = {
                "failure_reason": reason,
                "customer_intent": intent,
                "diagnosis": DIAGNOSIS[reason],
                "barrier": BARRIER[reason],
                "confidence": CONFIDENCE[reason],
                "evidence": evidence,
            }
            return schema.model_validate(payload)

        if schema.__name__ == "StrategistOutput":
            # The optimizer's ranking is supplied in the prompt; the mock agrees
            # with the top-ranked option, as a well-behaved model should.
            top = _field(prompt, "top_ranked_strategy", Strategy.REMINDER)
            if top not in set(Strategy):
                top = Strategy.REMINDER
            discount = _int_field(prompt, "top_ranked_discount_paise", 0)
            confidence = CONFIDENCE[reason]

            payload = {
                "strategy": top,
                "reason": (
                    f"{BARRIER[reason]} is the operative blocker. {top} carries the "
                    "highest expected net recovered value among the permitted options "
                    "after discount, contact and relationship-risk costs."),
                "confidence": confidence,
                "requested_discount_paise": discount,
                "requires_human_approval": confidence < 0.60,
            }
            return schema.model_validate(payload)

        raise NotImplementedError(f"MockProvider has no handler for {schema.__name__}")
