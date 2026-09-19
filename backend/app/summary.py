"""Assembles a TrustSummary from the check results (SPEC.md section 3).

This is the one place that decides what ran, what could not run, and how each
absence is explained. INV-2 is the governing rule: every input produces a
complete TrustSummary, and `retrieval.checks_completed` is the honest ledger
of which checks actually executed.

The synthetic check is a special case until the detection module lands (task
2.2): where there is analyzable text, the summary says outright that no
detection model is integrated yet, and `checks_completed` never lists a check
that did not run.
"""

from .checks import registry
from .checks.provenance import check_provenance
from .fetcher import MIN_EXTRACTABLE_CHARS, fetch_url
from .models import (
    AnalyzeRequest,
    CheckName,
    ProvenanceResult,
    RetrievalResult,
    Signal,
    SyntheticResult,
    TrustSummary,
)

MIN_DETECTION_WORDS = 100
"""SPEC.md section 4.2: below this, detection is unreliable and refuses."""

PLACEHOLDER_MODEL_NAME = "none"
PLACEHOLDER_MODEL_VERSION = "0.0.0"
"""Until task 2.1 selects a detector, there is no model to name. The detail
string states this outright; nothing pretends a model ran."""

NOT_INTEGRATED_DETAIL = (
    "The AI-text detection model has not been integrated yet, so no estimate was "
    "produced for this text. The other checks in this summary ran normally."
)


def _synthetic_skipped(detail: str) -> SyntheticResult:
    return SyntheticResult(
        status="skipped_no_content",
        model_name=PLACEHOLDER_MODEL_NAME,
        model_version=PLACEHOLDER_MODEL_VERSION,
        detail=detail,
    )


def _synthetic_not_integrated() -> SyntheticResult:
    """There was analyzable text, but no detection model exists yet (pre task 2.2)."""
    return SyntheticResult(
        status="not_integrated",
        model_name=PLACEHOLDER_MODEL_NAME,
        model_version=PLACEHOLDER_MODEL_VERSION,
        detail=NOT_INTEGRATED_DETAIL,
    )


def _synthetic_too_short(word_count: int) -> SyntheticResult:
    return SyntheticResult(
        status="text_too_short",
        model_name=PLACEHOLDER_MODEL_NAME,
        model_version=PLACEHOLDER_MODEL_VERSION,
        detail=(
            f"This passage is {word_count} words long. Detection needs at least "
            f"{MIN_DETECTION_WORDS} words to say anything meaningful, so no estimate "
            f"was produced."
        ),
    )


def _synthetic_for_text(text: str) -> tuple[SyntheticResult, bool]:
    """The synthetic result for analyzable text, and whether the check ran."""
    word_count = len(text.split())
    if word_count < MIN_DETECTION_WORDS:
        # The length check is a real, honest determination: it ran.
        return _synthetic_too_short(word_count), True
    return _synthetic_not_integrated(), False


def build_trust_summary(request: AnalyzeRequest) -> TrustSummary:
    """Produce the complete TrustSummary for one analyze request (INV-2)."""
    if request.text is not None:
        return _summarize_text(request.text)
    return _summarize_url(request.url or "")


def _summarize_text(text: str) -> TrustSummary:
    synthetic, synthetic_ran = _synthetic_for_text(text)
    checks: list[CheckName] = ["synthetic"] if synthetic_ran else []

    return TrustSummary(
        input_type="text",
        retrieval=RetrievalResult(
            status="ok",
            detail="Text was supplied directly, so no page retrieval was needed.",
            checks_completed=checks,
        ),
        synthetic=synthetic,
        provenance=check_provenance(url=None, host=None, html=None),
        credibility=registry.lookup(None),
    )


def _summarize_url(url: str) -> TrustSummary:
    fetched = fetch_url(url)
    checks: list[CheckName] = []

    # Synthetic: honest about what there was to analyze and what ran.
    if fetched.text and len(fetched.text) >= MIN_EXTRACTABLE_CHARS:
        synthetic, synthetic_ran = _synthetic_for_text(fetched.text)
        if synthetic_ran:
            checks.append("synthetic")
    else:
        synthetic = _synthetic_skipped(
            "No article text could be read from this page, so no estimate was produced."
        )

    # Provenance: domain-level checks run whenever a host is known, even for a
    # blocked or erroring page (INV-2). The fetcher's raw evidence (HTTP status,
    # redirects, paywall marker) is merged into the provenance signals so the
    # whole result stays traceable (INV-4).
    if fetched.host is not None:
        provenance = check_provenance(
            url=fetched.final_url or url,
            host=fetched.host,
            html=fetched.html,
        )
        checks.extend(["provenance", "registry"])
    else:
        provenance = ProvenanceResult(
            signals=[
                Signal(
                    name="retrieval_status",
                    raw_value=fetched.status,
                    interpretation=(
                        "The address could not be resolved to a site, so provenance "
                        "checks could not run."
                    ),
                )
            ]
        )

    provenance = provenance.model_copy(update={"signals": [*fetched.signals, *provenance.signals]})

    return TrustSummary(
        input_type="url",
        retrieval=RetrievalResult(
            status=fetched.status,
            detail=fetched.detail,
            checks_completed=checks,
        ),
        synthetic=synthetic,
        provenance=provenance,
        credibility=registry.lookup(provenance.domain),
    )
