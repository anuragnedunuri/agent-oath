# SPEC.md — Trust Verification Layer, Technical Specification

## §1. The Charter (verification contract — do not drift)

The prototype will be externally verified against these exact claims:

1. **Reliable output**: for any submitted English-language URL or pasted text,
   the system always renders a Trust Summary. Unretrievable content is reported
   honestly with whichever checks still completed. Pasted text always fully
   analyzes.
2. **Transparent scoring**: provenance and credibility checks are fully
   traceable (click reveals exact criteria/signals). AI-detection is reported as
   a probability estimate with model name, version, and section-level results.
3. **Enforced verification gate**: in Mode 2, no source can enter the agent's
   answer through any application pathway until the user has seen its Trust
   Summary and approved it. Evaluators are invited to attempt a bypass.

Evidence artifacts also promised: ~40-article benchmark (half verified human
journalism, half AI-generated-by-us so ground truth is certain), a 10–15 person
comprehension study, and full code + registry + methodology public on GitHub.

The GTC verification claims register (October 2026) expands these three
claims into C1–C8 (and C9 if evaluated as Integrated). C8, the exportable
session audit log, is specified in §6 and was approved as a scope addition on
2026-10-17. The register must never claim anything this document does not
specify.

## §2. Repository layout

```
agent-oath/
├── SPEC.md
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, CORS, routers
│   │   ├── models.py            # Pydantic request/response models
│   │   ├── checks/
│   │   │   ├── synthetic.py     # AI-text detection (chunked)
│   │   │   ├── provenance.py    # fetch, domain age, https, metadata, date
│   │   │   └── registry.py      # registry lookup
│   │   ├── fetcher.py           # URL retrieval w/ honest failure taxonomy
│   │   ├── summary.py           # assembles TrustSummary from check results
│   │   ├── agent/
│   │   │   ├── search.py        # source gathering for Mode 2 (seed + BYO)
│   │   │   ├── seed_topics.json # curated demo topics and their URLs
│   │   │   ├── demo_articles/   # AI-generated demo pages served at /demo/articles/<slug>
│   │   │   ├── approval.py      # server-side session + approval store (INV-1)
│   │   │   ├── answer.py        # LLM answer from APPROVED sources only
│   │   │   └── audit.py         # session audit log export
│   │   └── config.py
│   └── tests/
├── frontend/
│   └── src/
│       ├── pages/Inspector.tsx  # Mode 1
│       ├── pages/AgentDemo.tsx  # Mode 2
│       └── components/TrustSummaryCard.tsx
├── registry/
│   ├── registry.json            # the source registry (versioned)
│   ├── registry_candidates.csv  # publishers queued for verification
│   ├── assist_verify.py         # finds candidate evidence pages; decides nothing
│   ├── METHODOLOGY.md           # scoring criteria, public
│   └── validate_registry.py
└── benchmark/
    ├── articles/                # 40 labeled items
    └── run_benchmark.py
```

## §3. API contract (Mode 1)

### POST /api/analyze
Request: `{ "url": string | null, "text": string | null }` (exactly one set)

Response `TrustSummary`:
```json
{
  "input_type": "url | text",
  "retrieval": {
    "status": "ok | paywall_or_blocked | timeout | js_required | not_html | invalid_url | unreachable | http_error",
    "detail": "human-readable honest explanation",
    "checks_completed": ["synthetic", "provenance", "registry"]
  },
  "synthetic": {
    "status": "ok | skipped_no_content | text_too_short | not_integrated",
    "probability_ai": 0.78,
    "model_name": "...", "model_version": "...",
    "sections": [ { "excerpt_start": "...", "probability_ai": 0.81 } ],
    "disclaimer": "fixed uncertainty disclaimer string"
  },
  "provenance": {
    "domain": "...",
    "domain_age": { "value_years": 12.4, "status": "ok | unavailable", "source": "RDAP" },
    "https": true,
    "publisher_metadata_found": true,
    "publication_date": { "value": "2026-05-01", "status": "ok | not_found", "method": "htmldate" },
    "signals": [ { "name": "...", "raw_value": "...", "interpretation": "..." } ]
  },
  "credibility": {
    "status": "rated | unrated",
    "trust_tier": "high | medium | low | null",
    "matched_entry": { /* full registry entry */ } ,
    "unrated_explanation": "string when unrated"
  }
}
```
Every displayed number must be reconstructible from `signals` /
`matched_entry` / `sections` (INV-4).

### Retrieval failure taxonomy (INV-2)
- HTTP 401/402/403 or paywall markers → `paywall_or_blocked`
- timeout (10s) → `timeout`
- DNS failure, refused or failed connection, redirect loop → `unreachable`
- any other HTTP error status (404, 500, ...) → `http_error`
- <500 chars extractable text on 200 OK → `js_required`
- content-type not text/html → `not_html`
On any of these: run provenance domain-level signals + registry lookup anyway;
`checks_completed` lists what ran; synthetic check reports `skipped_no_content`.
While no detection model is integrated (before §4.2 is implemented), synthetic
reports `not_integrated` for analyzable text; the UI shows the reason verbatim.
A runtime model-load failure also reports `not_integrated`, with an explanatory
`detail` naming the failure (approved 2026-10-17; no dedicated status).

## §4. Check modules

### §4.1 Provenance
- Domain age: RDAP first (https://rdap.org bootstrap), WHOIS fallback, cache
  results (sqlite or dict+TTL). Failure → `unavailable`, never fake.
- HTTPS: scheme check + certificate validity via the request itself.
- Publisher metadata: presence of JSON-LD Organization/NewsArticle, og:site_name,
  meta author. Report found/not-found per signal.
- Publication date: `htmldate` library; report method and confidence.

### §4.2 Synthetic detection (INV-3, INV-5)
- Model: Hello-SimpleAI/chatgpt-detector-roberta (RoBERTa-base fine-tuned on
  the HC3 human/ChatGPT corpus). Selected in the task 2.1 spike, 2026-09-19,
  on a 10-item smoke set (5 verified pre-2022 human texts, 5 LLM-generated):
  perfect ranking separation (AUC 1.0), zero false positives on the human
  texts, ~280 ms per 512-token section on CPU. The openai-detector class model
  originally suggested here scored at chance (AUC 0.48) against modern LLM
  text and was rejected. The runner-up (fakespot-ai roberta-base v1) ranked
  equally well but assigned 0.92-0.99 AI probability to two of five verified
  human texts; that false-positive profile is the failure mode this charter
  can least accept. Known limits, to be documented publicly by the benchmark:
  smoke n=10, one generator family, encyclopedic human register, and weak
  calibration at a 0.5 threshold (the product reports the probability itself,
  never a thresholded verdict). Runs via local transformers, CPU adequate;
  serverless HF inference for community models is not dependable, so deployed
  inference is local to the backend (sizing decided in Task 6.1).
- Re-spike 2026-10-17 (task 2.1b), decision 2026-10-21: the current model, a
  COLING 2025 competition-winning DeBERTa detector, and a scaled-down
  zero-shot Binoculars were evaluated on 10 verified pre-2022 human texts and
  10 current-generation LLM texts. All three failed to rank frontier-model
  prose as machine-written (AUC 0.58 / 0.04 / 0.18); the competition winner
  scored every LLM text as confidently human. The current model is retained
  as the only candidate meeting the zero-false-positive-on-human-text bar.
  Full method and per-item numbers are in the 2.1b spike record (2026-10-17)
  in the project's working notes; the public benchmark (section 7) must
  include current-generation multi-model texts so this limitation is
  published with numbers.
- Chunking: split extracted text into ~400-token sections. Analyze up to the
  first 5 sections plus the final section (captures intro + body + close).
  Report per-section scores and the mean. UI states which sections were
  analyzed.
- Text under 100 words: return `text_too_short` with honest note that detection
  is unreliable at this length.

### §4.3 Registry lookup
Exact-match on registrable domain (use tldextract). Subdomains resolve to the
registered domain. Miss → `unrated` (INV-6).

## §5. Registry schema (registry/registry.json)

```json
{
  "version": "1.1.0",
  "updated": "2026-10-17",
  "criteria_doc": "METHODOLOGY.md",
  "entries": [
    {
      "domain": "reuters.com",
      "publisher_name": "Reuters",
      "trust_tier": "high",
      "source_type": "news_wire | newspaper | broadcaster | magazine | digital_native | institutional | other",
      "editorial_standards_public": true,
      "editorial_standards_url": "https://...",
      "corrections_policy_public": true,
      "corrections_policy_url": "https://...",
      "ownership_transparent": true,
      "ownership_url": "https://...",
      "country": "GB",
      "last_verified": "2026-10-17",
      "notes": ""
    }
  ]
}
```
- `trust_tier` derives mechanically from the three booleans:
  3 true = high, 2 = medium, otherwise low. Rule lives in METHODOLOGY.md and
  in `validate_registry.py`, which CI-checks every entry (schema + tier math).
- **Evidence rule (added 2026-10-17):** the three `*_url` fields are optional
  strings, but any boolean set to `true` MUST carry its matching URL, pointing
  at the public page that evidences it. `validate_registry.py` fails the build
  on a `true` without evidence. A `false` needs no URL. `last_verified` is the
  date a human last checked the three pages.
- Entries graded before the evidence rule (the original 21, desk-graded) are
  the verification backlog; they may fail validation until re-checked. The
  registry count stated in the claims register is the number of entries that
  pass validation on demo day, nothing more.
- `registry/assist_verify.py` fetches likely evidence pages for a candidate
  domain and outputs candidate URLs for a human to confirm. It never sets a
  boolean.
- Target 100+ entries. Booleans only, no essays. `notes` optional and short.

## §6. Mode 2 — Agent demo (INV-1 critical)

### Source gathering (decided 2026-10-17: seed list plus bring-your-own)
Two gathering modes, both returning the same structure and both running the
full Mode 1 analysis per source:
- **Seed topics**: `agent/seed_topics.json` defines 4 demo topics, each with
  4–5 URLs chosen to exercise distinct states: at least one fetchable
  registry-rated outlet, one bot-blocked outlet, one unrated domain, and one
  AI-generated article served by this backend at `/demo/articles/<slug>`
  (400–600 words, generated with an LLM, marked synthetic in an HTML comment).
  The seed list is disclosed to evaluators as curated.
- **Bring your own URLs**: the user supplies 3–5 URLs as the source set.
  Fewer than 3 or more than 5 is rejected with a plain-language error.

### Flow
1. `POST /api/agent/ask` `{question, topic_id | urls[]}` → backend gathers the
   candidate sources, fetches each, runs the full Mode 1 analysis on each,
   stores `{source_id → {url, extracted_text, trust_summary, approved: null}}`
   in the server-side session store under a new `session_id`. Returns
   summaries WITHOUT any answer.
2. Frontend renders each source's TrustSummaryCard with Approve / Reject.
3. `POST /api/agent/approve` `{session_id, source_id, approved: bool}` updates
   the server-side store. The store is the ONLY record of approval.
4. `POST /api/agent/answer` `{session_id}` → backend assembles the LLM prompt
   exclusively from store entries where approved == true. Unapproved text is
   never placed in any LLM payload (INV-1). If zero approved sources: the agent
   answers "I have no approved sources to answer from" — it does NOT fall back
   to model general knowledge silently; it says so explicitly.
5. Answer displays with per-source attribution and each cited source's tier.

### Session audit log (approved 2026-10-17)
The server-side session store records, with timestamps: the question, each
gathered source (URL, extracted-text length, full Trust Summary), each approve
or reject decision, the answer request, and the source IDs used to build the
answer. `GET /api/agent/export/{session_id}` returns the full record as JSON.
No cross-session persistence; the record lives for the session and is
exportable by the user.

### Bypass hardening checklist (evaluators will try all of these)
- Calling /answer before any approval → refuses, no LLM call with source text.
- Tampering source_id or posting text directly to /answer → endpoint accepts
  only session_id; there is no parameter that carries text.
- Prompt injection inside a rejected page's content → irrelevant: that text
  never reaches the model.
- The user's question itself is the only user-controlled text in the payload;
  system prompt instructs answering strictly from provided source blocks.
- Dev mode logs the exact LLM request body so absence of unapproved text can
  be shown on screen.

LLM: Anthropic API, max_tokens modest, sampling left at API defaults (the
current Messages API exposes no temperature parameter). Exact model id is pinned
in backend config, chosen at implementation time.

## §7. Benchmark (Task 5)

- 20 verified human-written articles (established outlets, pre-2022 preferred
  to avoid LLM-era ambiguity) + 20 articles generated by us with 2–3 current
  LLMs across the same topics. Ground truth certain by construction.
- `run_benchmark.py` runs the synthetic check on all 40, outputs
  `results.json` + a markdown report: per-item scores, confusion at various
  thresholds, false positives/negatives explicitly listed. Published as-is,
  including failures.

## §8. Deployment

- Inference is local to the backend (see §4.2); the model needs roughly 2 GB
  of memory and must load at startup, not on first request.
- Backend: Render or Railway, a plan with at least 2 GB RAM (~$25/mo),
  Docker image, `/health` endpoint, model warmed at boot. Sizing confirmed in
  Task 6.1.
- Frontend: Vercel/Netlify free tier.
- A free uptime pinger hits `/health` every 10 minutes so the service never
  sleeps before a demo.
- One-command local dev: `make dev` runs both, and is the demo fallback.
- A stable public URL must exist before the verification meeting.

## §9. Out of scope (do not build)

Video/image/deepfake detection. C2PA. Browser extension. Multilingual.
Accuracy-percentage promises. Auto-scoring of unknown sources. Accounts/auth
beyond a session id. Payments. Mobile apps.
