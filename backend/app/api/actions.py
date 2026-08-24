from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.orchestrator import InvalidTransition
from app.db.session import get_db
from app.models import RecoveryCase
from app.recovery.executor import execute_action

router = APIRouter(prefix="/api/recovery", tags=["recovery"])


class ExecuteResponse(BaseModel):
    case_id: str
    state: str
    strategy: str
    replayed: bool
    failed: bool
    failure_reason: str | None
    payment_reference: str | None
    payment_url: str | None
    amount_paise: int | None
    demo_mode: bool
    message: str | None
    channel: str | None


@router.post("/cases/{case_id}/execute", response_model=ExecuteResponse)
def execute(case_id: str, db: Session = Depends(get_db)):
    case = db.get(RecoveryCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")

    try:
        result = execute_action(db, case)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ExecuteResponse(
        case_id=result.case.id, state=result.case.state, strategy=result.strategy,
        replayed=result.replayed, failed=result.failed,
        failure_reason=result.failure_reason,
        payment_reference=result.payment_reference,
        payment_url=result.payment_url, amount_paise=result.amount_paise,
        demo_mode=result.demo_mode, message=result.message, channel=result.channel,
    )
