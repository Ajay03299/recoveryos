from datetime import datetime, timedelta, timezone

from app.models import Customer, RecoveryCase
from app.models.enums import CustomerSegment, FailureReason, RiskType
from app.recovery.scoring import calculate_recovery_score

NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


def make_customer(**kw) -> Customer:
    defaults = dict(
        id="CUS_T", name="Test", email="t@example.com", phone="+910000000000",
        segment=CustomerSegment.RETURNING, lifetime_value_paise=100000,
        previous_purchases=3, failed_payments=0,
        last_purchase_at=NOW - timedelta(days=10), engagement_score=70,
    )
    return Customer(**{**defaults, **kw})


def make_case(**kw) -> RecoveryCase:
    defaults = dict(
        id="CASE_T", customer_id="CUS_T", risk_type=RiskType.PAYMENT_FAILURE,
        amount_paise=500000, description="Test", payment_method="upi",
        failure_reason=FailureReason.UPI_TIMEOUT, attempt_count=1,
        contacts_sent=0, days_overdue=0,
    )
    return RecoveryCase(**{**defaults, **kw})


def test_score_is_bounded_0_to_100():
    result = calculate_recovery_score(make_case(), make_customer(), NOW)
    assert 0 <= result.score <= 100


def test_technical_failure_scores_above_abandonment():
    customer = make_customer()
    technical = calculate_recovery_score(
        make_case(failure_reason=FailureReason.UPI_TIMEOUT), customer, NOW)
    abandoned = calculate_recovery_score(
        make_case(failure_reason=FailureReason.USER_ABANDONED), customer, NOW)
    assert technical.score > abandoned.score


def test_repeated_attempts_reduce_score():
    customer = make_customer()
    first = calculate_recovery_score(make_case(attempt_count=1), customer, NOW)
    fifth = calculate_recovery_score(make_case(attempt_count=5), customer, NOW)
    assert fifth.score < first.score


def test_contact_fatigue_reduces_score():
    customer = make_customer()
    fresh = calculate_recovery_score(make_case(contacts_sent=0), customer, NOW)
    tired = calculate_recovery_score(make_case(contacts_sent=4), customer, NOW)
    assert tired.score < fresh.score


def test_loyal_customer_outscores_new_customer():
    loyal = make_customer(segment=CustomerSegment.LOYAL, previous_purchases=8,
                          failed_payments=0, engagement_score=90)
    new = make_customer(segment=CustomerSegment.NEW, previous_purchases=0,
                        failed_payments=0, last_purchase_at=None,
                        engagement_score=40)
    assert (calculate_recovery_score(make_case(), loyal, NOW).score
            > calculate_recovery_score(make_case(), new, NOW).score)


def test_breakdown_sums_to_score_when_unclamped():
    result = calculate_recovery_score(make_case(), make_customer(), NOW)
    assert sum(result.breakdown.values()) == result.score


def test_chronic_failure_case_scores_low():
    """Demo Case 5: repeated failures, stale, fatigued — agent must not act."""
    customer = make_customer(previous_purchases=3, failed_payments=6,
                             last_purchase_at=NOW - timedelta(days=140),
                             engagement_score=31)
    case = make_case(failure_reason=FailureReason.INSUFFICIENT_FUNDS,
                     attempt_count=5, contacts_sent=4)
    assert calculate_recovery_score(case, customer, NOW).score < 25
