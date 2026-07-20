"""X (Twitter) API v2 client.

Auth model: OAuth 2.0 **user context** (Authorization Code + PKCE), NOT an
app-only bearer token. The home-timeline endpoint requires a user access token
with scopes: tweet.read users.read offline.access.

Refresh-token rotation: X issues a NEW refresh_token on every refresh when the
app has offline.access. We therefore treat the DB copy as authoritative and
persist the rotated value after each refresh. The X_REFRESH_TOKEN env var only
seeds the very first run (produced by auth_setup.py).

Cost note: home-timeline reads are pay-per-use on the X API (~$0.005 per read;
reads of tweets you own are cheaper). To cut cost you can point at a curated
List instead of the full home timeline — see fetch_new_tweets(), it's a
one-line switch driven by the X_LIST_ID env var:
    home:  GET /2/users/:id/timelines/reverse_chronological
    list:  GET /2/lists/:id/tweets
"""
from __future__ import annotations

import base64
import time
from typing import Any, Optional

import httpx

from .config import config
from . import db

TOKEN_URL = "https://api.x.com/2/oauth2/token"
API_BASE = "https://api.x.com/2"

_KV_REFRESH = "x_refresh_token"
_KV_USER_ID = "x_user_id"

# Cached in-process access token (short-lived).
_access_token: Optional[str] = None
_access_expiry: float = 0.0


class XAuthError(RuntimeError):
    pass


def _basic_auth_header() -> dict[str, str]:
    """Confidential clients authenticate to the token endpoint with HTTP Basic.
    Public (PKCE-only) clients send client_id in the body instead."""
    if config.x_client_secret:
        raw = f"{config.x_client_id}:{config.x_client_secret}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
    return {}


def _current_refresh_token() -> str:
    """DB copy wins; fall back to the env seed on first ever run."""
    tok = db.kv_get(_KV_REFRESH)
    if tok:
        return tok
    if config.x_refresh_token_seed:
        db.kv_set(_KV_REFRESH, config.x_refresh_token_seed)
        return config.x_refresh_token_seed
    raise XAuthError(
        "No refresh token available. Run auth_setup.py once and set X_REFRESH_TOKEN."
    )


def refresh_access_token() -> str:
    """Exchange the stored refresh token for a fresh short-lived access token.
    Persists the rotated refresh token that X returns."""
    global _access_token, _access_expiry

    data = {
        "grant_type": "refresh_token",
        "refresh_token": _current_refresh_token(),
        "client_id": config.x_client_id,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded", **_basic_auth_header()}

    resp = httpx.post(TOKEN_URL, data=data, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise XAuthError(f"Token refresh failed [{resp.status_code}]: {resp.text}")
    payload = resp.json()

    _access_token = payload["access_token"]
    _access_expiry = time.monotonic() + int(payload.get("expires_in", 7200)) - 60

    # X rotates the refresh token — persist the new one or the next run breaks.
    new_refresh = payload.get("refresh_token")
    if new_refresh:
        db.kv_set(_KV_REFRESH, new_refresh)

    return _access_token


def _get_access_token() -> str:
    if _access_token and time.monotonic() < _access_expiry:
        return _access_token
    return refresh_access_token()


def _api_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """Authenticated GET with one automatic refresh-and-retry on 401."""
    token = _get_access_token()
    url = f"{API_BASE}{path}"

    def _do(tok: str) -> httpx.Response:
        return httpx.get(url, params=params, headers={"Authorization": f"Bearer {tok}"}, timeout=30)

    resp = _do(token)
    if resp.status_code == 401:
        # Access token likely expired/revoked mid-flight — refresh once & retry.
        resp = _do(refresh_access_token())
    if resp.status_code == 429:
        raise RuntimeError(f"X rate limit hit: {resp.text}")
    if resp.status_code != 200:
        raise RuntimeError(f"X API GET {path} failed [{resp.status_code}]: {resp.text}")
    return resp.json()


def resolve_user_id() -> str:
    """Resolve and cache the authenticated user's id via GET /2/users/me."""
    cached = db.kv_get(_KV_USER_ID)
    if cached:
        return cached
    data = _api_get("/users/me", {})
    user_id = data["data"]["id"]
    db.kv_set(_KV_USER_ID, user_id)
    return user_id


def fetch_new_tweets() -> list[dict[str, Any]]:
    """Pull the timeline, newest first, paginating until we reach a tweet id we
    already have stored (or hit the page cap). Returns a list of enriched tweet
    dicts, each with an added ``author`` object ({username, name}).

    Uses since_id (the newest stored id) as the primary bound and also stops
    early the moment we encounter a stored id, per spec.
    """
    since_id = db.latest_seen_id()

    if config.x_list_id:
        # --- List timeline (one-line swap from the home timeline) ---
        path = f"/lists/{config.x_list_id}/tweets"
    else:
        user_id = resolve_user_id()
        path = f"/users/{user_id}/timelines/reverse_chronological"

    base_params: dict[str, Any] = {
        "max_results": config.max_results,
        "tweet.fields": "created_at,author_id,entities",
        "expansions": "author_id",
        "user.fields": "username,name",
    }
    if since_id:
        base_params["since_id"] = since_id

    collected: list[dict[str, Any]] = []
    pagination_token: Optional[str] = None
    hit_seen = False

    for _ in range(config.max_pages):
        params = dict(base_params)
        if pagination_token:
            params["pagination_token"] = pagination_token

        payload = _api_get(path, params)
        tweets = payload.get("data", [])
        users = {u["id"]: u for u in payload.get("includes", {}).get("users", [])}

        for tw in tweets:
            if db.has_seen(tw["id"]):
                hit_seen = True
                break
            author = users.get(tw.get("author_id"), {})
            tw["author"] = {
                "username": author.get("username"),
                "name": author.get("name"),
            }
            collected.append(tw)

        meta = payload.get("meta", {})
        pagination_token = meta.get("next_token")
        if hit_seen or not pagination_token or not tweets:
            break

    return collected
