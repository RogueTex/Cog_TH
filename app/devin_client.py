"""Devin v3 API client (create + fetch sessions) built on httpx."""
from __future__ import annotations

import re
from typing import Any

import httpx

_SESSION_ID_RE = re.compile(r"(devin-[0-9a-f]+)", re.IGNORECASE)


class DevinError(RuntimeError):
    """Raised when a Devin API call fails."""


def extract_session_id(session_ref: str) -> str | None:
    """Pull a `devin-...` id out of a session id or app.devin.ai URL.

    The web app uses URLs like https://app.devin.ai/sessions/<hex> where the
    trailing segment is the id without the `devin-` prefix; the API expects the
    `devin-` prefixed form. We normalize both shapes.
    """
    if not session_ref:
        return None
    match = _SESSION_ID_RE.search(session_ref)
    if match:
        return match.group(1).lower()
    # Fall back to the last path segment of a URL (web app form, no prefix).
    tail = session_ref.rstrip("/").split("/")[-1]
    if re.fullmatch(r"[0-9a-f]{8,}", tail, re.IGNORECASE):
        return f"devin-{tail.lower()}"
    return None


class DevinClient:
    """Thin wrapper over the Devin v3 sessions API."""

    def __init__(
        self,
        api_key: str | None,
        org_id: str | None,
        *,
        api_base: str = "https://api.devin.ai",
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._org_id = org_id
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._org_id)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _require_config(self) -> None:
        if not self.configured:
            raise DevinError(
                "Devin API is not configured (DEVIN_API_KEY and DEVIN_ORG_ID required)"
            )

    def create_session(
        self,
        *,
        prompt: str,
        title: str | None = None,
        tags: list[str] | None = None,
        max_acu_limit: int | None = None,
        structured_output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a Devin session via POST /v3/organizations/{org_id}/sessions."""
        self._require_config()
        url = f"{self._api_base}/v3/organizations/{self._org_id}/sessions"
        payload: dict[str, Any] = {"prompt": prompt}
        if title:
            payload["title"] = title
        if tags:
            payload["tags"] = tags
        if max_acu_limit is not None:
            payload["max_acu_limit"] = max_acu_limit
        if structured_output_schema is not None:
            payload["structured_output_schema"] = structured_output_schema

        try:
            resp = httpx.post(
                url, headers=self._headers(), json=payload, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise DevinError(f"Devin request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise DevinError(
                f"Devin returned {resp.status_code} creating session: {resp.text[:500]}"
            )
        return resp.json()

    def get_session(self, session_ref: str) -> dict[str, Any]:
        """Fetch a session via GET /v3/organizations/{org_id}/sessions/{devin_id}."""
        self._require_config()
        session_id = extract_session_id(session_ref)
        if not session_id:
            raise DevinError(f"Could not extract a Devin session id from: {session_ref}")
        url = f"{self._api_base}/v3/organizations/{self._org_id}/sessions/{session_id}"
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise DevinError(f"Devin request failed: {exc}") from exc
        if resp.status_code == 404:
            raise DevinError(f"Devin session {session_id} not found")
        if resp.status_code >= 400:
            raise DevinError(
                f"Devin returned {resp.status_code} fetching session {session_id}: "
                f"{resp.text[:500]}"
            )
        return resp.json()
