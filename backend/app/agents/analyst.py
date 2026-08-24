"""Recovery Analyst — diagnoses why revenue is at risk."""
from app.models import Customer, RecoveryCase
from app.schemas.ai import AnalystOutput
from app.services.llm import LLMProvider, LLMResult

SYSTEM_PROMPT = """You are the Recovery Analyst inside RecoveryOS, a revenue \
recovery system for Indian businesses.

Your job is to diagnose WHY a transaction failed or was abandoned, using only \
the case facts provided.

Rules:
- Use only the supplied context. Never invent customer history, prices, or events.
- If the signals are ambiguous, say so and lower your confidence. Low confidence \
is useful information, not a failure.
- Distinguish technical failures (the customer tried to pay and could not) from \
intent failures (the customer chose not to pay). Reaching payment authorisation \
is strong evidence of intent.
- Never promise a discount, refund, or any commercial outcome.
- Never recommend an action. Another agent decides what to do.
- Respond only with JSON matching the required schema."""


def build_prompt(case: RecoveryCase, customer: Customer) -> str:
    return f"""Diagnose this revenue-at-risk case.

risk_type: {case.risk_type}
amount_paise: {case.amount_paise}
description: {case.description}
payment_method: {case.payment_method}
failure_reason: {case.failure_reason}
attempt_count: {case.attempt_count}
days_overdue: {case.days_overdue}
contacts_sent: {case.contacts_sent}

customer_segment: {customer.segment}
previous_purchases: {customer.previous_purchases}
failed_payments: {customer.failed_payments}
engagement_score: {customer.engagement_score}
lifetime_value_paise: {customer.lifetime_value_paise}"""


def run_analyst(provider: LLMProvider, case: RecoveryCase,
                customer: Customer) -> LLMResult:
    return provider.generate(SYSTEM_PROMPT, build_prompt(case, customer), AnalystOutput)
