"""FastAPI application wiring the GitHub -> Devin remediation automation.

Endpoints:
    POST /simulate          create a real Devin session from a GitHub issue
    POST /adopt             record an existing issue + session + PR (demo mode)
    GET  /runs              list all runs
    GET  /runs/{run_id}     fetch one run
    POST /runs/{run_id}/poll  refresh status from Devin + GitHub
    GET  /summary           Markdown status summary
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import __version__
from app.config import DEFAULT_TAGS, Settings, get_settings
from app.devin_client import DevinClient, DevinError, extract_session_id
from app.github_client import GitHubClient, GitHubError, parse_pull_request_url
from app.prompts import (
    STRUCTURED_OUTPUT_SCHEMA,
    build_session_title,
    build_work_order,
)
from app.report import build_report
from app.store import Store

app = FastAPI(
    title="devin-issue-runner",
    version=__version__,
    description="Workflow service: GitHub issue -> Devin remediation session -> status summary.",
)


# --- dependency wiring (module-level singletons; overridable in tests) ------

def _build_store() -> Store:
    return Store(get_settings().db_path)


store: Store = _build_store()


def _settings() -> Settings:
    return get_settings()


def _github(settings: Settings) -> GitHubClient:
    return GitHubClient(
        settings.github_token,
        api_base=settings.github_api_base,
        timeout=settings.request_timeout,
    )


def _devin(settings: Settings) -> DevinClient:
    return DevinClient(
        settings.devin_api_key,
        settings.devin_org_id,
        api_base=settings.devin_api_base,
        timeout=settings.request_timeout,
    )


# --- request models --------------------------------------------------------

class SimulateRequest(BaseModel):
    issue_number: int = Field(..., ge=1)
    repo: str | None = None
    force: bool = False


class AdoptRequest(BaseModel):
    issue_number: int = Field(..., ge=1)
    devin_session_url: str
    pull_request_url: str | None = None
    repo: str | None = None
    issue_url: str | None = None
    issue_title: str | None = None


# --- helpers ---------------------------------------------------------------

def _session_url(session_id: str | None, raw: str | None) -> str | None:
    """Prefer the API-provided URL, else reconstruct the web app URL."""
    if raw and raw.startswith("http"):
        return raw
    if session_id:
        bare = session_id.replace("devin-", "")
        return f"https://app.devin.ai/sessions/{bare}"
    return None


def _first_pr_url(session: dict[str, Any]) -> tuple[str | None, str | None]:
    prs = session.get("pull_requests") or []
    if not prs:
        return None, None
    pr = prs[0]
    return pr.get("pr_url") or pr.get("url"), pr.get("pr_state") or pr.get("state")


# --- endpoints -------------------------------------------------------------

@app.get("/")
def root() -> dict[str, Any]:
    settings = _settings()
    return {
        "service": "devin-issue-runner",
        "version": __version__,
        "repo": settings.github_repo,
        "devin_configured": settings.devin_configured,
        "github_configured": settings.github_configured,
        "endpoints": [
            "/simulate", "/adopt", "/runs", "/runs/{id}",
            "/runs/{id}/poll", "/summary",
        ],
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/simulate")
def simulate(req: SimulateRequest) -> dict[str, Any]:
    """Real mode: fetch a GitHub issue and create a Devin remediation session."""
    settings = _settings()
    repo = req.repo or settings.github_repo

    missing = settings.missing_for_real_mode()
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Real mode requires {', '.join(missing)}. "
                "Set them in .env, or use POST /adopt for the demo path."
            ),
        )

    # Idempotency: reuse an existing real run for this issue unless forced.
    if not req.force:
        existing = store.find_active_real_run(repo, req.issue_number)
        if existing:
            return {
                "run": existing,
                "idempotent": True,
                "detail": "Existing real run reused. Pass force=true to create a new session.",
            }

    gh = _github(settings)
    try:
        issue = gh.get_issue(repo, req.issue_number)
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    issue_url = issue.get("html_url", f"https://github.com/{repo}/issues/{req.issue_number}")
    issue_title = issue.get("title") or f"Issue #{req.issue_number}"

    prompt = build_work_order(
        repo=repo,
        issue_number=req.issue_number,
        issue_title=issue_title,
        issue_body=issue.get("body"),
        issue_url=issue_url,
    )

    devin = _devin(settings)
    try:
        session = devin.create_session(
            prompt=prompt,
            title=build_session_title(req.issue_number, issue_title),
            tags=list(DEFAULT_TAGS),
            max_acu_limit=settings.max_acu_limit,
            structured_output_schema=STRUCTURED_OUTPUT_SCHEMA,
        )
    except DevinError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session_id = session.get("session_id")
    pr_url, pr_state = _first_pr_url(session)
    run = store.create_run(
        mode="real",
        repo=repo,
        issue_number=req.issue_number,
        issue_url=issue_url,
        issue_title=issue_title,
        devin_session_id=session_id,
        devin_session_url=_session_url(session_id, session.get("url")),
        pull_request_url=pr_url,
        pr_state=pr_state,
        status=session.get("status", "new"),
        detail="Created via real mode (POST /simulate).",
    )
    return {"run": run, "idempotent": False}


@app.post("/adopt")
def adopt(req: AdoptRequest) -> dict[str, Any]:
    """Adopt mode: record an existing issue + Devin session + PR without API calls."""
    settings = _settings()
    repo = req.repo or settings.github_repo

    session_id = extract_session_id(req.devin_session_url)
    issue_url = req.issue_url or f"https://github.com/{repo}/issues/{req.issue_number}"

    pr_state = None
    if req.pull_request_url and not parse_pull_request_url(req.pull_request_url):
        raise HTTPException(
            status_code=400,
            detail=f"pull_request_url is not a valid GitHub PR URL: {req.pull_request_url}",
        )

    run = store.create_run(
        mode="adopt",
        repo=repo,
        issue_number=req.issue_number,
        issue_url=issue_url,
        issue_title=req.issue_title,
        devin_session_id=session_id,
        devin_session_url=_session_url(session_id, req.devin_session_url),
        pull_request_url=req.pull_request_url,
        pr_state=pr_state,
        status="adopted",
        detail="Recorded via adopt mode (POST /adopt).",
    )
    return {"run": run}


@app.get("/runs")
def list_runs() -> dict[str, Any]:
    runs = store.list_runs()
    return {"count": len(runs), "runs": runs}


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run


@app.post("/runs/{run_id}/poll")
def poll_run(run_id: str) -> dict[str, Any]:
    """Refresh a run: pull session status from Devin and PR metadata from GitHub."""
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    settings = _settings()
    updates: dict[str, Any] = {}
    notes: list[str] = []

    # 1) Devin session status (best-effort; needs credentials).
    if run.get("devin_session_id") and settings.devin_configured:
        devin = _devin(settings)
        try:
            session = devin.get_session(run["devin_session_id"])
            updates["status"] = session.get("status", run.get("status"))
            structured = session.get("structured_output")
            if structured:
                updates["structured_output"] = structured
            pr_url, pr_state = _first_pr_url(session)
            if pr_url and not run.get("pull_request_url"):
                updates["pull_request_url"] = pr_url
            if pr_state:
                updates["pr_state"] = pr_state
        except DevinError as exc:
            notes.append(f"Devin poll failed: {exc}")
    elif run.get("devin_session_id"):
        notes.append("Devin not configured; skipped session poll.")

    # 2) PR metadata from GitHub (works for adopt mode too).
    pr_url = updates.get("pull_request_url") or run.get("pull_request_url")
    if pr_url:
        gh = _github(settings)
        try:
            pr = gh.get_pull_request_by_url(pr_url)
            updates["pr_state"] = pr.get("state", updates.get("pr_state"))
            merged = pr.get("merged")
            if merged:
                updates["pr_state"] = "merged"
        except GitHubError as exc:
            notes.append(f"GitHub PR poll failed: {exc}")

    if notes:
        updates["detail"] = "; ".join(notes)

    updated = store.update_run(run_id, **updates) if updates else run
    return {"run": updated, "notes": notes}


@app.get("/summary", response_class=PlainTextResponse)
@app.get("/report", response_class=PlainTextResponse, include_in_schema=False)
def summary() -> str:
    """Markdown status summary of all runs."""
    settings = _settings()
    runs = store.list_runs()
    return build_report(runs, repo=settings.github_repo)


@app.get("/summary.json")
@app.get("/report.json", include_in_schema=False)
def summary_json() -> dict[str, Any]:
    """JSON-wrapped status summary."""
    settings = _settings()
    runs = store.list_runs()
    return {"markdown": build_report(runs, repo=settings.github_repo)}
