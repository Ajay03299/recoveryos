"""Seed the demo dataset. Idempotent: wipes and rebuilds."""
from datetime import timedelta

from app.db.session import SessionLocal, engine, init_db
from app.models import Base, Customer, RecoveryCase, MerchantPolicy, utcnow
from app.models.enums import RiskType, FailureReason, CaseState, CustomerSegment

R = 100  # paise per rupee


def seed() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    now = utcnow()

    customers = [
        Customer(id="CUS_1001", name="Rahul Mehta", email="rahul.mehta@example.com",
                 phone="+919876500001", segment=CustomerSegment.LOYAL,
                 lifetime_value_paise=2450000, previous_purchases=2, failed_payments=1,
                 last_purchase_at=now - timedelta(days=24), preferred_channel="whatsapp",
                 engagement_score=88),
        Customer(id="CUS_1002", name="Priya Nair", email="priya.nair@example.com",
                 phone="+919876500002", segment=CustomerSegment.NEW,
                 lifetime_value_paise=0, previous_purchases=0, failed_payments=0,
                 last_purchase_at=None, preferred_channel="email", engagement_score=74),
        Customer(id="CUS_1003", name="Arjun Shetty", email="arjun.shetty@example.com",
                 phone="+919876500003", segment=CustomerSegment.RETURNING,
                 lifetime_value_paise=1198800, previous_purchases=12, failed_payments=1,
                 last_purchase_at=now - timedelta(days=31), preferred_channel="whatsapp",
                 engagement_score=69),
        Customer(id="CUS_1004", name="Sunrise Traders Pvt Ltd", email="accounts@sunrisetraders.example",
                 phone="+919876500004", segment=CustomerSegment.VIP,
                 lifetime_value_paise=94500000, previous_purchases=27, failed_payments=0,
                 last_purchase_at=now - timedelta(days=62), preferred_channel="email",
                 engagement_score=55),
        Customer(id="CUS_1005", name="Kabir Anand", email="kabir.anand@example.com",
                 phone="+919876500005", segment=CustomerSegment.RETURNING,
                 lifetime_value_paise=340000, previous_purchases=3, failed_payments=6,
                 last_purchase_at=now - timedelta(days=140), preferred_channel="email",
                 engagement_score=31),
        Customer(id="CUS_1006", name="Meera Iyer", email="meera.iyer@example.com",
                 phone="+919876500006", segment=CustomerSegment.LOYAL,
                 lifetime_value_paise=1875000, previous_purchases=5, failed_payments=1,
                 last_purchase_at=now - timedelta(days=11), preferred_channel="whatsapp",
                 engagement_score=81),
    ]

    cases = [
        # 1 — easy win: technical failure, high intent
        RecoveryCase(id="CASE_0001", customer_id="CUS_1001", risk_type=RiskType.PAYMENT_FAILURE,
                     amount_paise=4999 * R, description="Sony WH-CH720N Headphones",
                     items=[{"name": "Sony WH-CH720N Headphones", "qty": 1, "price_paise": 4999 * R}],
                     payment_method="upi", failure_reason=FailureReason.UPI_TIMEOUT,
                     attempt_count=1, detected_at=now - timedelta(minutes=42)),
        # 2 — objection: needs reasoning, not a reminder
        RecoveryCase(id="CASE_0002", customer_id="CUS_1002", risk_type=RiskType.CHECKOUT_ABANDONMENT,
                     amount_paise=8499 * R, description="Adidas Ultraboost 22 — size uncertainty",
                     items=[{"name": "Adidas Ultraboost 22", "qty": 1, "price_paise": 8499 * R}],
                     payment_method="card", failure_reason=FailureReason.PRODUCT_UNCERTAINTY,
                     attempt_count=2, detected_at=now - timedelta(hours=6)),
        # 3 — subscription: payment instrument problem
        RecoveryCase(id="CASE_0003", customer_id="CUS_1003", risk_type=RiskType.SUBSCRIPTION_FAILURE,
                     amount_paise=999 * R, description="Pro Plan — monthly renewal",
                     items=[{"name": "Pro Plan (monthly)", "qty": 1, "price_paise": 999 * R}],
                     payment_method="card", failure_reason=FailureReason.EXPIRED_CARD,
                     attempt_count=3, detected_at=now - timedelta(days=2)),
        # 4 — high value: must escalate to a human
        RecoveryCase(id="CASE_0004", customer_id="CUS_1004", risk_type=RiskType.OVERDUE_INVOICE,
                     amount_paise=75000 * R, description="Invoice INV-2026-0431",
                     items=[{"name": "Quarterly supply contract", "qty": 1, "price_paise": 75000 * R}],
                     payment_method="netbanking", failure_reason=FailureReason.PAYMENT_OVERDUE,
                     attempt_count=1, days_overdue=35, contacts_sent=3,
                     detected_at=now - timedelta(days=35)),
        # 5 — unsafe: repeated failures, agent must stop
        RecoveryCase(id="CASE_0005", customer_id="CUS_1005", risk_type=RiskType.PAYMENT_FAILURE,
                     amount_paise=2499 * R, description="Kitchen Essentials Bundle",
                     items=[{"name": "Kitchen Essentials Bundle", "qty": 1, "price_paise": 2499 * R}],
                     payment_method="card", failure_reason=FailureReason.INSUFFICIENT_FUNDS,
                     attempt_count=5, contacts_sent=4, detected_at=now - timedelta(days=4)),
        # 6 — used to demo graceful provider failure
        RecoveryCase(id="CASE_0006", customer_id="CUS_1006", risk_type=RiskType.PAYMENT_FAILURE,
                     amount_paise=12750 * R, description="Apple Watch SE Band + AppleCare",
                     items=[{"name": "Apple Watch SE Band", "qty": 1, "price_paise": 12750 * R}],
                     payment_method="upi", failure_reason=FailureReason.TECHNICAL_ERROR,
                     attempt_count=1, detected_at=now - timedelta(minutes=12)),
    ]

    db.add_all(customers)
    db.add_all(cases)
    db.add(MerchantPolicy(id=1))
    db.commit()
    db.close()
    print(f"Seeded {len(customers)} customers, {len(cases)} cases, 1 policy.")


if __name__ == "__main__":
    seed()