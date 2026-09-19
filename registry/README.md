# Source registry

This directory will hold:

- `registry.json` - the versioned source registry (schema in SPEC.md section 5)
- `METHODOLOGY.md` - the public scoring criteria
- `validate_registry.py` - schema and trust-tier math validation, run in CI

Built in task 3.1. Empty until then by design: an unknown source returns
`unrated`, never an interpolated score (INV-6).
