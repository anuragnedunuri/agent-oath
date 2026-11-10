# Agent Oath: Trust Verification Layer

A prototype for the Global Trust Challenge (Prototyping Phase, Entry 117)
that treats honesty about uncertainty as the product, not the disclaimer.

Live demo: https://agent-oath.vercel.app

Two modes:

- **Mode 1, Inspector.** Submit a URL or paste text. The system runs three
  checks and renders a plain-language Trust Summary in which every number
  can be expanded to the raw signal that produced it.
- **Mode 2, Agent demo.** A research assistant that cannot use a source in
  its answer until you have seen that source's Trust Summary and approved
  it. With nothing approved, it says so and refuses; it never falls back to
  its own general knowledge silently.

## The three checks

1. **Retrieval and AI-text analysis.** Pages are fetched with an honest
   eight-state failure taxonomy (a paywall, a timeout, a dead domain, and a
   browser-only page are different facts and are reported as such). Text of
   100 words or more is scored by an open-source detection model in roughly
   400-token sections; the result is always a probability with the model's
   name, revision, per-section scores, and a fixed uncertainty disclaimer.
   Never a verdict.
2. **Provenance.** Domain age via RDAP with a WHOIS fallback (unavailable is
   reported, never estimated), HTTPS, publisher metadata, and publication
   date, each exposing its raw value and interpretation.
3. **Source registry.** A versioned, human-verified registry
   ([registry/registry.json](registry/registry.json)). Each entry records
   three published-accountability facts, every "yes" must carry a public
   evidence URL, and the trust tier derives mechanically from the three
   booleans. Unknown domains return unrated with an explanation: an absent
   rating is not a negative rating. Criteria and the correction process are
   public in [registry/METHODOLOGY.md](registry/METHODOLOGY.md).

## The approval gate (Mode 2)

Source text lives server-side only. The answer endpoint accepts a session id
and nothing else; requests carrying any other field are rejected before a
handler runs. The LLM request body is assembled in exactly one place,
exclusively from sources the user approved: rejected text is not filtered
out of the payload, it is never put in. A 14-test bypass suite exercises the
attack checklist (premature answers, forged and cross-session ids, smuggled
text, planted prompt injection), and in dev mode the literal request body is
shown on screen. Every session's event trail is exportable as JSON.

## Run it locally

Requirements: Python 3.11+, Node 20+.

```
make install
make dev
```

Backend on http://127.0.0.1:8000 (API docs at /docs), frontend on
http://localhost:5173. Copy `.env.example` to `.env`; everything may stay
blank except `ANTHROPIC_API_KEY`, which the Mode 2 answer step needs (the
rest of the system runs without it and says so honestly). On Windows,
install GNU make once with `winget install ezwinports.make`.

Tests and checks:

```
make test        # pytest suite (no test touches the live network)
make lint        # ruff + prettier
python registry/validate_registry.py   # registry schema, tier math, evidence rule
```

## Honest limitations

- **AI-text detection is weak on current-generation prose.** A documented
  re-evaluation (SPEC.md section 4.2) found every tested detector,
  including a 2025 competition winner, failing to rank current
  frontier-model text as machine-written. The shipped model is retained
  because it is the only candidate that never falsely accused a human text
  in our tests. This is why the product reports probabilities with a fixed
  disclaimer and why the benchmark will be published with its failures
  included.
- **English only.** Non-English pages and text are out of scope.
- **The registry measures published accountability, not editorial
  quality.** A low tier means the publisher's standards, corrections, or
  ownership pages could not be verified on its own site; it is not a
  judgment of truthfulness. Hosts on shared parent domains (for example
  abcnews.go.com) are unrated by design.
- **Bot-protected and paywalled pages** yield partial summaries: the
  checks that need page content are reported as skipped, the rest still
  run.
- **Audit records are session-scoped.** Nothing persists across sessions,
  by design.

## Global Trust Challenge context

This prototype is verified by external evaluators against a claims register
(C1 to C8) in which every claim maps to demonstrable behavior and a named
automated test. The technical contract lives in [SPEC.md](SPEC.md); the
registry methodology, the detector selection records, and the measured
performance notes are published as-is, including the unflattering parts.

## Performance notes

Measured, not estimated. On a development laptop, AI-text analysis runs at
roughly a quarter second per section. On the deployed 1 vCPU / 2 GB
instance it runs at roughly 2 to 3 seconds per section, so most URL
analyses complete within 30 seconds and every request is bounded by the
client-side stop, which reports what completed rather than hanging. Scores
are deterministic across both environments for the same input and model
revision. Inference threads are capped to the container's actual CPU budget
at startup.

## License

MIT, see [LICENSE](LICENSE).
