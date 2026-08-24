"""The agent loop.

detect -> score -> diagnose -> rank -> choose -> policy -> record

Every step writes an audit event. The LLM influences the diagnosis and the
strategy choice; it never moves the state machine or approves a financial action.
"""
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agents.analyst import run_analyst
from app.agents.strategist import run_strategist
from app.models import AuditEvent, Customer, MerchantPolicy, RecoveryCase
from app.models.enums import (
    ALLOWED_TRANSITIONS, CaseState, PolicyDecision, Strategy,
)
from app.policies.engine import evaluate_policy
from app.recovery.scoring import calculate_recovery_score
from app.recovery.strategies import best_strategy, evaluate_strategies
from app.schemas.ai import AnalystOutput, StrategistOutput
from app.services.llm import LLMError, LLMProvider, get_llm_provider


class InvalidTransition(Exception):
    pass


def transition(db: Session, case: RecoveryCase, to_state: CaseState,
               event_type: str, summary: str, actor: str = "agent",
               payload: dict | None = None) -> None:
    """Move the state machine and audit it. Illegal moves raise."""
    from_state = CaseState(case.state)
    if to_state not in ALLOWED_TRANSITIONS[from_state]:
        raise InvalidTransition(f"{from_state} -> {to_state} is not permitted.")

    db.add(AuditEvent(
        case_id=case.id, event_type=event_type, from_state=from_state,
        to_state=to_state, actor=actor, summary=summary, payload=payload,
    ))
    case.state = to_state


def audit(db: Session, case: RecoveryCase, event_type: str, summary: str,
          actor: str = "agent", payload: dict | None = None) -> None:
    """Record an event without changing state."""
    db.add(AuditEvent(
        case_id=case.id, event_type=event_type, from_state=case.state,
        to_state=case.state, actor=actor, summary=summary, payload=payload,
    ))


@dataclass
class AnalysisResult:
    case: RecoveryCase
    score: int
    score_breakdown: dict
    score_rationale: list[str]
    analysis: AnalystOutput
    options: list
    decision: StrategistOutput
    policy: Any
    llm_provider: str
    llm_latency_ms: int
    degraded: bool


def _fallback_strategy(case: RecoveryCase, score: int,
                       policy: MerchantPolicy) -> StrategistOutput:
    """Used when the LLM is unavailable or returns unusable output.

    The system degrades to the deterministic optimizer rather than stalling —
    and flags itself as degraded so the merchant knows.
    """
    option = best_strategy(case, score, policy)
    return StrategistOutput(
        strategy=option.strategy,
        reason=("AI layer unavailable. Fell back to the deterministic "
                f"expected-value optimizer, which ranks {option.strategy} highest."),
        confidence=0.50,
        requested_discount_paise=option.discount_paise,
        requires_human_approval=True,
    )


def analyze_case(db: Session, case: RecoveryCase,
                 provider: LLMProvider | None = None) -> AnalysisResult:
    provider = provider or get_llm_provider()
    customer: Customer = case.customer
    policy = db.get(MerchantPolicy, 1) or MerchantPolicy(id=1)
    degraded = False
    latency = 0

    transition(db, case, CaseState.ANALYZING, "ANALYSIS_STARTED",
               f"Agent picked up {case.id} for analysis.")

    # --- 1. Deterministic score ---
    score_result = calculate_recovery_score(case, customer)
    case.recovery_score = score_result.score
    case.score_breakdown = score_result.breakdown
    audit(db, case, "SCORE_COMPUTED",
          f"Recovery propensity {score_result.score}/100 ({score_result.band}).",
          actor="system", payload=score_result.breakdown)

    # --- 2. AI diagnosis ---
    try:
        result = run_analyst(provider, case, customer)
        analysis: AnalystOutput = result.output
        latency += result.latency_ms
        audit(db, case, "DIAGNOSIS", analysis.diagnosis, payload={
            "barrier": analysis.barrier,
            "customer_intent": analysis.customer_intent,
            "confidence": analysis.confidence,
            "evidence": analysis.evidence,
            "provider": result.provider,
            "latency_ms": result.latency_ms,
        })
    except LLMError as exc:
        degraded = True
        analysis = AnalystOutput(
            failure_reason=case.failure_reason,
            customer_intent=score_result.score,
            diagnosis=("AI diagnosis unavailable. Falling back to the recorded "
                       "failure signal and the deterministic recovery score."),
            barrier=str(case.failure_reason),
            confidence=0.40,
            evidence=[f"Recorded failure signal: {case.failure_reason}."],
        )
        audit(db, case, "AI_DEGRADED", f"Analyst unavailable: {exc}",
              actor="system")

    case.diagnosis = analysis.diagnosis

    # --- 3. Deterministic expected-value ranking ---
    options = evaluate_strategies(case, score_result.score, policy)
    audit(db, case, "OPTIONS_RANKED",
          f"{len(options)} strategies ranked by expected net value; "
          f"{options[0].strategy} leads.",
          actor="system",
          payload=[{"strategy": str(o.strategy), "probability": o.probability,
                    "expected_value_paise": o.expected_value_paise,
                    "discount_paise": o.discount_paise} for o in options])

    # --- 4. AI strategy choice ---
    try:
        result = run_strategist(provider, case, analysis, options, policy,
                                score_result.score)
        decision: StrategistOutput = result.output
        latency += result.latency_ms
    except LLMError as exc:
        degraded = True
        decision = _fallback_strategy(case, score_result.score, policy)
        audit(db, case, "AI_DEGRADED", f"Strategist unavailable: {exc}",
              actor="system")

    case.selected_strategy = decision.strategy
    case.strategy_reason = decision.reason
    case.ai_confidence = decision.confidence
    chosen = next((o for o in options if o.strategy == decision.strategy), options[0])
    case.expected_value_paise = chosen.expected_value_paise

    transition(db, case, CaseState.STRATEGY_SELECTED, "STRATEGY_SELECTED",
               f"Selected {decision.strategy}: {decision.reason}",
               payload={"strategy": str(decision.strategy),
                        "confidence": decision.confidence,
                        "expected_value_paise": chosen.expected_value_paise,
                        "requested_discount_paise": decision.requested_discount_paise})

    # --- 5. Guardrails. The LLM cannot influence this. ---
    effective_confidence = (0.0 if decision.requires_human_approval
                            else decision.confidence)
    policy_result = evaluate_policy(
        case=case, customer=customer, policy=policy,
        strategy=Strategy(decision.strategy), score=score_result.score,
        requested_discount_paise=decision.requested_discount_paise,
        ai_confidence=effective_confidence,
    )
    case.policy_decision = policy_result.decision
    case.policy_checks = policy_result.as_dict()["checks"]
    case.approved_discount_paise = policy_result.approved_discount_paise

    audit(db, case, "POLICY_EVALUATED",
          f"{policy_result.decision}: {policy_result.reason}",
          actor="policy", payload=policy_result.as_dict())

    # --- 6. Route on the policy outcome ---
    # A human handoff IS the action. An authorised ESCALATE_HUMAN lands in
    # ESCALATED, never in the automatic-execution queue.
    escalating = Strategy(decision.strategy) is Strategy.ESCALATE_HUMAN

    if policy_result.decision is PolicyDecision.BLOCKED:
        transition(db, case, CaseState.NO_RECOVERY, "ACTION_BLOCKED",
                   f"Automated recovery blocked. {policy_result.reason}",
                   actor="policy")
    elif policy_result.decision is PolicyDecision.REQUIRES_APPROVAL:
        transition(db, case, CaseState.ESCALATED, "ESCALATED_TO_HUMAN",
                   f"Human approval required. {policy_result.reason}",
                   actor="policy")
    elif escalating:
        transition(db, case, CaseState.ESCALATED, "ESCALATED_TO_HUMAN",
                   f"Agent selected a human handoff. {decision.reason}")
    else:
        transition(db, case, CaseState.AWAITING_ACTION, "ACTION_AUTHORIZED",
                   f"{decision.strategy} authorised for automatic execution.",
                   actor="policy")

    db.commit()
    db.refresh(case)

    return AnalysisResult(
        case=case, score=score_result.score,
        score_breakdown=score_result.breakdown,
        score_rationale=score_result.rationale,
        analysis=analysis, options=options, decision=decision,
        policy=policy_result, llm_provider=provider.name,
        llm_latency_ms=latency, degraded=degraded,
    )
