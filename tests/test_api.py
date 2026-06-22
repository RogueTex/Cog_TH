"""API-level tests for adopt mode, reporting, and simulate idempotency."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """Fresh app instance backed by a temp SQLite db, no real credentials."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("GITHUB_REPO", "RogueTex/superset")
    monkeypatch.delenv("DEVIN_API_KEY", raising=False)
    monkeypatch.delenv("DEVIN_ORG_ID", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    import app.main as main

    importlib.reload(main)
    return TestClient(main.app)


def test_adopt_and_list(client: TestClient):
    resp = client.post(
        "/adopt",
        json={
            "issue_number": 2,
            "devin_session_url": "https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744",
            "pull_request_url": "https://github.com/RogueTex/superset/pull/4",
        },
    )
    assert resp.status_code == 200, resp.text
    run = resp.json()["run"]
    assert run["mode"] == "adopt"
    assert run["issue_number"] == 2
    assert run["devin_session_id"] == "devin-edd1bd6ac10b4e899ba2a886a1b5f744"
    assert run["pull_request_url"].endswith("/pull/4")

    runs = client.get("/runs").json()
    assert runs["count"] == 1


def test_adopt_rejects_bad_pr_url(client: TestClient):
    resp = client.post(
        "/adopt",
        json={
            "issue_number": 2,
            "devin_session_url": "https://app.devin.ai/sessions/abc123def456",
            "pull_request_url": "not-a-url",
        },
    )
    assert resp.status_code == 400


def test_report_contains_adopted_run(client: TestClient):
    client.post(
        "/adopt",
        json={
            "issue_number": 2,
            "devin_session_url": "https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744",
            "pull_request_url": "https://github.com/RogueTex/superset/pull/4",
            "issue_title": "Remove dockerize init image",
        },
    )
    report = client.get("/report").text
    assert "# Devin Autopilot" in report
    assert "#2" in report
    assert "Remove dockerize init image" in report
    assert "/pull/4" in report


def test_simulate_requires_credentials(client: TestClient):
    resp = client.post("/simulate", json={"issue_number": 2})
    assert resp.status_code == 400
    assert "DEVIN_API_KEY" in resp.json()["detail"]


def test_simulate_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "idem.db"))
    monkeypatch.setenv("GITHUB_REPO", "RogueTex/superset")
    monkeypatch.setenv("DEVIN_API_KEY", "fake-key")
    monkeypatch.setenv("DEVIN_ORG_ID", "org-fake")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    import app.main as main

    importlib.reload(main)

    calls = {"create": 0}

    def fake_get_issue(self, repo, issue_number):
        return {
            "html_url": f"https://github.com/{repo}/issues/{issue_number}",
            "title": "Remove dockerize init image",
            "body": "remove it",
        }

    def fake_create_session(self, **kwargs):
        calls["create"] += 1
        return {
            "session_id": "devin-deadbeef",
            "url": "https://app.devin.ai/sessions/deadbeef",
            "status": "running",
            "pull_requests": [],
        }

    monkeypatch.setattr(main.GitHubClient, "get_issue", fake_get_issue)
    monkeypatch.setattr(main.DevinClient, "create_session", fake_create_session)

    c = TestClient(main.app)

    first = c.post("/simulate", json={"issue_number": 2})
    assert first.status_code == 200, first.text
    assert first.json()["idempotent"] is False

    second = c.post("/simulate", json={"issue_number": 2})
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True

    # Only one real session despite two calls.
    assert calls["create"] == 1

    forced = c.post("/simulate", json={"issue_number": 2, "force": True})
    assert forced.status_code == 200, forced.text
    assert calls["create"] == 2
