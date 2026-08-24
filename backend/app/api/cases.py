from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.orchestrator import InvalidTransition, analyze_case
from app.core.money import format_inr
from app.db.session import get_db
from app.models import RecoveryCase

router = APIRouter(prefix="/api/recovery", tags=["recovery"])


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    segment: str
    previous_purchases: int
    lifetime_value_paise: int
    engagement_score: int


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_type: str
    from_state: str | None
    to_state: str | None
    actor: str
    summary: str
    payload: dict | list | None
    created_at: object


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    customer_id: str
    risk_type: str
    state: str
    amount_paise: int
    amount_display: str = ""
    description: str
    payment_method: str
    failure_reason: str
    attempt_count: int
    days_overdue: int
    recovery_score: int | None
    score_breakdown: dict | None
    diagnosis: str | None
    selected_strategy: str | None
    strategy_reason: str | None
    ai_confidence: float | None
    expected_value_paise: int | None
    policy_decision: str | None
    policy_checks: list | None
    approved_discount_paise: int
    recovered_paise: int
    contacts_sent: int
    customer: CustomerOut


class CaseDetailOut(CaseOut):
    events: list[AuditEventOut] = []


def _with_display(case: RecoveryCase, model=CaseOut):
    out = model.model_validate(case)
    out.amount_display = format_inr(case.amount_paise)
    return out


@router.get("/cases", response_model=list[CaseOut])
def list_cases(db: Session = Depends(get_db)):
    stmt = select(RecoveryCase).order_by(RecoveryCase.detected_at.desc())
    return [_with_display(c) for c in db.scalars(stmt).all()]


@router.get("/cases/{case_id}", response_model=CaseDetailOut)
def get_case(case_id: str, db: Session = Depends(get_db)):
    case = db.get(RecoveryCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")
    return _with_display(case, CaseDetailOut)


class StrategyOptionOut(BaseModel):
    strategy: str
    probability: float
    discount_paise: int
    expected_value_paise: int
    explanation: str


class AnalyzeResponse(BaseModel):
    case: CaseDetailOut
    score_rationale: list[str]
    barrier: str
    customer_intent: int
    evidence: list[str]
    options: list[StrategyOptionOut]
    policy_reason: str
    llm_provider: str
    llm_latency_ms: int
    degraded: bool


@router.post("/cases/{case_id}/analyze", response_model=AnalyzeResponse)
def analyze(case_id: str, db: Session = Depends(get_db)):
    case = db.get(RecoveryCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")

    try:
        result = analyze_case(db, case)
    except InvalidTransition as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Case is in state {case.state} and cannot be analysed: {exc}",
        ) from exc

    return AnalyzeResponse(
        case=_with_display(result.case, CaseDetailOut),
        score_rationale=result.score_rationale,
        barrier=result.analysis.barrier,
        customer_intent=result.analysis.customer_intent,
        evidence=result.analysis.evidence,
        options=[StrategyOptionOut(
            strategy=str(o.strategy), probability=o.probability,
            discount_paise=o.discount_paise,
            expected_value_paise=o.expected_value_paise,
            explanation=o.explanation) for o in result.options],
        policy_reason=result.policy.reason,
        llm_provider=result.llm_provider,
        llm_latency_ms=result.llm_latency_ms,
        degraded=result.degraded,
    )
