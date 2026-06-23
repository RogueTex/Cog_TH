# Devin Issue Runner for Superset

This is a small workflow service that turns a GitHub issue into a Devin remediation run.

The concrete example is my Superset fork. Issue #2 tracks a Helm chart cleanup: removing the old `dockerize` init image from startup waits. Devin opened PR #4 for that change. This service is the control layer around that flow: it can start a Devin session from an issue, store the run, poll session/PR status, and show a short status summary.

I kept the service small on purpose: FastAPI, SQLite, Docker, and direct GitHub/Devin API calls.

---

## Example run

| Artifact | Link |
|---|---|
| Superset fork | https://github.com/RogueTex/superset |
| Issue #2 | https://github.com/RogueTex/superset/issues/2 |
| Devin PR #4 | https://github.com/RogueTex/superset/pull/4 |
| Devin session | https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744 |

---

## How it works

`POST /simulate` is the entry point: given an `issue_number`, it fetches the issue from GitHub, builds a work-order prompt, and dispatches a Devin session to fix it. In a real deployment you'd wire this to a GitHub webhook (`issues.opened` / `issues.labeled`); here it's an HTTP endpoint for easy triggering and testing. Each call produces a durable **run** record that tracks the session and PR as they progress.

### Two modes

- **Real mode** (`POST /simulate`) — fetches the issue from GitHub and creates a *new* Devin session. Requires `DEVIN_API_KEY`, `DEVIN_ORG_ID`, and `GITHUB_TOKEN`.
- **Adopt mode** (`POST /adopt`) — records an *already-created* session + PR without calling the Devin API. This makes the demo reproducible immediately using issue #2, PR #4, and the existing session — no credentials required.

### Architecture

```
GitHub issue  -->  POST /simulate  -->  build work-order prompt  -->  Devin v3 session
   (event)              |                  (app/prompts.py)            (app/devin_client.py)
                        v                                                      |
                   SQLite run record  <--------- poll status / PR <-----------'
                   (app/store.py)        (POST /runs/{id}/poll)
                        |
                        v
                   GET /summary  -->  Markdown status summary (app/report.py)
```

### Layout

```
app/
  main.py           FastAPI app + endpoints
  config.py         env-var configuration
  devin_client.py   Devin v3 API client (httpx)
  github_client.py  GitHub REST client (httpx)
  store.py          SQLite persistence for runs
  prompts.py        work-order prompt + structured-output schema
  report.py         Markdown status summary renderer
tests/
  test_prompts.py   prompt construction
  test_store.py     persistence + idempotency
  test_api.py       adopt / summary / simulate-idempotency (FastAPI TestClient)
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

SQLite state is persisted in the `autopilot-data` volume.

### Run locally (no Docker)

```bash
make dev      # install runtime + dev deps
make run      # uvicorn with autoreload on :8000
make test     # run the test suite
make lint     # ruff
```

---

## Walkthrough

1. **Start the service**
   ```bash
   docker compose up --build
   ```

2. **Check health**
   ```bash
   curl http://localhost:8000/health
   ```

3. **Adopt the existing run** (issue #2 / PR #4 / Devin session)
   ```bash
   curl -X POST http://localhost:8000/adopt \
     -H 'Content-Type: application/json' \
     -d '{
           "issue_number": 2,
           "devin_session_url": "https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744",
           "pull_request_url": "https://github.com/RogueTex/superset/pull/4",
           "issue_title": "Remove dockerize init image from Helm startup waits"
         }'
   ```

4. **View runs**
   ```bash
   curl http://localhost:8000/runs
   ```

5. **View the status summary**
   ```bash
   curl http://localhost:8000/summary
   ```

6. **Create a new Devin session** (requires credentials in `.env`)
   ```bash
   curl -X POST http://localhost:8000/simulate \
     -H 'Content-Type: application/json' \
     -d '{"issue_number": 2}'
   ```
   `/simulate` creates a real Devin session when `DEVIN_API_KEY`, `DEVIN_ORG_ID`, and `GITHUB_TOKEN` are set.

---

## API reference

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/simulate` | Create a Devin session from an issue (`issue_number`, optional `repo`, `force`) |
| `POST` | `/adopt` | Record an existing issue + session + PR (`issue_number`, `devin_session_url`, `pull_request_url`) |
| `GET` | `/runs` | List all runs |
| `GET` | `/runs/{run_id}` | Fetch one run |
| `POST` | `/runs/{run_id}/poll` | Refresh Devin session status + PR metadata |
| `GET` | `/summary` | Markdown status summary (alias: `/report`) |
| `GET` | `/summary.json` | JSON-wrapped status summary (alias: `/report.json`) |
| `GET` | `/` , `/health` | Service info / liveness |

---

## Tests

```bash
make test     # pytest: prompt construction, store idempotency, adopt/summary/simulate
make lint     # ruff
```

---

## Notes

- This project **does not modify** `RogueTex/superset` and **does not merge** PR #4.
- No real secrets are committed; `.env` is git-ignored and only `.env.example` ships.
- The Devin v3 API has no server-side idempotency flag, so idempotency is enforced locally in the store (one real session per issue unless `force=true`).
