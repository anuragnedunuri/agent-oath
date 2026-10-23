"""Server-side session and approval store (SPEC.md section 6, INV-1).

This store is the ONLY record of which sources exist in a session and which
are approved. Source text lives here, server-side, and nowhere else that the
answer pipeline reads from: the frontend never sends source text anywhere,
and the answer endpoint (task 4.2) resolves approved source IDs against this
store exclusively. That is what makes prompt injection through rejected
sources structurally impossible rather than merely discouraged.

Task 4.1 builds the store and fills it via gathering. The approve and answer
endpoints arrive in task 4.2; the exportable audit record in task 4.5.
In-memory and per-process by design: sessions are demo-lived, and the store
never persists across restarts.
"""

import threading
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from ..models import TrustSummary


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SourceRecord(BaseModel):
    """One gathered source: its text, its Trust Summary, and its approval state."""

    source_id: str
    url: str
    extracted_text: str = Field(
        default="",
        description="Empty when retrieval failed; the Trust Summary says why.",
    )
    trust_summary: TrustSummary
    approved: bool | None = Field(
        default=None,
        description="None until the user decides. Only task 4.2's endpoint changes this.",
    )
    gathered_at: str


class AgentSession(BaseModel):
    """One Mode 2 session: a question and its gathered sources."""

    session_id: str
    question: str
    created_at: str
    gathering_mode: str = Field(description="'seed' or 'byo', disclosed in the UI.")
    sources: dict[str, SourceRecord] = Field(default_factory=dict)


_lock = threading.Lock()
_sessions: dict[str, AgentSession] = {}


def create_session(
    question: str,
    gathering_mode: str,
    gathered: list[tuple[str, str, TrustSummary]],
) -> AgentSession:
    """Create and store a session from gathered (url, extracted_text, summary) triples."""
    session = AgentSession(
        session_id=uuid.uuid4().hex,
        question=question,
        created_at=_now_iso(),
        gathering_mode=gathering_mode,
    )
    for url, extracted_text, trust_summary in gathered:
        source_id = uuid.uuid4().hex[:12]
        session.sources[source_id] = SourceRecord(
            source_id=source_id,
            url=url,
            extracted_text=extracted_text,
            trust_summary=trust_summary,
            gathered_at=_now_iso(),
        )
    with _lock:
        _sessions[session.session_id] = session
    return session


def get_session(session_id: str) -> AgentSession | None:
    """Fetch a session by id, or None for an unknown id."""
    with _lock:
        return _sessions.get(session_id)


def reset_store() -> None:
    """Empty the store. Tests only."""
    with _lock:
        _sessions.clear()


def set_approval(session_id: str, source_id: str, approved: bool) -> SourceRecord | None:
    """Record one approve or reject decision. Returns None for unknown ids.

    This store is the ONLY record of approval (SPEC section 6). The answer
    pipeline reads approval state from here and from nowhere else.
    """
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return None
        record = session.sources.get(source_id)
        if record is None:
            return None
        record.approved = approved
        return record
