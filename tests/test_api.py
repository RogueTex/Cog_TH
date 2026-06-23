"""API-level tests for import, status, trigger, and comment flows."""
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


def _import_payload() -> dict[str, str | int]:
    return {
        "issue_number": 2,
        "devin_session_url": "https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744",
        "pull_request_url": "https://github.com/RogueTex/superset/pull/4",
        "issue_title": "Remove dockerize init image",
    }


def test_import_and_list(client: TestClient):
    resp = client.post("/runs/import", json=_import_payload())
    assert resp.status_code == 200, resp.text
    run = resp.json()["run"]
    assert run["mode"] == "import"
    assert run["status"] == "imported"
    assert run["issue_number"] == 2
    assert run["devin_session_id"] == "devin-edd1bd6ac10b4e899ba2a886a1b5f744"
    assert run["pull_request_url"].endswith("/pull/4")

    runs = client.get("/runs").json()
    assert runs["count"] == 1


def test_import_is_idempotent(client: TestClient):
    first = client.post("/runs/import", json=_import_payload())
    second = client.post("/runs/import", json=_import_payload())

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["idempotent"] is False
    assert second.json()["idempotent"] is True
    assert first.json()["run"]["id"] == second.json()["run"]["id"]


def test_import_rejects_bad_urls(client: TestClient):
    bad_session = _import_payload() | {"devin_session_url": "https://example.com/nope"}
    resp = client.post("/runs/import", json=bad_session)
    assert resp.status_code == 400

    bad_pr = _import_payload() | {"pull_request_url": "not-a-url"}
    resp = client.post("/runs/import", json=bad_pr)
    assert resp.status_code == 400

    wrong_repo = _import_payload() | {
        "pull_request_url": "https://github.com/Other/repo/pull/4"
    }
    resp = client.post("/runs/import", json=wrong_repo)
    assert resp.status_code == 400


def test_summary_and_status_include_imported_run(client: TestClient):
    client.post("/runs/import", json=_import_payload())

    summary = client.get("/summary").text
    assert "# Devin Issue Runner" in summary
    assert "#2" in summary
    assert "Remove dockerize init image" in summary
    assert "/pull/4" in summary

    status = client.get("/status").json()
    assert status["metrics"]["runs_total"] == 1
    assert status["metrics"]["pull_requests_recorded"] == 1
    assert status["metrics"]["status_counts"]["imported"] == 1


def test_dashboard_renders(client: TestClient):
    client.post("/runs/import", json=_import_payload())
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Devin Issue Runner" in resp.text
    assert "href=\"https://github.com/RogueTex/superset/pull/4\"" in resp.text


def test_devin_run_requires_credentials(client: TestClient):
    resp = client.post("/issues/2/devin-runs?repo=RogueTex/superset")
    assert resp.status_code == 400
    assert "DEVIN_API_KEY" in resp.json()["detail"]


def test_devin_run_is_idempotent(tmp_path, monkeypatch):
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

    first = c.post("/issues/2/devin-runs?repo=RogueTex/superset")
    assert first.status_code == 200, first.text
    assert first.json()["idempotent"] is False

    second = c.post("/issues/2/devin-runs?repo=RogueTex/superset")
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True

    assert calls["create"] == 1

    forced = c.post("/issues/2/devin-runs?repo=RogueTex/superset&force=true")
    assert forced.status_code == 200, forced.text
    assert calls["create"] == 2


def test_webhook_ignores_irrelevant_labels(client: TestClient):
    resp = client.post(
        "/webhooks/github",
        json={
            "action": "labeled",
            "label": {"name": "documentation"},
            "issue": {"number": 2, "labels": [{"name": "documentation"}]},
            "repository": {"full_name": "RogueTex/superset"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is False


def test_webhook_routes_matching_label(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "webhook.db"))
    monkeypatch.setenv("GITHUB_REPO", "RogueTex/superset")
    monkeypatch.setenv("DEVIN_API_KEY", "fake-key")
    monkeypatch.setenv("DEVIN_ORG_ID", "org-fake")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    import app.main as main

    importlib.reload(main)

    monkeypatch.setattr(
        main.GitHubClient,
        "get_issue",
        lambda self, repo, issue_number: {
            "html_url": f"https://github.com/{repo}/issues/{issue_number}",
            "title": "Remove dockerize init image",
            "body": "remove it",
        },
    )
    monkeypatch.setattr(
        main.DevinClient,
        "create_session",
        lambda self, **kwargs: {
            "session_id": "devin-deadbeef",
            "url": "https://app.devin.ai/sessions/deadbeef",
            "status": "running",
            "pull_requests": [],
        },
    )

    c = TestClient(main.app)
    resp = c.post(
        "/webhooks/github",
        json={
            "action": "labeled",
            "label": {"name": "devin-remediate"},
            "issue": {"number": 2, "labels": [{"name": "devin-remediate"}]},
            "repository": {"full_name": "RogueTex/superset"},
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] is True
    assert resp.json()["run"]["mode"] == "real"


def test_poll_extracts_structured_pr_url(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "poll.db"))
    monkeypatch.setenv("GITHUB_REPO", "RogueTex/superset")
    monkeypatch.setenv("DEVIN_API_KEY", "fake-key")
    monkeypatch.setenv("DEVIN_ORG_ID", "org-fake")

    import app.main as main

    importlib.reload(main)

    run = main.store.create_run(
        mode="real",
        repo="RogueTex/superset",
        issue_number=2,
        status="running",
        devin_session_id="devin-deadbeef",
    )

    monkeypatch.setattr(
        main.DevinClient,
        "get_session",
        lambda self, session_id: {
            "status": "exit",
            "structured_output": {
                "summary": "Fixed Helm startup waits.",
                "pull_request_url": "https://github.com/RogueTex/superset/pull/4",
                "validation": "pytest",
            },
        },
    )
    monkeypatch.setattr(
        main.GitHubClient,
        "get_pull_request_by_url",
        lambda self, pr_url: {"state": "open", "merged": False},
    )

    c = TestClient(main.app)
    resp = c.post(f"/runs/{run['id']}/poll")
    assert resp.status_code == 200, resp.text
    updated = resp.json()["run"]
    assert updated["status"] == "exit"
    assert updated["pull_request_url"] == "https://github.com/RogueTex/superset/pull/4"
    assert updated["pr_state"] == "open"


def test_poll_ignores_pr_url_from_other_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "mismatch.db"))
    monkeypatch.setenv("GITHUB_REPO", "RogueTex/superset")
    monkeypatch.setenv("DEVIN_API_KEY", "fake-key")
    monkeypatch.setenv("DEVIN_ORG_ID", "org-fake")

    import app.main as main

    importlib.reload(main)

    run = main.store.create_run(
        mode="real",
        repo="RogueTex/superset",
        issue_number=2,
        status="running",
        devin_session_id="devin-deadbeef",
    )

    monkeypatch.setattr(
        main.DevinClient,
        "get_session",
        lambda self, session_id: {
            "status": "exit",
            "structured_output": {
                "summary": "Opened a PR.",
                "pull_request_url": "https://github.com/Other/repo/pull/4",
                "validation": "pytest",
            },
            "pull_requests": [{"pr_url": "https://github.com/Other/repo/pull/4"}],
        },
    )

    c = TestClient(main.app)
    resp = c.post(f"/runs/{run['id']}/poll")
    assert resp.status_code == 200, resp.text
    updated = resp.json()["run"]
    assert updated["pull_request_url"] is None
    assert "Ignored PR URL outside RogueTex/superset" in updated["detail"]


def test_comment_posts_and_updates_issue(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "comment.db"))
    monkeypatch.setenv("GITHUB_REPO", "RogueTex/superset")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    import app.main as main

    importlib.reload(main)

    created_bodies: list[str] = []
    updated_bodies: list[str] = []
    run = main.store.create_run(
        mode="import",
        repo="RogueTex/superset",
        issue_number=2,
        status="imported",
        devin_session_url="https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744",
        pull_request_url="https://github.com/RogueTex/superset/pull/4",
    )

    def fake_create_issue_comment(self, repo, issue_number, body):
        created_bodies.append(body)
        return {
            "id": 1,
            "html_url": f"https://github.com/{repo}/issues/{issue_number}#issuecomment-1",
        }

    def fake_update_issue_comment(self, repo, comment_id, body):
        updated_bodies.append(body)
        return {
            "id": comment_id,
            "html_url": f"https://github.com/{repo}/issues/2#issuecomment-{comment_id}",
        }

    monkeypatch.setattr(main.GitHubClient, "create_issue_comment", fake_create_issue_comment)
    monkeypatch.setattr(main.GitHubClient, "update_issue_comment", fake_update_issue_comment)

    c = TestClient(main.app)
    resp = c.post(f"/runs/{run['id']}/comment")
    assert resp.status_code == 200, resp.text
    assert resp.json()["posted"] is True
    assert resp.json()["run"]["issue_comment_id"] == "1"
    assert resp.json()["run"]["issue_comment_url"].endswith("#issuecomment-1")
    assert "Devin issue runner update" in created_bodies[0]

    second = c.post(f"/runs/{run['id']}/comment")
    assert second.status_code == 200, second.text
    assert second.json()["posted"] is True
    assert "Devin issue runner update" in updated_bodies[0]


def test_labeled_issue_full_loop_posts_comment(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "loop.db"))
    monkeypatch.setenv("GITHUB_REPO", "RogueTex/superset")
    monkeypatch.setenv("DEVIN_API_KEY", "fake-key")
    monkeypatch.setenv("DEVIN_ORG_ID", "org-fake")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    import app.main as main

    importlib.reload(main)

    monkeypatch.setattr(
        main.GitHubClient,
        "get_issue",
        lambda self, repo, issue_number: {
            "html_url": f"https://github.com/{repo}/issues/{issue_number}",
            "title": "Remove dockerize init image",
            "body": "remove it",
        },
    )
    monkeypatch.setattr(
        main.DevinClient,
        "create_session",
        lambda self, **kwargs: {
            "session_id": "devin-deadbeef",
            "url": "https://app.devin.ai/sessions/deadbeef",
            "status": "running",
            "pull_requests": [],
        },
    )
    monkeypatch.setattr(
        main.DevinClient,
        "get_session",
        lambda self, session_id: {
            "status": "exit",
            "structured_output": {
                "summary": "Fixed Helm startup waits.",
                "pull_request_url": "https://github.com/RogueTex/superset/pull/4",
                "validation": "pytest",
            },
        },
    )
    monkeypatch.setattr(
        main.GitHubClient,
        "get_pull_request_by_url",
        lambda self, pr_url: {"state": "open", "merged": False},
    )
    monkeypatch.setattr(
        main.GitHubClient,
        "create_issue_comment",
        lambda self, repo, issue_number, body: {
            "id": 10,
            "html_url": f"https://github.com/{repo}/issues/{issue_number}#issuecomment-10",
        },
    )

    c = TestClient(main.app)
    start = c.post(
        "/webhooks/github",
        json={
            "action": "labeled",
            "label": {"name": "devin-remediate"},
            "issue": {"number": 2, "labels": [{"name": "devin-remediate"}]},
            "repository": {"full_name": "RogueTex/superset"},
        },
    )
    assert start.status_code == 200, start.text
    run_id = start.json()["run"]["id"]

    polled = c.post(f"/runs/{run_id}/poll?post_comment=true")
    assert polled.status_code == 200, polled.text
    run = polled.json()["run"]
    assert run["pull_request_url"] == "https://github.com/RogueTex/superset/pull/4"
    assert run["issue_comment_url"].endswith("#issuecomment-10")

    status = c.get("/status").json()
    assert status["metrics"]["runs_total"] == 1
    assert status["metrics"]["issue_comments_posted"] == 1
    assert "Devin Issue Runner" in c.get("/dashboard").text
