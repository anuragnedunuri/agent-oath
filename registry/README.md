# Source registry

The versioned source registry behind the credibility check.

- `registry.json` - the registry itself: versioned, dated, currently 21 seed
  entries across wires, newspapers, broadcasters, magazines, and digital
  native outlets
- `METHODOLOGY.md` - the public criteria every entry is scored against, the
  mechanical tier rule, and how to challenge an entry
- `validate_registry.py` - schema, tier math, and hygiene validation; run by
  continuous integration on every push

Sources not in the registry are reported as unrated with an honest
explanation. An absent rating is not a negative rating (INV-6), and the
backend refuses to serve any entry whose tier contradicts its criteria.
