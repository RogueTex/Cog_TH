# Devin Issue Runner for Superset

Small FastAPI service that connects a GitHub issue to a Devin session, tracks the resulting pull request, and posts the outcome back to the issue.

The concrete target is my Superset fork. Issue #2 tracks a Helm chart cleanup: removing the old `dockerize` init image from startup waits. Devin opened PR #4 for that change. This repo is the control layer around that flow.

## Reference Run

| Item | Link |
|---|---|
| Superset fork | https://github.com/RogueTex/superset |
| Issue #2 | https://github.com/RogueTex/superset/issues/2 |
| Devin PR #4 | https://github.com/RogueTex/superset/pull/4 |
| Devin session | https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744 |

## What It Does

1. Watches for a GitHub issue labeled `devin-remediate`.
2. Fetches the issue and turns it into a focused Devin work order.
3. Creates a Devin v3 session with a structured output schema.
4. Polls Devin and GitHub for session, PR, and validation state.
5. Stores each run in SQLite.
6. Posts a concise status comment back to the GitHub issue.
7. Exposes `/status`, `/dashboard`, and `/summary` so the workflow is easy to inspect.

## Why Devin

The useful unit here is not "run a script when a label changes." The useful unit is handing a scoped repository problem to a coding agent that can inspect the repo, make a branch, open a PR, and return structured status.

This service keeps the orchestration boring: GitHub owns the queue, Devin owns the code change, SQLite records state, and the issue thread gets the update a reviewer actually needs.

## Architecture

```
GitHub issue label
        |
        v
GitHub Actions or webhook
        |
        v
POST /issues/{issue_number}/devin-runs
        |
        v
GitHub issue fetch -> Devin work order -> Devin v3 session
        |
        v
SQLite run record
        |
        v
POST /runs/{run_id}/poll
        |
        v
GitHub PR metadata + issue comment
        |
        v
/status, /dashboard, /summary
```

## Repo Layout

```
app/
  main.py           FastAPI routes and workflow glue
  config.py         environment configuration
  devin_client.py   Devin v3 API client
  github_client.py  GitHub issue, PR, and comment client
  store.py          SQLite persistence
  prompts.py        Devin work-order prompt and structured-output schema
  summary.py        Markdown status summary renderer
docs/
  devin-issue-runner.workflow.yml
tests/
  test_api.py
  test_prompts.py
  test_store.py
Dockerfile
docker-compose.yml
Makefile
.env.example
```

## Setup

```bash
git clone https://github.com/RogueTex/Cog_TH.git
cd Cog_TH
make env
```

Fill `.env` when you want the service to create new Devin sessions or post issue comments.

| Variable | Required for | Notes |
|---|---|---|
| `DEVIN_API_KEY` | creating/polling Devin sessions | Bearer token |
| `DEVIN_ORG_ID` | creating/polling Devin sessions | Must include the `org-` prefix |
| `GITHUB_TOKEN` | issue fetch, PR poll, issue comment | Fine-grained token with issue read/write and PR read access |
| `GITHUB_REPO` | all modes | Defaults to `RogueTex/superset` |
| `WEBHOOK_LABEL` | trigger filtering | Defaults to `devin-remediate` |
| `MAX_ACU_LIMIT` | Devin session creation | Defaults to `10` |
| `DB_PATH` | persistence | Defaults to `data/devin_issue_runner.db` locally |

## Run

```bash
docker compose up --build
```

The API is available at `http://localhost:8000`. Interactive docs are at `/docs`.

Local Python path:

```bash
make dev
make run
```

## Walkthrough

Import the known Superset run:

```bash
curl -fsS -X POST http://localhost:8000/runs/import \
  -H 'Content-Type: application/json' \
  -d '{
        "issue_number": 2,
        "devin_session_url": "https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744",
        "pull_request_url": "https://github.com/RogueTex/superset/pull/4",
        "issue_title": "Remove dockerize init image from Helm startup waits"
      }'
```

Inspect the state:

```bash
curl -fsS http://localhost:8000/status
curl -fsS http://localhost:8000/summary
open http://localhost:8000/dashboard
```

Create a new Devin session from an issue:

```bash
curl -fsS -X POST \
  'http://localhost:8000/issues/2/devin-runs?repo=RogueTex/superset'
```

Poll a run and post the issue comment:

```bash
curl -fsS -X POST 'http://localhost:8000/runs/<run_id>/poll?post_comment=true'
```

## GitHub Trigger

The workflow template in `docs/devin-issue-runner.workflow.yml` runs on:

- an issue opened with label `devin-remediate`
- an issue later labeled `devin-remediate`
- manual dispatch with an issue number

Copy it to `.github/workflows/devin-issue-runner.yml` in the target repo, such
as `RogueTex/superset`, when you want issue label events to route that same
repo. From this repo, use manual dispatch or the HTTP endpoint directly.

It starts the run, polls for a PR or terminal status, and then posts the issue
comment. The first call is:

```text
POST {RUNNER_ENDPOINT}/issues/{issue_number}/devin-runs?repo={repo}
```

Set these repository secrets before relying on the workflow:

| Secret | Purpose |
|---|---|
| `RUNNER_ENDPOINT` | Public URL for this FastAPI service |
| `RUNNER_SHARED_TOKEN` | Optional bearer token if you put the service behind an auth proxy |

For a local walkthrough, skip Actions and call the endpoint with `curl`.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/issues/{issue_number}/devin-runs` | Create a Devin session from a GitHub issue |
| `POST` | `/runs/import` | Record an existing issue, Devin session, and PR |
| `POST` | `/webhooks/github` | Accept issue events from a webhook-compatible caller |
| `GET` | `/runs` | List tracked runs |
| `GET` | `/runs/{run_id}` | Fetch one run |
| `POST` | `/runs/{run_id}/poll` | Refresh Devin and GitHub state |
| `POST` | `/runs/{run_id}/comment` | Post the current run state to the GitHub issue |
| `GET` | `/status` | JSON metrics |
| `GET` | `/dashboard` | HTML dashboard |
| `GET` | `/summary` | Markdown summary |
| `GET` | `/summary.json` | JSON-wrapped Markdown summary |
| `GET` | `/health` | Liveness check |

## Tests

```bash
make test
make lint
```

The tests cover prompt construction, SQLite persistence, import idempotency, trigger filtering, repo-bound PR URLs, status rendering, issue comment create/update, and a mocked labeled-issue loop through status and dashboard.

## Boundaries

- The service does not merge PRs.
- The service does not commit secrets.
- Imported runs let reviewers inspect the known issue #2 -> PR #4 path without spending Devin credits.
- New sessions require real `DEVIN_API_KEY`, `DEVIN_ORG_ID`, and `GITHUB_TOKEN` values.
