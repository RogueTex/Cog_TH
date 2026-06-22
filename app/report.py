"""Render an engineering-leadership Markdown report from stored runs."""
from __future__ import annotations

from typing import Any

_OPEN_STATUSES = {"new", "claimed", "running", "resuming", "adopted", "created"}


def _fmt(value: Any, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def _link(url: str | None, label: str | None = None) -> str:
    if not url:
        return "—"
    return f"[{label or url}]({url})"


def build_report(runs: list[dict[str, Any]], *, repo: str) -> str:
    """Build a Markdown leadership report summarizing all automation runs."""
    total = len(runs)
    with_session = sum(1 for r in runs if r.get("devin_session_url"))
    with_pr = sum(1 for r in runs if r.get("pull_request_url"))
    adopted = sum(1 for r in runs if r.get("mode") == "adopt")
    real = sum(1 for r in runs if r.get("mode") == "real")
    open_runs = sum(1 for r in runs if (r.get("status") or "").lower() in _OPEN_STATUSES)

    lines: list[str] = []
    lines.append("# Devin Autopilot — Remediation Report")
    lines.append("")
    lines.append(f"_Repository:_ `{repo}`")
    lines.append("")
    lines.append("## Executive summary")
    lines.append("")
    lines.append(f"- **Issues detected / runs tracked:** {total}")
    lines.append(f"- **Devin sessions (created + adopted):** {with_session} "
                 f"({real} real, {adopted} adopted)")
    lines.append(f"- **Pull requests produced:** {with_pr}")
    lines.append(f"- **Runs still open / in progress:** {open_runs}")
    lines.append("")

    if not runs:
        lines.append("_No runs recorded yet. Trigger one with `POST /simulate` or `POST /adopt`._")
        lines.append("")
        return "\n".join(lines)

    lines.append("## Runs")
    lines.append("")
    lines.append("| Issue | Mode | Devin session | Pull request | PR state | Status |")
    lines.append("|---|---|---|---|---|---|")
    for r in runs:
        issue = _link(r.get("issue_url"), f"#{r.get('issue_number')}")
        session = _link(r.get("devin_session_url"), "session")
        pr = _link(r.get("pull_request_url"), "PR")
        lines.append(
            f"| {issue} | {_fmt(r.get('mode'))} | {session} | {pr} | "
            f"{_fmt(r.get('pr_state'))} | {_fmt(r.get('status'))} |"
        )
    lines.append("")

    lines.append("## Details")
    lines.append("")
    for r in runs:
        lines.append(f"### Issue #{r.get('issue_number')}: {_fmt(r.get('issue_title'))}")
        lines.append("")
        lines.append(f"- **Run id:** `{r.get('id')}`")
        lines.append(f"- **Mode:** {_fmt(r.get('mode'))}")
        lines.append(f"- **Issue:** {_link(r.get('issue_url'))}")
        lines.append(f"- **Devin session:** {_link(r.get('devin_session_url'))}")
        lines.append(f"- **Pull request:** {_link(r.get('pull_request_url'))} "
                     f"(state: {_fmt(r.get('pr_state'))})")
        lines.append(f"- **Status:** {_fmt(r.get('status'))}")

        structured = r.get("structured_output")
        if isinstance(structured, dict) and structured:
            lines.append("- **Validation summary (from Devin):**")
            for key in ("summary", "root_cause", "validation", "remaining_risks"):
                if structured.get(key):
                    label = key.replace("_", " ").capitalize()
                    lines.append(f"    - _{label}:_ {structured[key]}")
        elif r.get("detail"):
            lines.append(f"- **Notes:** {r.get('detail')}")
        lines.append("")

    lines.append("## Remaining risks / next human action")
    lines.append("")
    risk_lines: list[str] = []
    for r in runs:
        status = (r.get("status") or "").lower()
        pr_state = (r.get("pr_state") or "").lower()
        num = r.get("issue_number")
        if status == "error":
            risk_lines.append(f"- Issue #{num}: session errored — needs human triage.")
        elif not r.get("pull_request_url"):
            risk_lines.append(
                f"- Issue #{num}: no PR yet — monitor the Devin session or re-poll."
            )
        elif pr_state == "merged":
            continue
        elif pr_state == "open":
            risk_lines.append(f"- Issue #{num}: PR open — needs human review & merge.")
        else:
            risk_lines.append(
                f"- Issue #{num}: PR recorded (state unknown) — run poll, then review & merge."
            )
    if not risk_lines:
        risk_lines.append("- No outstanding risks: every run has a PR and none are errored.")
    lines.extend(risk_lines)
    lines.append("")
    return "\n".join(lines)
