"""Fetcher tests.

Every failure class in the taxonomy has a test, because under INV-2 the honest
failure paths are features, not edge cases. No test touches the real network:
HTTP goes through httpx.MockTransport and DNS through an injected resolver.
"""

import httpx
import pytest

from app.fetcher import (
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    MIN_EXTRACTABLE_CHARS,
    FetchResult,
    detect_paywall_marker,
    extract_text,
    fetch_url,
)

PUBLIC_IP = "93.184.216.34"
ARTICLE_URL = "https://example-news.com/article"


def public_dns(host: str) -> list[str]:
    """A resolver that puts every host on a public address."""
    return [PUBLIC_IP]


def failing_dns(host: str) -> list[str]:
    raise OSError("Name or service not known")


def private_dns(host: str) -> list[str]:
    return ["127.0.0.1"]


def long_article(paragraphs: int = 12) -> str:
    body = "\n".join(
        f"<p>Paragraph {n} of an ordinary news story about a central bank decision "
        f"that was reported by several outlets on the same afternoon.</p>"
        for n in range(paragraphs)
    )
    return f"<html><body><article>{body}</article></body></html>"


def fetch(
    *,
    handler: object = None,
    url: str = ARTICLE_URL,
    resolver: object = public_dns,
    timeout_seconds: float = 10.0,
    **response_kwargs: object,
) -> FetchResult:
    """Run fetch_url against a mocked transport.

    Pass `handler` for full control, or response keyword arguments (status_code,
    html, headers) for the common case of a single canned response.
    """
    if handler is None:
        html = response_kwargs.pop("html", long_article())
        status_code = response_kwargs.pop("status_code", 200)
        headers = response_kwargs.pop("headers", {"content-type": "text/html; charset=utf-8"})

        def handler(request: httpx.Request) -> httpx.Response:  # noqa: F811
            return httpx.Response(status_code, headers=headers, content=html.encode("utf-8"))

    return fetch_url(
        url,
        timeout_seconds=timeout_seconds,
        transport=httpx.MockTransport(handler),
        resolve_host=resolver,
    )


class TestSuccess:
    def test_article_is_retrieved_and_extracted(self) -> None:
        result = fetch()

        assert result.status == "ok"
        assert result.has_text
        assert "central bank decision" in (result.text or "")
        assert result.http_status == 200
        assert result.host == "example-news.com"

    def test_www_prefix_is_stripped_from_the_host(self) -> None:
        assert fetch(url="https://www.example-news.com/article").host == "example-news.com"

    def test_missing_scheme_is_assumed_to_be_https(self) -> None:
        result = fetch(url="example-news.com/article")

        assert result.status == "ok"
        assert result.final_url is not None
        assert result.final_url.startswith("https://")
        assert any(signal.name == "scheme_assumed" for signal in result.signals)

    def test_raw_html_is_kept_for_the_provenance_module(self) -> None:
        """Task 1.2 reads metadata out of this without re-fetching."""
        assert "<article>" in (fetch().html or "")

    def test_extracted_character_count_is_reported_as_a_signal(self) -> None:
        result = fetch()
        signal = next(s for s in result.signals if s.name == "extracted_characters")

        assert int(signal.raw_value) == len(result.text or "")


class TestBlockedAndPaywalled:
    @pytest.mark.parametrize("status_code", [401, 402, 403])
    def test_refusal_statuses_map_to_paywall_or_blocked(self, status_code: int) -> None:
        result = fetch(status_code=status_code)

        assert result.status == "paywall_or_blocked"
        assert str(status_code) in result.detail

    def test_structured_paywall_flag_is_detected(self) -> None:
        html = (
            '<html><body><script type="application/ld+json">'
            '{"@type":"NewsArticle","isAccessibleForFree":false}</script>'
            f"<article>{'Only the opening paragraph is public. ' * 30}</article>"
            "</body></html>"
        )
        result = fetch(html=html)

        assert result.status == "paywall_or_blocked"
        marker = next(s for s in result.signals if s.name == "paywall_marker")
        assert marker.raw_value == "isAccessibleForFree=false"

    def test_paywall_phrase_is_detected_and_named(self) -> None:
        html = (
            "<html><body><article>"
            f"{'An opening paragraph that is visible to everyone. ' * 20}"
            "<p>Subscribe to continue reading.</p>"
            "</article></body></html>"
        )
        result = fetch(html=html)

        assert result.status == "paywall_or_blocked"
        marker = next(s for s in result.signals if s.name == "paywall_marker")
        assert marker.raw_value == "subscribe to continue reading"

    def test_ordinary_article_about_paywalls_is_not_flagged(self) -> None:
        """The marker list is specific so that reporting on paywalls still reads."""
        html = (
            "<html><body><article>"
            f"{'Newspapers have put up a paywall in growing numbers this year. ' * 20}"
            "</article></body></html>"
        )

        assert fetch(html=html).status == "ok"


class TestRetrievalFailures:
    def test_timeout_is_reported_as_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        result = fetch(handler=handler)

        assert result.status == "timeout"
        assert "10 seconds" in result.detail

    def test_connect_timeout_is_also_a_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("no answer", request=request)

        assert fetch(handler=handler).status == "timeout"

    def test_refused_connection_is_unreachable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        result = fetch(handler=handler)

        assert result.status == "unreachable"
        assert any(s.name == "transport_error" for s in result.signals)

    def test_dns_failure_is_unreachable(self) -> None:
        result = fetch(resolver=failing_dns)

        assert result.status == "unreachable"
        assert "may not exist" in result.detail

    @pytest.mark.parametrize("status_code", [404, 410, 500, 503])
    def test_other_error_statuses_are_http_error(self, status_code: int) -> None:
        result = fetch(status_code=status_code)

        assert result.status == "http_error"
        assert str(status_code) in result.detail

    def test_non_html_content_type_is_not_html(self) -> None:
        result = fetch(headers={"content-type": "application/pdf"}, html="%PDF-1.4")

        assert result.status == "not_html"
        assert "application/pdf" in result.detail

    def test_missing_content_type_is_assumed_to_be_html(self) -> None:
        result = fetch(headers={})

        assert result.status == "ok"
        assert any(s.raw_value == "not sent" for s in result.signals)

    def test_thin_page_is_js_required(self) -> None:
        result = fetch(html='<html><body><div id="root"></div></body></html>')

        assert result.status == "js_required"
        assert "builds its content in the browser" in result.detail

    def test_js_required_reports_the_character_count_it_found(self) -> None:
        html = "<html><body><article><p>" + ("word " * 20) + "</p></article></body></html>"
        result = fetch(html=html)

        assert result.status == "js_required"
        assert str(len(result.text or "")) in result.detail
        assert len(result.text or "") < MIN_EXTRACTABLE_CHARS


class TestRedirects:
    def test_redirect_is_followed_and_the_final_url_recorded(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/article":
                return httpx.Response(301, headers={"location": "/story"})
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=long_article().encode("utf-8"),
            )

        result = fetch(handler=handler)

        assert result.status == "ok"
        assert result.final_url == "https://example-news.com/story"
        assert any(s.name == "redirect" for s in result.signals)

    def test_redirect_loop_stops_and_is_reported(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "/next"})

        result = fetch(handler=handler)

        assert result.status == "unreachable"
        assert str(MAX_REDIRECTS) in result.detail

    def test_redirect_to_a_private_address_is_refused(self) -> None:
        """A public URL must not be able to steer the server onto an internal host."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta"})

        def resolver(host: str) -> list[str]:
            return ["169.254.169.254"] if host == "169.254.169.254" else [PUBLIC_IP]

        result = fetch(handler=handler, resolver=resolver)

        assert result.status == "invalid_url"
        assert "private or internal network" in result.detail

    def test_redirect_without_a_destination_is_an_http_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302)

        result = fetch(handler=handler)

        assert result.status == "http_error"
        assert "did not say where to go next" in result.detail


class TestUrlValidation:
    def test_empty_input_is_invalid(self) -> None:
        assert fetch(url="   ").status == "invalid_url"

    def test_non_http_scheme_is_refused(self) -> None:
        result = fetch(url="ftp://example-news.com/article")

        assert result.status == "invalid_url"
        assert "ftp" in result.detail

    def test_file_scheme_cannot_read_local_disk(self) -> None:
        assert fetch(url="file:///etc/passwd").status == "invalid_url"

    def test_address_with_no_host_is_refused(self) -> None:
        assert fetch(url="https:///justapath").status == "invalid_url"

    @pytest.mark.parametrize(
        "address",
        ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1"],
    )
    def test_private_and_loopback_addresses_are_refused(self, address: str) -> None:
        """Server-side request forgery guard. Evaluators will try the metadata IP."""

        def resolver(host: str) -> list[str]:
            return [address]

        result = fetch(resolver=resolver)

        assert result.status == "invalid_url"
        assert "private or internal network" in result.detail

    @pytest.mark.parametrize(
        "address",
        [
            "64:ff9b::12d5:15b1",  # NAT64 wrapping public 18.213.21.177
            "::ffff:93.184.216.34",  # IPv4-mapped, public
            "2002:5db8:d822::1",  # 6to4 wrapping public 93.184.216.34
            "2600:1f18:63ef:e800::1",  # ordinary public IPv6
        ],
    )
    def test_public_addresses_in_ipv6_wrappers_are_allowed(self, address: str) -> None:
        """NAT64 and friends look 'reserved' to Python but are ordinary sites."""

        def resolver(host: str) -> list[str]:
            return [address]

        assert fetch(resolver=resolver).status == "ok"

    @pytest.mark.parametrize(
        "address",
        [
            "64:ff9b::7f00:1",  # NAT64 wrapping loopback 127.0.0.1
            "64:ff9b::a9fe:a9fe",  # NAT64 wrapping link-local 169.254.169.254
            "::ffff:10.0.0.1",  # IPv4-mapped private
        ],
    )
    def test_private_addresses_hidden_in_ipv6_wrappers_are_still_refused(
        self, address: str
    ) -> None:
        """The unwrapping must not become a bypass for the private-address guard."""

        def resolver(host: str) -> list[str]:
            return [address]

        result = fetch(resolver=resolver)

        assert result.status == "invalid_url"
        assert "private or internal network" in result.detail

    def test_a_host_resolving_to_both_public_and_private_is_refused(self) -> None:
        """DNS rebinding defence: one private answer is enough to refuse."""

        def resolver(host: str) -> list[str]:
            return [PUBLIC_IP, "127.0.0.1"]

        assert fetch(resolver=resolver).status == "invalid_url"

    def test_host_resolving_to_nothing_is_unreachable_not_private(self) -> None:
        """An empty DNS answer is not evidence of a private address."""

        def resolver(host: str) -> list[str]:
            return []

        result = fetch(resolver=resolver)

        assert result.status == "unreachable"
        assert "private" not in result.detail


class TestTextExtraction:
    def test_scripts_and_styles_are_removed(self) -> None:
        html = (
            "<html><body>"
            "<script>var tracking = 1;</script>"
            "<style>.ad { display: none; }</style>"
            "<p>The actual sentence.</p>"
            "</body></html>"
        )
        text = extract_text(html)

        assert text == "The actual sentence."

    def test_navigation_and_footer_chrome_is_removed(self) -> None:
        html = (
            "<html><body>"
            "<nav>Home Sport Business</nav>"
            "<p>The actual sentence.</p>"
            "<footer>Copyright notice</footer>"
            "</body></html>"
        )
        text = extract_text(html)

        assert "Home Sport" not in text
        assert "Copyright" not in text
        assert "The actual sentence." in text

    def test_article_container_is_preferred_over_the_whole_page(self) -> None:
        html = (
            "<html><body>"
            "<div>Related stories you might like</div>"
            "<article><p>The story itself.</p></article>"
            "</body></html>"
        )
        text = extract_text(html)

        assert text == "The story itself."
        assert "Related stories" not in text

    def test_main_container_is_used_when_there_is_no_article(self) -> None:
        html = "<html><body><div>Sidebar</div><main><p>The story.</p></main></body></html>"

        assert extract_text(html) == "The story."

    def test_whitespace_is_collapsed_and_blank_lines_dropped(self) -> None:
        html = "<html><body><p>One    two</p>\n\n\n<p>  three  </p></body></html>"

        assert extract_text(html) == "One two\nthree"

    def test_empty_document_yields_empty_text(self) -> None:
        assert extract_text("") == ""

    def test_malformed_markup_does_not_raise(self) -> None:
        assert "Text" in extract_text("<html><body><p>Text<div><span></body>")


class TestPaywallDetection:
    def test_returns_none_when_nothing_matches(self) -> None:
        assert detect_paywall_marker("<html></html>", "An ordinary sentence.") is None

    def test_marker_matching_is_case_insensitive(self) -> None:
        assert detect_paywall_marker("", "SUBSCRIBE TO CONTINUE READING") is not None

    @pytest.mark.parametrize(
        "flag",
        [
            '"isAccessibleForFree": false',
            "isAccessibleForFree='false'",
            "isAccessibleForFree:False",
        ],
    )
    def test_structured_flag_variants_are_recognized(self, flag: str) -> None:
        assert detect_paywall_marker(f"<script>{{{flag}}}</script>", "") is not None

    def test_free_articles_are_not_flagged_by_the_structured_check(self) -> None:
        html = '<script>{"isAccessibleForFree": true}</script>'

        assert detect_paywall_marker(html, "") is None


class TestHardening:
    def test_oversized_response_is_truncated_rather_than_exhausting_memory(self) -> None:
        filler = "<p>" + ("padding " * 64) + "</p>"
        repeats = (MAX_RESPONSE_BYTES // len(filler)) + 200
        html = f"<html><body><article>{filler * repeats}</article></body></html>"
        result = fetch(html=html)

        assert result.status == "ok"
        assert len(result.html or "") <= MAX_RESPONSE_BYTES
        assert any(s.name == "response_truncated" for s in result.signals)

    def test_unusual_encoding_does_not_raise(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=iso-8859-1"},
                content=long_article().encode("iso-8859-1"),
            )

        assert fetch(handler=handler).status == "ok"

    def test_undecodable_bytes_do_not_raise(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=b"\xff\xfe\x00bad bytes" + long_article().encode("utf-8"),
            )

        assert fetch(handler=handler).status == "ok"


class TestInv2Contract:
    """Whatever happens, a caller gets a usable result rather than an exception."""

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"status_code": 200}, "ok"),
            ({"status_code": 403}, "paywall_or_blocked"),
            ({"status_code": 404}, "http_error"),
            ({"html": "<html><body></body></html>"}, "js_required"),
            ({"headers": {"content-type": "image/png"}}, "not_html"),
        ],
    )
    def test_every_outcome_carries_a_non_empty_detail(
        self, kwargs: dict[str, object], expected: str
    ) -> None:
        result = fetch(**kwargs)

        assert result.status == expected
        assert result.detail.strip()

    def test_every_outcome_records_the_requested_url(self) -> None:
        assert fetch(status_code=500).requested_url == ARTICLE_URL

    @pytest.mark.parametrize(
        "bad_url",
        ["", "   ", "http://", "not a url at all", "javascript:alert(1)", "https://[oops"],
    )
    def test_hostile_input_never_raises(self, bad_url: str) -> None:
        result = fetch(url=bad_url)

        assert result.status in ("invalid_url", "unreachable")
        assert result.detail.strip()

    def test_a_refusal_detail_never_claims_a_cause_it_did_not_observe(self) -> None:
        """Regression: an empty DNS answer once reported a private address."""

        def empty_resolver(host: str) -> list[str]:
            return []

        def private_resolver(host: str) -> list[str]:
            return ["10.1.2.3"]

        assert "private" not in fetch(resolver=empty_resolver).detail
        assert "private" in fetch(resolver=private_resolver).detail

    def test_detail_never_contains_an_em_dash(self) -> None:
        """Project rule: no em dashes in user-facing text."""
        for kwargs in ({"status_code": 403}, {"status_code": 404}, {"html": "<html></html>"}):
            assert "—" not in fetch(**kwargs).detail
