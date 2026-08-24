from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    database_url: str = "sqlite:///./recoveryos.db"

    # LLM
    llm_provider: str = "mock"
    llm_model: str = "gemini-2.5-flash"
    gemini_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Payments
    payment_provider: str = "simulated"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    @property
    def llm_status(self) -> str:
        if self.llm_provider == "mock":
            return "mock (deterministic, no API key required)"
        key = {
            "gemini": self.gemini_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
        }.get(self.llm_provider, "")
        return f"{self.llm_provider} (ready)" if key else f"{self.llm_provider} (missing API key)"

    @property
    def payment_status(self) -> str:
        if self.payment_provider == "simulated":
            return "simulated (DEMO MODE - no real payments)"
        if self.razorpay_key_id and self.razorpay_key_secret:
            mode = "test" if self.razorpay_key_id.startswith("rzp_test_") else "LIVE"
            return f"razorpay ({mode} mode)"
        return "razorpay (missing credentials)"

    @property
    def is_demo_mode(self) -> bool:
        return self.payment_provider == "simulated"


settings = Settings()
