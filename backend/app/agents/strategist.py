"""Recovery Strategist — chooses the intervention.

The strategist sees the deterministic expected-value ranking and the merchant's
limits. It may agree or disagree, but its choice still passes through the policy
engine before anything executes.
"""
from app.core.money import format_inr
from app.models import MerchantPolicy, RecoveryCase
from app.recovery.strategies import StrategyOption
from app.schemas.ai import AnalystOutput, StrategistOutput
from app.services.llm import LLMProvider, LLMResult

SYSTEM_PROMPT = """You are the Recovery Strategist inside RecoveryOS.

You choose ONE recovery strategy for a case that another agent has diagnosed.

You are optimising expected NET recovered value — not conversion probability. A \
cheaper action that converts slightly less often is often the better choice, and \
a human handoff can beat any automated action on a large or delicate account.

Rules:
- Choose only from the allowed strategy set.
- Never exceed the merchant's stated discount limits. Request zero discount \
unless price is genuinely the blocker.
- Never claim a payment succeeded, and never execute anything yourself. You are \
proposing; deterministic systems decide and act.
- Escalate to a human when the amount is large, the signals conflict, or your \
confidence is low.
- An expected-value ranking is provided. Deviate from the top-ranked option only \
if you can state a concrete reason from the case facts.
- Respond only with JSON matching the required schema."""


def build_prompt(case: RecoveryCase, analysis: AnalystOutput,
                 options: list[StrategyOption], policy: MerchantPolicy,
                 score: int) -> str:
    ranked = "\n".join(
        f"  {i + 1}. {o.strategy} | probability {o.probability} | "
        f"discount {o.discount_paise} paise | "
        f"expected_net_value {o.expected_value_paise} paise"
        for i, o in enumerate(options)
    )
    top = options[0]
    return f"""Choose the recovery strategy for this case.

DIAGNOSIS
barrier: {analysis.barrier}
diagnosis: {analysis.diagnosis}
customer_intent: {analysis.customer_intent}
analyst_confidence: {analysis.confidence}
failure_reason: {analysis.failure_reason}

CASE
amount_paise: {case.amount_paise} ({format_inr(case.amount_paise)})
recovery_score: {score}
attempt_count: {case.attempt_count}
contacts_sent: {case.contacts_sent}
days_overdue: {case.days_overdue}

MERCHANT LIMITS
max_discount_pct: {policy.max_discount_pct}
max_auto_discount_paise: {policy.max_auto_discount_paise}
max_recovery_attempts: {policy.max_recovery_attempts}
max_contacts_per_week: {policy.max_contacts_per_week}
high_value_threshold_paise: {policy.high_value_threshold_paise}

EXPECTED VALUE RANKING
{ranked}

top_ranked_strategy: {top.strategy}
top_ranked_discount_paise: {top.discount_paise}"""


def run_strategist(provider: LLMProvider, case: RecoveryCase,
                   analysis: AnalystOutput, options: list[StrategyOption],
                   policy: MerchantPolicy, score: int) -> LLMResult:
    prompt = build_prompt(case, analysis, options, policy, score)
    return provider.generate(SYSTEM_PROMPT, prompt, StrategistOutput)
