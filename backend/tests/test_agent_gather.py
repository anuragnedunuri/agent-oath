"""Mode 2 source gathering tests (task 4.1, SPEC.md section 6).

Network fetches are monkeypatched; demo articles load from disk for real, so
the synthetic-marking and extraction of the shipped articles is actually
tested. The detector is faked as in the analyze API tests.
"""

import pytest
from fastapi.testclient import TestClient

import app.agent.search as search_module
import app.checks.synthetic as synthetic_module
from app.agent.approval import get_session, reset_store
from app.agent.search import (
    DEMO_PATH_PREFIX,
    MAX_BYO_URLS,
    MIN_BYO_URLS,
    create_agent_session,
    gather_urls,
    load_seed_topics,
)
from app.fetcher import FetchResult
from app.models import Signal, TrustSummary


@pytest.fixture(autouse=True)
def clean_store() -> None:
    reset_store()


@pytest.fixture(autouse=True)
def fake_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_loader() -> tuple[object, str]:
        return (lambda section: 0.9), "test-revision"

    monkeypatch.setattr(synthetic_module, "_load_default_scorer", fake_loader)


@pytest.fixture(autouse=True)
def offline_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep provenance lookups off the network in every gathering test."""

    def no_age(domain: str, **kwargs: object):  # noqa: ANN202
        from app.models import DomainAge

        return (
            DomainAge(status="unavailable", source="none", detail="Offline in tests."),
            [Signal(name="domain_age_lookup", raw_value="offline", interpretation="Test stub.")],
        )

    monkeypatch.setattr("app.checks.provenance.domain_age", no_age)


def fake_network_fetch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace network fetches with canned ok results; returns the call log."""
    calls: list[str] = []
    body = "An ordinary paragraph of source text for the agent to consider. " * 30

    def fake(url: str, **kwargs: object) -> FetchResult:
        calls.append(url)
        return FetchResult(
            status="ok",
            detail="The page was retrieved.",
            requested_url=url,
            final_url=url,
            host="example-news.com",
            html=f"<html><body><article>{body}</article></body></html>",
            text=body.strip(),
            signals=[Signal(name="http_status", raw_value="200", interpretation="Returned.")],
        )

    monkeypatch.setattr(search_module, "fetch_url", fake)
    return calls


class TestSeedTopics:
    def test_four_topics_each_with_four_to_five_urls(self) -> None:
        topics = load_seed_topics()

        assert len(topics) == 4
        for topic in topics:
            assert 4 <= len(topic.urls) <= 5

    def test_every_topic_exercises_the_required_states(self) -> None:
        """SPEC 6: rated outlet, bot-blocked outlet, unrated domain, demo article."""
        for topic in load_seed_topics():
            exercises = " ".join(seed.exercises for seed in topic.urls)
            assert "registry-rated" in exercises
            assert "bot-blocked" in exercises
            assert "unrated" in exercises
            assert any(seed.url.startswith(DEMO_PATH_PREFIX) for seed in topic.urls)

    def test_gathering_a_topic_populates_the_session_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_network_fetch(monkeypatch)

        session = create_agent_session("How?", topic_id="climate-adaptation")
        stored = get_session(session.session_id)

        assert stored is not None
        assert stored.gathering_mode == "seed"
        assert len(stored.sources) == 4
        for record in stored.sources.values():
            assert record.approved is None
            assert isinstance(record.trust_summary, TrustSummary)

    def test_unknown_topic_is_rejected_with_the_known_list(self) -> None:
        with pytest.raises(ValueError, match="Known topics"):
            create_agent_session("How?", topic_id="not-a-topic")


class TestDemoArticles:
    def test_demo_article_loads_from_disk_and_is_analyzed(self) -> None:
        url, text, summary = search_module.gather_source("/demo/articles/ai-regulation")

        assert summary.retrieval.status == "ok"
        assert "AI-generated demonstration article" in summary.retrieval.detail
        assert len(text.split()) >= 400
        assert summary.synthetic.status == "ok"
        assert summary.provenance.domain is None
        assert summary.credibility.status == "unrated"
        names = [signal.name for signal in summary.provenance.signals]
        assert "demo_article" in names

    def test_every_shipped_article_is_marked_synthetic_and_sized(self) -> None:
        """SPEC 6: 400 to 600 words, synthetic marker in the source."""
        for topic in load_seed_topics():
            for seed in topic.urls:
                if not seed.url.startswith(DEMO_PATH_PREFIX):
                    continue
                slug = seed.url.removeprefix(DEMO_PATH_PREFIX)
                path = search_module.demo_article_path(slug)
                assert path is not None, slug
                html = path.read_text(encoding="utf-8")
                assert "<!-- synthetic:" in html, slug
                words = len(search_module.extract_text(html).split())
                assert 400 <= words <= 600, f"{slug}: {words} words"

    def test_unknown_demo_slug_yields_an_honest_summary(self) -> None:
        _, _, summary = search_module.gather_source("/demo/articles/does-not-exist")

        assert summary.retrieval.status == "invalid_url"
        assert "No demo article exists" in summary.retrieval.detail

    def test_demo_articles_are_served_over_http(self, client: TestClient) -> None:
        response = client.get("/demo/articles/lunar-exploration")

        assert response.status_code == 200
        assert "<!-- synthetic:" in response.text
        assert client.get("/demo/articles/nope").status_code == 404
        assert client.get("/demo/articles/..%2fsecrets").status_code == 404


class TestBringYourOwn:
    def test_byo_populates_the_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = fake_network_fetch(monkeypatch)
        urls = [f"https://example-news.com/{n}" for n in range(3)]

        session = create_agent_session("How?", urls=urls)

        assert calls == urls
        stored = get_session(session.session_id)
        assert stored is not None
        assert stored.gathering_mode == "byo"
        assert len(stored.sources) == 3

    @pytest.mark.parametrize("count", [0, 1, 2, 6, 9])
    def test_wrong_url_count_is_rejected_plainly(self, count: int) -> None:
        with pytest.raises(ValueError, match="between 3 and 5"):
            gather_urls([f"https://example.org/{n}" for n in range(count)])

    def test_blank_entries_do_not_count_toward_the_minimum(self) -> None:
        with pytest.raises(ValueError, match="between 3 and 5"):
            gather_urls(["https://example.org/a", "  ", "", "https://example.org/b"])

    def test_bounds_are_inclusive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_network_fetch(monkeypatch)

        assert len(gather_urls([f"https://e.org/{n}" for n in range(MIN_BYO_URLS)])) == 3
        assert len(gather_urls([f"https://e.org/{n}" for n in range(MAX_BYO_URLS)])) == 5

    def test_exactly_one_gathering_mode_must_be_chosen(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            create_agent_session("How?")
        with pytest.raises(ValueError, match="exactly one"):
            create_agent_session("How?", topic_id="climate-adaptation", urls=["a", "b", "c"])
