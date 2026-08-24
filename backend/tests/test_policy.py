from app.models import MerchantPolicy
from app.models.enums import PolicyDecision, Strategy
from app.policies.engine import evaluate_policy
from app.recovery.strategies import best_strategy, evaluate_strategies
from tests.test_scoring import make_case, make_customer


def make_policy(**kw) -> MerchantPolicy:
    defaults = dict(
        id=1, max_discount_pct=10, max_auto_discount_paise=50_000,
        max_recovery_attempts=2, min_recovery_score=45, max_contacts_per_week=2,
        high_value_threshold_paise=5_000_000, min_ai_confidence=0.60,
        payment_actions_allowed=True, refunds_require_approval=True,
    )
    return MerchantPolicy(**{**defaults, **kw})


# --- Policy engine ---

def test_healthy_case_is_auto_allowed():
    result = evaluate_policy(make_case(), make_customer(), make_policy(),
                             Strategy.PAYMENT_LINK, score=85, ai_confidence=0.9)
    assert result.decision is PolicyDecision.AUTO_ALLOWED


def test_low_score_is_blocked():
    result = evaluate_policy(make_case(), make_customer(), make_policy(),
                             Strategy.PAYMENT_LINK, score=20, ai_confidence=0.9)
    assert result.decision is PolicyDecision.BLOCKED
    assert "min_recovery_score" in {c.name for c in result.checks if not c.passed}


def test_attempt_limit_exceeded_is_blocked():
    result = evaluate_policy(make_case(attempt_count=5), make_customer(),
                             make_policy(), Strategy.PAYMENT_LINK,
                             score=80, ai_confidence=0.9)
    assert result.decision is PolicyDecision.BLOCKED


def test_contact_fatigue_is_blocked():
    result = evaluate_policy(make_case(contacts_sent=3), make_customer(),
                             make_policy(), Strategy.REMINDER,
                             score=80, ai_confidence=0.9)
    assert result.decision is PolicyDecision.BLOCKED


def test_high_value_requires_human_approval():
    result = evaluate_policy(make_case(amount_paise=7_500_000), make_customer(),
                             make_policy(), Strategy.PAYMENT_LINK,
                             score=80, ai_confidence=0.9)
    assert result.decision is PolicyDecision.REQUIRES_APPROVAL


def test_low_ai_confidence_requires_human_approval():
    result = evaluate_policy(make_case(), make_customer(), make_policy(),
                             Strategy.PAYMENT_LINK, score=80, ai_confidence=0.3)
    assert result.decision is PolicyDecision.REQUIRES_APPROVAL


def test_excessive_discount_is_capped_not_honoured():
    """The agent asking for ₹5,000 off must never get more than the policy cap."""
    result = evaluate_policy(make_case(amount_paise=849_900), make_customer(),
                             make_policy(), Strategy.INCENTIVE, score=70,
                             requested_discount_paise=500_000, ai_confidence=0.9)
    assert result.approved_discount_paise == 50_000


def test_payment_actions_disabled_blocks_payment_strategy():
    result = evaluate_policy(make_case(), make_customer(),
                             make_policy(payment_actions_allowed=False),
                             Strategy.PAYMENT_LINK, score=85, ai_confidence=0.9)
    assert result.decision is PolicyDecision.BLOCKED


def test_escalation_bypasses_score_and_attempt_limits():
    """A human can always be asked to look, even at a hopeless case."""
    result = evaluate_policy(make_case(attempt_count=9, contacts_sent=9),
                             make_customer(), make_policy(),
                             Strategy.ESCALATE_HUMAN, score=5, ai_confidence=0.9)
    assert result.decision is not PolicyDecision.BLOCKED


# --- Expected value optimizer ---

def test_options_are_ranked_by_expected_value():
    options = evaluate_strategies(make_case(), 80, make_policy())
    values = [o.expected_value_paise for o in options]
    assert values == sorted(values, reverse=True)


def test_technical_failure_prefers_payment_link():
    assert best_strategy(make_case(), 85, make_policy()).strategy is Strategy.PAYMENT_LINK


def test_high_value_overdue_prefers_human_escalation():
    """The optimizer should reach for a human on a ₹75,000 invoice on its own."""
    from app.models.enums import FailureReason, RiskType
    case = make_case(amount_paise=7_500_000, risk_type=RiskType.OVERDUE_INVOICE,
                     failure_reason=FailureReason.PAYMENT_OVERDUE, days_overdue=35)
    assert best_strategy(case, 61, make_policy()).strategy is Strategy.ESCALATE_HUMAN


def test_discount_never_exceeds_policy_cap():
    options = evaluate_strategies(make_case(amount_paise=10_000_000), 70, make_policy())
    assert all(o.discount_paise <= 50_000 for o in options)
