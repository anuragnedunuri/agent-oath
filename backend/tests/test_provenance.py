"""Provenance module tests.

No test touches the real network: RDAP goes through httpx.MockTransport and
WHOIS through an injected query function. The failure paths (RDAP down, WHOIS
useless, page unreadable) are tested as carefully as the happy paths, because
under INV-2 they are features.
"""

from datetime import UTC, datetime

import httpx
import pytest

import app.checks.provenance as provenance_module
from app.checks.provenance import (
    CacheEntry,
    check_provenance,
    domain_age,
    registrable_domain,
)
from app.models import ProvenanceResult, Signal

FIXED_NOW = datetime(2026, 9, 19, tzinfo=UTC)

RDAP_OK_PAYLOAD = {
    "events": [
        {"eventAction": "registration", "eventDate": "1997-04-22T04:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2027-04-21T04:00:00Z"},
    ]
}

WHOIS_REGISTERED = datetime(1997, 4, 22, 4, tzinfo=UTC)
WHOIS_RAW = "1997-04-22 04:00:00"

ARTICLE_HTML = """
<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "NewsArticle",
 "publisher": {"@type": "Organization", "name": "Example News"},
 "datePublished": "2026-05-01T10:00:00+00:00"}
</script>
<meta property="og:site_name" content="Example News" />
<meta name="author" content="A. Reporter" />
<meta property="article:published_time" content="2026-05-01T10:00:00+00:00" />
</head><body><article><p>Body text.</p></article></body></html>
"""

BARE_HTML = "<html><head><title>Untitled</title></head><body><p>Words.</p></body></html>"


def rdap_transport(
    status_code: int = 200, payload: object = None, raise_error: bool = False
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if raise_error:
            raise httpx.ConnectError("rdap down", request=request)
        return httpx.Response(status_code, json=payload if payload is not None else {})

    return httpx.MockTransport(handler)


def working_whois(domain: str) -> tuple[datetime, str]:
    return WHOIS_REGISTERED, WHOIS_RAW


def useless_whois(domain: str) -> None:
    return None


def broken_whois(domain: str) -> None:
    raise OSError("connection refused")


def fixed_now() -> datetime:
    return FIXED_NOW


def get_age(
    domain: str = "example-news.com",
    *,
    transport: httpx.MockTransport | None = None,
    whois: object = broken_whois,
    cache: dict[str, CacheEntry] | None = None,
) -> tuple[object, list[Signal]]:
    return domain_age(
        domain,
        transport=transport or rdap_transport(payload=RDAP_OK_PAYLOAD),
        whois_lookup=whois,
        cache=cache if cache is not None else {},
        now=fixed_now,
    )


class TestRegistrableDomain:
    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("reuters.com", "reuters.com"),
            ("news.sky.com", "sky.com"),
            ("deep.sub.example.co.uk", "example.co.uk"),
        ],
    )
    def test_subdomains_reduce_to_the_registered_domain(self, host: str, expected: str) -> None:
        assert registrable_domain(host) == expected

    @pytest.mark.parametrize("host", ["93.184.216.34", "localhost"])
    def test_unreducible_hosts_come_back_unchanged(self, host: str) -> None:
        assert registrable_domain(host) == host


class TestDomainAge:
    def test_rdap_registration_gives_an_age_in_years(self) -> None:
        age, signals = get_age()

        assert age.status == "ok"
        assert age.source == "RDAP"
        assert age.value_years == pytest.approx(29.4, abs=0.1)
        assert signals[0].name == "rdap_registration_date"
        assert signals[0].raw_value == "1997-04-22T04:00:00Z"

    def test_rdap_miss_falls_back_to_whois(self) -> None:
        age, signals = get_age(transport=rdap_transport(status_code=404), whois=working_whois)

        assert age.status == "ok"
        assert age.source == "WHOIS"
        assert signals[0].name == "whois_creation_date"

    def test_rdap_network_failure_falls_back_to_whois(self) -> None:
        age, _ = get_age(transport=rdap_transport(raise_error=True), whois=working_whois)

        assert age.status == "ok"
        assert age.source == "WHOIS"

    def test_rdap_response_without_a_registration_event_falls_back(self) -> None:
        payload = {"events": [{"eventAction": "expiration", "eventDate": "2027-01-01T00:00:00Z"}]}
        age, _ = get_age(transport=rdap_transport(payload=payload), whois=working_whois)

        assert age.source == "WHOIS"

    def test_both_sources_failing_is_reported_not_faked(self) -> None:
        """SPEC 4.1: failure means unavailable, never an invented number."""
        age, signals = get_age(transport=rdap_transport(raise_error=True), whois=useless_whois)

        assert age.status == "unavailable"
        assert age.value_years is None
        assert "RDAP could not be reached" in age.detail
        assert "WHOIS gave no usable registration date" in age.detail
        assert signals[0].name == "domain_age_lookup"

    def test_whois_transport_errors_are_contained(self) -> None:
        age, _ = get_age(transport=rdap_transport(status_code=500), whois=broken_whois)

        assert age.status == "unavailable"

    def test_result_is_cached_so_rdap_is_hit_once(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=RDAP_OK_PAYLOAD)

        cache: dict[str, CacheEntry] = {}
        transport = httpx.MockTransport(handler)
        first, _ = get_age(transport=transport, cache=cache)
        second, _ = get_age(transport=transport, cache=cache)

        assert calls == 1
        assert first == second

    def test_expired_cache_entries_are_refreshed(self) -> None:
        cache: dict[str, CacheEntry] = {}
        get_age(cache=cache)
        entry = cache["example-news.com"]
        cache["example-news.com"] = (0.0, entry[1], entry[2])  # expired long ago

        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=RDAP_OK_PAYLOAD)

        get_age(transport=httpx.MockTransport(handler), cache=cache)

        assert calls == 1

    def test_whois_signal_carries_the_raw_value(self) -> None:
        _, signals = get_age(transport=rdap_transport(status_code=404), whois=working_whois)

        assert signals[0].raw_value == WHOIS_RAW


class TestDefaultWhoisLookup:
    """Unit tests for the python-whois wrapper, with the library call faked."""

    def run(self, monkeypatch: pytest.MonkeyPatch, response: object) -> object:
        calls: dict[str, object] = {}

        def fake_whois(domain: str, **kwargs: object) -> object:
            calls.update(kwargs, domain=domain)
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setattr(provenance_module.whois, "whois", fake_whois)
        result = provenance_module._default_whois_lookup("example-news.com")
        self.last_calls = calls
        return result

    def test_passes_an_explicit_timeout_to_the_library(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Project rule: every external call has an explicit timeout."""
        self.run(monkeypatch, {"creation_date": WHOIS_REGISTERED})

        assert self.last_calls["timeout"] == provenance_module.WHOIS_TIMEOUT_SECONDS

    def test_naive_datetime_is_made_utc_aware(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = self.run(monkeypatch, {"creation_date": datetime(1997, 4, 22, 4)})

        assert result is not None
        assert result[0] == WHOIS_REGISTERED

    def test_list_of_dates_uses_the_earliest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dates = [datetime(2005, 1, 1, tzinfo=UTC), WHOIS_REGISTERED]
        result = self.run(monkeypatch, {"creation_date": dates})

        assert result is not None
        assert result[0] == WHOIS_REGISTERED

    def test_missing_creation_date_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self.run(monkeypatch, {"creation_date": None}) is None

    def test_domain_not_found_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        error = provenance_module.WhoisDomainNotFoundError("no match")

        assert self.run(monkeypatch, error) is None


def provenance(
    *,
    url: str | None = "https://example-news.com/article",
    host: str | None = "example-news.com",
    html: str | None = ARTICLE_HTML,
    whois: object = broken_whois,
    transport: httpx.MockTransport | None = None,
) -> ProvenanceResult:
    return check_provenance(
        url=url,
        host=host,
        html=html,
        transport=transport or rdap_transport(payload=RDAP_OK_PAYLOAD),
        whois_lookup=whois,
        age_cache={},
        now=fixed_now,
    )


class TestCheckProvenance:
    def test_full_page_produces_every_field(self) -> None:
        result = provenance()

        assert result.domain == "example-news.com"
        assert result.https is True
        assert result.publisher_metadata_found is True
        assert result.publication_date is not None
        assert result.publication_date.status == "ok"
        assert result.publication_date.value == "2026-05-01"
        assert result.publication_date.method == "htmldate"
        assert result.domain_age is not None
        assert result.domain_age.status == "ok"

    def test_http_url_reports_https_false(self) -> None:
        result = provenance(url="http://example-news.com/article")

        assert result.https is False
        scheme = next(s for s in result.signals if s.name == "url_scheme")
        assert scheme.raw_value == "http"
        assert "not encrypted" in scheme.interpretation

    def test_page_without_metadata_reports_absence_per_signal(self) -> None:
        result = provenance(html=BARE_HTML)

        assert result.publisher_metadata_found is False
        assert result.publication_date is not None
        assert result.publication_date.status == "not_found"
        for name in ("jsonld_type", "og:site_name", "meta_author"):
            signal = next(s for s in result.signals if s.name == name)
            assert signal.raw_value == "Not present"

    def test_unreadable_page_leaves_page_checks_null_with_a_reason(self) -> None:
        """A blocked page still gets domain-level provenance (INV-2)."""
        result = provenance(html=None)

        assert result.publisher_metadata_found is None
        assert result.publication_date is None
        assert result.domain_age is not None
        assert result.domain_age.status == "ok"
        page = next(s for s in result.signals if s.name == "page_content")
        assert "could not be checked" in page.interpretation

    def test_pasted_text_has_null_provenance_and_an_explanation(self) -> None:
        result = provenance(url=None, host=None, html=None)

        assert result.domain is None
        assert result.https is None
        assert result.domain_age is None
        assert result.signals[0].name == "input_type"

    def test_subdomain_reduces_before_lookups_run(self) -> None:
        queried: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            queried.append(request.url.path)
            return httpx.Response(200, json=RDAP_OK_PAYLOAD)

        result = provenance(host="live.example-news.com", transport=httpx.MockTransport(handler))

        assert result.domain == "example-news.com"
        assert queried == ["/domain/example-news.com"]

    def test_jsonld_graph_wrapper_is_understood(self) -> None:
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@context": "https://schema.org", "@graph": ['
            '{"@type": "Organization", "name": "Example News"},'
            '{"@type": "WebSite"}]}'
            "</script></head><body></body></html>"
        )
        result = provenance(html=html)

        signal = next(s for s in result.signals if s.name == "jsonld_type")
        assert signal.raw_value == "Organization"

    def test_invalid_jsonld_is_ignored_not_fatal(self) -> None:
        html = (
            '<html><head><script type="application/ld+json">{not json'
            "</script></head><body></body></html>"
        )
        result = provenance(html=html)

        signal = next(s for s in result.signals if s.name == "jsonld_type")
        assert signal.raw_value == "Not present"


class TestInv4Traceability:
    def test_every_signal_is_complete(self) -> None:
        for result in (
            provenance(),
            provenance(html=None),
            provenance(url=None, host=None, html=None),
            provenance(transport=rdap_transport(raise_error=True)),
        ):
            assert result.signals
            for signal in result.signals:
                assert signal.name.strip()
                assert signal.raw_value.strip()
                assert signal.interpretation.strip()

    def test_no_em_dash_in_any_user_facing_string(self) -> None:
        result = provenance(transport=rdap_transport(raise_error=True), html=None)

        for signal in result.signals:
            assert "—" not in signal.raw_value
            assert "—" not in signal.interpretation
        assert result.domain_age is not None
        assert "—" not in result.domain_age.detail

    def test_the_displayed_age_is_reconstructible_from_its_signal(self) -> None:
        """SPEC 3: every displayed number must be derivable from signals."""
        result = provenance()

        assert result.domain_age is not None
        raw = next(s for s in result.signals if s.name == "rdap_registration_date").raw_value
        registered = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        expected = round((FIXED_NOW - registered).days / 365.25, 1)
        assert result.domain_age.value_years == expected
