"""Session audit export tests (task 4.5, SPEC.md section 6).

The core claim: an exported record reconstructs a scripted session exactly.
The script below performs a fixed sequence of actions; the test then derives
the session's story purely from the export and checks it matches the script,
event for event, in order, with parseable timestamps.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import app.agent.search as search_module
import app.checks.synthetic as synthetic_module
from app.agent.answer import reset_last_llm_payload
from app.agent.approval import reset_store
from app.config import get_settings
from app.fetcher import FetchResult
from app.models import AgentSessionExport, Signal

BODY = ("A steady paragraph of source text for the audit script to gather. " * 30).strip()


@pytest.fixture(autouse=True)
def clean_state() -> None:
    reset_store()
    reset_last_llm_payload()


@pytest.fixture(autouse=True)
def fake_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_loader() -> tuple[object, str]:
        return (lambda section: 0.2), "test-revision"

    monkeypatch.setattr(synthetic_module, "_load_default_scorer", fake_loader)


@pytest.fixture(autouse=True)
def offline_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_age(domain: str, **kwargs: object):  # noqa: ANN202
        from app.models import DomainAge

        return (
            DomainAge(status="unavailable", source="none", detail="Offline in tests."),
            [Signal(name="domain_age_lookup", raw_value="offline", interpretation="Stub.")],
        )

    monkeypatch.setattr("app.checks.provenance.domain_age", no_age)


@pytest.fixture(autouse=True)
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(url: str, **kwargs: object) -> FetchResult:
        return FetchResult(
            status="ok",
            detail="The page was retrieved.",
            requested_url=url,
            final_url=url,
            host="example-news.com",
            html=f"<html><body><article>{BODY}</article></body></html>",
            text=f"{BODY} TAIL::{url}",
            signals=[Signal(name="http_status", raw_value="200", interpretation="Returned.")],
        )

    monkeypatch.setattr(search_module, "fetch_url", fake)


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.agent.answer._default_llm_call", lambda request_body: "A gated answer [S1]."
    )
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")


URLS = ["https://a.org/one", "https://b.org/two", "https://c.org/three"]
QUESTION = "What does the scripted session establish?"


def run_script(client: TestClient) -> tuple[str, str, str]:
    """The fixed script: gather 3, approve first, reject second, answer twice."""
    ask = client.post("/api/agent/ask", json={"question": QUESTION, "urls": URLS}).json()
    sid = ask["session_id"]
    ids = [s["source_id"] for s in ask["sources"]]
    client.post("/api/agent/answer", json={"session_id": sid})  # refusal first
    client.post(
        "/api/agent/approve", json={"session_id": sid, "source_id": ids[0], "approved": True}
    )
    client.post(
        "/api/agent/approve", json={"session_id": sid, "source_id": ids[1], "approved": False}
    )
    client.post("/api/agent/answer", json={"session_id": sid})  # real (faked) answer
    return sid, ids[0], ids[1]


class TestExportReconstruction:
    def test_exported_record_reconstructs_the_scripted_session_exactly(
        self, client: TestClient, fake_llm: None
    ) -> None:
        sid, approved_id, rejected_id = run_script(client)

        export = AgentSessionExport.model_validate(client.get(f"/api/agent/export/{sid}").json())

        # Identity and inputs
        assert export.session_id == sid
        assert export.question == QUESTION
        assert export.gathering_mode == "byo"

        # Sources: urls in gathered order, approval states per script,
        # text lengths matching what the (faked) fetch produced.
        assert [s.url for s in export.sources] == URLS
        states = {s.source_id: s.approved for s in export.sources}
        assert states[approved_id] is True
        assert states[rejected_id] is False
        assert [a for a in states.values()].count(None) == 1
        for source in export.sources:
            assert source.extracted_text_length == len(f"{BODY} TAIL::{source.url}")
            assert source.trust_summary.retrieval.status == "ok"

        # Event timeline: the script, in order, nothing more, nothing less.
        assert [e.event for e in export.events] == [
            "session_created",
            "source_gathered",
            "source_gathered",
            "source_gathered",
            "answer_requested",
            "approval_set",
            "approval_set",
            "answer_requested",
        ]
        created, g1, g2, g3, refusal, ap1, ap2, answered = export.events
        assert created.data == {"question": QUESTION, "gathering_mode": "byo"}
        assert [g.data["url"] for g in (g1, g2, g3)] == URLS
        for g in (g1, g2, g3):
            assert g.data["extracted_text_length"] == len(f"{BODY} TAIL::{g.data['url']}")
            assert g.data["retrieval_status"] == "ok"
        assert refusal.data == {"status": "no_approved_sources", "source_ids_used": []}
        assert ap1.data == {"source_id": approved_id, "approved": True}
        assert ap2.data == {"source_id": rejected_id, "approved": False}
        assert answered.data == {"status": "ok", "source_ids_used": [approved_id]}

        # Every event is timestamped, parseable, and in order.
        stamps = [datetime.fromisoformat(e.at) for e in export.events]
        assert stamps == sorted(stamps)

    def test_export_never_contains_the_source_text_itself(
        self, client: TestClient, fake_llm: None
    ) -> None:
        """SPEC 6 exports the length; the text stays server-side (INV-1 hygiene)."""
        sid, _, _ = run_script(client)

        raw = client.get(f"/api/agent/export/{sid}").text

        assert "TAIL::" not in raw
        assert "extracted_text_length" in raw

    def test_unknown_session_export_is_a_plain_404(self, client: TestClient) -> None:
        response = client.get("/api/agent/export/nope")

        assert response.status_code == 404
        assert "lives for the session only" in response.json()["detail"]
