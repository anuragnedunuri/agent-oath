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
│   │   │   ├── search.py        # source gathering for Mode 2
│   │   │   ├── approval.py      # server-side approval store (INV-1)
│   │   │   └── answer.py        # LLM answer from APPROVED sources only
│   │   └── config.py
│   └── tests/
├── frontend/
│   └── src/
│       ├── pages/Inspector.tsx  # Mode 1
│       ├── pages/AgentDemo.tsx  # Mode 2
│       └── components/TrustSummaryCard.tsx
├── registry/
│   ├── registry.json            # the source registry (versioned)
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
  "version": "1.0.0",
  "updated": "2026-08-01",
  "criteria_doc": "METHODOLOGY.md",
  "entries": [
    {
      "domain": "reuters.com",
      "publisher_name": "Reuters",
      "trust_tier": "high",
      "source_type": "news_wire | newspaper | broadcaster | magazine | digital_native | institutional | other",
      "editorial_standards_public": true,
      "corrections_policy_public": true,
      "ownership_transparent": true,
      "country": "GB",
      "last_verified": "2026-08-01",
      "notes": ""
    }
  ]
}
```
- `trust_tier` derives mechanically from the three booleans:
  3 true = high, 2 = medium, otherwise low. Rule lives in METHODOLOGY.md and
  in `validate_registry.py`, which CI-checks every entry (schema + tier math).
- Target 100+ entries. Populate via structured research passes; booleans only,
  no essays. `notes` optional and short.

## §6. Mode 2 — Agent demo (INV-1 critical)

Flow:
1. `POST /api/agent/ask` `{question}` → backend gathers 3–5 candidate source
   URLs (search API or curated seed list per topic — Task 4.1 decides; a
   curated seed list is acceptable and demo-safe), fetches each, runs the full
   Mode 1 analysis on each, stores `{source_id → {url, extracted_text,
   trust_summary}}` in the server-side session store. Returns summaries WITHOUT
   any answer.
2. Frontend renders each source's TrustSummaryCard with Approve / Reject.
3. `POST /api/agent/approve` `{session_id, source_id, approved: bool}` updates
   the server-side store. The store is the ONLY record of approval.
4. `POST /api/agent/answer` `{session_id}` → backend assembles the LLM prompt
   exclusively from store entries where approved == true. Unapproved text is
   never placed in any LLM payload (INV-1). If zero approved sources: the agent
   answers "I have no approved sources to answer from" — it does NOT fall back
   to model general knowledge silently; it says so explicitly.
5. Answer displays with per-source attribution and each cited source's tier.

Bypass hardening checklist (evaluators will try all of these):
- Calling /answer before any approval → refuses, no LLM call with source text.
- Tampering source_id or posting text directly to /answer → endpoint accepts
  only session_id; there is no parameter that carries text.
- Prompt injection inside a rejected page's content → irrelevant: that text
  never reaches the model.
- The user's question itself is the only user-controlled text in the payload;
  system prompt instructs answering strictly from provided source blocks.

LLM: Anthropic API, temperature low, max_tokens modest. Exact model id is pinned
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

- Backend: Render/Railway paid tier (~$10–25/mo) OR backend-anywhere + HF
  Inference API for the model (avoids RAM pressure). Decision in Task 6.1.
- Frontend: Vercel/Netlify free tier.
- One-command local dev: `make dev` runs both.
- A stable public URL must exist by the GTC readiness check (see the build plan).

## §9. Out of scope (do not build)

Video/image/deepfake detection. C2PA. Browser extension. Multilingual.
Accuracy-percentage promises. Auto-scoring of unknown sources. Accounts/auth
beyond a session id. Payments. Mobile apps.
