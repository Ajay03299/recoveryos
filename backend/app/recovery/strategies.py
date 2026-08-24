"""Expected-value ranking of candidate recovery strategies.

The best action is not the one most likely to convert. It is the one with the
highest expected net recovered value after discount, contact and risk costs.
"""
from dataclasses import dataclass

from app.models import MerchantPolicy, RecoveryCase
from app.models.enums import FailureReason, Strategy

CONTACT_COST_PAISE = 200        # ₹2 — messaging + attention cost
ESCALATION_COST_PAISE = 50_000  # ₹500 — human collections time
MAX_PROBABILITY = 0.95          # never claim near-certainty
HIGH_VALUE_RISK_PCT = 3.0       # relationship risk of automating a large account

# Which strategies are even plausible for a given barrier.
STRATEGY_FIT: dict[FailureReason, list[Strategy]] = {
    FailureReason.UPI_TIMEOUT: [Strategy.PAYMENT_LINK, Strategy.RETRY_PAYMENT, Strategy.REMINDER],
    FailureReason.TECHNICAL_ERROR: [Strategy.PAYMENT_LINK, Strategy.RETRY_PAYMENT, Strategy.REMINDER],
    FailureReason.CARD_DECLINED: [Strategy.PAYMENT_METHOD_UPDATE, Strategy.PAYMENT_LINK, Strategy.REMINDER],
    FailureReason.EXPIRED_CARD: [Strategy.PAYMENT_METHOD_UPDATE, Strategy.PAYMENT_LINK, Strategy.REMINDER],
    FailureReason.INSUFFICIENT_FUNDS: [Strategy.REMINDER, Strategy.PAYMENT_LINK, Strategy.INCENTIVE],
    FailureReason.PRODUCT_UNCERTAINTY: [Strategy.ANSWER_OBJECTION, Strategy.INCENTIVE, Strategy.REMINDER],
    FailureReason.PRICE_OBJECTION: [Strategy.INCENTIVE, Strategy.ANSWER_OBJECTION, Strategy.REMINDER],
    FailureReason.USER_ABANDONED: [Strategy.REMINDER, Strategy.PAYMENT_LINK, Strategy.INCENTIVE],
    FailureReason.PAYMENT_OVERDUE: [Strategy.REMINDER, Strategy.PAYMENT_LINK, Strategy.ESCALATE_HUMAN],
    FailureReason.UNKNOWN: [Strategy.REMINDER, Strategy.PAYMENT_LINK],
}

# Multiplier applied to the base recovery probability.
STRATEGY_LIFT: dict[Strategy, float] = {
    Strategy.RETRY_PAYMENT: 0.85,
    Strategy.REMINDER: 0.80,
    Strategy.PAYMENT_LINK: 1.10,
    Strategy.PAYMENT_METHOD_UPDATE: 1.15,
    Strategy.ANSWER_OBJECTION: 1.20,
    Strategy.INCENTIVE: 1.45,
    Strategy.ESCALATE_HUMAN: 1.35,
    Strategy.DO_NOT_CONTACT: 0.0,
}

PAYMENT_STRATEGIES = {
    Strategy.RETRY_PAYMENT,
    Strategy.PAYMENT_LINK,
    Strategy.PAYMENT_METHOD_UPDATE,
}


@dataclass
class StrategyOption:
    strategy: Strategy
    probability: float
    discount_paise: int
    operational_cost_paise: int
    risk_penalty_paise: int
    expected_value_paise: int
    explanation: str


def _proposed_discount(case: RecoveryCase, policy: MerchantPolicy) -> int:
    """Largest discount that stays inside merchant limits. Never invented by the LLM."""
    pct_cap = int(case.amount_paise * policy.max_discount_pct / 100)
    return min(pct_cap, policy.max_auto_discount_paise)


def evaluate_strategies(case: RecoveryCase, score: int,
                        policy: MerchantPolicy) -> list[StrategyOption]:
    """Rank every plausible strategy by expected net recovered value."""
    base_p = score / 100
    candidates = list(STRATEGY_FIT.get(case.failure_reason, [Strategy.REMINDER]))
    if Strategy.ESCALATE_HUMAN not in candidates:
        candidates.append(Strategy.ESCALATE_HUMAN)

    options: list[StrategyOption] = []
    for strategy in candidates:
        p = min(base_p * STRATEGY_LIFT[strategy], MAX_PROBABILITY)

        discount = _proposed_discount(case, policy) if strategy is Strategy.INCENTIVE else 0
        op_cost = (ESCALATION_COST_PAISE if strategy is Strategy.ESCALATE_HUMAN
                   else CONTACT_COST_PAISE)

        # Automating a large account carries relationship risk; a human does not.
        # A missing threshold must fail CLOSED: treat it as 0 so every amount
        # counts as high-value, rather than silently disabling the guardrail.
        threshold = policy.high_value_threshold_paise
        threshold = 0 if threshold is None else threshold
        risk = 0
        if (case.amount_paise > threshold
                and strategy is not Strategy.ESCALATE_HUMAN):
            risk = int(case.amount_paise * HIGH_VALUE_RISK_PCT / 100)

        net_recoverable = case.amount_paise - discount
        ev = int(p * net_recoverable) - op_cost - risk

        options.append(StrategyOption(
            strategy=strategy,
            probability=round(p, 3),
            discount_paise=discount,
            operational_cost_paise=op_cost,
            risk_penalty_paise=risk,
            expected_value_paise=ev,
            explanation=(
                f"{int(p * 100)}% recovery probability on "
                f"{net_recoverable} paise net of discount, minus "
                f"{op_cost + risk} paise in costs."
            ),
        ))

    return sorted(options, key=lambda o: o.expected_value_paise, reverse=True)


def best_strategy(case: RecoveryCase, score: int,
                  policy: MerchantPolicy) -> StrategyOption:
    return evaluate_strategies(case, score, policy)[0]
