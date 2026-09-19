"""End-to-end tests for POST /api/analyze (task 1.3).

The fetcher and the provenance module have their own suites; here they are
replaced through monkeypatching so these tests exercise the assembly logic:
what ran, what could not, and whether every outcome is a complete TrustSummary.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import app.summary as summary_module
from app.fetcher import FetchResult
from app.models import DomainAge, ProvenanceResult, PublicationDate, Signal, TrustSummary

LONG_TEXT = ("An ordinary paragraph of source text for the analyzer to consider. " * 40).strip()
SHORT_TEXT = "Only a handful of words."


def fake_fetch(result: FetchResult) -> object:
    def _fetch(url: str, **kwargs: object) -> FetchResult:
        return result

    return _fetch


def fake_provenance() -> object:
    def _check(
        *, url: str | None, host: str | None, html: str | None, **kwargs: object
    ) -> ProvenanceResult:
        return ProvenanceResult(
            domain="example-news.com",
            domain_age=DomainAge(status="ok", value_years=29.4, source="RDAP"),
            https=True,
            publisher_metadata_found=bool(html),
            publication_date=(
                PublicationDate(status="ok", value="2026-05-01", method="htmldate")
                if html
                else None
            ),
            signals=[
                Signal(
                    name="rdap_registration_date",
                    raw_value="1997-04-22T04:00:00Z",
                    interpretation="The domain was registered 29.4 years ago, per RDAP.",
                )
            ],
        )

    return _check


def ok_fetch_result(text: str = LONG_TEXT) -> FetchResult:
    return FetchResult(
        status="ok",
        detail=f"The page was retrieved and {len(text)} characters of text were extracted.",
        requested_url="https://example-news.com/article",
        final_url="https://example-news.com/article",
        host="example-news.com",
        http_status=200,
        content_type="text/html",
        html=f"<html><body><article>{text}</article></body></html>",
        text=text,
        signals=[Signal(name="http_status", raw_value="200", interpretation="Page returned.")],
    )


def blocked_fetch_result() -> FetchResult:
    return FetchResult(
        status="paywall_or_blocked",
        detail=(
            "The site returned HTTP 403, so the page content could not be read. "
            "This usually means a paywall, a login requirement, or bot protection."
        ),
        requested_url="https://example-news.com/article",
        final_url="https://example-news.com/article",
        host="example-news.com",
        http_status=403,
        content_type="text/html",
        signals=[Signal(name="http_status", raw_value="403", interpretation="Refused.")],
    )


def invalid_fetch_result() -> FetchResult:
    return FetchResult(
        status="invalid_url",
        detail="That web address does not contain a usable site name.",
        requested_url="not a url at all",
        signals=[Signal(name="hostname", raw_value="not a url", interpretation="Invalid.")],
    )


class TestUrlInput:
    def test_readable_page_runs_provenance_and_registry(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(summary_module, "fetch_url", fake_fetch(ok_fetch_result()))
        monkeypatch.setattr(summary_module, "check_provenance", fake_provenance())

        body = client.post("/api/analyze", json={"url": "https://example-news.com/article"})
        summary = TrustSummary.model_validate(body.json())

        assert body.status_code == 200
        assert summary.retrieval.status == "ok"
        assert summary.retrieval.checks_completed == ["provenance", "registry"]
        assert summary.provenance.domain == "example-news.com"
        assert summary.credibility.status == "unrated"

    def test_detection_is_declared_not_integrated_never_faked(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Until task 2.2 there is no detector, and the summary says exactly that."""
        monkeypatch.setattr(summary_module, "fetch_url", fake_fetch(ok_fetch_result()))
        monkeypatch.setattr(summary_module, "check_provenance", fake_provenance())

        summary = TrustSummary.model_validate(
            client.post("/api/analyze", json={"url": "https://example-news.com/article"}).json()
        )

        assert summary.synthetic.status == "not_integrated"
        assert summary.synthetic.probability_ai is None
        assert "not been integrated" in summary.synthetic.detail
        assert "synthetic" not in summary.retrieval.checks_completed

    def test_blocked_page_yields_honest_partial_summary(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The demo checkpoint case: paywalled URL, partial but complete summary."""
        monkeypatch.setattr(summary_module, "fetch_url", fake_fetch(blocked_fetch_result()))
        monkeypatch.setattr(summary_module, "check_provenance", fake_provenance())

        summary = TrustSummary.model_validate(
            client.post("/api/analyze", json={"url": "https://example-news.com/article"}).json()
        )

        assert summary.retrieval.status == "paywall_or_blocked"
        assert summary.retrieval.detail
        assert summary.retrieval.checks_completed == ["provenance", "registry"]
        assert summary.synthetic.status == "skipped_no_content"
        assert summary.provenance.domain_age is not None
        assert summary.provenance.domain_age.value_years == 29.4

    def test_fetch_signals_are_merged_into_provenance_signals(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INV-4: the HTTP evidence stays visible in the traceable signals."""
        monkeypatch.setattr(summary_module, "fetch_url", fake_fetch(blocked_fetch_result()))
        monkeypatch.setattr(summary_module, "check_provenance", fake_provenance())

        summary = TrustSummary.model_validate(
            client.post("/api/analyze", json={"url": "https://example-news.com/article"}).json()
        )
        names = [signal.name for signal in summary.provenance.signals]

        assert "http_status" in names
        assert "rdap_registration_date" in names

    def test_invalid_url_still_yields_a_complete_summary(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(summary_module, "fetch_url", fake_fetch(invalid_fetch_result()))

        response = client.post("/api/analyze", json={"url": "not a url at all"})
        summary = TrustSummary.model_validate(response.json())

        assert response.status_code == 200
        assert summary.retrieval.status == "invalid_url"
        assert summary.retrieval.checks_completed == []
        assert summary.provenance.domain is None
        assert summary.credibility.status == "unrated"
        assert "no domain" in summary.credibility.unrated_explanation


class TestTextInput:
    """Pasted text never touches the network: these run the real assembly."""

    def test_short_text_refuses_detection_honestly(self, client: TestClient) -> None:
        summary = TrustSummary.model_validate(
            client.post("/api/analyze", json={"text": SHORT_TEXT}).json()
        )

        assert summary.input_type == "text"
        assert summary.synthetic.status == "text_too_short"
        assert "5 words" in summary.synthetic.detail
        assert summary.retrieval.checks_completed == ["synthetic"]
        assert summary.provenance.domain is None
        assert summary.credibility.status == "unrated"

    def test_long_text_reports_detector_not_integrated(self, client: TestClient) -> None:
        summary = TrustSummary.model_validate(
            client.post("/api/analyze", json={"text": LONG_TEXT}).json()
        )

        assert summary.synthetic.status == "not_integrated"
        assert summary.synthetic.probability_ai is None
        assert "not been integrated" in summary.synthetic.detail
        assert summary.retrieval.checks_completed == []

    def test_text_provenance_explains_why_it_could_not_run(self, client: TestClient) -> None:
        summary = TrustSummary.model_validate(
            client.post("/api/analyze", json={"text": SHORT_TEXT}).json()
        )

        assert summary.provenance.signals[0].name == "input_type"


class TestRequestValidation:
    def test_neither_input_is_rejected(self, client: TestClient) -> None:
        assert client.post("/api/analyze", json={}).status_code == 422

    def test_both_inputs_are_rejected(self, client: TestClient) -> None:
        response = client.post("/api/analyze", json={"url": "https://example.org", "text": "words"})

        assert response.status_code == 422


class TestLiveWiring:
    """One test through the real fetcher with a mocked transport end to end."""

    def test_real_assembly_with_mocked_network(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.checks.provenance as provenance_module
        import app.fetcher as fetcher_module

        article = (
            "<html><head><meta property='og:site_name' content='Example News'/></head>"
            f"<body><article><p>{LONG_TEXT}</p></article></body></html>"
        )

        def page_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"content-type": "text/html"}, content=article.encode()
            )

        def rdap_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "events": [{"eventAction": "registration", "eventDate": "1997-04-22T04:00:00Z"}]
                },
            )

        original_fetch = fetcher_module.fetch_url
        monkeypatch.setattr(
            summary_module,
            "fetch_url",
            lambda url, **kw: original_fetch(
                url,
                transport=httpx.MockTransport(page_handler),
                resolve_host=lambda host: ["93.184.216.34"],
            ),
        )
        original_provenance = provenance_module.check_provenance
        monkeypatch.setattr(
            summary_module,
            "check_provenance",
            lambda **kw: original_provenance(
                **kw,
                transport=httpx.MockTransport(rdap_handler),
                whois_lookup=lambda domain: None,
                age_cache={},
            ),
        )

        summary = TrustSummary.model_validate(
            client.post("/api/analyze", json={"url": "https://example-news.com/article"}).json()
        )

        assert summary.retrieval.status == "ok"
        assert summary.provenance.domain == "example-news.com"
        assert summary.provenance.domain_age is not None
        assert summary.provenance.domain_age.value_years is not None
        assert summary.provenance.publisher_metadata_found is True
        names = [signal.name for signal in summary.provenance.signals]
        assert "extracted_characters" in names
        assert "rdap_registration_date" in names
