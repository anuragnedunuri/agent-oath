"""Validate registry.json: schema, tier math, and hygiene rules.

Run from anywhere: python registry/validate_registry.py
Exit code 0 means every entry is valid; otherwise each problem prints on its
own line and the exit code is 1. Continuous integration runs this on every
push, so an entry that contradicts METHODOLOGY.md cannot land.

The schema and the tier rule are validated through the backend's RegistryEntry
model, so this script, the API, and METHODOLOGY.md cannot drift apart.
"""

import datetime
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

import tldextract  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.models import RegistryEntry  # noqa: E402

REGISTRY_PATH = HERE / "registry.json"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
MAX_NOTES_CHARS = 200

_extract = tldextract.TLDExtract(suffix_list_urls=())


def _parse_date(value: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def validate(path: Path = REGISTRY_PATH) -> list[str]:
    """Return a list of problems. Empty list means the registry is valid."""
    problems: list[str] = []
    today = datetime.date.today()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"registry.json could not be read as JSON: {error}"]

    for key in ("version", "updated", "criteria_doc", "entries"):
        if key not in data:
            problems.append(f"top level: missing required key '{key}'")
    if problems:
        return problems

    if not SEMVER_RE.match(str(data["version"])):
        problems.append(f"version: '{data['version']}' is not MAJOR.MINOR.PATCH")

    updated = _parse_date(str(data["updated"]))
    if updated is None:
        problems.append(f"updated: '{data['updated']}' is not an ISO date")
    elif updated > today:
        problems.append(f"updated: {updated} is in the future")

    if not (HERE / str(data["criteria_doc"])).exists():
        problems.append(f"criteria_doc: '{data['criteria_doc']}' does not exist next to registry.json")

    entries = data["entries"]
    if not isinstance(entries, list) or not entries:
        return [*problems, "entries: must be a non-empty list"]

    seen_domains: set[str] = set()
    for position, raw in enumerate(entries):
        label = f"entry {position} ({raw.get('domain', 'no domain') if isinstance(raw, dict) else 'not an object'})"

        try:
            entry = RegistryEntry.model_validate(raw)
        except ValidationError as error:
            for issue in error.errors():
                field = ".".join(str(part) for part in issue["loc"]) or "entry"
                problems.append(f"{label}: {field}: {issue['msg']}")
            continue

        if entry.domain != entry.domain.lower():
            problems.append(f"{label}: domain must be lowercase")
        registrable = _extract(entry.domain).top_domain_under_public_suffix
        if registrable and registrable != entry.domain:
            problems.append(
                f"{label}: domain is not registrable; use '{registrable}' instead"
            )
        if entry.domain in seen_domains:
            problems.append(f"{label}: duplicate domain")
        seen_domains.add(entry.domain)

        verified = _parse_date(entry.last_verified)
        if verified is None:
            problems.append(f"{label}: last_verified '{entry.last_verified}' is not an ISO date")
        elif verified > today:
            problems.append(f"{label}: last_verified {verified} is in the future")

        if len(entry.notes) > MAX_NOTES_CHARS:
            problems.append(
                f"{label}: notes exceed {MAX_NOTES_CHARS} characters; structured fields only"
            )

    return problems


def main() -> int:
    """CLI entry point."""
    problems = validate()
    if problems:
        for problem in problems:
            print(f"INVALID  {problem}")
        return 1

    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    tiers: dict[str, int] = {}
    for raw in data["entries"]:
        tiers[raw["trust_tier"]] = tiers.get(raw["trust_tier"], 0) + 1
    breakdown = ", ".join(f"{count} {tier}" for tier, count in sorted(tiers.items()))
    print(
        f"OK  registry v{data['version']} ({data['updated']}): "
        f"{len(data['entries'])} entries valid ({breakdown})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
