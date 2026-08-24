"""Transparent recovery propensity score.

This is a heuristic model, NOT a trained ML model. Every component is
explainable and surfaced to the merchant in the dashboard.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models import Customer, RecoveryCase
from app.models.enums import CustomerSegment, FailureReason

# How recoverable is this failure type, independent of the customer? (0-30)
# Rationale: reaching payment authorization is strong revealed intent, so
# technical failures score highest. Intent-related drop-offs score lowest.
FAILURE_RECOVERABILITY: dict[FailureReason, int] = {
    FailureReason.UPI_TIMEOUT: 30,
    FailureReason.TECHNICAL_ERROR: 28,
    FailureReason.EXPIRED_CARD: 26,
    FailureReason.CARD_DECLINED: 22,
    FailureReason.PRODUCT_UNCERTAINTY: 22,
    FailureReason.PRICE_OBJECTION: 19,
    FailureReason.PAYMENT_OVERDUE: 18,
    FailureReason.INSUFFICIENT_FUNDS: 12,
    FailureReason.UNKNOWN: 12,
    FailureReason.USER_ABANDONED: 10,
}

SEGMENT_VALUE: dict[CustomerSegment, int] = {
    CustomerSegment.VIP: 15,
    CustomerSegment.LOYAL: 12,
    CustomerSegment.RETURNING: 8,
    CustomerSegment.NEW: 4,
}

ATTEMPT_PENALTY_EACH = 5
ATTEMPT_PENALTY_CAP = 25
CONTACT_PENALTY_EACH = 3
CONTACT_PENALTY_CAP = 12


@dataclass
class ScoreResult:
    score: int
    breakdown: dict[str, int] = field(default_factory=dict)
    rationale: list[str] = field(default_factory=list)

    @property
    def band(self) -> str:
        if self.score >= 70:
            return "HIGH"
        if self.score >= 45:
            return "MEDIUM"
        return "LOW"


def _payment_history_points(customer: Customer) -> int:
    """0-20. Track record of actually completing payments."""
    total = customer.previous_purchases + customer.failed_payments
    if total == 0:
        return 10  # no evidence either way — neutral, don't punish new customers
    ratio = customer.previous_purchases / total
    return round(20 * ratio)


def _recency_points(customer: Customer, now: datetime) -> int:
    """0-15. A recent buyer is a warmer prospect."""
    if customer.last_purchase_at is None:
        return 7
    last = customer.last_purchase_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    days = (now - last).days
    if days <= 7:
        return 15
    if days <= 30:
        return 12
    if days <= 60:
        return 9
    if days <= 120:
        return 5
    return 2


def calculate_recovery_score(case: RecoveryCase, customer: Customer,
                             now: datetime | None = None) -> ScoreResult:
    now = now or datetime.now(timezone.utc)

    failure = FAILURE_RECOVERABILITY.get(case.failure_reason, 12)
    history = _payment_history_points(customer)
    recency = _recency_points(customer, now)
    value = SEGMENT_VALUE.get(customer.segment, 4)
    engagement = round(20 * min(customer.engagement_score, 100) / 100)

    attempt_penalty = -min(max(case.attempt_count - 1, 0) * ATTEMPT_PENALTY_EACH,
                           ATTEMPT_PENALTY_CAP)
    contact_penalty = -min(case.contacts_sent * CONTACT_PENALTY_EACH,
                           CONTACT_PENALTY_CAP)

    breakdown = {
        "failure_recoverability": failure,
        "payment_history": history,
        "recency": recency,
        "customer_value": value,
        "engagement": engagement,
        "repeated_attempt_penalty": attempt_penalty,
        "contact_fatigue_penalty": contact_penalty,
    }

    score = max(0, min(100, sum(breakdown.values())))

    rationale = [
        f"{case.failure_reason} is scored {failure}/30 for recoverability.",
        f"{customer.previous_purchases} successful vs {customer.failed_payments} "
        f"failed payments → {history}/20.",
        f"Customer segment {customer.segment} → {value}/15.",
        f"Engagement signal {customer.engagement_score}/100 → {engagement}/20.",
    ]
    if attempt_penalty:
        rationale.append(f"{case.attempt_count} attempts already made → {attempt_penalty}.")
    if contact_penalty:
        rationale.append(f"{case.contacts_sent} prior contacts → {contact_penalty} (fatigue).")

    return ScoreResult(score=score, breakdown=breakdown, rationale=rationale)
