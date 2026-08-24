"""Outbound customer messaging.

Deterministic templates. The wording is generated in code, but every number in
it — especially the discount — comes from the policy engine, never the LLM.
"""
from dataclasses import dataclass

from app.core.money import format_inr
from app.models import Customer, RecoveryCase
from app.models.enums import Strategy


@dataclass
class Delivery:
    channel: str
    body: str
    delivered: bool = True


TEMPLATES: dict[str, str] = {
    Strategy.PAYMENT_LINK: (
        "Hi {name}, your payment for {description} didn't go through — it looks "
        "like the payment session expired rather than anything on your side. Your "
        "cart is still saved. You can complete it here: {url}"),
    Strategy.RETRY_PAYMENT: (
        "Hi {name}, we weren't able to complete your payment for {description}. "
        "Nothing was charged. You can retry without rebuilding your cart: {url}"),
    Strategy.PAYMENT_METHOD_UPDATE: (
        "Hi {name}, your renewal for {description} couldn't be processed because "
        "the saved card has expired. Your subscription is still active — you can "
        "update your payment method here: {url}"),
    Strategy.INCENTIVE: (
        "Hi {name}, you left {description} in your cart. We've applied a "
        "{discount} offer to help you complete it: {url}"),
    Strategy.ANSWER_OBJECTION: (
        "Hi {name}, we noticed you had a question about {description}. Happy to "
        "help you get it answered before you decide."),
    Strategy.REMINDER: (
        "Hi {name}, just a reminder that your order for {description} "
        "({amount}) is still pending."),
}


def build_message(case: RecoveryCase, customer: Customer, strategy: Strategy,
                  url: str = "", discount_paise: int = 0) -> str:
    template = TEMPLATES.get(strategy, TEMPLATES[Strategy.REMINDER])
    return template.format(
        name=customer.name.split()[0],
        description=case.description,
        amount=format_inr(case.amount_paise),
        discount=format_inr(discount_paise),
        url=url,
    )


def send(customer: Customer, body: str) -> Delivery:
    """Simulated delivery. No real messages are sent in this build."""
    return Delivery(channel=customer.preferred_channel, body=body, delivered=True)
