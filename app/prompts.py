"""Construction of the Devin work-order prompt and structured-output schema.

Kept free of I/O so it is trivially unit-testable.
"""
from __future__ import annotations

from typing import Any

# JSON Schema (Draft 7) the Devin session is asked to satisfy via structured output.
STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "description": "One-paragraph summary of the fix."},
        "root_cause": {"type": "string", "description": "Root cause of the issue."},
        "pull_request_url": {"type": "string", "description": "URL of the opened PR."},
        "validation": {
            "type": "string",
            "description": "How the change was validated (tests, lint, manual).",
        },
        "remaining_risks": {
            "type": "string",
            "description": "Outstanding risks or follow-ups for a human.",
        },
    },
    "required": ["summary", "pull_request_url", "validation"],
}


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def build_work_order(
    *,
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body: str | None,
    issue_url: str,
    body_char_limit: int = 6000,
) -> str:
    """Build the remediation prompt sent to Devin for a GitHub issue.

    The prompt is deterministic given its inputs so it can be snapshot-tested.
    """
    body = _truncate(issue_body or "(no description provided)", body_char_limit)
    title = issue_title.strip() or f"Issue #{issue_number}"

    return f"""\
You are an autonomous remediation engineer working on the repository `{repo}`.

A GitHub issue has been detected and routed to you for a fix.

## Issue
- Repository: {repo}
- Issue number: #{issue_number}
- Issue title: {title}
- Issue URL: {issue_url}

## Issue description
{body}

## Your task
1. Reproduce and diagnose the problem described in the issue.
2. Implement a minimal, well-scoped fix that follows the repository's existing conventions.
3. Add or update tests where appropriate and run the project's lint/test suite.
4. Open a pull request from a new branch (do NOT push to the default branch and do NOT merge).
5. Reference issue #{issue_number} in the pull request description.

## Required output
When you are done, call provide_structured_output with:
- summary: what you changed and why
- root_cause: the underlying cause of the issue
- pull_request_url: the URL of the PR you opened
- validation: how you verified the fix (tests/lint/manual)
- remaining_risks: anything a human should review or follow up on

Keep the change small and focused. If you become blocked, document the blocker
clearly in the structured output instead of guessing.
"""


def build_session_title(issue_number: int, issue_title: str) -> str:
    """Human-friendly Devin session title."""
    title = issue_title.strip() or "remediation"
    return _truncate(f"Remediate #{issue_number}: {title}", 120)
