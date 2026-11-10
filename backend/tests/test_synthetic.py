"""Synthetic detection module tests (INV-3, INV-5).

The scorer is always injected: no test loads torch, the model, or touches the
network. Chunking, section selection, aggregation, and every refusal path are
covered, because the honest refusals are features (INV-2).
"""

import pytest

from app.checks.synthetic import (
    MAX_LEADING_SECTIONS,
    MIN_WORDS,
    MODEL_ID,
    SECTION_WORDS,
    analyze_text,
    sections_to_analyze,
    split_sections,
)
from app.models import AI_DETECTION_DISCLAIMER


def words(count: int, stem: str = "word") -> str:
    return " ".join(f"{stem}{n}" for n in range(count))


def flat_scorer(value: float):  # noqa: ANN201
    def score(section: str) -> float:
        return value

    return score


class TestChunking:
    def test_short_document_is_one_section(self) -> None:
        assert len(split_sections(words(250))) == 1

    def test_sections_hold_the_configured_word_count(self) -> None:
        sections = split_sections(words(SECTION_WORDS * 3))

        assert len(sections) == 3
        assert all(len(section.split()) == SECTION_WORDS for section in sections)

    def test_tiny_remainder_merges_into_the_previous_section(self) -> None:
        """A 50-word tail is below the 100-word reliability floor: never scored alone."""
        sections = split_sections(words(SECTION_WORDS * 2 + 50))

        assert len(sections) == 2
        assert len(sections[-1].split()) == SECTION_WORDS + 50

    def test_large_remainder_stays_its_own_section(self) -> None:
        sections = split_sections(words(SECTION_WORDS * 2 + MIN_WORDS))

        assert len(sections) == 3
        assert len(sections[-1].split()) == MIN_WORDS

    def test_no_words_are_lost_or_duplicated(self) -> None:
        text = words(SECTION_WORDS * 4 + 17)

        assert " ".join(split_sections(text)) == text


class TestSectionSelection:
    def test_small_documents_analyze_every_section(self) -> None:
        assert sections_to_analyze(4) == [0, 1, 2, 3]

    def test_boundary_document_analyzes_every_section(self) -> None:
        assert sections_to_analyze(MAX_LEADING_SECTIONS + 1) == [0, 1, 2, 3, 4, 5]

    def test_long_documents_analyze_first_five_plus_final(self) -> None:
        """SPEC 4.2: intro and body via the head, the close via the tail."""
        assert sections_to_analyze(12) == [0, 1, 2, 3, 4, 11]


class TestRefusals:
    def test_short_text_refuses_with_the_word_count(self) -> None:
        result = analyze_text(words(MIN_WORDS - 1), scorer=flat_scorer(0.9))

        assert result.status == "text_too_short"
        assert result.probability_ai is None
        assert str(MIN_WORDS - 1) in result.detail

    def test_boundary_length_is_analyzed(self) -> None:
        assert analyze_text(words(MIN_WORDS), scorer=flat_scorer(0.5)).status == "ok"

    def test_model_load_failure_becomes_an_honest_result_not_an_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INV-2: a broken model is a service problem, stated as such."""
        import app.checks.synthetic as synthetic_module

        def broken_loader() -> None:
            raise RuntimeError("no torch here")

        monkeypatch.setattr(synthetic_module, "_load_default_scorer", broken_loader)
        result = analyze_text(words(500))

        assert result.status == "not_integrated"
        assert "could not be loaded" in result.detail
        assert "not a statement about the text" in result.detail


class TestScoring:
    def test_mean_is_the_mean_of_the_analyzed_sections(self) -> None:
        values = iter([0.2, 0.4, 0.6])

        def score(section: str) -> float:
            return next(values)

        result = analyze_text(words(SECTION_WORDS * 3), scorer=score)

        assert result.status == "ok"
        assert result.probability_ai == pytest.approx(0.4)
        assert [s.probability_ai for s in result.sections] == [0.2, 0.4, 0.6]

    def test_long_document_reports_which_sections_were_analyzed(self) -> None:
        result = analyze_text(words(SECTION_WORDS * 12), scorer=flat_scorer(0.5))

        assert result.total_sections == 12
        assert [s.section_index for s in result.sections] == [0, 1, 2, 3, 4, 11]

    def test_every_section_carries_a_locating_excerpt(self) -> None:
        result = analyze_text(words(SECTION_WORDS * 7), scorer=flat_scorer(0.5))

        final_section = result.sections[-1]
        assert final_section.excerpt_start.startswith(f"word{SECTION_WORDS * 6}")

    def test_scores_outside_the_unit_interval_are_clamped(self) -> None:
        result = analyze_text(words(200), scorer=flat_scorer(1.7))

        assert result.probability_ai == 1.0

    def test_result_carries_model_name_and_fixed_disclaimer(self) -> None:
        """INV-3: identity and uncertainty travel with every score."""
        result = analyze_text(words(200), scorer=flat_scorer(0.3), model_version="test-rev")

        assert result.model_name == MODEL_ID
        assert result.model_version == "test-rev"
        assert result.disclaimer == AI_DETECTION_DISCLAIMER


class TestCpuBudget:
    def test_effective_cpu_count_is_a_sane_positive_integer(self) -> None:
        from app.checks.synthetic import _effective_cpu_count

        count = _effective_cpu_count()

        assert isinstance(count, int)
        assert count >= 1
