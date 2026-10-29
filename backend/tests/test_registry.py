"""Registry lookup tests (SPEC.md sections 4.3 and 5, INV-6).

The repository registry currently holds zero verified entries (the owner's
human-verified-only boundary, applied 2026-10-21), so rated-path behavior is
tested against injected fixture registries, and the real file is tested for
what it truthfully is: valid, versioned, and empty until verification lands.
"""

import json
from pathlib import Path

import pytest

from app.checks.registry import (
    REGISTRY_PATH,
    load_registry,
    lookup,
)
from app.models import RegistryEntry

FIXTURE_ENTRIES = [
    {
        "domain": "example-news.co.uk",
        "publisher_name": "Example News",
        "trust_tier": "high",
        "source_type": "newspaper",
        "editorial_standards_public": True,
        "editorial_standards_url": "https://example-news.co.uk/editorial-code",
        "corrections_policy_public": True,
        "corrections_policy_url": "https://example-news.co.uk/corrections",
        "ownership_transparent": True,
        "ownership_url": "https://example-news.co.uk/about",
        "country": "GB",
        "last_verified": "2026-10-20",
        "notes": "",
    },
    {
        "domain": "example-wire.com",
        "publisher_name": "Example Wire",
        "trust_tier": "medium",
        "source_type": "news_wire",
        "editorial_standards_public": True,
        "editorial_standards_url": "https://example-wire.com/standards",
        "corrections_policy_public": False,
        "corrections_policy_url": None,
        "ownership_transparent": True,
        "ownership_url": "https://example-wire.com/about",
        "country": "US",
        "last_verified": "2026-10-20",
        "notes": "",
    },
]


@pytest.fixture
def fixture_registry(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "version": "9.0.0",
                "updated": "2026-10-20",
                "criteria_doc": "METHODOLOGY.md",
                "entries": FIXTURE_ENTRIES,
            }
        ),
        encoding="utf-8",
    )
    return path


class TestRepositoryRegistry:
    def test_the_repository_registry_loads_and_validates(self) -> None:
        registry = load_registry()

        assert registry is not None
        assert registry.version == "1.2.0"
        assert registry.criteria_doc == "METHODOLOGY.md"

    def test_every_entry_honours_evidence_and_tier_rules(self) -> None:
        """Holds for every entry present; vacuously true while the registry is empty."""
        registry = load_registry()
        assert registry is not None

        for entry in registry.entries:
            if entry.editorial_standards_public:
                assert entry.editorial_standards_url, entry.domain
            if entry.corrections_policy_public:
                assert entry.corrections_policy_url, entry.domain
            if entry.ownership_transparent:
                assert entry.ownership_url, entry.domain
            expected = RegistryEntry.derive_tier(
                entry.editorial_standards_public,
                entry.corrections_policy_public,
                entry.ownership_transparent,
            )
            assert entry.trust_tier == expected

    def test_unverified_boundary_every_lookup_is_currently_unrated(self) -> None:
        """Owner boundary 2026-10-21: no human-verified entries means no ratings."""
        registry = load_registry()
        assert registry is not None

        if not registry.entries:
            assert lookup("theguardian.com").status == "unrated"


class TestLookupHits:
    def test_known_domain_is_rated_with_its_entry(self, fixture_registry: Path) -> None:
        result = lookup("example-news.co.uk", registry_path=fixture_registry)

        assert result.status == "rated"
        assert result.trust_tier == "high"
        assert result.matched_entry is not None
        assert result.matched_entry.publisher_name == "Example News"
        assert result.registry_version == "9.0.0"

    def test_subdomain_resolves_to_the_registered_domain(self, fixture_registry: Path) -> None:
        """SPEC 4.3: news.example-news.co.uk is the example-news.co.uk entry."""
        result = lookup("news.example-news.co.uk", registry_path=fixture_registry)

        assert result.status == "rated"
        assert result.matched_entry is not None
        assert result.matched_entry.domain == "example-news.co.uk"

    def test_lookup_is_case_insensitive(self, fixture_registry: Path) -> None:
        assert lookup("Example-News.CO.UK", registry_path=fixture_registry).status == "rated"

    def test_medium_tier_entries_surface_their_failing_criterion(
        self, fixture_registry: Path
    ) -> None:
        result = lookup("example-wire.com", registry_path=fixture_registry)

        assert result.status == "rated"
        assert result.trust_tier == "medium"
        assert result.matched_entry is not None
        assert result.matched_entry.corrections_policy_public is False
        assert result.matched_entry.corrections_policy_url is None


class TestLookupMisses:
    def test_unknown_domain_is_unrated_with_an_explanation(self, fixture_registry: Path) -> None:
        result = lookup("unknown-site.org", registry_path=fixture_registry)

        assert result.status == "unrated"
        assert result.trust_tier is None
        assert result.matched_entry is None
        assert "not a negative rating" in result.unrated_explanation
        assert result.registry_version == "9.0.0"

    def test_no_domain_is_unrated_with_its_own_explanation(self) -> None:
        result = lookup(None)

        assert result.status == "unrated"
        assert "no domain" in result.unrated_explanation
        assert result.registry_version == "1.2.0"


class TestRegistryUnavailable:
    """INV-2: a broken registry is a service problem, stated as such."""

    def test_missing_file_yields_an_honest_unavailable_result(self, tmp_path: Path) -> None:
        result = lookup("theguardian.com", registry_path=tmp_path / "missing.json")

        assert result.status == "unrated"
        assert "service problem" in result.unrated_explanation
        assert result.registry_version == "unavailable"

    def test_malformed_json_yields_the_same_honest_result(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")

        result = lookup("theguardian.com", registry_path=broken)

        assert result.status == "unrated"
        assert result.registry_version == "unavailable"

    def test_an_entry_with_dishonest_tier_math_poisons_nothing(self, tmp_path: Path) -> None:
        """A tier contradicting its booleans must not be served (INV-6)."""
        rigged = tmp_path / "rigged.json"
        entry = dict(FIXTURE_ENTRIES[0])
        entry["editorial_standards_public"] = False
        entry["editorial_standards_url"] = None  # booleans now derive medium, tier says high
        rigged.write_text(
            json.dumps(
                {
                    "version": "9.9.9",
                    "updated": "2026-10-20",
                    "criteria_doc": "METHODOLOGY.md",
                    "entries": [entry],
                }
            ),
            encoding="utf-8",
        )

        result = lookup("example-news.co.uk", registry_path=rigged)

        assert result.status == "unrated"
        assert result.registry_version == "unavailable"


def _load_validator():  # noqa: ANN202
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "validate_registry", REGISTRY_PATH.parent / "validate_registry.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestValidatorScript:
    def test_the_ci_validator_passes_on_the_repository_registry(self) -> None:
        assert _load_validator().validate() == []

    def test_empty_registry_is_a_warning_not_a_failure(self) -> None:
        module = _load_validator()
        registry = load_registry()
        assert registry is not None

        if not registry.entries:
            assert any("zero verified entries" in w for w in module.warnings())

    def test_the_validator_rejects_a_true_without_evidence(self, tmp_path: Path) -> None:
        module = _load_validator()
        entry = dict(FIXTURE_ENTRIES[0])
        entry["editorial_standards_url"] = None
        mutated = tmp_path / "registry.json"
        mutated.write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "updated": "2026-10-20",
                    "criteria_doc": "METHODOLOGY.md",
                    "entries": [entry],
                }
            ),
            encoding="utf-8",
        )

        problems = module.validate(mutated)

        assert any("carries no evidence URL" in problem for problem in problems)

    @pytest.mark.parametrize(
        ("mutation", "expected_fragment"),
        [
            ({"version": "one"}, "MAJOR.MINOR.PATCH"),
            ({"updated": "2099-01-01"}, "in the future"),
        ],
    )
    def test_the_validator_rejects_bad_top_level_fields(
        self, tmp_path: Path, mutation: dict, expected_fragment: str
    ) -> None:
        module = _load_validator()
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        data.update(mutation)
        mutated = tmp_path / "registry.json"
        mutated.write_text(json.dumps(data), encoding="utf-8")

        problems = module.validate(mutated)

        assert any(expected_fragment in problem for problem in problems)
