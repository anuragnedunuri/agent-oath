"""Contract tests.

These assert that the charter invariants are enforced by the models, so a
Trust Summary that violates one cannot be constructed at all.
"""

import pytest
from pydantic import ValidationError

from app.models import (
    AI_DETECTION_DISCLAIMER,
    AnalyzeRequest,
    CredibilityResult,
    DomainAge,
    ProvenanceResult,
    PublicationDate,
    RegistryEntry,
    RetrievalResult,
    SyntheticResult,
    TrustSummary,
)

from .fixtures import (
    make_credibility,
    make_provenance,
    make_registry_entry,
    make_synthetic,
    make_trust_summary,
)


class TestAnalyzeRequest:
    def test_url_only_is_valid(self) -> None:
        assert AnalyzeRequest(url="https://example.org").text is None

    def test_text_only_is_valid(self) -> None:
        assert AnalyzeRequest(text="some pasted text").url is None

    def test_both_inputs_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            AnalyzeRequest(url="https://example.org", text="some text")

    def test_neither_input_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            AnalyzeRequest()

    def test_malformed_url_is_accepted_by_the_model(self) -> None:
        """INV-2: a bad URL must become a Trust Summary with status invalid_url.

        If this field were a pydantic HttpUrl, FastAPI would return a 422 and the
        user would get an unexplained error instead of an honest summary.
        """
        assert AnalyzeRequest(url="not a url at all").url == "not a url at all"


class TestRetrievalResult:
    def test_detail_is_required_even_on_success(self) -> None:
        with pytest.raises(ValidationError):
            RetrievalResult(status="ok", detail="", checks_completed=["synthetic"])

    def test_failed_retrieval_can_still_report_completed_checks(self) -> None:
        """INV-2: a blocked page still runs domain-level provenance and registry."""
        result = RetrievalResult(
            status="paywall_or_blocked",
            detail="The site returned HTTP 403, so the article text could not be read.",
            checks_completed=["provenance", "registry"],
        )

        assert "synthetic" not in result.checks_completed


class TestSyntheticResult:
    def test_valid_result_carries_the_fixed_disclaimer(self) -> None:
        assert make_synthetic().disclaimer == AI_DETECTION_DISCLAIMER

    def test_disclaimer_cannot_be_altered(self) -> None:
        """INV-3: the uncertainty disclaimer is fixed, not per-result copy."""
        with pytest.raises(ValidationError, match="fixed"):
            make_synthetic(disclaimer="Confirmed AI.")

    def test_ok_status_requires_a_probability(self) -> None:
        with pytest.raises(ValidationError, match="requires probability_ai"):
            make_synthetic(probability_ai=None)

    def test_ok_status_requires_section_scores(self) -> None:
        """INV-5: an aggregate score is never shown without its sections."""
        with pytest.raises(ValidationError, match="analyzed sections"):
            make_synthetic(sections=[])

    @pytest.mark.parametrize("status", ["skipped_no_content", "text_too_short", "not_integrated"])
    def test_non_ok_status_must_not_carry_a_probability(self, status: str) -> None:
        with pytest.raises(ValidationError, match="must not carry a probability"):
            make_synthetic(status=status, detail="Some honest reason.")

    @pytest.mark.parametrize("status", ["skipped_no_content", "text_too_short", "not_integrated"])
    def test_non_ok_status_requires_a_detail(self, status: str) -> None:
        """INV-2: a skipped check always explains itself."""
        with pytest.raises(ValidationError, match="honest detail"):
            make_synthetic(status=status, probability_ai=None, sections=[], detail="")

    def test_skipped_check_is_valid_when_it_explains_itself(self) -> None:
        result = SyntheticResult(
            status="skipped_no_content",
            model_name="placeholder-detector",
            model_version="0.0.0",
            detail="No article text could be read from the page, so no estimate was produced.",
        )

        assert result.probability_ai is None
        assert result.disclaimer == AI_DETECTION_DISCLAIMER

    def test_analyzed_sections_cannot_exceed_the_total(self) -> None:
        with pytest.raises(ValidationError, match="outnumber"):
            make_synthetic(total_sections=1)

    def test_probability_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            make_synthetic(probability_ai=1.4)


class TestDomainAge:
    def test_unavailable_age_must_not_carry_a_value(self) -> None:
        """SPEC 4.1: failure means unavailable, never a fabricated number."""
        with pytest.raises(ValidationError, match="must not carry a value"):
            DomainAge(status="unavailable", value_years=12.0, source="RDAP", detail="RDAP down.")

    def test_unavailable_age_requires_a_reason(self) -> None:
        with pytest.raises(ValidationError, match="requires a detail"):
            DomainAge(status="unavailable", source="RDAP")

    def test_ok_age_requires_a_value(self) -> None:
        with pytest.raises(ValidationError, match="requires value_years"):
            DomainAge(status="ok", source="RDAP")


class TestPublicationDate:
    def test_not_found_must_not_carry_a_value(self) -> None:
        with pytest.raises(ValidationError, match="must not carry a value"):
            PublicationDate(status="not_found", value="2026-05-01", method="htmldate")

    def test_ok_requires_a_value(self) -> None:
        with pytest.raises(ValidationError, match="requires a value"):
            PublicationDate(status="ok", method="htmldate")


class TestProvenanceResult:
    def test_signals_are_mandatory(self) -> None:
        """INV-4: no result without the raw signals that produced it."""
        with pytest.raises(ValidationError):
            make_provenance(signals=[])

    def test_pasted_text_provenance_is_null_but_still_explained(self) -> None:
        """Pasted text has no domain. The nulls are explained by the signals."""
        result = ProvenanceResult(
            signals=[
                {
                    "name": "input_type",
                    "raw_value": "text",
                    "interpretation": "Provenance checks need a URL, so none could run.",
                }
            ]
        )

        assert result.domain is None
        assert result.https is None
        assert result.signals


class TestRegistryEntry:
    @pytest.mark.parametrize(
        ("editorial", "corrections", "ownership", "expected"),
        [
            (True, True, True, "high"),
            (True, True, False, "medium"),
            (True, False, True, "medium"),
            (False, True, True, "medium"),
            (True, False, False, "low"),
            (False, False, False, "low"),
        ],
    )
    def test_tier_math_matches_spec_section_5(
        self, editorial: bool, corrections: bool, ownership: bool, expected: str
    ) -> None:
        assert RegistryEntry.derive_tier(editorial, corrections, ownership) == expected

    def test_entry_contradicting_its_booleans_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="contradicts the criteria booleans"):
            make_registry_entry(ownership_transparent=False)

    def test_country_must_be_two_letters(self) -> None:
        with pytest.raises(ValidationError):
            make_registry_entry(country="GBR")


class TestCredibilityResult:
    def test_unrated_carries_no_tier(self) -> None:
        """INV-6: an unknown source is never given an interpolated score."""
        with pytest.raises(ValidationError, match="must not carry a trust tier"):
            CredibilityResult(
                status="unrated",
                trust_tier="medium",
                unrated_explanation="Not in the registry.",
                registry_version="1.0.0",
            )

    def test_unrated_requires_an_explanation(self) -> None:
        with pytest.raises(ValidationError, match="requires an honest explanation"):
            CredibilityResult(status="unrated", registry_version="1.0.0")

    def test_valid_unrated_result(self) -> None:
        result = CredibilityResult(
            status="unrated",
            unrated_explanation=(
                "This domain is not in the source registry, so it has no credibility rating. "
                "An absent rating is not a negative rating."
            ),
            registry_version="1.0.0",
        )

        assert result.trust_tier is None
        assert result.matched_entry is None

    def test_rated_requires_the_matched_entry(self) -> None:
        """INV-4: the tier must be traceable to the entry that produced it."""
        with pytest.raises(ValidationError, match="requires the matched registry entry"):
            CredibilityResult(status="rated", trust_tier="high", registry_version="1.0.0")

    def test_tier_must_equal_the_matched_entry_tier(self) -> None:
        with pytest.raises(ValidationError, match="must equal the matched entry"):
            make_credibility(trust_tier="low")


class TestTrustSummary:
    def test_complete_summary_round_trips_through_json(self) -> None:
        summary = make_trust_summary()

        assert TrustSummary.model_validate_json(summary.model_dump_json()) == summary

    def test_serialized_shape_matches_the_spec_contract(self) -> None:
        """The frontend types in frontend/src/types.ts mirror these exact keys."""
        payload = make_trust_summary().model_dump()

        assert set(payload) == {
            "input_type",
            "retrieval",
            "synthetic",
            "provenance",
            "credibility",
        }
        assert set(payload["retrieval"]) == {"status", "detail", "checks_completed"}
        assert set(payload["provenance"]) == {
            "domain",
            "domain_age",
            "https",
            "publisher_metadata_found",
            "publication_date",
            "signals",
        }

    def test_no_field_can_express_a_binary_verdict(self) -> None:
        """INV-3: the schema itself offers nowhere to put 'confirmed AI'.

        Checks field names only. The word 'verdict' legitimately appears in the
        disclaimer copy, which says the score is not one.
        """
        schema = TrustSummary.model_json_schema()
        field_names = {
            name.lower()
            for definition in schema["$defs"].values()
            for name in definition.get("properties", {})
        }

        for forbidden in ("verified_fake", "confirmed_ai", "is_ai", "verdict", "is_fake"):
            assert forbidden not in field_names

    def test_pasted_text_summary_is_valid(self) -> None:
        """INV-2: pasted text always fully analyzes, with provenance honestly absent."""
        summary = make_trust_summary(
            input_type="text",
            retrieval=RetrievalResult(
                status="ok",
                detail="Text was supplied directly, so no retrieval was needed.",
                checks_completed=["synthetic"],
            ),
            provenance=ProvenanceResult(
                signals=[
                    {
                        "name": "input_type",
                        "raw_value": "text",
                        "interpretation": "Provenance checks need a URL, so none could run.",
                    }
                ]
            ),
            credibility=CredibilityResult(
                status="unrated",
                unrated_explanation="Pasted text has no domain to look up in the registry.",
                registry_version="1.0.0",
            ),
        )

        assert summary.synthetic.status == "ok"
        assert summary.provenance.domain is None
