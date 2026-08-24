import pytest

from app.agents.orchestrator import InvalidTransition, analyze_case
from app.core.flags import PAYMENT_PROVIDER_OUTAGE, set_flag
from app.models import IdempotencyRecord, PaymentRecord
from app.models.enums import CaseState, FailureReason, Strategy
from app.recovery.executor import confirm_payment, execute_action
from app.services.llm.mock import MockProvider
from app.services.payments import PaymentStatus
from app.services.payments.simulated import SimulatedProvider
from tests.test_agents import db, seed_case  # noqa: F401


def prepared_case(db, **kw):
    """A case that has been analysed and authorised for automatic action."""
    case = seed_case(db, **kw)
    analyze_case(db, case, MockProvider())
    return case


# --- Idempotency ---

def test_execution_is_idempotent(db):
    case = prepared_case(db)
    first = execute_action(db, case, SimulatedProvider(db))
    second = execute_action(db, case, SimulatedProvider(db))

    assert first.replayed is False and second.replayed is True
    assert first.payment_reference == second.payment_reference
    assert db.query(PaymentRecord).count() == 1


def test_replay_does_not_send_a_second_message(db):
    case = prepared_case(db)
    execute_action(db, case, SimulatedProvider(db))
    contacts_after_first = case.contacts_sent
    execute_action(db, case, SimulatedProvider(db))
    assert case.contacts_sent == contacts_after_first


def test_replay_is_recorded_in_the_audit_trail(db):
    case = prepared_case(db)
    execute_action(db, case, SimulatedProvider(db))
    execute_action(db, case, SimulatedProvider(db))
    assert "IDEMPOTENT_REPLAY" in [e.event_type for e in case.events]


# --- Failure handling ---

def test_provider_outage_fails_safely(db):
    """No artefact, no charge, no idempotency record — and a clear audit entry."""
    case = prepared_case(db)
    set_flag(db, PAYMENT_PROVIDER_OUTAGE, True)

    result = execute_action(db, case, SimulatedProvider(db))

    assert result.failed is True
    assert result.failure_reason == "PAYMENT_PROVIDER_UNAVAILABLE"
    assert case.state == CaseState.ACTION_FAILED
    assert db.query(PaymentRecord).count() == 0
    assert db.query(IdempotencyRecord).count() == 0
    assert "ACTION_BLOCKED" in [e.event_type for e in case.events]


def test_case_is_retryable_after_an_outage(db):
    case = prepared_case(db)
    set_flag(db, PAYMENT_PROVIDER_OUTAGE, True)
    execute_action(db, case, SimulatedProvider(db))

    set_flag(db, PAYMENT_PROVIDER_OUTAGE, False)
    retry = execute_action(db, case, SimulatedProvider(db))

    assert retry.failed is False
    assert retry.payment_reference is not None
    assert case.state == CaseState.ACTION_EXECUTED


def test_cannot_execute_an_unanalysed_case(db):
    case = seed_case(db)
    with pytest.raises(InvalidTransition):
        execute_action(db, case, SimulatedProvider(db))


# --- Verification before recovery ---

def test_unpaid_link_is_not_counted_as_recovered(db):
    """The whole point: a link existing is not the same as money arriving."""
    case = prepared_case(db)
    result = execute_action(db, case, SimulatedProvider(db))

    confirmation = confirm_payment(db, result.payment_reference, SimulatedProvider(db))

    assert confirmation.verified is False
    assert case.state != CaseState.RECOVERED
    assert case.recovered_paise == 0


def test_verified_payment_records_recovery(db):
    case = prepared_case(db)
    result = execute_action(db, case, SimulatedProvider(db))

    provider = SimulatedProvider(db)
    provider.capture(result.payment_reference)
    confirmation = confirm_payment(db, result.payment_reference, provider)

    assert confirmation.verified is True
    assert case.state == CaseState.RECOVERED
    assert case.recovered_paise == result.amount_paise
    assert case.resolved_at is not None


def test_double_confirmation_does_not_double_count_revenue(db):
    case = prepared_case(db)
    result = execute_action(db, case, SimulatedProvider(db))
    provider = SimulatedProvider(db)
    provider.capture(result.payment_reference)

    confirm_payment(db, result.payment_reference, provider)
    once = case.recovered_paise
    second = confirm_payment(db, result.payment_reference, provider)

    assert second.already_recorded is True
    assert case.recovered_paise == once


# --- Money handling ---

def test_discount_is_applied_by_the_backend_not_the_agent(db):
    case = prepared_case(db, amount_paise=849_900,
                         failure_reason=FailureReason.PRICE_OBJECTION)
    if Strategy(case.selected_strategy) is not Strategy.INCENTIVE:
        pytest.skip("Optimizer did not select INCENTIVE for this fixture.")

    result = execute_action(db, case, SimulatedProvider(db))
    assert result.amount_paise == case.amount_paise - case.approved_discount_paise
    assert case.approved_discount_paise <= 50_000


def test_payment_artefacts_are_flagged_as_demo_mode(db):
    """We never present a simulated payment as a real one."""
    case = prepared_case(db)
    result = execute_action(db, case, SimulatedProvider(db))
    record = db.get(PaymentRecord, result.payment_reference)
    assert result.demo_mode is True and record.demo_mode is True
    assert record.status == PaymentStatus.CREATED


def test_replay_never_mutates_the_case(db):
    """A replay is a read: same state, same contacts, same payment reference.

    This is the property a retry queue depends on. If replaying a completed
    action could advance the case, retries would corrupt state.
    """
    case = prepared_case(db)
    first = execute_action(db, case, SimulatedProvider(db))
    before = (case.state, case.contacts_sent, case.payment_reference)

    for _ in range(3):
        replay = execute_action(db, case, SimulatedProvider(db))
        assert replay.replayed is True
        assert replay.payment_reference == first.payment_reference

    assert (case.state, case.contacts_sent, case.payment_reference) == before
    assert db.query(PaymentRecord).count() == 1
    assert db.query(IdempotencyRecord).count() == 1
