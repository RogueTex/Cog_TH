"""Tests for the SQLite store, including idempotency lookups."""
from __future__ import annotations

import pytest

from app.store import Store


@pytest.fixture()
def store() -> Store:
    s = Store(":memory:")
    yield s
    s.close()


def test_create_and_get_run(store: Store):
    run = store.create_run(
        mode="import",
        repo="RogueTex/superset",
        issue_number=2,
        status="imported",
        issue_url="https://github.com/RogueTex/superset/issues/2",
        devin_session_url="https://app.devin.ai/sessions/edd1bd6ac10b4e899ba2a886a1b5f744",
        pull_request_url="https://github.com/RogueTex/superset/pull/4",
    )
    assert run["id"]
    fetched = store.get_run(run["id"])
    assert fetched is not None
    assert fetched["issue_number"] == 2
    assert fetched["mode"] == "import"


def test_list_runs_orders_newest_first(store: Store):
    a = store.create_run(mode="import", repo="r", issue_number=1, status="imported")
    b = store.create_run(mode="import", repo="r", issue_number=2, status="imported")
    runs = store.list_runs()
    ids = [r["id"] for r in runs]
    assert set(ids) == {a["id"], b["id"]}
    assert len(runs) == 2


def test_update_run(store: Store):
    run = store.create_run(mode="real", repo="r", issue_number=5, status="new")
    updated = store.update_run(run["id"], status="running", pull_request_url="http://x/pull/1")
    assert updated is not None
    assert updated["status"] == "running"
    assert updated["pull_request_url"] == "http://x/pull/1"
    assert updated["updated_at"] >= run["updated_at"]


def test_update_run_rejects_unknown_column(store: Store):
    run = store.create_run(mode="real", repo="r", issue_number=5, status="new")
    with pytest.raises(ValueError):
        store.update_run(run["id"], bogus_column="x")


def test_structured_output_roundtrip(store: Store):
    payload = {"summary": "fixed it", "pull_request_url": "http://x/pull/1"}
    run = store.create_run(
        mode="real", repo="r", issue_number=7, status="exit", structured_output=payload
    )
    fetched = store.get_run(run["id"])
    assert fetched is not None
    assert fetched["structured_output"] == payload


def test_idempotency_find_active_real_run(store: Store):
    # No real run yet.
    assert store.find_active_real_run("r", 9) is None
    # Imported run should not count as a real run.
    store.create_run(mode="import", repo="r", issue_number=9, status="imported")
    assert store.find_active_real_run("r", 9) is None
    # A real run is found.
    real = store.create_run(mode="real", repo="r", issue_number=9, status="new")
    found = store.find_active_real_run("r", 9)
    assert found is not None
    assert found["id"] == real["id"]
    # Different issue number is isolated.
    assert store.find_active_real_run("r", 10) is None


def test_reserve_real_run_is_idempotent(store: Store):
    first, created = store.reserve_real_run("r", 12)
    assert created is True
    assert first["status"] == "queued"

    second, created = store.reserve_real_run("r", 12)
    assert created is False
    assert second["id"] == first["id"]
