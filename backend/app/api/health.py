from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "app_env": settings.app_env,
        "demo_mode": settings.is_demo_mode,
        "database": "connected" if db_ok else "unavailable",
        "payments": settings.payment_status,
        "llm": settings.llm_status,
    }
