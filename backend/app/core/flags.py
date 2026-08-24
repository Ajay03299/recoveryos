"""Runtime feature flags, persisted so they survive a reload mid-demo."""
from sqlalchemy.orm import Session

from app.models import SystemFlag

PAYMENT_PROVIDER_OUTAGE = "demo.payment_provider_outage"


def get_flag(db: Session, key: str, default: bool = False) -> bool:
    flag = db.get(SystemFlag, key)
    if flag is None:
        return default
    return bool(flag.value.get("enabled", default))


def set_flag(db: Session, key: str, enabled: bool) -> None:
    flag = db.get(SystemFlag, key)
    if flag is None:
        db.add(SystemFlag(key=key, value={"enabled": enabled}))
    else:
        flag.value = {"enabled": enabled}
    db.commit()
