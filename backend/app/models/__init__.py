from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, Text, DateTime, JSON, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.models.enums import (
    RiskType, FailureReason, CaseState, Strategy, CustomerSegment, PolicyDecision,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(20))
    segment: Mapped[CustomerSegment] = mapped_column(String(20))

    # All money in integer paise. Never float.
    lifetime_value_paise: Mapped[int] = mapped_column(Integer, default=0)
    previous_purchases: Mapped[int] = mapped_column(Integer, default=0)
    failed_payments: Mapped[int] = mapped_column(Integer, default=0)
    last_purchase_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferred_channel: Mapped[str] = mapped_column(String(20), default="email")

    # 0-100 behavioural signal, computed upstream / seeded
    engagement_score: Mapped[int] = mapped_column(Integer, default=50)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cases: Mapped[list["RecoveryCase"]] = relationship(back_populates="customer")


class RecoveryCase(Base):
    __tablename__ = "recovery_cases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)

    risk_type: Mapped[RiskType] = mapped_column(String(32))
    state: Mapped[CaseState] = mapped_column(String(32), default=CaseState.DETECTED, index=True)

    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    description: Mapped[str] = mapped_column(String(240))
    items: Mapped[list | None] = mapped_column(JSON, default=list)

    payment_method: Mapped[str] = mapped_column(String(24), default="upi")
    failure_reason: Mapped[FailureReason] = mapped_column(String(32), default=FailureReason.UNKNOWN)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    days_overdue: Mapped[int] = mapped_column(Integer, default=0)

    # --- Filled in by the recovery engine + AI layer ---
    recovery_score: Mapped[int | None] = mapped_column(Integer)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON)
    diagnosis: Mapped[str | None] = mapped_column(Text)
    ai_confidence: Mapped[float | None] = mapped_column()
    selected_strategy: Mapped[Strategy | None] = mapped_column(String(32))
    strategy_reason: Mapped[str | None] = mapped_column(Text)
    expected_value_paise: Mapped[int | None] = mapped_column(Integer)

    # --- Policy outcome ---
    policy_decision: Mapped[PolicyDecision | None] = mapped_column(String(24))
    policy_checks: Mapped[list | None] = mapped_column(JSON)

    # --- Outcome ---
    approved_discount_paise: Mapped[int] = mapped_column(Integer, default=0)
    recovered_paise: Mapped[int] = mapped_column(Integer, default=0)
    contacts_sent: Mapped[int] = mapped_column(Integer, default=0)
    payment_reference: Mapped[str | None] = mapped_column(String(64))

    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped["Customer"] = relationship(back_populates="cases")
    events: Mapped[list["AuditEvent"]] = relationship(
        back_populates="case", order_by="AuditEvent.created_at"
    )
    conversation: Mapped[list["ConversationTurn"]] = relationship(
        order_by="ConversationTurn.created_at"
    )


class AuditEvent(Base):
    """Append-only. Nothing in the app may UPDATE or DELETE a row here."""
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("recovery_cases.id"), index=True)

    event_type: Mapped[str] = mapped_column(String(48))
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str | None] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(24), default="agent")  # agent | policy | system | human
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped["RecoveryCase"] = relationship(back_populates="events")


class MerchantPolicy(Base):
    """Single-row config for the demo merchant. Editable from the dashboard."""
    __tablename__ = "merchant_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    max_discount_pct: Mapped[int] = mapped_column(Integer, default=10)
    max_auto_discount_paise: Mapped[int] = mapped_column(Integer, default=50000)   # ₹500
    max_recovery_attempts: Mapped[int] = mapped_column(Integer, default=2)
    min_recovery_score: Mapped[int] = mapped_column(Integer, default=45)
    max_contacts_per_week: Mapped[int] = mapped_column(Integer, default=2)
    high_value_threshold_paise: Mapped[int] = mapped_column(Integer, default=5000000)  # ₹50,000
    min_ai_confidence: Mapped[float] = mapped_column(default=0.60)
    payment_actions_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    refunds_require_approval: Mapped[bool] = mapped_column(Boolean, default=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class IdempotencyRecord(Base):
    """(case_id, action_type) -> the result we already produced."""
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class PaymentRecord(Base):
    """Local mirror of every payment artefact we create.

    Exists so we can answer "did we already do this?" without trusting an
    external provider to be reachable.
    """
    __tablename__ = "payment_records"

    reference: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("recovery_cases.id"), index=True)
    provider: Mapped[str] = mapped_column(String(24))
    demo_mode: Mapped[bool] = mapped_column(Boolean, default=False)

    amount_paise: Mapped[int] = mapped_column(Integer)
    amount_paid_paise: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="CREATED")
    url: Mapped[str] = mapped_column(String(400), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemFlag(Base):
    """Runtime toggles. Used to demonstrate failure handling on stage."""
    __tablename__ = "system_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ConversationTurn(Base):
    """One message in the customer-facing recovery conversation."""
    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("recovery_cases.id"), index=True)

    role: Mapped[str] = mapped_column(String(16))  # customer | agent
    body: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SimulationRun(Base):
    """A deterministic baseline-vs-RecoveryOS experiment."""
    __tablename__ = "simulation_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    seed: Mapped[int] = mapped_column(Integer)
    case_count: Mapped[int] = mapped_column(Integer)
    results: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
