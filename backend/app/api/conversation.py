from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.conversation import run_conversation
from app.agents.orchestrator import audit
from app.core.money import format_inr
from app.db.session import get_db
from app.models import ConversationTurn, RecoveryCase
from app.models.enums import CaseState
from app.services.llm import LLMError, get_llm_provider

router = APIRouter(prefix="/api/recovery", tags=["conversation"])


class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=800)


class TurnOut(BaseModel):
    role: str
    body: str
    tool_calls: list | None = None


class MessageOut(BaseModel):
    case_id: str
    state: str
    reply: str
    intent: str
    tool_calls: list
    approved_discount_paise: int
    payment_url: str | None
    degraded: bool


@router.get("/cases/{case_id}/conversation", response_model=list[TurnOut])
def get_conversation(case_id: str, db: Session = Depends(get_db)):
    case = db.get(RecoveryCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")
    return [TurnOut(role=t.role, body=t.body, tool_calls=t.tool_calls)
            for t in case.conversation]


@router.post("/cases/{case_id}/message", response_model=MessageOut)
def post_message(case_id: str, payload: MessageIn, db: Session = Depends(get_db)):
    case = db.get(RecoveryCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")

    db.add(ConversationTurn(case_id=case.id, role="customer", body=payload.message))
    db.flush()

    tool_calls: list[dict] = []
    degraded = False

    try:
        result, discount = run_conversation(db, get_llm_provider(), case, payload.message)
        output = result.output
        reply, intent = output.reply, output.intent
        wants_link = output.wants_payment_link
        if output.wants_offer_check:
            tool_calls.append({
                "tool": "check_discount_policy",
                "result": ("APPROVED " + format_inr(discount)) if discount else "NO_OFFER",
            })
    except LLMError:
        degraded = True
        discount, wants_link, intent = 0, False, "general"
        reply = ("I'm having trouble responding right now. A member of the team will "
                 "follow up shortly.")

    payment_url = None
    if wants_link and case.payment_reference:
        from app.models import PaymentRecord
        record = db.get(PaymentRecord, case.payment_reference)
        if record is not None:
            payment_url = record.url
            tool_calls.append({"tool": "get_payment_link", "result": record.reference})

    db.add(ConversationTurn(case_id=case.id, role="agent", body=reply,
                            tool_calls=tool_calls or None))

    if CaseState(case.state) is CaseState.ACTION_EXECUTED:
        from app.agents.orchestrator import transition
        transition(db, case, CaseState.CUSTOMER_RESPONDED, "CUSTOMER_RESPONDED",
                   f"Customer replied (intent: {intent}).", actor="system")
    else:
        audit(db, case, "CONVERSATION_TURN",
              f"Customer message handled (intent: {intent}).", actor="agent",
              payload={"tool_calls": tool_calls})

    db.commit()
    db.refresh(case)

    return MessageOut(case_id=case.id, state=case.state, reply=reply, intent=intent,
                      tool_calls=tool_calls, approved_discount_paise=discount,
                      payment_url=payment_url, degraded=degraded)
