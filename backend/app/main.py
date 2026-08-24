from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.db.session import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="RecoveryOS API",
    description="Autonomous revenue recovery for modern businesses.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")


@app.get("/")
def root():
    return {"service": "RecoveryOS API", "docs": "/docs"}
