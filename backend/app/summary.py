"""Assembles a TrustSummary from the check results (SPEC.md section 3).

This is the one place that decides what ran, what could not run, and how each
absence is explained. INV-2 is the governing rule: every input produces a
complete TrustSummary, and `retrieval.checks_completed` is the honest ledger
of which checks actually executed.

The synthetic check counts as completed when it produced a determination: a
score, or an honest text_too_short refusal. A check that could not run (no
content, or the model failed to load) never appears in `checks_completed`.
"""

from .checks import registry
from .checks.provenance import check_provenance
from .checks.synthetic import MODEL_ID, analyze_text
from .fetcher import MIN_EXTRACTABLE_CHARS, FetchResult, fetch_url
from .models import (
    AnalyzeRequest,
    CheckName,
    ProvenanceResult,
    RetrievalResult,
    Signal,
    SyntheticResult,
    TrustSummary,
)


def _synthetic_skipped(detail: str) -> SyntheticResult:
    return SyntheticResult(
        status="skipped_no_content",
        model_name=MODEL_ID,
        model_version="n/a",
        detail=detail,
    )


def _synthetic_for_text(text: str) -> tuple[SyntheticResult, bool]:
    """The synthetic result for analyzable text, and whether the check ran."""
    result = analyze_text(text)
    return result, result.status in ("ok", "text_too_short")


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
    return summarize_fetch_result(fetch_url(url), url)


def summarize_fetch_result(fetched: FetchResult, requested_url: str) -> TrustSummary:
    """Assemble the TrustSummary for an already-fetched URL input.

    Public so Mode 2 gathering (agent/search.py) can run the identical
    assembly on sources it retrieves itself, including backend-served demo
    articles that never go through a network fetch.
    """
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
            url=fetched.final_url or requested_url,
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
                        "No public website is associated with this input, so "
                        "provenance checks could not run."
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
