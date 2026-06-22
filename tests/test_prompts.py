"""Tests for Devin work-order prompt construction."""
from __future__ import annotations

from app.prompts import (
    STRUCTURED_OUTPUT_SCHEMA,
    build_session_title,
    build_work_order,
)


def _build(**overrides):
    kwargs = dict(
        repo="RogueTex/superset",
        issue_number=2,
        issue_title="Remove dockerize init image",
        issue_body="The dockerize init image is no longer needed and should be removed.",
        issue_url="https://github.com/RogueTex/superset/issues/2",
    )
    kwargs.update(overrides)
    return build_work_order(**kwargs)


def test_prompt_contains_core_fields():
    prompt = _build()
    assert "RogueTex/superset" in prompt
    assert "#2" in prompt
    assert "Remove dockerize init image" in prompt
    assert "https://github.com/RogueTex/superset/issues/2" in prompt
    assert "dockerize init image is no longer needed" in prompt


def test_prompt_instructs_pr_and_structured_output():
    prompt = _build()
    assert "pull request" in prompt.lower()
    assert "provide_structured_output" in prompt
    # Should never instruct a merge.
    assert "merge" not in prompt.lower() or "do NOT" in prompt


def test_prompt_is_deterministic():
    assert _build() == _build()


def test_prompt_handles_missing_body():
    prompt = _build(issue_body=None)
    assert "(no description provided)" in prompt


def test_prompt_truncates_long_body():
    long_body = "x" * 10000
    prompt = _build(issue_body=long_body, body_char_limit=500)
    # Truncated body plus ellipsis, not the full 10k chars.
    assert "x" * 10000 not in prompt
    assert "\u2026" in prompt


def test_session_title_is_bounded():
    title = build_session_title(2, "z" * 500)
    assert len(title) <= 120
    assert title.startswith("Remediate #2")


def test_structured_output_schema_shape():
    assert STRUCTURED_OUTPUT_SCHEMA["type"] == "object"
    assert "pull_request_url" in STRUCTURED_OUTPUT_SCHEMA["properties"]
    assert "pull_request_url" in STRUCTURED_OUTPUT_SCHEMA["required"]
