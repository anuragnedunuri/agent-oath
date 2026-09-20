"""FastAPI application: CORS, routers, and the /health endpoint."""

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .checks.synthetic import warm_up
from .config import get_settings
from .models import AnalyzeRequest, HealthResponse, TrustSummary
from .summary import build_trust_summary

SERVICE_NAME = "agent-oath-backend"
VERSION = "0.1.0"

settings = get_settings()


@asynccontextmanager
async def _lifespan(application: FastAPI):  # noqa: ANN202
    """Warm the detection model in the background at startup.

    The first analysis then does not pay the model load. Best effort: failure
    is retried and reported honestly by the check itself.
    """
    if settings.preload_detector:
        threading.Thread(target=warm_up, name="detector-warmup", daemon=True).start()
    yield


app = FastAPI(
    lifespan=_lifespan,
    title="Agent Oath: Trust Verification Layer",
    description="Trust Summary API for Mode 1 (Inspector) and Mode 2 (Agent demo).",
    version=VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.post("/api/analyze", response_model=TrustSummary, tags=["mode1"])
def analyze(request: AnalyzeRequest) -> TrustSummary:
    """Analyze one URL or one pasted text and return its Trust Summary.

    Always returns a complete summary for a valid request (INV-2). Runs as a
    sync endpoint so FastAPI executes it in the threadpool: the fetch and the
    provenance lookups are blocking calls with explicit timeouts.
    """
    return build_trust_summary(request)


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    """Liveness probe. Used by local dev, CI, and deployment uptime checks."""
    return HealthResponse(
        status="ok",
        service=SERVICE_NAME,
        version=VERSION,
        app_env=settings.app_env,
    )
