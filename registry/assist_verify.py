"""Find candidate evidence pages for registry candidates. Decides nothing.

Reads a CSV of candidate publishers (column `domain`, optionally
`publisher_name`, `source_type`, `country`), probes each site for pages that
look like editorial standards, corrections policy, and ownership disclosures,
and writes a CSV of candidate URLs for a HUMAN to confirm.

This tool never sets a boolean and never writes to registry.json (SPEC.md
section 5). A domain yielding no candidates usually means the site blocks
automated access; it says nothing about the publisher.

Usage:
    python registry/assist_verify.py [candidates.csv] [output.csv]
Defaults: registry/registry_candidates.csv -> .notes/registry_candidates_assisted.csv
"""

import csv
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE / "registry_candidates.csv"
DEFAULT_OUTPUT = HERE.parent / ".notes" / "registry_candidates_assisted.csv"

TIMEOUT = 8.0
UA = "Mozilla/5.0 (compatible; AgentOathRegistryAssist/0.1; evidence page discovery)"
MAX_CANDIDATES_PER_CRITERION = 3

KEYWORDS = {
    "editorial_standards": (
        "editorial code", "editorial guideline", "editorial standard", "editorial polic",
        "code of ethics", "ethics", "standards and practices", "trust principles",
        "journalistic standard", "leitlinien", "charte",
    ),
    "corrections_policy": (
        "correction", "complaint", "accuracy", "feedback and corrections", "report an error",
    ),
    "ownership": (
        "ownership", "who we are", "who owns", "about us", "annual report", "funding",
        "impressum", "imprint", "governance",
    ),
}

PROBE_PATHS = {
    "editorial_standards": ("/ethics", "/editorial-standards", "/editorial-guidelines", "/code-of-ethics"),
    "corrections_policy": ("/corrections", "/complaints", "/corrections-policy"),
    "ownership": ("/about", "/about-us"),
}

LINK_RE = re.compile(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def _fetch(client: httpx.Client, url: str) -> str | None:
    try:
        response = client.get(url)
        if response.status_code == 200 and "html" in response.headers.get("content-type", "html"):
            return response.text[:400_000]
    except httpx.HTTPError:
        return None
    return None


def _same_site(base_host: str, url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "" or host == base_host or host.endswith("." + base_host.removeprefix("www."))


def candidates_for(client: httpx.Client, domain: str) -> dict[str, list[str]]:
    """Candidate evidence URLs per criterion, from homepage links plus probes."""
    base = f"https://{domain}"
    found: dict[str, list[str]] = {k: [] for k in KEYWORDS}

    homepage = _fetch(client, base)
    if homepage:
        for href, inner in LINK_RE.findall(homepage):
            text = TAG_RE.sub(" ", inner).strip().lower()
            target = urljoin(base + "/", href.strip())
            if not _same_site(urlsplit(base).hostname or "", target):
                continue
            haystack = f"{text} {target.lower()}"
            for criterion, words in KEYWORDS.items():
                if len(found[criterion]) >= MAX_CANDIDATES_PER_CRITERION:
                    continue
                if any(word in haystack for word in words) and target not in found[criterion]:
                    found[criterion].append(target)

    for criterion, paths in PROBE_PATHS.items():
        if found[criterion]:
            continue  # homepage already offered candidates; probing adds noise
        for path in paths:
            probe = base + path
            if _fetch(client, probe) is not None:
                found[criterion].append(probe)
                break

    return found


def main() -> int:
    """CLI entry point."""
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    fieldnames = [
        "domain", "publisher_name", "source_type", "country",
        "editorial_standards_candidates", "corrections_policy_candidates",
        "ownership_candidates", "reachable",
    ]
    yielded = 0
    with output_path.open("w", encoding="utf-8", newline="") as handle, httpx.Client(
        timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA}
    ) as client:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            domain = (row.get("domain") or "").strip().lower()
            if not domain:
                continue
            found = candidates_for(client, domain)
            any_candidate = any(found.values())
            yielded += 1 if any_candidate else 0
            writer.writerow(
                {
                    "domain": domain,
                    "publisher_name": row.get("publisher_name", ""),
                    "source_type": row.get("source_type", ""),
                    "country": row.get("country", ""),
                    "editorial_standards_candidates": " | ".join(found["editorial_standards"]),
                    "corrections_policy_candidates": " | ".join(found["corrections_policy"]),
                    "ownership_candidates": " | ".join(found["ownership"]),
                    "reachable": "yes" if any_candidate else "no",
                }
            )
            print(f"{domain:28} {'candidates' if any_candidate else 'nothing reachable'}")

    print(f"\n{yielded} of {len(rows)} domains yielded at least one candidate evidence page")
    print(f"output: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
