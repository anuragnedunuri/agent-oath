"""Registry lookup tests (SPEC.md sections 4.3 and 5, INV-6).

The real registry file is exercised directly, so a broken entry in the
repository fails the suite as well as the standalone validator. Failure paths
use injected temporary files, never a mutated real registry.
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


class TestRegistryFile:
    def test_the_repository_registry_loads_and_validates(self) -> None:
        registry = load_registry()

        assert registry is not None
        assert registry.version == "1.0.0"
        assert registry.criteria_doc == "METHODOLOGY.md"
        assert len(registry.entries) >= 20

    def test_every_entry_tier_matches_its_booleans(self) -> None:
        """The model enforces this on load; this test makes the claim explicit."""
        registry = load_registry()
        assert registry is not None

        for entry in registry.entries:
            expected = RegistryEntry.derive_tier(
                entry.editorial_standards_public,
                entry.corrections_policy_public,
                entry.ownership_transparent,
            )
            assert entry.trust_tier == expected

    def test_domains_are_unique(self) -> None:
        registry = load_registry()
        assert registry is not None
        domains = [entry.domain for entry in registry.entries]

        assert len(domains) == len(set(domains))


class TestLookupHits:
    def test_known_domain_is_rated_with_its_entry(self) -> None:
        result = lookup("theguardian.com")

        assert result.status == "rated"
        assert result.trust_tier == "high"
        assert result.matched_entry is not None
        assert result.matched_entry.publisher_name == "The Guardian"
        assert result.registry_version == "1.0.0"

    def test_subdomain_resolves_to_the_registered_domain(self) -> None:
        """SPEC 4.3: news.bbc.co.uk is the bbc.co.uk entry."""
        result = lookup("news.bbc.co.uk")

        assert result.status == "rated"
        assert result.matched_entry is not None
        assert result.matched_entry.domain == "bbc.co.uk"

    def test_lookup_is_case_insensitive(self) -> None:
        assert lookup("TheGuardian.com").status == "rated"

    def test_medium_tier_entries_surface_their_failing_criterion(self) -> None:
        """The mechanical tier is visible: 2 of 3 criteria gives medium."""
        result = lookup("aljazeera.com")

        assert result.status == "rated"
        assert result.trust_tier == "medium"
        assert result.matched_entry is not None
        assert result.matched_entry.corrections_policy_public is False


class TestLookupMisses:
    def test_unknown_domain_is_unrated_with_an_explanation(self) -> None:
        result = lookup("example-news.com")

        assert result.status == "unrated"
        assert result.trust_tier is None
        assert result.matched_entry is None
        assert "not a negative rating" in result.unrated_explanation
        assert result.registry_version == "1.0.0"

    def test_no_domain_is_unrated_with_its_own_explanation(self) -> None:
        result = lookup(None)

        assert result.status == "unrated"
        assert "no domain" in result.unrated_explanation
        assert result.registry_version == "1.0.0"

    def test_subdomain_of_an_unknown_domain_is_still_unrated(self) -> None:
        assert lookup("live.example-news.com").status == "unrated"


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
        rigged.write_text(
            json.dumps(
                {
                    "version": "9.9.9",
                    "updated": "2026-09-28",
                    "criteria_doc": "METHODOLOGY.md",
                    "entries": [
                        {
                            "domain": "example-news.com",
                            "publisher_name": "Example News",
                            "trust_tier": "high",
                            "source_type": "digital_native",
                            "editorial_standards_public": False,
                            "corrections_policy_public": False,
                            "ownership_transparent": False,
                            "country": "US",
                            "last_verified": "2026-09-28",
                            "notes": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = lookup("example-news.com", registry_path=rigged)

        assert result.status == "unrated"
        assert result.registry_version == "unavailable"


class TestValidatorScript:
    def test_the_ci_validator_passes_on_the_repository_registry(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "validate_registry", REGISTRY_PATH.parent / "validate_registry.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert module.validate() == []

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
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "validate_registry", REGISTRY_PATH.parent / "validate_registry.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        data.update(mutation)
        mutated = tmp_path / "registry.json"
        mutated.write_text(json.dumps(data), encoding="utf-8")

        problems = module.validate(mutated)

        assert any(expected_fragment in problem for problem in problems)
