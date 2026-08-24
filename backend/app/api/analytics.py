import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.money import format_inr
from app.db.session import get_db
from app.models import AuditEvent, MerchantPolicy, RecoveryCase, SimulationRun
from app.models.enums import CaseState
from app.simulation.engine import run_simulation

router = APIRouter(prefix="/api", tags=["analytics"])

OPEN_STATES = {CaseState.DETECTED, CaseState.ANALYZING, CaseState.STRATEGY_SELECTED,
               CaseState.AWAITING_ACTION, CaseState.ACTION_EXECUTED,
               CaseState.CUSTOMER_RESPONDED, CaseState.ACTION_FAILED}


@router.get("/analytics/overview")
def overview(db: Session = Depends(get_db)):
    cases = db.scalars(select(RecoveryCase)).all()
    total = len(cases)
    recovered = [c for c in cases if CaseState(c.state) is CaseState.RECOVERED]

    at_risk = sum(c.amount_paise for c in cases if CaseState(c.state) in OPEN_STATES)
    recovered_paise = sum(c.recovered_paise for c in recovered)
    discount_cost = sum(c.approved_discount_paise for c in recovered)

    pipeline: dict[str, int] = {}
    for case in cases:
        pipeline[case.state] = pipeline.get(case.state, 0) + 1

    resolved = [c for c in recovered if c.resolved_at and c.detected_at]
    avg_minutes = (round(sum((c.resolved_at - c.detected_at).total_seconds()
                             for c in resolved) / len(resolved) / 60, 1)
                   if resolved else 0.0)

    return {
        "revenue_at_risk_paise": at_risk,
        "revenue_at_risk_display": format_inr(at_risk),
        "recovered_paise": recovered_paise,
        "recovered_display": format_inr(recovered_paise),
        "net_recovered_paise": recovered_paise - discount_cost,
        "discount_cost_paise": discount_cost,
        "recovery_rate_pct": round(100 * len(recovered) / total, 1) if total else 0.0,
        "total_cases": total,
        "recovered_cases": len(recovered),
        "active_cases": sum(1 for c in cases if CaseState(c.state) in OPEN_STATES),
        "escalated_cases": pipeline.get(CaseState.ESCALATED, 0),
        "avg_recovery_minutes": avg_minutes,
        "pipeline": pipeline,
    }


@router.get("/analytics/activity")
def activity(limit: int = Query(default=40, le=200), db: Session = Depends(get_db)):
    """Live agent console feed."""
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    events = list(db.scalars(stmt).all())
    return [{
        "id": e.id, "case_id": e.case_id, "event_type": e.event_type,
        "actor": e.actor, "summary": e.summary,
        "from_state": e.from_state, "to_state": e.to_state,
        "created_at": e.created_at.isoformat(),
    } for e in reversed(events)]


class SimulationIn(BaseModel):
    case_count: int = 200
    seed: int = 42


@router.post("/simulation/run")
def start_simulation(payload: SimulationIn, db: Session = Depends(get_db)):
    if not 10 <= payload.case_count <= 2000:
        raise HTTPException(status_code=422, detail="case_count must be 10–2000.")

    results = run_simulation(n=payload.case_count, seed=payload.seed)
    run = SimulationRun(id=f"SIM_{uuid.uuid4().hex[:10].upper()}", seed=payload.seed,
                        case_count=payload.case_count, results=results)
    db.add(run)
    db.commit()
    return {"id": run.id, **results}


@router.get("/simulation/{run_id}")
def get_simulation(run_id: str, db: Session = Depends(get_db)):
    run = db.get(SimulationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Simulation run not found")
    return {"id": run.id, **run.results}


class PolicyOut(BaseModel):
    max_discount_pct: int
    max_auto_discount_paise: int
    max_recovery_attempts: int
    min_recovery_score: int
    max_contacts_per_week: int
    high_value_threshold_paise: int
    min_ai_confidence: float
    payment_actions_allowed: bool
    refunds_require_approval: bool


@router.get("/policies", response_model=PolicyOut)
def get_policy(db: Session = Depends(get_db)):
    policy = db.get(MerchantPolicy, 1) or MerchantPolicy(id=1)
    return PolicyOut(**{f: getattr(policy, f) for f in PolicyOut.model_fields})


@router.put("/policies", response_model=PolicyOut)
def update_policy(payload: PolicyOut, db: Session = Depends(get_db)):
    policy = db.get(MerchantPolicy, 1)
    if policy is None:
        policy = MerchantPolicy(id=1)
        db.add(policy)
    for field, value in payload.model_dump().items():
        setattr(policy, field, value)
    db.commit()
    return payload
