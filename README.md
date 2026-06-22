# cognition-devin-autopilot

A small, Dockerized, event-driven automation that turns a **GitHub issue** into a
**Devin remediation session**, tracks that session through to a **pull request**, and
produces an **engineering-leadership report** — all behind a tiny FastAPI service.

Built as a Cognition take-home against the Apache Superset fork
[`RogueTex/superset`](https://github.com/RogueTex/superset).

---

## The take-home problem

> Build an event-driven automation, using the Devin API, applied to Apache Superset.

The scenario: a bug or chore lands as a GitHub issue. Instead of a human picking it up,
the issue **triggers** an autonomous remediation: a Devin session is created with a
work-order prompt, Devin opens a PR, and engineering leadership gets a single report
showing what was detected, what Devin did, and what still needs a human.

This repo implements that loop and ties together the artifacts we already have:

| Artifact | Link |
|---|---|
| Superset fork | https://github.com/RogueTex/superset |
| Triggering issue **#2** | https://github.com/RogueTex/superset/issues/2 |
| Devin-created PR **#4** | https://github.com/RogueTex/superset/pull/4 |
| Devin session | https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744 |

## Why this is event-driven

The unit of work is an **event** — a GitHub issue — not a schedule or a manual script.
`POST /simulate` is the event handler: given an `issue_number`, it fetches the issue,
synthesizes a work order, and dispatches a Devin session to remediate it. In a real
deployment you would wire this handler to a GitHub **webhook** (`issues.opened` /
`issues.labeled`); here we expose it as an HTTP endpoint so it is trivial to trigger,
test, and demo. Each event produces a durable **run** record that can be polled as the
session and PR progress.

## Why Devin is the core primitive

Devin is not a helper in this design — it *is* the remediation worker. The automation's
only job is orchestration: translate an issue into a precise work order, hand it to
Devin via the v3 API (`POST /v3/organizations/{org_id}/sessions`), and then observe the
results (session status, structured output, and the PR Devin opens). The "business
logic" of actually understanding and fixing the code is delegated entirely to Devin.
That makes the system small, general, and able to handle issues it has never seen.

---

## Architecture

```
GitHub issue  ──▶  POST /simulate  ──▶  build work-order prompt  ──▶  Devin v3 session
   (event)              │                  (app/prompts.py)            (app/devin_client.py)
                        ▼                                                      │
                   SQLite run record  ◀───────── poll status / PR ◀───────────┘
                   (app/store.py)        (POST /runs/{id}/poll)
                        │
                        ▼
                   GET /report  ──▶  Markdown leadership report (app/report.py)
```

### Two modes

- **real mode** (`POST /simulate`) — fetches the issue from GitHub and creates a *new*
  Devin session. Requires `DEVIN_API_KEY`, `DEVIN_ORG_ID`, and `GITHUB_TOKEN`.
- **adopt mode** (`POST /adopt`) — records an *already-created* session + PR without
  calling the Devin API. This makes the demo reproducible immediately using issue #2,
  PR #4, and the existing session — no credentials required.

### Layout

```
app/
  main.py           FastAPI app + endpoints
  config.py         env-var configuration
  devin_client.py   Devin v3 API client (httpx)
  github_client.py  GitHub REST client (httpx)
  store.py          SQLite persistence for runs
  prompts.py        work-order prompt + structured-output schema
  report.py         Markdown leadership report renderer
tests/
  test_prompts.py   prompt construction
  test_store.py     persistence + idempotency
  test_api.py       adopt / report / simulate-idempotency (FastAPI TestClient)
Dockerfile  docker-compose.yml  Makefile  .env.example  .gitignore
```

---

## Setup

Requires Python 3.11+ (for local runs) or Docker.

```bash
git clone https://github.com/RogueTex/Cog_TH.git
cd Cog_TH
make env          # creates .env from .env.example
```

### Environment variables

| Var | Required for | Description |
|---|---|---|
| `DEVIN_API_KEY` | real mode | Devin API key (Bearer token) |
| `DEVIN_ORG_ID` | real mode | Org id, prefixed `org-` |
| `GITHUB_TOKEN` | real mode | GitHub PAT with issue/PR read access |
| `GITHUB_REPO` | both | Target repo, defaults to `RogueTex/superset` |
| `MAX_ACU_LIMIT` | optional | ACU ceiling per real session (default `10`) |
| `DB_PATH`, `DEVIN_API_BASE`, `GITHUB_API_BASE`, `REQUEST_TIMEOUT` | optional | overrides |

> Adopt mode needs **none** of the credentials — it is the zero-setup demo path.

---

## Run with Docker

```bash
docker compose up --build
# API on http://localhost:8000  (interactive docs at /docs)
```

`docker compose up` starts the API even without a `.env` (the env file is optional).
SQLite state is persisted in the `autopilot-data` volume.

### Run locally (no Docker)

```bash
make dev      # install runtime + dev deps
make run      # uvicorn with autoreload on :8000
make test     # run the test suite
make lint     # ruff
```

---

## Demo: adopt mode (works immediately)

This records the **existing** issue #2 + PR #4 + Devin session and renders the report.
No credentials required.

```bash
# one-shot helper (adopt + print report)
make demo

# or by hand:
curl -X POST http://localhost:8000/adopt \
  -H 'Content-Type: application/json' \
  -d '{
        "issue_number": 2,
        "devin_session_url": "https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744",
        "pull_request_url": "https://github.com/RogueTex/superset/pull/4",
        "issue_title": "Remove dockerize-init image from docker-compose"
      }'

curl http://localhost:8000/runs                 # list runs
curl http://localhost:8000/runs/<run_id>        # one run
curl -X POST http://localhost:8000/runs/<run_id>/poll   # refresh status + PR state
curl http://localhost:8000/report               # Markdown leadership report
```

## Real mode: create a new Devin session from an issue

With `DEVIN_API_KEY`, `DEVIN_ORG_ID`, and `GITHUB_TOKEN` set:

```bash
curl -X POST http://localhost:8000/simulate \
  -H 'Content-Type: application/json' \
  -d '{"issue_number": 2}'
```

This fetches issue #2 from GitHub, builds the work-order prompt, and creates a Devin v3
session tagged `cognition-takehome`, `superset`, `devin-remediate`, capped at
`MAX_ACU_LIMIT` ACUs, asking Devin to open a PR and return structured output.

**Idempotency:** calling `/simulate` again for the same issue returns the existing run
instead of spawning a duplicate session. Pass `{"force": true}` to override.

---

## API reference

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/simulate` | real mode: create a Devin session from an issue (`issue_number`, optional `repo`, `force`) |
| `POST` | `/adopt` | record an existing issue + session + PR (`issue_number`, `devin_session_url`, `pull_request_url`) |
| `GET` | `/runs` | list all runs |
| `GET` | `/runs/{run_id}` | fetch one run |
| `POST` | `/runs/{run_id}/poll` | refresh Devin session status + PR metadata |
| `GET` | `/report` | Markdown leadership report (`/report.json` for JSON) |
| `GET` | `/` , `/health` | service info / liveness |

---

## Sample leadership report

```markdown
# Devin Autopilot — Remediation Report

_Repository:_ `RogueTex/superset`

## Executive summary

- **Issues detected / runs tracked:** 1
- **Devin sessions (created + adopted):** 1 (0 real, 1 adopted)
- **Pull requests produced:** 1
- **Runs still open / in progress:** 1

## Runs

| Issue | Mode | Devin session | Pull request | PR state | Status |
|---|---|---|---|---|---|
| #2 | adopt | session | PR | — | adopted |

## Details

### Issue #2: Remove dockerize-init image from docker-compose

- **Run id:** `0be11ec0364040f5949f1d8ee8cc9e2d`
- **Mode:** adopt
- **Issue:** https://github.com/RogueTex/superset/issues/2
- **Devin session:** https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744
- **Pull request:** https://github.com/RogueTex/superset/pull/4 (state: —)
- **Status:** adopted

## Remaining risks / next human action

- Issue #2: PR recorded (state unknown) — run poll, then review & merge.
```

After a `POST /runs/{id}/poll` (with credentials), the PR state and Devin structured
output (summary, root cause, validation, remaining risks) are filled in automatically.

---

## Tests

```bash
make test     # pytest: prompt construction, store idempotency, adopt/report/simulate
make lint     # ruff
```

Covered: deterministic prompt construction, structured-output schema, store CRUD +
idempotent run lookup, adopt flow, report rendering, and `/simulate` idempotency
(no duplicate sessions unless `force=true`).

---

## Loom talking points

**What** — An event-driven automation: a GitHub issue triggers a Devin session that
remediates it and opens a PR; the service tracks every run and emits a leadership report.

**How** — FastAPI + SQLite, packaged with Docker. `POST /simulate` fetches the issue,
builds a work-order prompt (`app/prompts.py`), and creates a Devin v3 session
(`app/devin_client.py`). Runs persist in SQLite (`app/store.py`); `/runs/{id}/poll`
refreshes status from Devin and PR state from GitHub; `/report` renders Markdown for
leadership. `POST /adopt` records the existing issue #2 / PR #4 / session so the demo
works with zero credentials.

**Why Devin** — Devin is the core primitive that does the actual engineering. The
automation is thin orchestration: turn an event into a precise work order, dispatch it,
and observe the PR. The hard part — understanding and fixing unfamiliar code — is
delegated to Devin, which keeps the system small and general.

**Next steps** — Wire `/simulate` to a real GitHub `issues` webhook; add label-based
routing and auto-`/poll` on a timer; post the report back to the issue/PR and to Slack;
expand structured output into pass/fail gates that decide whether to request human review.

---

## Notes & constraints

- This project **does not modify** `RogueTex/superset` and **does not merge** PR #4.
- No real secrets are committed; `.env` is git-ignored and only `.env.example` ships.
- The Devin v3 API has no server-side idempotency flag, so idempotency is enforced
  locally in the store (one real session per issue unless `force=true`).
