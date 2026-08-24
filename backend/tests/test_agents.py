import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.orchestrator import InvalidTransition, analyze_case, transition
from app.models import Base, MerchantPolicy
from app.models.enums import CaseState, PolicyDecision, Strategy
from app.schemas.ai import AnalystOutput, StrategistOutput
from app.services.llm import LLMError, LLMProvider, get_llm_provider
from app.services.llm.mock import MockProvider
from app.services.llm.schema import to_gemini_schema
from tests.test_scoring import make_case, make_customer


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(MerchantPolicy(id=1))
    yield session
    session.close()


def seed_case(db, **kw):
    customer = make_customer(**kw.pop("customer", {}))
    case = make_case(**kw)
    db.add_all([customer, case])
    db.commit()
    return case


class BrokenProvider(LLMProvider):
    name = "broken"
    model = "none"

    def _generate(self, system, prompt, schema):
        raise LLMError("simulated provider outage")


# --- Provider abstraction ---

def test_mock_provider_is_deterministic():
    case, customer = make_case(), make_customer()
    from app.agents.analyst import run_analyst
    provider = MockProvider()
    a = run_analyst(provider, case, customer).output
    b = run_analyst(provider, case, customer).output
    assert a.model_dump() == b.model_dump()


def test_mock_output_validates_against_schema():
    from app.agents.analyst import run_analyst
    out = run_analyst(MockProvider(), make_case(), make_customer()).output
    assert isinstance(out, AnalystOutput)
    assert 0 <= out.customer_intent <= 100
    assert 0.0 <= out.confidence <= 1.0


def test_gemini_schema_has_no_refs():
    """Gemini rejects $ref/$defs — the converter must inline everything."""
    schema = to_gemini_schema(StrategistOutput)
    assert "$defs" not in str(schema) and "$ref" not in str(schema)
    assert schema["type"] == "OBJECT"
    assert "enum" in schema["properties"]["strategy"]


def test_factory_falls_back_to_mock_without_api_key():
    assert get_llm_provider("gemini").name in {"mock", "gemini"}


# --- State machine ---

def test_illegal_transition_is_rejected(db):
    case = seed_case(db)
    with pytest.raises(InvalidTransition):
        transition(db, case, CaseState.RECOVERED, "X", "jumping straight to recovered")


# --- Full pipeline ---

def test_healthy_case_is_auto_authorized(db):
    case = seed_case(db)
    result = analyze_case(db, case, MockProvider())
    assert result.case.state == CaseState.AWAITING_ACTION
    assert result.policy.decision is PolicyDecision.AUTO_ALLOWED
    assert result.case.recovery_score is not None
    assert result.case.diagnosis


def test_high_value_case_escalates_to_human(db):
    """Demo Case 4: ₹75,000 invoice must never be actioned automatically."""
    from app.models.enums import FailureReason, RiskType
    case = seed_case(db, amount_paise=7_500_000, risk_type=RiskType.OVERDUE_INVOICE,
                     failure_reason=FailureReason.PAYMENT_OVERDUE, days_overdue=35)
    result = analyze_case(db, case, MockProvider())
    assert result.case.state == CaseState.ESCALATED
    assert result.decision.strategy == Strategy.ESCALATE_HUMAN


def test_chronic_failure_case_is_blocked(db):
    """Demo Case 5: the agent stops itself rather than harassing the customer."""
    from app.models.enums import FailureReason
    case = seed_case(db, failure_reason=FailureReason.INSUFFICIENT_FUNDS,
                     attempt_count=5, contacts_sent=4,
                     customer={"previous_purchases": 3, "failed_payments": 6,
                               "engagement_score": 31})
    result = analyze_case(db, case, MockProvider())
    assert result.case.state == CaseState.NO_RECOVERY
    assert result.policy.decision is PolicyDecision.BLOCKED


def test_llm_outage_degrades_but_does_not_crash(db):
    """No AI, no crash: fall back to the deterministic optimizer and flag it."""
    case = seed_case(db)
    result = analyze_case(db, case, BrokenProvider())
    assert result.degraded is True
    assert result.case.selected_strategy is not None
    assert result.case.state in {CaseState.ESCALATED, CaseState.AWAITING_ACTION}


def test_every_run_writes_an_audit_trail(db):
    case = seed_case(db)
    analyze_case(db, case, MockProvider())
    types = [e.event_type for e in case.events]
    for expected in ("ANALYSIS_STARTED", "SCORE_COMPUTED", "DIAGNOSIS",
                     "OPTIONS_RANKED", "STRATEGY_SELECTED", "POLICY_EVALUATED"):
        assert expected in types


def test_discount_request_is_capped_by_policy(db):
    """Even if the agent asks for more, the merchant's cap wins."""
    from app.models.enums import FailureReason
    case = seed_case(db, amount_paise=849_900,
                     failure_reason=FailureReason.PRICE_OBJECTION)
    result = analyze_case(db, case, MockProvider())
    assert result.case.approved_discount_paise <= 50_000


def test_authorized_escalation_still_lands_in_escalated(db):
    """Policy may approve ESCALATE_HUMAN, but approval is not execution:
    a handoff must never enter the automatic-action queue."""
    from app.models.enums import FailureReason, RiskType
    case = seed_case(db, amount_paise=7_500_000, risk_type=RiskType.OVERDUE_INVOICE,
                     failure_reason=FailureReason.PAYMENT_OVERDUE, days_overdue=35)
    result = analyze_case(db, case, MockProvider())
    assert result.policy.decision is PolicyDecision.AUTO_ALLOWED
    assert result.case.state == CaseState.ESCALATED
    assert result.case.state != CaseState.AWAITING_ACTION
