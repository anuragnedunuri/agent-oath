"""AI-text detection, chunked (SPEC.md section 4.2, INV-3, INV-5).

Model: Hello-SimpleAI/chatgpt-detector-roberta, selected in the task 2.1 spike
and recorded in SPEC.md section 4.2.

Design rules carried by this module:

  INV-3  the result is always a probability estimate. The fixed disclaimer,
         model name, and version travel with every score, enforced by the
         SyntheticResult model itself.
  INV-5  long documents are split into ~400-token sections; the first five and
         the final section are analyzed, and the result names exactly which
         sections those were, so the UI can state coverage honestly.

The scorer is injectable and lazily loaded: unit tests never touch torch, the
model files, or the network. If the model cannot be loaded at runtime, the
result says so plainly instead of raising (INV-2).
"""

import logging
import math
import os
import threading
from collections.abc import Callable
from pathlib import Path

from ..models import SyntheticResult, SyntheticSection

MODEL_ID = "Hello-SimpleAI/chatgpt-detector-roberta"

MIN_WORDS = 100
"""SPEC.md section 4.2: below this, detection is unreliable and refuses."""

SECTION_WORDS = 300
"""Words per section. Roughly 400 tokens of English, inside the model's
512-token window with margin for tokenization overhead."""

MAX_LEADING_SECTIONS = 5
"""Analyze up to the first five sections plus the final one (intro + body +
close), per SPEC.md section 4.2."""

EXCERPT_WORDS = 8

SectionScorer = Callable[[str], float]
"""Takes section text, returns P(AI-generated) in [0, 1]."""

_scorer_lock = threading.Lock()
_default_scorer: SectionScorer | None = None
_model_version: str = "unknown"


def split_sections(text: str) -> list[str]:
    """Split text into consecutive sections of SECTION_WORDS words.

    A trailing remainder shorter than MIN_WORDS is merged into the section
    before it: the model is unreliable on tiny fragments (that is the whole
    point of the 100-word rule), so no scored section may be one.
    """
    words = text.split()
    sections = [
        " ".join(words[start : start + SECTION_WORDS])
        for start in range(0, len(words), SECTION_WORDS)
    ]
    if len(sections) > 1 and len(sections[-1].split()) < MIN_WORDS:
        tail = sections.pop()
        sections[-1] = f"{sections[-1]} {tail}"
    return sections


def sections_to_analyze(total: int) -> list[int]:
    """Which section indices get analyzed: first five plus the last (INV-5)."""
    if total <= MAX_LEADING_SECTIONS + 1:
        return list(range(total))
    return [*range(MAX_LEADING_SECTIONS), total - 1]


def _excerpt(section: str) -> str:
    return " ".join(section.split()[:EXCERPT_WORDS])


def _effective_cpu_count() -> int:
    """The CPU budget actually available to this process, container-aware.

    torch defaults its thread pool to the host's core count, which inside a
    CPU-limited container (for example a 1 vCPU cloud instance) oversubscribes
    threads against a throttled quota and slows inference dramatically. Order
    of truth: cgroup v2 quota, then scheduler affinity, then os.cpu_count().
    """
    try:
        quota = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="ascii").split()
        if quota and quota[0] != "max":
            return max(1, math.ceil(int(quota[0]) / int(quota[1])))
    except (OSError, ValueError, IndexError):
        pass
    try:
        return max(1, len(os.sched_getaffinity(0)))  # type: ignore[attr-defined]
    except AttributeError:
        return max(1, os.cpu_count() or 1)


def _load_default_scorer() -> tuple[SectionScorer, str]:
    """Load the detection model once, process-wide.

    Heavy imports stay inside this function so the module, and every unit
    test, imports without torch installed.
    """
    global _default_scorer, _model_version
    with _scorer_lock:
        if _default_scorer is not None:
            return _default_scorer, _model_version

        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        cpus = _effective_cpu_count()
        try:
            torch.set_num_threads(cpus)
        except RuntimeError:
            pass
        logging.getLogger("agent.synthetic").info(
            "torch inference threads set to %d (host reports %s cpus)", cpus, os.cpu_count()
        )

        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID)
        model.eval()

        # This model's classes are {0: Human, 1: ChatGPT}; resolve by name so a
        # model update that reorders labels cannot silently flip the meaning.
        ai_index = next(
            int(i) for i, label in model.config.id2label.items() if label.lower() != "human"
        )

        def score(section_text: str) -> float:
            encoded = tokenizer(section_text, truncation=True, max_length=512, return_tensors="pt")
            with torch.no_grad():
                logits = model(**encoded).logits
            return torch.softmax(logits, dim=-1)[0, ai_index].item()

        _default_scorer = score
        _model_version = str(getattr(model.config, "_commit_hash", None) or "main")
        return _default_scorer, _model_version


def warm_up() -> None:
    """Load the model ahead of the first request.

    Failure here is not fatal; the first analysis will retry and report
    honestly if it fails again.
    """
    try:
        _load_default_scorer()
    except Exception:  # noqa: BLE001  warm-up is best effort by design
        pass


def analyze_text(
    text: str,
    *,
    scorer: SectionScorer | None = None,
    model_version: str | None = None,
) -> SyntheticResult:
    """Run chunked AI-text detection over extracted text (INV-3, INV-5).

    Args:
        text: The extracted prose to analyze.
        scorer: Injected section scorer. Tests pass a fake; production leaves
            it None and the shared model loads on first use.
        model_version: Version string to report alongside an injected scorer.

    Returns:
        A SyntheticResult. Never raises: a model that cannot load produces an
        honest not_integrated result instead (INV-2).
    """
    word_count = len(text.split())
    if word_count < MIN_WORDS:
        return SyntheticResult(
            status="text_too_short",
            model_name=MODEL_ID,
            model_version=model_version or "n/a",
            detail=(
                f"This passage is {word_count} words long. Detection needs at least "
                f"{MIN_WORDS} words to say anything meaningful, so no estimate was produced."
            ),
        )

    version = model_version or "injected"
    if scorer is None:
        try:
            scorer, version = _load_default_scorer()
        except Exception:  # noqa: BLE001  any load failure must become an honest result
            return SyntheticResult(
                status="not_integrated",
                model_name=MODEL_ID,
                model_version="unavailable",
                detail=(
                    "The detection model could not be loaded, so no estimate was produced. "
                    "This is a service problem, not a statement about the text."
                ),
            )

    sections = split_sections(text)
    analyzed_indices = sections_to_analyze(len(sections))

    scored_sections = [
        SyntheticSection(
            section_index=index,
            excerpt_start=_excerpt(sections[index]),
            probability_ai=max(0.0, min(1.0, scorer(sections[index]))),
        )
        for index in analyzed_indices
    ]
    mean_probability = sum(s.probability_ai for s in scored_sections) / len(scored_sections)

    return SyntheticResult(
        status="ok",
        probability_ai=round(mean_probability, 4),
        model_name=MODEL_ID,
        model_version=version,
        sections=scored_sections,
        total_sections=len(sections),
    )
