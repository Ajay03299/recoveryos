"""Action executor.

Two invariants this module exists to enforce:

1. Idempotency — retrying the same action never creates a second payment.
2. Verification — a case is marked RECOVERED only after the provider confirms
   the money arrived. A webhook saying so is not sufficient evidence.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.agents.orchestrator import InvalidTransition, audit, transition
from app.core.money import format_inr
from app.models import Customer, IdempotencyRecord, PaymentRecord, RecoveryCase
from app.models.enums import CaseState, Strategy
from app.services import notifications
from app.services.payments import (
    PaymentError, PaymentProvider, PaymentProviderUnavailable, PaymentStatus,
    get_payment_provider,
)

# Strategies that produce a payment artefact.
LINK_STRATEGIES = {
    Strategy.PAYMENT_LINK,
    Strategy.RETRY_PAYMENT,
    Strategy.PAYMENT_METHOD_UPDATE,
    Strategy.INCENTIVE,
}


@dataclass
class ExecutionResult:
    case: RecoveryCase
    strategy: str
    replayed: bool = False
    failed: bool = False
    failure_reason: str | None = None
    payment_reference: str | None = None
    payment_url: str | None = None
    amount_paise: int | None = None
    demo_mode: bool = False
    message: str | None = None
    channel: str | None = None


@dataclass
class ConfirmationResult:
    case: RecoveryCase
    verified: bool
    status: str
    recovered_paise: int
    already_recorded: bool = False


def _idempotency_key(case: RecoveryCase, strategy: Strategy) -> str:
    return f"{case.id}:{strategy}"


def execute_action(db: Session, case: RecoveryCase,
                   provider: PaymentProvider | None = None) -> ExecutionResult:
    if case.selected_strategy is None:
        raise InvalidTransition("Case has no selected strategy. Analyse it first.")

    strategy = Strategy(case.selected_strategy)
    key = _idempotency_key(case, strategy)

    # --- Idempotency FIRST. ---
    # "Have I already done this?" must be answered before "am I allowed to do
    # this?". A retry, a double-click or a redelivered webhook arrives after the
    # case has already moved on; if the state guard ran first it would raise,
    # and the caller would retry again. Replaying is the safe answer.
    existing = db.get(IdempotencyRecord, key)
    if existing is not None:
        audit(db, case, "IDEMPOTENT_REPLAY",
              f"{strategy} was already executed for this case. Returned the "
              "original result instead of acting again.", actor="system",
              payload=existing.result)
        db.commit()
        return ExecutionResult(
            case=case, strategy=str(strategy), replayed=True,
            payment_reference=existing.result.get("payment_reference"),
            payment_url=existing.result.get("payment_url"),
            amount_paise=existing.result.get("amount_paise"),
            demo_mode=existing.result.get("demo_mode", False),
            message=existing.result.get("message"),
            channel=existing.result.get("channel"),
        )

    # --- Then the state guard, for genuinely new work. ---
    state = CaseState(case.state)
    if state is CaseState.ACTION_FAILED:
        transition(db, case, CaseState.AWAITING_ACTION, "ACTION_RETRY",
                   "Retrying after an earlier provider failure.", actor="system")
    elif state is not CaseState.AWAITING_ACTION:
        raise InvalidTransition(
            f"Case is in state {state}; only AWAITING_ACTION or ACTION_FAILED "
            "may be executed.")

    provider = provider or get_payment_provider(db)
    customer: Customer = case.customer

    # The charge amount is computed here, deterministically, from the
    # policy-approved discount. The LLM never touches this number.
    discount = case.approved_discount_paise if strategy is Strategy.INCENTIVE else 0
    charge_paise = max(case.amount_paise - discount, 100)

    payment: PaymentRecord | None = None
    url = ""

    if strategy in LINK_STRATEGIES:
        try:
            link = provider.create_payment_link(
                case_id=case.id, amount_paise=charge_paise,
                description=case.description, customer_name=customer.name,
                customer_email=customer.email, customer_phone=customer.phone,
            )
        except PaymentProviderUnavailable as exc:
            # Fail safe: no artefact, no idempotency record, nothing charged.
            # The case stays retryable and the merchant can see exactly why.
            transition(db, case, CaseState.ACTION_FAILED, "ACTION_BLOCKED",
                       f"Payment action paused safely. No duplicate payment was "
                       f"attempted. Reason: {exc}", actor="system",
                       payload={"reason": "PAYMENT_PROVIDER_UNAVAILABLE",
                                "detail": str(exc)})
            db.commit()
            db.refresh(case)
            return ExecutionResult(
                case=case, strategy=str(strategy), failed=True,
                failure_reason="PAYMENT_PROVIDER_UNAVAILABLE")
        except PaymentError as exc:
            transition(db, case, CaseState.ACTION_FAILED, "ACTION_BLOCKED",
                       f"Payment action rejected: {exc}", actor="system",
                       payload={"reason": "PAYMENT_REJECTED", "detail": str(exc)})
            db.commit()
            db.refresh(case)
            return ExecutionResult(
                case=case, strategy=str(strategy), failed=True,
                failure_reason="PAYMENT_REJECTED")

        payment = PaymentRecord(
            reference=link.reference, case_id=case.id, provider=link.provider,
            demo_mode=link.demo_mode, amount_paise=link.amount_paise,
            status=PaymentStatus.CREATED, url=link.url,
        )
        db.add(payment)
        case.payment_reference = link.reference
        url = link.url

        audit(db, case, "PAYMENT_LINK_CREATED",
              f"Payment link created for {format_inr(link.amount_paise)}"
              + (f" after a {format_inr(discount)} approved discount." if discount
                 else "."),
              payload={"reference": link.reference, "url": link.url,
                       "amount_paise": link.amount_paise,
                       "discount_paise": discount,
                       "provider": link.provider, "demo_mode": link.demo_mode})

    # --- Notify the customer ---
    body = notifications.build_message(case, customer, strategy, url, discount)
    delivery = notifications.send(customer, body)
    case.contacts_sent += 1
    audit(db, case, "MESSAGE_SENT",
          f"Recovery message sent via {delivery.channel}.",
          payload={"channel": delivery.channel, "body": body})

    transition(db, case, CaseState.ACTION_EXECUTED, "ACTION_EXECUTED",
               f"{strategy} executed successfully.")

    result_payload = {
        "payment_reference": payment.reference if payment else None,
        "payment_url": url or None,
        "amount_paise": charge_paise if payment else None,
        "demo_mode": payment.demo_mode if payment else False,
        "message": body,
        "channel": delivery.channel,
    }
    db.add(IdempotencyRecord(key=key, result=result_payload))
    db.commit()
    db.refresh(case)

    return ExecutionResult(
        case=case, strategy=str(strategy),
        payment_reference=result_payload["payment_reference"],
        payment_url=result_payload["payment_url"],
        amount_paise=result_payload["amount_paise"],
        demo_mode=result_payload["demo_mode"],
        message=body, channel=delivery.channel,
    )


def confirm_payment(db: Session, reference: str,
                    provider: PaymentProvider | None = None) -> ConfirmationResult:
    """Verify with the provider, then record recovery. Never the other way round."""
    record = db.get(PaymentRecord, reference)
    if record is None:
        raise PaymentError(f"Unknown payment reference {reference}.")

    case = db.get(RecoveryCase, record.case_id)
    provider = provider or get_payment_provider(db)

    state = provider.get_payment_state(reference)
    record.status = state.status
    record.amount_paid_paise = state.amount_paid_paise

    if state.status is not PaymentStatus.PAID:
        audit(db, case, "PAYMENT_NOT_VERIFIED",
              f"Provider reports status {state.status}. Recovery not recorded.",
              actor="system", payload={"reference": reference,
                                       "status": str(state.status)})
        db.commit()
        return ConfirmationResult(case=case, verified=False,
                                  status=str(state.status), recovered_paise=0)

    if CaseState(case.state) is CaseState.RECOVERED:
        audit(db, case, "IDEMPOTENT_REPLAY",
              "Payment already confirmed for this case. No change made.",
              actor="system", payload={"reference": reference})
        db.commit()
        return ConfirmationResult(case=case, verified=True, status="PAID",
                                  recovered_paise=case.recovered_paise,
                                  already_recorded=True)

    case.recovered_paise = state.amount_paid_paise
    case.resolved_at = datetime.now(timezone.utc)
    if record.paid_at is None:
        record.paid_at = case.resolved_at

    transition(db, case, CaseState.RECOVERED, "REVENUE_RECOVERED",
               f"{format_inr(state.amount_paid_paise)} recovered and verified "
               f"with {state.provider}.", actor="system",
               payload={"reference": reference,
                        "amount_paise": state.amount_paid_paise,
                        "demo_mode": state.demo_mode})
    db.commit()
    db.refresh(case)

    return ConfirmationResult(case=case, verified=True, status="PAID",
                              recovered_paise=case.recovered_paise)
