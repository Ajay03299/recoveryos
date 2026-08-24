"""Deterministic baseline-vs-RecoveryOS simulation.

Nothing here is hardcoded. Both arms face the same synthetic population under
the same seeded outcome draws, so the difference reported is produced by the
decision logic rather than chosen by us.

This is a SYNTHETIC evaluation. It is not a claim about production performance.
"""
import random
from dataclasses import dataclass, field

from app.models import Customer, MerchantPolicy, RecoveryCase
from app.models.enums import (
    CustomerSegment, FailureReason, PolicyDecision, RiskType, Strategy,
)
from app.policies.engine import evaluate_policy
from app.recovery.scoring import calculate_recovery_score
from app.recovery.strategies import (
    CONTACT_COST_PAISE, ESCALATION_COST_PAISE, STRATEGY_LIFT, best_strategy,
)

FAILURE_MIX = [
    (FailureReason.UPI_TIMEOUT, 0.18),
    (FailureReason.CARD_DECLINED, 0.14),
    (FailureReason.USER_ABANDONED, 0.16),
    (FailureReason.INSUFFICIENT_FUNDS, 0.11),
    (FailureReason.EXPIRED_CARD, 0.10),
    (FailureReason.PRICE_OBJECTION, 0.10),
    (FailureReason.PRODUCT_UNCERTAINTY, 0.08),
    (FailureReason.TECHNICAL_ERROR, 0.06),
    (FailureReason.PAYMENT_OVERDUE, 0.04),
    (FailureReason.UNKNOWN, 0.03),
]

SEGMENTS = [
    (CustomerSegment.NEW, 0.40), (CustomerSegment.RETURNING, 0.34),
    (CustomerSegment.LOYAL, 0.20), (CustomerSegment.VIP, 0.06),
]


@dataclass
class ArmResult:
    recovered_paise: int = 0
    discount_cost_paise: int = 0
    contact_cost_paise: int = 0
    attempts: int = 0
    contacts: int = 0
    escalations: int = 0
    recoveries: int = 0
    strategy_mix: dict[str, int] = field(default_factory=dict)

    @property
    def net_recovered_paise(self) -> int:
        return self.recovered_paise - self.discount_cost_paise - self.contact_cost_paise


def _weighted(rng: random.Random, choices: list[tuple]) -> object:
    r, total = rng.random(), 0.0
    for value, weight in choices:
        total += weight
        if r <= total:
            return value
    return choices[-1][0]


def _generate_population(rng: random.Random, n: int) -> list[tuple[RecoveryCase, Customer]]:
    population = []
    for i in range(n):
        segment = _weighted(rng, SEGMENTS)
        purchases = {CustomerSegment.NEW: 0, CustomerSegment.RETURNING: rng.randint(1, 5),
                     CustomerSegment.LOYAL: rng.randint(4, 12),
                     CustomerSegment.VIP: rng.randint(10, 40)}[segment]

        customer = Customer(
            id=f"SIMCUS_{i:04d}", name=f"Sim Customer {i}", email=f"sim{i}@example.com",
            phone="+910000000000", segment=segment,
            lifetime_value_paise=purchases * rng.randint(80_000, 400_000),
            previous_purchases=purchases, failed_payments=rng.randint(0, 3),
            last_purchase_at=None, engagement_score=rng.randint(20, 95),
        )
        reason = _weighted(rng, FAILURE_MIX)
        case = RecoveryCase(
            id=f"SIMCASE_{i:04d}", customer_id=customer.id,
            risk_type=(RiskType.OVERDUE_INVOICE if reason == FailureReason.PAYMENT_OVERDUE
                       else RiskType.PAYMENT_FAILURE),
            amount_paise=rng.choice([49_900, 99_900, 249_900, 499_900, 849_900,
                                     1_275_000, 2_499_000, 7_500_000]),
            description="Synthetic at-risk transaction", payment_method="upi",
            failure_reason=reason, attempt_count=rng.randint(1, 3),
            contacts_sent=rng.randint(0, 2), days_overdue=0,
        )
        population.append((case, customer))
    return population


def _converts(rng: random.Random, probability: float, draw: float) -> bool:
    """Shared outcome draw. Both arms face the same luck on the same case."""
    return draw < probability


def default_policy() -> MerchantPolicy:
    """A fully-populated in-memory policy.

    Column defaults are applied by the database on INSERT, so a bare
    MerchantPolicy() has None everywhere. The simulation never touches the DB,
    so every field is set explicitly here.
    """
    return MerchantPolicy(
        id=1, max_discount_pct=10, max_auto_discount_paise=50_000,
        max_recovery_attempts=2, min_recovery_score=45, max_contacts_per_week=2,
        high_value_threshold_paise=5_000_000, min_ai_confidence=0.60,
        payment_actions_allowed=True, refunds_require_approval=True,
    )


def run_simulation(n: int = 200, seed: int = 42,
                   policy: MerchantPolicy | None = None) -> dict:
    rng = random.Random(seed)
    population = _generate_population(rng, n)
    draws = [random.Random(seed + 1_000 + i).random() for i in range(n)]
    policy = policy or default_policy()

    baseline, agent = ArmResult(), ArmResult()
    total_at_risk = 0

    for (case, customer), draw in zip(population, draws):
        total_at_risk += case.amount_paise
        score = calculate_recovery_score(case, customer).score
        base_p = score / 100

        # --- Arm A: baseline. Same reminder to everyone, always. ---
        baseline.attempts += 1
        baseline.contacts += 1
        baseline.contact_cost_paise += CONTACT_COST_PAISE
        baseline.strategy_mix[Strategy.REMINDER] = (
            baseline.strategy_mix.get(Strategy.REMINDER, 0) + 1)
        if _converts(rng, min(base_p * STRATEGY_LIFT[Strategy.REMINDER], 0.95), draw):
            baseline.recovered_paise += case.amount_paise
            baseline.recoveries += 1

        # --- Arm B: RecoveryOS. Diagnose, optimize, then obey the policy. ---
        option = best_strategy(case, score, policy)
        decision = evaluate_policy(
            case=case, customer=customer, policy=policy, strategy=option.strategy,
            score=score, requested_discount_paise=option.discount_paise,
            ai_confidence=0.85,
        )

        if decision.decision is PolicyDecision.BLOCKED:
            # Choosing not to act is a real outcome: no cost, no fatigue.
            agent.strategy_mix["NO_ACTION"] = agent.strategy_mix.get("NO_ACTION", 0) + 1
            continue

        agent.attempts += 1
        agent.strategy_mix[option.strategy] = agent.strategy_mix.get(option.strategy, 0) + 1

        if option.strategy is Strategy.ESCALATE_HUMAN:
            agent.escalations += 1
            agent.contact_cost_paise += ESCALATION_COST_PAISE
        else:
            agent.contacts += 1
            agent.contact_cost_paise += CONTACT_COST_PAISE

        discount = decision.approved_discount_paise
        if _converts(rng, option.probability, draw):
            agent.recovered_paise += case.amount_paise - discount
            agent.discount_cost_paise += discount
            agent.recoveries += 1

    def rate(arm: ArmResult) -> float:
        return round(100 * arm.recoveries / n, 2)

    incremental = agent.net_recovered_paise - baseline.net_recovered_paise
    uplift = (round(100 * incremental / baseline.net_recovered_paise, 2)
              if baseline.net_recovered_paise else 0.0)

    return {
        "seed": seed,
        "case_count": n,
        "total_at_risk_paise": total_at_risk,
        "baseline": {
            "recovered_paise": baseline.recovered_paise,
            "net_recovered_paise": baseline.net_recovered_paise,
            "discount_cost_paise": baseline.discount_cost_paise,
            "contact_cost_paise": baseline.contact_cost_paise,
            "recoveries": baseline.recoveries,
            "recovery_rate_pct": rate(baseline),
            "contacts": baseline.contacts,
            "escalations": baseline.escalations,
            "strategy_mix": {str(k): v for k, v in baseline.strategy_mix.items()},
        },
        "recoveryos": {
            "recovered_paise": agent.recovered_paise,
            "net_recovered_paise": agent.net_recovered_paise,
            "discount_cost_paise": agent.discount_cost_paise,
            "contact_cost_paise": agent.contact_cost_paise,
            "recoveries": agent.recoveries,
            "recovery_rate_pct": rate(agent),
            "contacts": agent.contacts,
            "escalations": agent.escalations,
            "strategy_mix": {str(k): v for k, v in agent.strategy_mix.items()},
        },
        "incremental_net_recovered_paise": incremental,
        "incremental_uplift_pct": uplift,
        "contacts_saved": baseline.contacts - agent.contacts,
        "disclaimer": ("Synthetic evaluation on generated data with a fixed seed. "
                       "Not a claim about production performance."),
    }
