"""Provenance signals: domain age, https, publisher metadata, publication date.

SPEC.md section 4.1, under INV-4: every value this module reports is backed by
a Signal carrying the raw observation and a plain-language interpretation, so
nothing on screen is an unexplained number.

Honesty rules baked in:
  - Domain age comes from RDAP (https://rdap.org bootstrap), falling back to
    WHOIS. When both fail the age is reported as unavailable with the reason.
    It is never estimated (the DomainAge model enforces this).
  - Page-level checks (publisher metadata, publication date) run only when the
    page content was actually readable. When it was not, they are null and a
    signal says why, rather than being reported as absent from the page.

All network access has explicit timeouts and is injectable, so tests never
touch the real internet.
"""

import json
import time
from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime
from urllib.parse import urlsplit

import htmldate
import httpx
import tldextract
import whois
from bs4 import BeautifulSoup
from whois.parser import WhoisDomainNotFoundError, WhoisUnknownDateFormatError

from ..models import DomainAge, ProvenanceResult, PublicationDate, Signal

RDAP_BOOTSTRAP = "https://rdap.org/domain/"
"""Public RDAP bootstrap. Redirects to the registry that owns the TLD."""

RDAP_TIMEOUT_SECONDS = 5.0
WHOIS_TIMEOUT_SECONDS = 5

AGE_CACHE_TTL_SECONDS = 24 * 60 * 60
"""Domain ages move slowly; a day-old answer is still honest. Dict with TTL,
per SPEC.md section 4.1 (sqlite deferred until deployment needs persistence)."""

JSONLD_PUBLISHER_TYPES = frozenset(
    {"NewsArticle", "ReportageNewsArticle", "Article", "Organization", "NewsMediaOrganization"}
)
"""JSON-LD @type values that indicate publisher-grade structured data."""

WhoisLookup = Callable[[str], tuple[datetime, str] | None]
"""Returns (registration datetime, raw value as received) or None."""
Clock = Callable[[], datetime]
CacheEntry = tuple[float, DomainAge, list[Signal]]

_extract = tldextract.TLDExtract(suffix_list_urls=())
"""Uses the bundled public suffix snapshot only: no network fetch on first use."""

_default_age_cache: dict[str, CacheEntry] = {}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _signal(name: str, raw_value: object, interpretation: str) -> Signal:
    return Signal(name=name, raw_value=str(raw_value), interpretation=interpretation)


def registrable_domain(host: str) -> str:
    """Reduce a hostname to its registrable domain, e.g. news.sky.com to sky.com.

    An IP address or a suffix-less name comes back unchanged; the lookups that
    follow will then fail honestly rather than querying a made-up domain.
    """
    extracted = _extract(host)
    return extracted.top_domain_under_public_suffix or host


def _parse_rdap_registration(payload: object) -> str | None:
    """Pull the registration event date out of an RDAP response, if present."""
    if not isinstance(payload, dict):
        return None
    for event in payload.get("events", []):
        if isinstance(event, dict) and event.get("eventAction") == "registration":
            date = event.get("eventDate")
            if isinstance(date, str) and date:
                return date
    return None


def _parse_event_date(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _age_years(registered: datetime, now: datetime) -> float:
    return round((now - registered).days / 365.25, 1)


def _default_whois_lookup(domain: str) -> tuple[datetime, str] | None:
    """Registration date via the python-whois library, with an explicit timeout.

    The library returns creation_date as a datetime, a list of datetimes (some
    registries report several), or nothing. Normalized here to one aware UTC
    datetime, keeping the raw value for the signal (INV-4).
    """
    try:
        record = whois.whois(domain, quiet=True, timeout=WHOIS_TIMEOUT_SECONDS)
    except (WhoisDomainNotFoundError, WhoisUnknownDateFormatError, OSError, ValueError):
        return None

    created = record.get("creation_date")
    raw = str(created)
    if isinstance(created, list):
        created = min((d for d in created if isinstance(d, datetime)), default=None)
    if not isinstance(created, datetime):
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return created, raw


def domain_age(
    domain: str,
    *,
    transport: httpx.BaseTransport | None = None,
    whois_lookup: WhoisLookup | None = None,
    cache: MutableMapping[str, CacheEntry] | None = None,
    now: Clock = _utc_now,
) -> tuple[DomainAge, list[Signal]]:
    """Determine how long ago a domain was registered, or say why we cannot.

    RDAP first, WHOIS as fallback, dict cache with a 24 hour TTL. Independently
    callable and fully injectable for tests.

    Returns:
        The DomainAge plus the signals that justify it (INV-4).
    """
    cache = _default_age_cache if cache is None else cache
    cached = cache.get(domain)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1], cached[2]

    failures: list[str] = []
    result: tuple[DomainAge, list[Signal]] | None = None

    try:
        with httpx.Client(
            timeout=RDAP_TIMEOUT_SECONDS, follow_redirects=True, transport=transport
        ) as client:
            response = client.get(f"{RDAP_BOOTSTRAP}{domain}")
        if response.status_code == 200:
            raw_date = _parse_rdap_registration(response.json())
            registered = _parse_event_date(raw_date) if raw_date else None
            if registered is not None:
                years = _age_years(registered, now())
                result = (
                    DomainAge(status="ok", value_years=years, source="RDAP"),
                    [
                        _signal(
                            "rdap_registration_date",
                            raw_date,
                            f"The domain was registered {years} years ago, per RDAP.",
                        )
                    ],
                )
            else:
                failures.append("RDAP answered without a registration date")
        else:
            failures.append(f"RDAP returned HTTP {response.status_code}")
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        failures.append("RDAP could not be reached")

    if result is None:
        try:
            whois_result = (whois_lookup or _default_whois_lookup)(domain)
        except OSError:
            whois_result = None
        if whois_result is not None:
            registered, raw_date = whois_result
            years = _age_years(registered, now())
            result = (
                DomainAge(status="ok", value_years=years, source="WHOIS"),
                [
                    _signal(
                        "whois_creation_date",
                        raw_date,
                        f"The domain was registered {years} years ago, per WHOIS.",
                    )
                ],
            )
        else:
            failures.append("WHOIS gave no usable registration date")

    if result is None:
        detail = f"The registration age could not be determined: {'; '.join(failures)}."
        result = (
            DomainAge(status="unavailable", source="none", detail=detail),
            [_signal("domain_age_lookup", "; ".join(failures), detail)],
        )

    cache[domain] = (time.monotonic() + AGE_CACHE_TTL_SECONDS, result[0], result[1])
    return result


def _jsonld_publisher_types(soup: BeautifulSoup) -> list[str]:
    """Collect publisher-grade @type values from every JSON-LD block on the page."""
    found: list[str] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            for item in [node, *node.get("@graph", [])] if "@graph" in node else [node]:
                if not isinstance(item, dict):
                    continue
                types = item.get("@type", [])
                for type_name in [types] if isinstance(types, str) else types:
                    if type_name in JSONLD_PUBLISHER_TYPES and type_name not in found:
                        found.append(type_name)
    return found


def _meta_content(soup: BeautifulSoup, **attrs: str) -> str | None:
    tag = soup.find("meta", attrs=attrs)
    content = tag.get("content") if tag else None
    if isinstance(content, str) and content.strip():
        return content.strip()
    return None


def _page_level_checks(
    html: str,
) -> tuple[bool, PublicationDate, list[Signal]]:
    """Publisher metadata and publication date, from page content (SPEC 4.1)."""
    soup = BeautifulSoup(html, "html.parser")
    signals: list[Signal] = []

    jsonld_types = _jsonld_publisher_types(soup)
    signals.append(
        _signal(
            "jsonld_type",
            ", ".join(jsonld_types) if jsonld_types else "Not present",
            "The page identifies itself with publisher-grade structured data."
            if jsonld_types
            else "No publisher-grade structured data was found on the page.",
        )
    )

    site_name = _meta_content(soup, property="og:site_name")
    signals.append(
        _signal(
            "og:site_name",
            site_name or "Not present",
            "The page names its publisher in metadata."
            if site_name
            else "The page does not name its publisher in metadata.",
        )
    )

    author = _meta_content(soup, name="author")
    signals.append(
        _signal(
            "meta_author",
            author or "Not present",
            "The page names an author in metadata."
            if author
            else "No author was named in the page metadata.",
        )
    )

    metadata_found = bool(jsonld_types or site_name or author)

    try:
        found_date = htmldate.find_date(html, original_date=True)
    except ValueError:
        found_date = None
    if found_date:
        publication_date = PublicationDate(status="ok", value=found_date, method="htmldate")
        signals.append(
            _signal(
                "htmldate_result",
                found_date,
                "The publication date extracted from the page by the htmldate library.",
            )
        )
    else:
        publication_date = PublicationDate(status="not_found", method="htmldate")
        signals.append(
            _signal(
                "htmldate_result",
                "Not found",
                "The htmldate library could not find a publication date on the page.",
            )
        )

    return metadata_found, publication_date, signals


def check_provenance(
    *,
    url: str | None,
    host: str | None,
    html: str | None,
    transport: httpx.BaseTransport | None = None,
    whois_lookup: WhoisLookup | None = None,
    age_cache: MutableMapping[str, CacheEntry] | None = None,
    now: Clock = _utc_now,
) -> ProvenanceResult:
    """Run every provenance check the available inputs allow (INV-2, INV-4).

    Args:
        url: The final URL the content came from, when there is one.
        host: Hostname from the fetcher, or None for pasted text.
        html: Raw page markup, or None when the page was not readable.
        transport: Injected httpx transport for RDAP. Tests use MockTransport.
        whois_lookup: Injected WHOIS lookup. Tests use a canned function.
        age_cache: Injected domain-age cache.
        now: Injected clock.

    Returns:
        A ProvenanceResult whose every field is traceable to a signal.
    """
    if host is None:
        return ProvenanceResult(
            signals=[
                _signal(
                    "input_type",
                    "text",
                    "Provenance checks need a URL, so none of them could run on pasted text.",
                )
            ]
        )

    domain = registrable_domain(host)
    signals: list[Signal] = [
        _signal(
            "registrable_domain",
            domain,
            "The registered domain that provenance and registry checks run against.",
        )
    ]

    https: bool | None = None
    if url:
        scheme = urlsplit(url).scheme.lower()
        https = scheme == "https"
        signals.append(
            _signal(
                "url_scheme",
                scheme,
                "The connection to this page is encrypted."
                if https
                else "The connection to this page is not encrypted.",
            )
        )

    age, age_signals = domain_age(
        domain, transport=transport, whois_lookup=whois_lookup, cache=age_cache, now=now
    )
    signals.extend(age_signals)

    if html is None:
        metadata_found: bool | None = None
        publication_date: PublicationDate | None = None
        signals.append(
            _signal(
                "page_content",
                "not available",
                "The page content was not readable, so publisher metadata and the "
                "publication date could not be checked.",
            )
        )
    else:
        metadata_found, publication_date, page_signals = _page_level_checks(html)
        signals.extend(page_signals)

    return ProvenanceResult(
        domain=domain,
        domain_age=age,
        https=https,
        publisher_metadata_found=metadata_found,
        publication_date=publication_date,
        signals=signals,
    )
