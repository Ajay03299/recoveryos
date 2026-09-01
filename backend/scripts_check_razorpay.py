"""Standalone Razorpay connectivity check. Run before any demo."""
from app.core.config import settings
from app.services.payments.razorpay import RazorpayProvider

key = settings.RAZORPAY_KEY_ID
print(f"PAYMENT_PROVIDER = {settings.PAYMENT_PROVIDER}")
print(f"KEY_ID           = {key[:14] + '…' if key else 'NOT SET'}")

if not key.startswith("rzp_test_"):
    print("\n⚠️  Key is not a test key (must start with rzp_test_). Stopping.")
    raise SystemExit(1)

provider = RazorpayProvider()
link = provider.create_payment_link(
    case_id="CONNECTIVITY_CHECK", amount_paise=100,
    description="RecoveryOS connectivity check", customer_name="Test User",
    customer_email="test@example.com", customer_phone="+919876543210",
)
print(f"\n✅ Link created: {link.reference}")
print(f"   Open in browser: {link.url}")

state = provider.get_payment_state(link.reference)
print(f"✅ Status read back: {state.status}")
print("\nRazorpay test mode is working.")
