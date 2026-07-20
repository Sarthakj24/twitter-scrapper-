#!/usr/bin/env python3
"""ONE-TIME local OAuth 2.0 (Authorization Code + PKCE) setup for the X API.

Run this on your laptop once:

    export X_CLIENT_ID=...            # from your X app
    export X_CLIENT_SECRET=...        # only if your app is a *confidential* client
    export X_REDIRECT_URI=http://127.0.0.1:8721/callback   # must match the app config
    python auth_setup.py

It will:
  1. open the X authorize URL in your browser,
  2. catch the redirect on localhost,
  3. exchange the code for tokens,
  4. print the refresh_token — paste it into your Railway env as X_REFRESH_TOKEN.

The long-running worker uses that refresh_token to mint short-lived access
tokens. X rotates refresh tokens, so the worker persists the rotated value in
SQLite after the first run; this env value is only the seed.

Scopes requested: tweet.read users.read offline.access (offline.access is what
gets you a refresh_token at all).
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import os
import secrets
import threading
import urllib.parse
import webbrowser

import httpx

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"

CLIENT_ID = os.environ.get("X_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("X_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("X_REDIRECT_URI", "http://127.0.0.1:8721/callback")
SCOPES = os.environ.get("X_SCOPES", "tweet.read users.read offline.access")

_result: dict[str, str] = {}


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        _result.update({k: v[0] for k, v in params.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>Authorization received.</h2>"
            b"<p>You can close this tab and return to the terminal.</p></body></html>"
        )

    def log_message(self, *args):  # silence the default request logging
        pass


def _serve_once(port: int) -> None:
    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.handle_request()  # blocks until exactly one request is served


def main() -> None:
    if not CLIENT_ID:
        raise SystemExit("Set X_CLIENT_ID (and X_CLIENT_SECRET if confidential) first.")

    parsed = urllib.parse.urlparse(REDIRECT_URI)
    port = parsed.port or 8721

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)

    auth_params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(auth_params)

    server_thread = threading.Thread(target=_serve_once, args=(port,), daemon=True)
    server_thread.start()

    print("\nOpening the X authorization page in your browser...")
    print("If it doesn't open, paste this URL manually:\n")
    print(url + "\n")
    webbrowser.open(url)

    server_thread.join(timeout=300)

    if _result.get("state") != state:
        raise SystemExit(f"State mismatch or timeout. Got: {_result}")
    if "code" not in _result:
        raise SystemExit(f"No authorization code received. Got: {_result}")

    data = {
        "grant_type": "authorization_code",
        "code": _result["code"],
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
        "client_id": CLIENT_ID,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if CLIENT_SECRET:
        raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()

    resp = httpx.post(TOKEN_URL, data=data, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise SystemExit(f"Token exchange failed [{resp.status_code}]: {resp.text}")

    tokens = resp.json()
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise SystemExit(
            "No refresh_token returned. Make sure 'offline.access' is in your scopes "
            f"and enabled on the app. Response: {tokens}"
        )

    print("\n" + "=" * 68)
    print("SUCCESS. Set this in your Railway (or local) environment:\n")
    print(f"X_REFRESH_TOKEN={refresh}")
    print("=" * 68 + "\n")
    print("Granted scopes:", tokens.get("scope"))


if __name__ == "__main__":
    main()
