"""Source gathering for Mode 2 (SPEC.md section 6; decided 2026-10-17).

Two gathering modes, both producing the same structure and both running the
identical Mode 1 analysis per source:

- Seed topics: curated demo topics from seed_topics.json, each URL chosen to
  exercise a distinct retrieval or rating state. Disclosed as curated.
- Bring your own: the user supplies 3 to 5 URLs. Anything else is rejected
  with a plain-language error.

Demo articles (paths under /demo/articles/) are served by this backend and
are loaded from disk here rather than fetched over the network: the fetcher's
private-address guard rightly refuses localhost, and a disk read is the same
content without weakening that guard. Their summaries say what they are.
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from ..fetcher import FetchResult, extract_text, fetch_url
from ..models import Signal, TrustSummary
from ..summary import summarize_fetch_result
from .approval import AgentSession, create_session

SEED_TOPICS_PATH = Path(__file__).resolve().parent / "seed_topics.json"
DEMO_ARTICLES_DIR = Path(__file__).resolve().parent / "demo_articles"
DEMO_PATH_PREFIX = "/demo/articles/"

MIN_BYO_URLS = 3
MAX_BYO_URLS = 5

BYO_COUNT_ERROR = (
    f"Provide between {MIN_BYO_URLS} and {MAX_BYO_URLS} web addresses. The agent needs at "
    f"least {MIN_BYO_URLS} candidate sources to be worth gating, and more than "
    f"{MAX_BYO_URLS} makes the approval step unreasonably long."
)


class SeedUrl(BaseModel):
    """One curated URL and the state it exists to exercise."""

    url: str
    exercises: str = Field(description="Which retrieval or rating state this URL shows.")


class SeedTopic(BaseModel):
    """One curated demo topic."""

    topic_id: str
    title: str
    urls: list[SeedUrl]


@lru_cache(maxsize=1)
def load_seed_topics() -> list[SeedTopic]:
    """The curated topics, validated once per process."""
    raw = json.loads(SEED_TOPICS_PATH.read_text(encoding="utf-8"))
    return [SeedTopic.model_validate(topic) for topic in raw["topics"]]


def demo_article_path(slug: str) -> Path | None:
    """Disk path for a demo article slug, or None when the slug is not one of ours."""
    if not slug.replace("-", "").isalnum():
        return None
    path = DEMO_ARTICLES_DIR / f"{slug}.html"
    return path if path.is_file() else None


def _fetch_demo_article(url: str) -> FetchResult:
    """Load a backend-served demo article from disk, shaped like a fetch (INV-2)."""
    slug = url.removeprefix(DEMO_PATH_PREFIX).strip("/")
    path = demo_article_path(slug)
    if path is None:
        return FetchResult(
            status="invalid_url",
            detail="No demo article exists at this address.",
            requested_url=url,
            signals=[
                Signal(
                    name="demo_article",
                    raw_value=slug or "empty",
                    interpretation="The requested demo article slug does not exist.",
                )
            ],
        )

    html = path.read_text(encoding="utf-8")
    text = extract_text(html)
    return FetchResult(
        status="ok",
        detail=(
            "This is an AI-generated demonstration article served by this backend. "
            f"{len(text)} characters of text were extracted."
        ),
        requested_url=url,
        final_url=url,
        host=None,
        html=html,
        text=text,
        signals=[
            Signal(
                name="demo_article",
                raw_value=slug,
                interpretation=(
                    "A demonstration article generated with a large language model and "
                    "served by this backend. It is marked synthetic in its source."
                ),
            )
        ],
    )


def gather_source(url: str) -> tuple[str, str, TrustSummary]:
    """Gather one source: fetch (or disk-load a demo article) and run Mode 1 analysis."""
    if url.startswith(DEMO_PATH_PREFIX):
        fetched = _fetch_demo_article(url)
    else:
        fetched = fetch_url(url)
    summary = summarize_fetch_result(fetched, url)
    return url, fetched.text or "", summary


def gather_for_topic(topic_id: str) -> list[tuple[str, str, TrustSummary]]:
    """Gather every curated source for one seed topic."""
    topic = next((t for t in load_seed_topics() if t.topic_id == topic_id), None)
    if topic is None:
        known = ", ".join(t.topic_id for t in load_seed_topics())
        raise ValueError(f"Unknown demo topic '{topic_id}'. Known topics: {known}.")
    return [gather_source(seed.url) for seed in topic.urls]


def gather_urls(urls: list[str]) -> list[tuple[str, str, TrustSummary]]:
    """Gather a bring-your-own source set of 3 to 5 URLs."""
    cleaned = [url.strip() for url in urls if url and url.strip()]
    if not MIN_BYO_URLS <= len(cleaned) <= MAX_BYO_URLS:
        raise ValueError(BYO_COUNT_ERROR)
    return [gather_source(url) for url in cleaned]


def create_agent_session(
    question: str,
    *,
    topic_id: str | None = None,
    urls: list[str] | None = None,
) -> AgentSession:
    """Gather sources by exactly one mode and store them as a new session."""
    if (topic_id is None) == (urls is None):
        raise ValueError("Choose exactly one: a demo topic, or your own list of URLs.")
    if topic_id is not None:
        gathered = gather_for_topic(topic_id)
        mode = "seed"
    else:
        gathered = gather_urls(urls or [])
        mode = "byo"
    return create_session(question, mode, gathered)
