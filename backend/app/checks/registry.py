"""Registry lookup on the registrable domain (SPEC.md section 4.3, INV-6).

The registry is a versioned JSON file at registry/registry.json, validated in
continuous integration by registry/validate_registry.py against the criteria
published in registry/METHODOLOGY.md. Lookups are exact matches on the
registrable domain, so news.bbc.co.uk resolves to the bbc.co.uk entry.

Honesty rules carried here:

  INV-6  a miss returns unrated with an explanation. Nothing is estimated.
  INV-2  a registry file that is missing or malformed produces an honest
         "registry unavailable" result, never an exception. The RegistryEntry
         model refuses any entry whose tier contradicts its criteria booleans,
         so a corrupted rating cannot be served at all.
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ValidationError

from ..models import CredibilityResult, RegistryEntry
from .provenance import registrable_domain

REGISTRY_PATH = Path(__file__).resolve().parents[3] / "registry" / "registry.json"

UNRATED_DOMAIN_EXPLANATION = (
    "This domain is not in the source registry, so it has no credibility rating. "
    "An absent rating is not a negative rating. It means this source has not been "
    "assessed against the published criteria."
)

UNRATED_NO_DOMAIN_EXPLANATION = (
    "This input has no domain, so there is nothing to look up in the source registry."
)

UNRATED_REGISTRY_UNAVAILABLE_EXPLANATION = (
    "The source registry could not be loaded, so no source can be rated right now. "
    "This is a service problem, not a statement about this source."
)


class RegistryFile(BaseModel):
    """The versioned registry document (SPEC.md section 5)."""

    version: str
    updated: str
    criteria_doc: str
    entries: list[RegistryEntry]

    def find(self, domain: str) -> RegistryEntry | None:
        """Exact match on a registrable domain."""
        return next((entry for entry in self.entries if entry.domain == domain), None)


@lru_cache(maxsize=4)
def _load(path_text: str) -> RegistryFile | None:
    """Load and validate the registry once per process. None means unusable."""
    try:
        raw = json.loads(Path(path_text).read_text(encoding="utf-8"))
        return RegistryFile.model_validate(raw)
    except (OSError, ValueError, ValidationError):
        return None


def load_registry(path: Path = REGISTRY_PATH) -> RegistryFile | None:
    """The parsed registry, or None when the file is missing or invalid."""
    return _load(str(path))


def lookup(domain: str | None, *, registry_path: Path = REGISTRY_PATH) -> CredibilityResult:
    """Look a domain up in the source registry.

    Args:
        domain: A hostname or registrable domain, or None when the input has
            no domain (pasted text, or a URL that could not be parsed).
        registry_path: Injected registry location. Tests point this at
            temporary files; production uses the repository registry.

    Returns:
        A CredibilityResult that is traceable to the registry version it came
        from. Never raises (INV-2), never estimates (INV-6).
    """
    registry = load_registry(registry_path)

    if domain is None:
        return CredibilityResult(
            status="unrated",
            unrated_explanation=UNRATED_NO_DOMAIN_EXPLANATION,
            registry_version=registry.version if registry else "unavailable",
        )

    if registry is None:
        return CredibilityResult(
            status="unrated",
            unrated_explanation=UNRATED_REGISTRY_UNAVAILABLE_EXPLANATION,
            registry_version="unavailable",
        )

    entry = registry.find(registrable_domain(domain.lower()))
    if entry is None:
        return CredibilityResult(
            status="unrated",
            unrated_explanation=UNRATED_DOMAIN_EXPLANATION,
            registry_version=registry.version,
        )

    return CredibilityResult(
        status="rated",
        trust_tier=entry.trust_tier,
        matched_entry=entry,
        registry_version=registry.version,
    )
