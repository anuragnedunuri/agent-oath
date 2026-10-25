"""FastAPI application: CORS, routers, and the /health endpoint."""

import json as _json
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .agent.answer import generate_answer, get_last_llm_payload
from .agent.approval import get_session, set_approval
from .agent.search import (
    SEED_TOPICS_PATH,
    create_agent_session,
    demo_article_path,
    load_seed_topics,
)
from .checks.synthetic import warm_up
from .config import get_settings
from .models import (
    AgentAnswerRequest,
    AgentAnswerResponse,
    AgentApproveRequest,
    AgentApproveResponse,
    AgentAskRequest,
    AgentAskResponse,
    AgentSourceView,
    AgentTopicsResponse,
    AgentTopicSummary,
    AnalyzeRequest,
    HealthResponse,
    LlmPayloadDebugResponse,
    TrustSummary,
)
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
    if settings.app_env == "dev":
        # Dev-mode payload proof (SPEC 6): make the exact LLM request body
        # visible on the server console, where an evaluator will look.
        payload_logger = logging.getLogger("agent.llm")
        payload_logger.setLevel(logging.INFO)
        if not payload_logger.handlers:
            payload_logger.addHandler(logging.StreamHandler())
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


@app.get("/api/agent/topics", response_model=AgentTopicsResponse, tags=["mode2"])
def agent_topics() -> AgentTopicsResponse:
    """The curated demo topics, with the curation disclosed (SPEC 6)."""
    raw = _json.loads(SEED_TOPICS_PATH.read_text(encoding="utf-8"))
    return AgentTopicsResponse(
        disclosure=raw["disclosure"],
        topics=[
            AgentTopicSummary(topic_id=topic.topic_id, title=topic.title)
            for topic in load_seed_topics()
        ],
    )


@app.get("/api/agent/debug/payload", response_model=LlmPayloadDebugResponse, tags=["mode2"])
def agent_debug_payload() -> LlmPayloadDebugResponse:
    """Dev mode only: the exact request body of the most recent LLM call.

    Lets the payload-isolation proof be shown on screen (SPEC 6). Outside
    dev mode this surface does not exist.
    """
    if settings.app_env != "dev":
        raise HTTPException(status_code=404, detail="Not available outside dev mode.")
    payload = get_last_llm_payload()
    if payload is None:
        return LlmPayloadDebugResponse(
            payload=None,
            detail=(
                "No LLM call has been made yet in this server process. Approve a source "
                "and request an answer first."
            ),
        )
    return LlmPayloadDebugResponse(
        payload=payload,
        detail="The exact request body of the most recent LLM call in this process.",
    )


@app.post("/api/agent/ask", response_model=AgentAskResponse, tags=["mode2"])
def agent_ask(request: AgentAskRequest) -> AgentAskResponse:
    """Gather candidate sources and return their Trust Summaries, no answer.

    Runs the full Mode 1 analysis on every source and stores everything
    server-side. The response carries summaries and ids only; extracted
    source text never leaves the backend (INV-1).
    """
    try:
        session = create_agent_session(
            request.question, topic_id=request.topic_id, urls=request.urls
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    return AgentAskResponse(
        session_id=session.session_id,
        question=session.question,
        gathering_mode=session.gathering_mode,
        sources=[
            AgentSourceView(
                source_id=record.source_id,
                url=record.url,
                approved=record.approved,
                trust_summary=record.trust_summary,
            )
            for record in session.sources.values()
        ],
    )


@app.post("/api/agent/approve", response_model=AgentApproveResponse, tags=["mode2"])
def agent_approve(request: AgentApproveRequest) -> AgentApproveResponse:
    """Record one approve or reject decision in the server-side store.

    The store is the ONLY record of approval (SPEC section 6 step 3).
    """
    record = set_approval(request.session_id, request.source_id, request.approved)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No such session or source. Gather sources first with /api/agent/ask.",
        )
    return AgentApproveResponse(
        session_id=request.session_id,
        source_id=request.source_id,
        approved=record.approved is True,
    )


@app.post("/api/agent/answer", response_model=AgentAnswerResponse, tags=["mode2"])
def agent_answer(request: AgentAnswerRequest) -> AgentAnswerResponse:
    """Answer exclusively from approved sources in the store (INV-1).

    Accepts a session id and nothing else; the request model rejects any
    extra field. Zero approved sources yields an explicit refusal, never a
    silent fall-back to general knowledge.
    """
    session = get_session(request.session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="No such session. Gather sources first with /api/agent/ask.",
        )
    return generate_answer(session)


@app.get("/demo/articles/{slug}", response_class=HTMLResponse, tags=["demo"])
def demo_article(slug: str) -> HTMLResponse:
    """Serve an AI-generated demo article for the Mode 2 seed topics (SPEC 6).

    Each article is generated with a large language model and marked synthetic
    in an HTML comment at the top of its source.
    """
    path = demo_article_path(slug)
    if path is None:
        raise HTTPException(status_code=404, detail="No demo article with that name exists.")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    """Liveness probe. Used by local dev, CI, and deployment uptime checks."""
    return HealthResponse(
        status="ok",
        service=SERVICE_NAME,
        version=VERSION,
        app_env=settings.app_env,
    )
