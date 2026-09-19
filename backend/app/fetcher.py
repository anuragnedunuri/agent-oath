"""URL retrieval and text extraction with an honest failure taxonomy (INV-2).

Every path through this module returns a FetchResult. No network failure, no
malformed page, and no hostile response escapes as an exception: each one maps
to a retrieval status and a plain-language detail the Trust Summary can show.

Taxonomy (SPEC.md section 3):

    ok                  content retrieved and enough text extracted
    paywall_or_blocked  HTTP 401/402/403, or a paywall marker in the page
    timeout             no response within TIMEOUT_SECONDS
    js_required         HTTP 200 but under MIN_EXTRACTABLE_CHARS of text
    not_html            content-type is not HTML
    invalid_url         unparseable, non-http, or a private network address
    unreachable         DNS failure, refused connection, or TLS failure
    http_error          any other error status, for example 404 or 500

All eight statuses are part of the SPEC.md section 3 taxonomy, so a domain
that does not resolve and a page that returns 404 both have an honest status,
as INV-2 requires: every input still yields a Trust Summary.

Redirects are followed by hand rather than by httpx, so that every hop is
re-checked against the private-address guard. Following them automatically
would let a public URL redirect the server onto an internal address.
"""

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from .models import RetrievalStatus, Signal

TIMEOUT_SECONDS = 10.0
"""Total budget for a retrieval, per SPEC.md section 3."""

MIN_EXTRACTABLE_CHARS = 500
"""Below this much text on a 200 OK, the page is treated as js_required."""

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
"""Hard cap on a response body, so a huge page cannot exhaust backend memory."""

MAX_REDIRECTS = 5

USER_AGENT = "Mozilla/5.0 (compatible; AgentOathBot/0.1; trust verification prototype)"
"""Honest about being a bot. Sites that refuse bots become paywall_or_blocked,
which is a result we report plainly rather than disguise."""

HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml", "application/xml", "text/xml"})

BLOCKED_TAGS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "form",
    "button",
    "nav",
    "aside",
    "footer",
)
"""Page chrome and non-prose removed before text extraction."""

PAYWALL_STRUCTURED_RE = re.compile(r"isAccessibleForFree[\"']?\s*[:=]\s*[\"']?false", re.IGNORECASE)
"""The schema.org paywall flag. The most precise signal available."""

PAYWALL_TEXT_MARKERS = (
    "subscribe to continue reading",
    "subscribe to keep reading",
    "to continue reading, subscribe",
    "this article is for subscribers",
    "this content is for subscribers",
    "you have reached your article limit",
    "create a free account to keep reading",
    "sign in to read the full story",
    "register to continue reading",
)
"""Deliberately specific phrases. A matched marker is always reported as a
signal (INV-4), so a false positive stays visible to the user."""

HOSTNAME_RE = re.compile(r"^[a-z0-9\-._:]+$")
"""Characters a hostname may contain, including ':' for a bare IPv6 address.
Anything else (a space, a parenthesis) is not an address we will try to open."""

HostResolver = Callable[[str], Iterable[str]]


class FetchResult(BaseModel):
    """Outcome of one retrieval attempt.

    Internal to the backend, not part of the public API contract. app/summary.py
    maps this onto the RetrievalResult in models.py (task 1.3), and the
    provenance module reads `html` and `final_url` from it (task 1.2).
    """

    status: RetrievalStatus
    detail: str = Field(min_length=1, description="Honest explanation. Never blank (INV-2).")
    requested_url: str
    final_url: str | None = Field(default=None, description="URL after redirects.")
    host: str | None = Field(default=None, description="Hostname, lowercased, no 'www.' prefix.")
    http_status: int | None = None
    content_type: str | None = None
    html: str | None = Field(default=None, description="Raw markup, when a body was read.")
    text: str | None = Field(default=None, description="Extracted prose, when any was found.")
    signals: list[Signal] = Field(default_factory=list, description="Raw evidence (INV-4).")

    @property
    def has_text(self) -> bool:
        """Whether enough text was extracted for the synthetic check to run."""
        return len(self.text or "") >= MIN_EXTRACTABLE_CHARS


class _RefusedTarget(Exception):  # noqa: N818
    """Internal control flow: a URL this service will not request.

    Carries the FetchResult to return. Never escapes fetch_url.
    """

    def __init__(self, result: FetchResult) -> None:
        super().__init__(result.detail)
        self.result = result


def _signal(name: str, raw_value: object, interpretation: str) -> Signal:
    return Signal(name=name, raw_value=str(raw_value), interpretation=interpretation)


def _collapse_whitespace(text: str) -> str:
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def extract_text(html: str) -> str:
    """Extract readable prose from HTML markup.

    Prefers an <article> or <main> container when the page provides one, since
    that excludes navigation and related-story rails without guesswork.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(list(BLOCKED_TAGS)):
        tag.decompose()

    container = soup.find("article") or soup.find("main") or soup.body or soup
    return _collapse_whitespace(container.get_text(separator="\n"))


def detect_paywall_marker(html: str, text: str) -> str | None:
    """Return the name of the first paywall marker found, or None."""
    if PAYWALL_STRUCTURED_RE.search(html):
        return "isAccessibleForFree=false"

    lowered = text.lower()
    for marker in PAYWALL_TEXT_MARKERS:
        if marker in lowered:
            return marker
    return None


def _resolve_host(host: str) -> Iterable[str]:
    """Resolve a hostname to its IP addresses. Injected so tests never use DNS."""
    return {info[4][0] for info in socket.getaddrinfo(host, None)}


NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")
"""RFC 6052 well-known prefix. Python reports it as reserved, but it carries an
ordinary public IPv4 address and resolvers on IPv6-only networks return it for
perfectly normal sites. The embedded IPv4 is what must actually be judged."""


def _unwrap_embedded_ipv4(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Return the IPv4 address carried inside a NAT64, mapped, or 6to4 address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip in NAT64_PREFIX:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if ip.sixtofour is not None:
            return ip.sixtofour
    return ip


def _is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False

    ip = _unwrap_embedded_ipv4(ip)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _failure(
    status: RetrievalStatus,
    detail: str,
    requested_url: str,
    *,
    host: str | None = None,
    signals: list[Signal] | None = None,
) -> FetchResult:
    return FetchResult(
        status=status,
        detail=detail,
        requested_url=requested_url,
        host=host,
        signals=list(signals or []),
    )


def _validate_target(
    target: str,
    *,
    requested_url: str,
    signals: list[Signal],
    resolve_host: HostResolver,
    last_host: str | None,
) -> str:
    """Check one URL before it is requested. Returns the display host.

    Raises:
        _RefusedTarget: when the URL is malformed, non-http, or resolves to a
            private, loopback, or link-local address. Applied to every redirect
            hop as well as the original URL, so a redirect cannot smuggle the
            server onto an internal network.
    """
    try:
        parsed = urlsplit(target)
        hostname = parsed.hostname
        parsed.port  # noqa: B018  raises ValueError on a malformed port
    except ValueError:
        raise _RefusedTarget(
            _failure(
                "invalid_url",
                "That web address could not be read as a URL.",
                requested_url,
                host=last_host,
                signals=signals,
            )
        ) from None

    if parsed.scheme not in ("http", "https"):
        raise _RefusedTarget(
            _failure(
                "invalid_url",
                f"Only http and https addresses can be checked, and this one uses "
                f"'{parsed.scheme}'.",
                requested_url,
                host=last_host,
                signals=[*signals, _signal("url_scheme", parsed.scheme, "Unsupported scheme.")],
            )
        )

    if not hostname:
        raise _RefusedTarget(
            _failure(
                "invalid_url",
                "That web address has no site name in it.",
                requested_url,
                host=last_host,
                signals=signals,
            )
        )

    host = hostname.lower()
    display_host = host.removeprefix("www.")

    if not HOSTNAME_RE.match(host):
        raise _RefusedTarget(
            _failure(
                "invalid_url",
                "That web address does not contain a usable site name.",
                requested_url,
                host=last_host,
                signals=[*signals, _signal("hostname", host, "Not a valid hostname.")],
            )
        )

    try:
        addresses = list(resolve_host(host))
    except OSError:
        raise _RefusedTarget(
            _failure(
                "unreachable",
                "That site name could not be looked up, so it may not exist.",
                requested_url,
                host=display_host,
                signals=[
                    *signals,
                    _signal("dns_lookup", "failed", "The hostname did not resolve."),
                ],
            )
        ) from None

    if not addresses:
        # Not a private address, just no answer. Saying otherwise would be a lie.
        raise _RefusedTarget(
            _failure(
                "unreachable",
                "That site name did not resolve to any address, so it could not be reached.",
                requested_url,
                host=display_host,
                signals=[
                    *signals,
                    _signal("dns_lookup", "no addresses", "The hostname resolved to nothing."),
                ],
            )
        )

    if not all(_is_public_address(address) for address in addresses):
        raise _RefusedTarget(
            _failure(
                "invalid_url",
                "That address points to a private or internal network location, which this "
                "service will not fetch.",
                requested_url,
                host=display_host,
                signals=[
                    *signals,
                    _signal(
                        "resolved_address",
                        ", ".join(addresses) or "none",
                        "Not a public internet address.",
                    ),
                ],
            )
        )

    return display_host


def fetch_url(
    url: str,
    *,
    timeout_seconds: float = TIMEOUT_SECONDS,
    transport: httpx.BaseTransport | None = None,
    resolve_host: HostResolver = _resolve_host,
) -> FetchResult:
    """Retrieve a URL and extract its text, reporting failure honestly (INV-2).

    Args:
        url: The address to fetch. A missing scheme is treated as https.
        timeout_seconds: Total retrieval budget.
        transport: Injected httpx transport. Tests pass an httpx.MockTransport.
        resolve_host: Injected DNS resolver, used for the private-address guard.

    Returns:
        A FetchResult. This function never raises on network or HTTP errors.
    """
    requested_url = url.strip()
    if not requested_url:
        return _failure("invalid_url", "No web address was supplied.", requested_url)

    target = requested_url if "://" in requested_url else f"https://{requested_url}"
    signals: list[Signal] = []
    if target != requested_url:
        signals.append(
            _signal("scheme_assumed", "https", "No scheme was given, so https was assumed.")
        )

    host: str | None = None
    client = httpx.Client(
        timeout=timeout_seconds,
        follow_redirects=False,
        transport=transport,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en",
        },
    )

    try:
        with client:
            for _ in range(MAX_REDIRECTS + 1):
                host = _validate_target(
                    target,
                    requested_url=requested_url,
                    signals=signals,
                    resolve_host=resolve_host,
                    last_host=host,
                )
                with client.stream("GET", urlunsplit(urlsplit(target))) as response:
                    # has_redirect_location, not is_redirect: the latter is true
                    # for a 3xx with no Location header, which has nowhere to go.
                    if response.has_redirect_location:
                        location = response.headers["location"]
                        signals.append(
                            _signal(
                                "redirect",
                                f"{response.status_code} to {location}",
                                "The site redirected the request.",
                            )
                        )
                        target = urljoin(target, location)
                        continue
                    return _read_response(
                        response,
                        requested_url=requested_url,
                        final_url=target,
                        host=host,
                        signals=signals,
                    )

        return _failure(
            "unreachable",
            f"The site redirected more than {MAX_REDIRECTS} times without settling on a page.",
            requested_url,
            host=host,
            signals=[*signals, _signal("redirects", MAX_REDIRECTS, "Redirect limit exceeded.")],
        )
    except _RefusedTarget as refused:
        return refused.result
    except httpx.TimeoutException:
        return _failure(
            "timeout",
            f"The site did not respond within {timeout_seconds:.0f} seconds.",
            requested_url,
            host=host,
            signals=[
                *signals,
                _signal("timeout_seconds", timeout_seconds, "Retrieval budget exhausted."),
            ],
        )
    except httpx.UnsupportedProtocol:
        return _failure(
            "invalid_url",
            "That web address uses a protocol this service cannot open.",
            requested_url,
            host=host,
            signals=signals,
        )
    except httpx.InvalidURL:
        # Not an HTTPError subclass, so it needs its own clause or it would
        # escape as a raw exception and break INV-2.
        return _failure(
            "invalid_url",
            "That web address could not be read as a URL.",
            requested_url,
            host=host,
            signals=signals,
        )
    except httpx.HTTPError as exc:
        return _failure(
            "unreachable",
            "The connection to that site failed, so its content could not be read.",
            requested_url,
            host=host,
            signals=[
                *signals,
                _signal("transport_error", type(exc).__name__, "The connection did not complete."),
            ],
        )


def _read_response(
    response: httpx.Response,
    *,
    requested_url: str,
    final_url: str,
    host: str,
    signals: list[Signal],
) -> FetchResult:
    """Classify a response that arrived, and extract its text when it has any."""
    content_type = response.headers.get("content-type")
    media_type = (content_type or "").split(";")[0].strip().lower()

    signals = [*signals, _signal("http_status", response.status_code, _status_meaning(response))]
    if final_url != requested_url:
        signals.append(_signal("final_url", final_url, "The address that finally answered."))

    common: dict[str, object] = {
        "requested_url": requested_url,
        "final_url": final_url,
        "host": host,
        "http_status": response.status_code,
        "content_type": content_type,
    }

    if response.status_code in (401, 402, 403):
        return FetchResult(
            status="paywall_or_blocked",
            detail=(
                f"The site returned HTTP {response.status_code}, so the page content could not "
                f"be read. This usually means a paywall, a login requirement, or bot protection."
            ),
            signals=signals,
            **common,
        )

    if response.status_code >= 400:
        return FetchResult(
            status="http_error",
            detail=(
                f"The site returned HTTP {response.status_code}, so there was no page content "
                f"to read."
            ),
            signals=signals,
            **common,
        )

    if 300 <= response.status_code < 400:
        return FetchResult(
            status="http_error",
            detail=(
                f"The site answered with HTTP {response.status_code} but did not say where to "
                f"go next, so no page could be read."
            ),
            signals=signals,
            **common,
        )

    if content_type is None:
        signals.append(
            _signal("content_type", "not sent", "No content type was sent, so HTML was assumed.")
        )
    elif media_type not in HTML_MEDIA_TYPES:
        return FetchResult(
            status="not_html",
            detail=(
                f"That address returned {media_type or 'an unnamed content type'} rather than a "
                f"web page, so there is no article text to analyze."
            ),
            signals=[*signals, _signal("content_type", media_type, "Not a web page.")],
            **common,
        )

    body = bytearray()
    truncated = False
    for chunk in response.iter_bytes():
        remaining = MAX_RESPONSE_BYTES - len(body)
        if len(chunk) >= remaining:
            body.extend(chunk[:remaining])
            truncated = True
            break
        body.extend(chunk)

    html = bytes(body).decode(response.charset_encoding or "utf-8", errors="replace")
    text = extract_text(html)

    if truncated:
        signals.append(
            _signal(
                "response_truncated",
                f"{MAX_RESPONSE_BYTES} bytes",
                "The page exceeded the size limit, so only its first part was read.",
            )
        )

    signals.append(
        _signal("extracted_characters", len(text), "Amount of readable text found on the page.")
    )

    marker = detect_paywall_marker(html, text)
    if marker is not None:
        return FetchResult(
            status="paywall_or_blocked",
            detail=(
                "The page carries a paywall marker, so its full article text is probably not "
                "available to this service."
            ),
            html=html,
            text=text or None,
            signals=[
                *signals,
                _signal("paywall_marker", marker, "Matched a known paywall marker."),
            ],
            **common,
        )

    if len(text) < MIN_EXTRACTABLE_CHARS:
        return FetchResult(
            status="js_required",
            detail=(
                f"The page returned successfully but held only {len(text)} characters of "
                f"readable text. It most likely builds its content in the browser, which this "
                f"service cannot run."
            ),
            html=html,
            text=text or None,
            signals=signals,
            **common,
        )

    return FetchResult(
        status="ok",
        detail=f"The page was retrieved and {len(text)} characters of text were extracted.",
        html=html,
        text=text,
        signals=signals,
        **common,
    )


def _status_meaning(response: httpx.Response) -> str:
    if response.status_code < 300:
        return "The site returned the page successfully."
    if response.status_code in (401, 402, 403):
        return "The site refused the request."
    if response.status_code >= 400:
        return "The site reported an error instead of a page."
    return "The site redirected the request."
