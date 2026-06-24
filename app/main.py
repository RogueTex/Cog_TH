"""FastAPI application wiring the GitHub -> Devin remediation workflow.

Endpoints:
    POST /issues/{issue_number}/devin-runs  create a Devin session from a GitHub issue
    POST /runs/import       record an existing issue + session + PR
    POST /webhooks/github   trigger from a GitHub issue event
    GET  /runs              list all runs
    GET  /runs/{run_id}     fetch one run
    POST /runs/{run_id}/poll  refresh status from Devin + GitHub
    POST /runs/{run_id}/comment  post a status comment to the GitHub issue
    GET  /status            JSON status metrics
    GET  /dashboard         simple HTML dashboard
    GET  /summary           Markdown status summary
"""
from __future__ import annotations

import html
import time
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
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
from app.store import Store
from app.summary import build_summary

app = FastAPI(
    title="devin-issue-runner",
    version=__version__,
    description="Workflow service: GitHub issue -> Devin session -> PR/status feedback.",
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


class ImportRunRequest(BaseModel):
    issue_number: int = Field(..., ge=1)
    devin_session_url: str
    pull_request_url: str | None = None
    repo: str | None = None
    issue_url: str | None = None
    issue_title: str | None = None
    force: bool = False


class CommentRequest(BaseModel):
    force: bool = False


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


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _pr_url_for_repo(value: str | None, repo: str) -> str | None:
    if not value:
        return None
    parsed = parse_pull_request_url(value)
    if not parsed:
        return None
    parsed_repo, _ = parsed
    if parsed_repo.lower() != repo.lower():
        return None
    return value


def _structured_pr_url(structured: Any, repo: str) -> str | None:
    if not isinstance(structured, dict):
        return None
    value = structured.get("pull_request_url") or structured.get("pr_url")
    return _pr_url_for_repo(value, repo) if isinstance(value, str) else None


def _html_link(url: str | None, label: str) -> str:
    if not url:
        return "&mdash;"
    safe_url = html.escape(url, quote=True)
    safe_label = html.escape(label)
    return f'<a href="{safe_url}">{safe_label}</a>'


def _html_text(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _create_real_run(
    *,
    settings: Settings,
    repo: str,
    issue_number: int,
    force: bool,
) -> dict[str, Any]:
    missing = settings.missing_for_real_mode()
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Creating a new Devin session requires {', '.join(missing)}. "
                "Set them in .env, or use POST /runs/import for the reference run."
            ),
        )

    if not force:
        reserved, created = store.reserve_real_run(repo, issue_number)
        if not created:
            return {
                "run": reserved,
                "idempotent": True,
                "detail": "Existing run reused. Pass force=true to create a new session.",
            }
    else:
        reserved = None

    gh = _github(settings)
    try:
        issue = gh.get_issue(repo, issue_number)
    except GitHubError as exc:
        if reserved:
            store.update_run(reserved["id"], status="error", detail=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    issue_url = issue.get("html_url", f"https://github.com/{repo}/issues/{issue_number}")
    issue_title = issue.get("title") or f"Issue #{issue_number}"
    prompt = build_work_order(
        repo=repo,
        issue_number=issue_number,
        issue_title=issue_title,
        issue_body=issue.get("body"),
        issue_url=issue_url,
    )

    devin = _devin(settings)
    try:
        session = devin.create_session(
            prompt=prompt,
            title=build_session_title(issue_number, issue_title),
            tags=list(DEFAULT_TAGS),
            max_acu_limit=settings.max_acu_limit,
            structured_output_schema=STRUCTURED_OUTPUT_SCHEMA,
        )
    except DevinError as exc:
        if reserved:
            store.update_run(reserved["id"], status="error", detail=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session_id = session.get("session_id")
    session_pr_url, pr_state = _first_pr_url(session)
    pr_url = _pr_url_for_repo(session_pr_url, repo)
    if session_pr_url and not pr_url:
        pr_state = None
    run_data = dict(
        issue_url=issue_url,
        issue_title=issue_title,
        devin_session_id=session_id,
        devin_session_url=_session_url(session_id, session.get("url")),
        pull_request_url=pr_url,
        pr_state=pr_state,
        status=session.get("status", "new"),
        detail="Created from a GitHub issue.",
    )
    if reserved:
        run = store.update_run(reserved["id"], **run_data)
        assert run is not None
    else:
        run = store.create_run(
            mode="real",
            repo=repo,
            issue_number=issue_number,
            **run_data,
        )
    return {"run": run, "idempotent": False}


def _require_runner_auth(authorization: str | None = Header(default=None)) -> None:
    """Require a shared bearer token when RUNNER_SHARED_TOKEN is configured."""
    settings = _settings()
    if not settings.runner_shared_token:
        return
    expected = f"Bearer {settings.runner_shared_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid runner token.")


def _comment_body(run: dict[str, Any]) -> str:
    lines = [
        "### Devin issue runner update",
        "",
        f"- Status: `{run.get('status') or 'unknown'}`",
        f"- Run id: `{run.get('id')}`",
    ]
    if run.get("devin_session_url"):
        lines.append(f"- Devin session: {run['devin_session_url']}")
    if run.get("pull_request_url"):
        lines.append(f"- Pull request: {run['pull_request_url']}")
    if run.get("pr_state"):
        lines.append(f"- PR state: `{run['pr_state']}`")

    structured = run.get("structured_output")
    if isinstance(structured, dict):
        for key, label in (
            ("summary", "Summary"),
            ("validation", "Validation"),
            ("remaining_risks", "Remaining risks"),
        ):
            if structured.get(key):
                lines.extend(["", f"**{label}:** {structured[key]}"])

    if not run.get("pull_request_url"):
        lines.extend(["", "_No pull request has been recorded for this run yet._"])
    return "\n".join(lines)


def _post_issue_comment(
    run: dict[str, Any],
    settings: Settings,
    *,
    force: bool = False,
) -> tuple[dict[str, Any], bool]:
    if not settings.github_token:
        raise HTTPException(
            status_code=400,
            detail="GITHUB_TOKEN is required to post GitHub issue comments.",
        )

    gh = _github(settings)
    try:
        body = _comment_body(run)
        if run.get("issue_comment_id") and not force:
            comment = gh.update_issue_comment(run["repo"], int(run["issue_comment_id"]), body)
        else:
            comment = gh.create_issue_comment(run["repo"], int(run["issue_number"]), body)
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    updated = store.update_run(
        run["id"],
        issue_comment_id=str(comment.get("id") or run.get("issue_comment_id") or ""),
        issue_comment_url=comment.get("html_url") or comment.get("url"),
        issue_comment_posted_at=_now(),
    )
    assert updated is not None
    return updated, True


def _status_payload(settings: Settings) -> dict[str, Any]:
    runs = store.list_runs()
    status_counts: dict[str, int] = {}
    for run in runs:
        ds = str(run.get("display_status") or run.get("status") or "unknown")
        status_counts[ds] = status_counts.get(ds, 0) + 1
    return {
        "repo": settings.github_repo,
        "configured": {
            "devin": settings.devin_configured,
            "github": settings.github_configured,
            "runner_auth": settings.runner_auth_configured,
            "webhook_label": settings.webhook_label,
        },
        "metrics": {
            "runs_total": len(runs),
            "sessions_tracked": sum(1 for r in runs if r.get("devin_session_url")),
            "pull_requests_recorded": sum(1 for r in runs if r.get("pull_request_url")),
            "issue_comments_posted": sum(1 for r in runs if r.get("issue_comment_url")),
            "status_counts": status_counts,
        },
        "recent_runs": runs[:10],
    }


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
        "runner_auth_configured": settings.runner_auth_configured,
        "endpoints": [
            "/issues/{issue_number}/devin-runs",
            "/runs/import",
            "/webhooks/github",
            "/runs",
            "/runs/{run_id}",
            "/runs/{run_id}/poll",
            "/runs/{run_id}/comment",
            "/status",
            "/dashboard",
            "/summary",
            "/summary.json",
            "/health",
        ],
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/issues/{issue_number}/devin-runs")
def create_devin_run(
    issue_number: int,
    repo: str | None = None,
    force: bool = False,
    _: None = Depends(_require_runner_auth),
) -> dict[str, Any]:
    """Create a Devin session from a GitHub issue."""
    settings = _settings()
    return _create_real_run(
        settings=settings,
        repo=repo or settings.github_repo,
        issue_number=issue_number,
        force=force,
    )


@app.post("/runs/import")
def import_run(
    req: ImportRunRequest,
    _: None = Depends(_require_runner_auth),
) -> dict[str, Any]:
    """Import an existing issue + Devin session + PR without API calls."""
    settings = _settings()
    repo = req.repo or settings.github_repo

    session_id = extract_session_id(req.devin_session_url)
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail=f"devin_session_url is not a valid Devin session URL: {req.devin_session_url}",
        )
    issue_url = req.issue_url or f"https://github.com/{repo}/issues/{req.issue_number}"

    pr_state = None
    if req.pull_request_url and not _pr_url_for_repo(req.pull_request_url, repo):
        raise HTTPException(
            status_code=400,
            detail=f"pull_request_url is not a GitHub PR URL for {repo}: {req.pull_request_url}",
        )

    if not req.force:
        existing = store.find_issue_run(repo, req.issue_number, mode="import")
        if existing:
            return {
                "run": existing,
                "idempotent": True,
                "detail": "Existing imported run reused. Pass force=true to create another.",
            }

    run = store.create_run(
        mode="import",
        repo=repo,
        issue_number=req.issue_number,
        issue_url=issue_url,
        issue_title=req.issue_title,
        devin_session_id=session_id,
        devin_session_url=_session_url(session_id, req.devin_session_url),
        pull_request_url=req.pull_request_url,
        pr_state=pr_state,
        status="imported",
        detail="Imported existing Devin session and PR.",
    )
    return {"run": run, "idempotent": False}


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
def poll_run(
    run_id: str,
    post_comment: bool = False,
    _: None = Depends(_require_runner_auth),
) -> dict[str, Any]:
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
                structured_pr = _structured_pr_url(structured, run["repo"])
                if structured_pr and not run.get("pull_request_url"):
                    updates["pull_request_url"] = structured_pr
            pr_url, pr_state = _first_pr_url(session)
            matched_pr_url = _pr_url_for_repo(pr_url, run["repo"])
            if pr_url and not matched_pr_url:
                notes.append(f"Ignored PR URL outside {run['repo']}: {pr_url}")
            if matched_pr_url and not run.get("pull_request_url"):
                updates["pull_request_url"] = matched_pr_url
            if pr_state:
                updates["pr_state"] = pr_state
        except DevinError as exc:
            notes.append(f"Devin poll failed: {exc}")
    elif run.get("devin_session_id"):
        notes.append("Devin not configured; skipped session poll.")

    # 2) PR metadata from GitHub (works for imported runs too).
    pr_url = updates.get("pull_request_url") or run.get("pull_request_url")
    if pr_url:
        if not _pr_url_for_repo(pr_url, run["repo"]):
            raise HTTPException(
                status_code=400,
                detail=f"pull_request_url is not a GitHub PR URL for {run['repo']}: {pr_url}",
            )
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
    comment_posted = False
    if post_comment:
        updated, comment_posted = _post_issue_comment(updated, settings)
    return {"run": updated, "notes": notes, "comment_posted": comment_posted}


@app.post("/runs/{run_id}/comment")
def comment_run(
    run_id: str,
    req: CommentRequest | None = None,
    _: None = Depends(_require_runner_auth),
) -> dict[str, Any]:
    """Post the current run status back to the GitHub issue."""
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    updated, posted = _post_issue_comment(
        run,
        _settings(),
        force=req.force if req else False,
    )
    return {"run": updated, "posted": posted}


@app.post("/webhooks/github")
def github_webhook(
    payload: dict[str, Any],
    _: None = Depends(_require_runner_auth),
) -> dict[str, Any]:
    """Accept GitHub issue events and route labeled issues into Devin."""
    settings = _settings()
    action = payload.get("action")
    issue = payload.get("issue") or {}
    repo_info = payload.get("repository") or {}
    repo = repo_info.get("full_name") or settings.github_repo
    issue_number = issue.get("number")
    if not issue_number:
        raise HTTPException(status_code=400, detail="Webhook payload is missing issue.number")

    labels = {
        label.get("name")
        for label in (issue.get("labels") or [])
        if isinstance(label, dict)
    }
    event_label = (payload.get("label") or {}).get("name")
    label_matches = settings.webhook_label in labels or event_label == settings.webhook_label
    if action not in {"opened", "labeled"} or not label_matches:
        return {
            "accepted": False,
            "reason": f"Ignored action={action!r}; waiting for label {settings.webhook_label!r}.",
        }

    result = _create_real_run(
        settings=settings,
        repo=repo,
        issue_number=int(issue_number),
        force=False,
    )
    return {"accepted": True, **result}


@app.get("/status")
def status() -> dict[str, Any]:
    """JSON status metrics for the runner."""
    return _status_payload(_settings())


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    """Small static dashboard for local review."""
    data = _status_payload(_settings())
    rows = []
    for run in data["recent_runs"]:
        ds = run.get("display_status") or run.get("status")
        rows.append(
            "<tr>"
            f"<td>#{_html_text(run.get('issue_number'))}</td>"
            f"<td>{_html_text(ds)}</td>"
            f"<td>{_html_text(run.get('mode'))}</td>"
            f"<td>{_html_link(run.get('devin_session_url'), 'session')}</td>"
            f"<td>{_html_link(run.get('pull_request_url'), 'PR')}</td>"
            f"<td>{_html_link(run.get('issue_comment_url'), 'comment')}</td>"
            "</tr>"
        )
    metrics = data["metrics"]
    repo = _html_text(data["repo"])
    return f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Devin Issue Runner</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 32px;
      color: #1f2328;
    }}
    main {{ max-width: 1000px; margin: 0 auto; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 24px 0;
    }}
    .metric {{ border: 1px solid #d0d7de; border-radius: 6px; padding: 14px; }}
    .metric b {{ display: block; font-size: 28px; margin-bottom: 4px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
    th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid #d8dee4; }}
    th {{ background: #f6f8fa; }}
    code {{ background: #f6f8fa; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
<main>
  <h1>Devin Issue Runner</h1>
  <p>Target repo: <code>{repo}</code></p>
  <section class="metrics">
    <div class="metric"><b>{metrics["runs_total"]}</b>runs</div>
    <div class="metric"><b>{metrics["sessions_tracked"]}</b>sessions</div>
    <div class="metric"><b>{metrics["pull_requests_recorded"]}</b>PRs</div>
    <div class="metric"><b>{metrics["issue_comments_posted"]}</b>comments</div>
  </section>
  <h2>Recent runs</h2>
  <table>
    <thead>
      <tr>
        <th>Issue</th><th>Status</th><th>Mode</th>
        <th>Session</th><th>PR</th><th>Comment</th>
      </tr>
    </thead>
    <tbody>{''.join(rows) or '<tr><td colspan="6">No runs yet.</td></tr>'}</tbody>
  </table>
</main>
</body>
</html>
"""


@app.get("/summary", response_class=PlainTextResponse)
def summary() -> str:
    """Markdown status summary of all runs."""
    settings = _settings()
    runs = store.list_runs()
    return build_summary(runs, repo=settings.github_repo)


@app.get("/summary.json")
def summary_json() -> dict[str, Any]:
    """JSON-wrapped status summary."""
    settings = _settings()
    runs = store.list_runs()
    return {"markdown": build_summary(runs, repo=settings.github_repo)}
