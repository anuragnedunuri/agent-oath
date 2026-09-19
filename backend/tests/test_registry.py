"""Registry stub tests (INV-6). The real registry arrives with task 3.1."""

from app.checks.registry import REGISTRY_VERSION, lookup


def test_unknown_domain_is_unrated_with_an_explanation() -> None:
    result = lookup("example-news.com")

    assert result.status == "unrated"
    assert result.trust_tier is None
    assert result.matched_entry is None
    assert "not a negative rating" in result.unrated_explanation


def test_no_domain_is_unrated_with_its_own_explanation() -> None:
    result = lookup(None)

    assert result.status == "unrated"
    assert "no domain" in result.unrated_explanation


def test_every_result_names_the_registry_version() -> None:
    assert lookup("example-news.com").registry_version == REGISTRY_VERSION
    assert lookup(None).registry_version == REGISTRY_VERSION
