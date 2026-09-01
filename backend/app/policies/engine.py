"""Deterministic merchant guardrails.

The LLM proposes. This module disposes. No AI output reaches a financial
action without passing through here.
"""
from dataclasses import dataclass, field

from app.models import Customer, MerchantPolicy, RecoveryCase
from app.models.enums import PolicyDecision, Strategy
from app.recovery.strategies import LOW_COST_STRATEGIES, PAYMENT_STRATEGIES


@dataclass
class PolicyCheck:
    name: str
    passed: bool
    detail: str
    blocking: bool = False        # hard stop
    needs_approval: bool = False  # human sign-off required


@dataclass
class PolicyResult:
    decision: PolicyDecision
    checks: list[PolicyCheck] = field(default_factory=list)
    approved_discount_paise: int = 0
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "decision": str(self.decision),
            "approved_discount_paise": self.approved_discount_paise,
            "reason": self.reason,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
        }


def evaluate_policy(
    case: RecoveryCase,
    customer: Customer,
    policy: MerchantPolicy,
    strategy: Strategy,
    score: int,
    requested_discount_paise: int = 0,
    ai_confidence: float = 1.0,
) -> PolicyResult:
    checks: list[PolicyCheck] = []

    if strategy is Strategy.DO_NOT_CONTACT:
        return PolicyResult(
            decision=PolicyDecision.BLOCKED,
            checks=[PolicyCheck("do_not_contact", False,
                                "Strategy is DO_NOT_CONTACT.", blocking=True)],
            reason="Agent determined no contact should be made.",
        )

    is_escalation = strategy is Strategy.ESCALATE_HUMAN
    # A reminder costs ~₹2 to send. A discount gives money away and an
    # escalation consumes a person. Applying one score threshold to all three
    # is wrong in both directions: it blocks nearly-free actions that are
    # clearly worth attempting, and it is too permissive for costly ones.
    is_low_cost = strategy in LOW_COST_STRATEGIES

    # --- 1. Recovery score floor ---
    ok = score >= policy.min_recovery_score or is_escalation or is_low_cost
    checks.append(PolicyCheck(
        "min_recovery_score", ok,
        f"Score {score} vs minimum {policy.min_recovery_score}"
        + (" (waived: low-cost action)." if is_low_cost and score < policy.min_recovery_score
           else "."),
        blocking=not ok,
    ))

    # --- 2. Attempt limit ---
    ok = case.attempt_count <= policy.max_recovery_attempts or is_escalation
    checks.append(PolicyCheck(
        "max_recovery_attempts", ok,
        f"{case.attempt_count} attempts vs limit {policy.max_recovery_attempts}.",
        blocking=not ok,
    ))

    # --- 3. Contact fatigue ---
    # The budget exists to protect the customer from being pestered. One more
    # low-cost message at the edge of the limit is a smaller harm than
    # abandoning recoverable revenue, so low-cost actions get one grace contact.
    limit = policy.max_contacts_per_week + (1 if is_low_cost else 0)
    ok = case.contacts_sent < limit or is_escalation
    checks.append(PolicyCheck(
        "max_contacts_per_week", ok,
        f"{case.contacts_sent} contacts sent vs limit {policy.max_contacts_per_week}"
        + (" (+1 grace for a low-cost action)." if is_low_cost else "."),
        blocking=not ok,
    ))

    # --- 4. Payment actions enabled ---
    if strategy in PAYMENT_STRATEGIES:
        ok = policy.payment_actions_allowed
        checks.append(PolicyCheck(
            "payment_actions_allowed", ok,
            "Automated payment actions are "
            f"{'enabled' if ok else 'disabled'} for this merchant.",
            blocking=not ok,
        ))

    # --- 5. Discount ceiling ---
    approved_discount = 0
    if requested_discount_paise > 0:
        pct_cap = int(case.amount_paise * policy.max_discount_pct / 100)
        hard_cap = min(pct_cap, policy.max_auto_discount_paise)
        approved_discount = min(requested_discount_paise, hard_cap)
        within = requested_discount_paise <= hard_cap
        checks.append(PolicyCheck(
            "discount_within_limits", within,
            f"Requested {requested_discount_paise} paise; "
            f"approved {approved_discount} paise (cap {hard_cap}).",
        ))

    # --- 6. High-value transactions need a human ---
    high_value = case.amount_paise > policy.high_value_threshold_paise
    if high_value and not is_escalation:
        checks.append(PolicyCheck(
            "high_value_threshold", False,
            f"Amount exceeds {policy.high_value_threshold_paise} paise "
            "— human approval required.",
            needs_approval=True,
        ))
    elif high_value:
        checks.append(PolicyCheck(
            "high_value_threshold", True,
            f"Amount exceeds {policy.high_value_threshold_paise} paise, and the "
            "selected action is already a human handoff.",
        ))
    else:
        checks.append(PolicyCheck(
            "high_value_threshold", True,
            "Transaction is within the automated value threshold.",
        ))

    # --- 7. Low AI confidence needs a human ---
    confident = ai_confidence >= policy.min_ai_confidence
    checks.append(PolicyCheck(
        "min_ai_confidence", confident,
        f"AI confidence {ai_confidence:.2f} vs minimum {policy.min_ai_confidence:.2f}.",
        needs_approval=not confident,
    ))

    # --- Resolve ---
    blockers = [c for c in checks if c.blocking]
    if blockers:
        return PolicyResult(
            decision=PolicyDecision.BLOCKED,
            checks=checks,
            approved_discount_paise=0,
            reason="; ".join(c.detail for c in blockers),
        )

    approvals = [c for c in checks if c.needs_approval]
    if approvals:
        return PolicyResult(
            decision=PolicyDecision.REQUIRES_APPROVAL,
            checks=checks,
            approved_discount_paise=approved_discount,
            reason="; ".join(c.detail for c in approvals),
        )

    return PolicyResult(
        decision=PolicyDecision.AUTO_ALLOWED,
        checks=checks,
        approved_discount_paise=approved_discount,
        reason="All merchant policy checks passed.",
    )
