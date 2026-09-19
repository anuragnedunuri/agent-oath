"""Valid model instances used across the contract tests.

Each builder returns a well-formed object with overridable fields, so a test can
change one thing and assert that the contract rejects it.
"""

from typing import Any

from app.models import (
    CredibilityResult,
    DomainAge,
    ProvenanceResult,
    PublicationDate,
    RegistryEntry,
    RetrievalResult,
    Signal,
    SyntheticResult,
    SyntheticSection,
    TrustSummary,
)


def make_registry_entry(**overrides: Any) -> RegistryEntry:
    """A high-tier entry: all three criteria booleans true."""
    fields: dict[str, Any] = {
        "domain": "reuters.com",
        "publisher_name": "Reuters",
        "trust_tier": "high",
        "source_type": "news_wire",
        "editorial_standards_public": True,
        "corrections_policy_public": True,
        "ownership_transparent": True,
        "country": "GB",
        "last_verified": "2026-08-01",
        "notes": "",
    }
    return RegistryEntry(**(fields | overrides))


def make_retrieval(**overrides: Any) -> RetrievalResult:
    """A successful retrieval with all three checks completed."""
    fields: dict[str, Any] = {
        "status": "ok",
        "detail": "The page was retrieved and its article text extracted.",
        "checks_completed": ["synthetic", "provenance", "registry"],
    }
    return RetrievalResult(**(fields | overrides))


def make_synthetic(**overrides: Any) -> SyntheticResult:
    """A completed detection run over two sections."""
    fields: dict[str, Any] = {
        "status": "ok",
        "probability_ai": 0.42,
        "model_name": "placeholder-detector",
        "model_version": "0.0.0",
        "sections": [
            SyntheticSection(
                section_index=0, excerpt_start="The first section", probability_ai=0.4
            ),
            SyntheticSection(
                section_index=1, excerpt_start="The second section", probability_ai=0.44
            ),
        ],
        "total_sections": 2,
    }
    return SyntheticResult(**(fields | overrides))


def make_provenance(**overrides: Any) -> ProvenanceResult:
    """Full provenance for a URL input."""
    fields: dict[str, Any] = {
        "domain": "reuters.com",
        "domain_age": DomainAge(status="ok", value_years=29.3, source="RDAP"),
        "https": True,
        "publisher_metadata_found": True,
        "publication_date": PublicationDate(status="ok", value="2026-05-01", method="htmldate"),
        "signals": [
            Signal(
                name="url_scheme",
                raw_value="https",
                interpretation="The connection is encrypted.",
            )
        ],
    }
    return ProvenanceResult(**(fields | overrides))


def make_credibility(**overrides: Any) -> CredibilityResult:
    """A rated source backed by a matched registry entry."""
    entry = make_registry_entry()
    fields: dict[str, Any] = {
        "status": "rated",
        "trust_tier": entry.trust_tier,
        "matched_entry": entry,
        "registry_version": "1.0.0",
    }
    return CredibilityResult(**(fields | overrides))


def make_trust_summary(**overrides: Any) -> TrustSummary:
    """A complete, valid Trust Summary for a URL input."""
    fields: dict[str, Any] = {
        "input_type": "url",
        "retrieval": make_retrieval(),
        "synthetic": make_synthetic(),
        "provenance": make_provenance(),
        "credibility": make_credibility(),
    }
    return TrustSummary(**(fields | overrides))
