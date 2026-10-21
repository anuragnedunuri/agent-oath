# Source Registry Methodology

This document defines exactly how every entry in `registry.json` is produced.
It is published so that any rating shown by the system can be reconstructed by
a reader from the entry itself. There are no other inputs to a rating.

## What the registry measures, and what it does not

The registry measures **transparency practices** of a publisher: whether it
publicly commits to editorial standards, publicly explains how it corrects
errors, and is open about who owns and funds it.

It does **not** measure the accuracy, bias, or quality of any individual
article. A high tier means the publisher operates transparently. It is one
signal among several in a Trust Summary, never a verdict.

## The three criteria

Each entry records three booleans, checked against the publisher's own
published pages:

| Field | True means |
| --- | --- |
| `editorial_standards_public` | The publisher publishes its editorial standards, code of ethics, or equivalent, findable from its own site. |
| `corrections_policy_public` | The publisher publishes how it handles corrections, findable from its own site. |
| `ownership_transparent` | The publisher's ownership, control, or funding structure is publicly disclosed by the publisher or its parent. |

A value of `false` means the reviewer **did not find** the item published at
review time. It is a statement about findability on the review date, not a
claim that the item does not exist. Publishers can have an entry corrected by
pointing to the published page (see Corrections below).

## The evidence rule

Since registry version 1.1.0, every `true` must carry a matching evidence URL
(`editorial_standards_url`, `corrections_policy_url`, `ownership_url`)
pointing at the public page that evidences it. The validator fails the build
on a `true` without evidence; a `false` needs no URL. Tooling
(`assist_verify.py`) may suggest candidate pages, but it decides nothing: a
human confirms each page before an entry counts as verified, and
`last_verified` records that human check. Entries whose evidence was located
by tooling but not yet humanly confirmed say so in `notes`.

## The tier rule

The trust tier derives mechanically from the three booleans:

- all 3 true: `high`
- exactly 2 true: `medium`
- otherwise: `low`

There is no editorial discretion in the tier. The rule is enforced twice: by
`validate_registry.py` in continuous integration, and by the backend's data
model, which refuses to load an entry whose tier contradicts its booleans.

## Unknown means unrated

A domain not present in the registry is reported as **unrated**, with an
explanation that an absent rating is not a negative rating. The system never
estimates or interpolates a rating for an unknown source.

## Entry mechanics

- `domain` is the registrable domain (for example `news.bbc.co.uk` resolves to
  the `bbc.co.uk` entry). Lookups are exact matches on the registrable domain.
- `last_verified` is the date the three booleans were last reviewed.
- `notes` is optional and short, used mainly to explain a `false`.
- The registry file carries a version and an updated date. Ratings shown by
  the system always name the registry version they came from.

## Review process

1. Locate the publisher's editorial standards, corrections policy, and
   ownership disclosure starting from the publisher's own site.
2. Set each boolean per the definitions above. When in doubt, record `false`
   with a note; a conservative false is honest and correctable, an optimistic
   true is not.
3. Derive the tier by the rule. Set `last_verified`.
4. Run `python registry/validate_registry.py` before committing.

## Corrections to the registry

Anyone, including a listed publisher, can challenge an entry by opening an
issue on this repository with a link to the published page that contradicts
the entry. Corrections update `last_verified` and the registry version.

## Current limitations

- Coverage is small and skewed toward large, established outlets while the
  registry is being seeded. Absence of an entry says nothing about a source.
- Criteria are checked as published in English or the publisher's primary
  language; findability judgments can err, in the conservative direction.
- Transparency practices are a proxy. A transparent publisher can still be
  wrong, and an opaque one can still be right. The Trust Summary presents
  this rating alongside independent signals for that reason.
