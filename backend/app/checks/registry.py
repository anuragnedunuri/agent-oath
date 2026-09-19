"""Registry lookup on the registrable domain (SPEC.md section 4.3, INV-6).

Task 3.1 builds the real registry file, its validator, and the seeded entries.
Until then this is an honest stub: the registry holds zero entries, so every
lookup returns unrated with an explanation. Unknown means unrated, never a
synthesized score, and that stays true when the real registry lands: a miss
takes exactly this same path.
"""

from ..models import CredibilityResult

REGISTRY_VERSION = "0.0.0"
"""Pre-seed version. Task 3.1 replaces this with the version from registry.json."""

UNRATED_DOMAIN_EXPLANATION = (
    "This domain is not in the source registry, so it has no credibility rating. "
    "An absent rating is not a negative rating. It means this source has not been "
    "assessed against the published criteria."
)

UNRATED_NO_DOMAIN_EXPLANATION = (
    "This input has no domain, so there is nothing to look up in the source registry."
)


def lookup(domain: str | None) -> CredibilityResult:
    """Look a registrable domain up in the source registry.

    Args:
        domain: The registrable domain, or None when the input has no domain
            (pasted text, or a URL that could not be parsed).

    Returns:
        A CredibilityResult. With the registry unseeded this is always unrated;
        the INV-6 contract on CredibilityResult holds for the real registry too.
    """
    if domain is None:
        return CredibilityResult(
            status="unrated",
            unrated_explanation=UNRATED_NO_DOMAIN_EXPLANATION,
            registry_version=REGISTRY_VERSION,
        )
    return CredibilityResult(
        status="unrated",
        unrated_explanation=UNRATED_DOMAIN_EXPLANATION,
        registry_version=REGISTRY_VERSION,
    )
