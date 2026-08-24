"""Structured contracts for LLM output.

The model returns JSON matching these schemas or its output is rejected.
It never returns free text that reaches a financial action.
"""
from pydantic import BaseModel, Field

from app.models.enums import FailureReason, Strategy


class AnalystOutput(BaseModel):
    """Recovery Analyst: diagnose why revenue is at risk."""
    failure_reason: FailureReason = Field(
        description="The most likely underlying cause, from the allowed set.")
    customer_intent: int = Field(
        ge=0, le=100, description="How strongly the customer signalled purchase intent.")
    diagnosis: str = Field(
        max_length=400, description="One or two sentences a merchant can read.")
    barrier: str = Field(
        max_length=200, description="The single blocker preventing payment.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this diagnosis.")
    evidence: list[str] = Field(
        default_factory=list, description="Facts from the case that support the diagnosis.")


class StrategistOutput(BaseModel):
    """Recovery Strategist: choose the intervention."""
    strategy: Strategy = Field(description="Chosen strategy, from the allowed set.")
    reason: str = Field(max_length=400, description="Why this strategy over the others.")
    confidence: float = Field(ge=0.0, le=1.0)
    requested_discount_paise: int = Field(
        default=0, ge=0,
        description="Discount to request in paise. The policy engine may reduce or refuse it.")
    requires_human_approval: bool = Field(
        default=False, description="Set true if the agent is unsure it should act alone.")
