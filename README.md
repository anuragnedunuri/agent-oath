# Agent Oath: Trust Verification Layer

Prototype for the Global Trust Challenge (Prototyping Phase, Entry 117).

- **Mode 1, Inspector**: submit a URL or paste text, get a plain-language Trust
  Summary built from a synthetic-text check, a provenance check, and a source
  registry lookup.
- **Mode 2, Agent demo**: a research assistant that cannot use a source in its
  answer until you have seen that source's Trust Summary and approved it.

The charter this prototype is verified against lives in [SPEC.md](SPEC.md)
section 1.

## Requirements

- Python 3.11 or newer
- Node.js 20 or newer

## Setup

```
make install
```

Then copy `.env.example` to `.env`. Every value in it can stay blank for now;
the backend starts without any secrets.

## Run it

```
make dev
```

- Backend: http://127.0.0.1:8000 (health check at `/health`, API docs at `/docs`)
- Frontend: http://localhost:5173

The Vite dev server proxies `/api` and `/health` to the backend, so the browser
talks to a single origin during development.

## Other commands

| Command       | What it does                                        |
| ------------- | --------------------------------------------------- |
| `make test`   | pytest suite                                         |
| `make lint`   | ruff check, ruff format check, prettier check        |
| `make format` | ruff format, ruff autofix, prettier write            |
| `make build`  | typecheck and production-build the frontend          |

On Windows, install GNU make once with `winget install ezwinports.make` and
open a new terminal. All make targets then work from any shell.

CI runs the backend lint and test jobs and the frontend format and build jobs on
every push and pull request. See
[.github/workflows/ci.yml](.github/workflows/ci.yml).

## Status

Through task 1.3: scaffold, locked API contract, URL fetcher with its honest
failure taxonomy, provenance checks, and a live /api/analyze wired to the Inspector
page. AI-text detection, the seeded registry, and the benchmark are still to come,
and the summary says so honestly wherever they would appear.
