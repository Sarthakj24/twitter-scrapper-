# twitter-scrapper — X hiring-digest service

Monitors your X (Twitter) **home timeline**, classifies hiring-related posts with
Claude, stores them in SQLite, and emails you a daily digest. Deploys on Railway.

> Despite the repo name, this uses the **official X API v2** (OAuth 2.0 user
> context) — not scraping.

## How it works

```
X home timeline ──▶ keyword pre-filter ──▶ Claude (haiku) ──▶ SQLite ──▶ email digest
   (API v2)          (cheap skip)          (JSON verdict)     (dedup)     (SMTP)
```

- **Fetch**: `GET /2/users/:id/timelines/reverse_chronological`, `max_results=100`,
  paginating until it reaches a tweet id already stored (bounded by `since_id`).
- **Classify**: a cheap keyword pre-filter discards clearly-non-hiring tweets;
  everything ambiguous goes to `claude-haiku-4-5-20251001` which returns strict
  JSON (`is_hiring`, `role`, `company`, `location`, `seniority`, `apply_url`,
  `summary`).
- **Store**: `seen(id, created_at, is_hiring, emailed, data)` — dedup on `id`,
  full classified record kept as JSON in `data`.
- **Email**: HTML + text digest of hiring posts not yet emailed, then marks them
  emailed. Sent via stdlib `smtplib` (any SMTP provider).

## Endpoints

| Method | Path      | Purpose                                             |
|--------|-----------|-----------------------------------------------------|
| GET    | `/health` | Liveness + DB reachability probe.                   |
| POST   | `/run`    | Run the full pipeline now. `?send=false` for a dry run. |
| GET    | `/run`    | Same as POST (convenient for a cron `curl`).        |

## One-time auth (run locally, once)

X home timeline needs **OAuth 2.0 user context** (Authorization Code + PKCE), not
an app-only bearer token.

1. Create an app at <https://developer.x.com>, enable **OAuth 2.0**.
   - App type **Web App / Automated** → confidential (has a client secret).
   - App type **Native** → public (PKCE only, no secret).
   - Set the **callback URL** to `http://127.0.0.1:8721/callback`.
   - Scopes: `tweet.read users.read offline.access`.
2. Run the flow:
   ```bash
   pip install -r requirements.txt
   export X_CLIENT_ID=...            # and X_CLIENT_SECRET=... if confidential
   export X_REDIRECT_URI=http://127.0.0.1:8721/callback
   python auth_setup.py
   ```
   It opens the authorize page, catches the redirect, and prints
   `X_REFRESH_TOKEN=...`. Copy that into your env.

> **Refresh-token rotation:** X issues a *new* refresh token on every refresh
> (because of `offline.access`). The worker persists the rotated token in SQLite
> and always uses the latest — so the `X_REFRESH_TOKEN` env value is only the
> first-run seed. This is why the SQLite volume must persist (below).

## Run locally

```bash
cp .env.example .env      # fill in the values
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
curl -X POST 'http://localhost:8000/run?send=false'   # dry run, no email
```

## Deploy on Railway

1. **Push this repo** and create a Railway project from it (uses the `Dockerfile`).
2. **Add a Volume** mounted at `/app/data` and set `DB_PATH=/app/data/hiring.db`
   so SQLite (and the rotated refresh token) survive restarts/redeploys.
3. **Set env vars** from `.env.example` (X_*, ANTHROPIC_API_KEY, SMTP_*, EMAIL_*).
4. Healthcheck path is `/health` (already in `railway.toml`).
5. **Daily digest** — add a second service in the same project as a **Cron**:
   - Schedule (Railway cron, UTC): `0 13 * * *` (e.g. 1pm UTC).
   - Command: `curl -fsS -X POST "$WEB_URL/run"`
     where `WEB_URL` is your web service's URL (reference it via a Railway
     variable). The cron container spins up, hits `/run`, and exits.

## Cost & switching to a List

X v2 timeline reads are **pay-per-use (~$0.005/read; reads of tweets you own are
cheaper)**. To narrow scope and cut cost, read a curated **List** instead of the
whole home timeline — set `X_LIST_ID`; the client switches to
`GET /2/lists/:id/tweets` automatically (a one-line change in `app/x_client.py`).

## Layout

```
app/
  main.py        FastAPI: /health, /run
  config.py      env config
  db.py          sqlite3 storage (seen + kv)
  x_client.py    OAuth refresh (+rotation), timeline fetch, pagination
  classify.py    keyword pre-filter + Claude classification
  digest.py      HTML/text email build + SMTP send
  pipeline.py    fetch → classify → store → email
auth_setup.py    one-time local PKCE flow
```

## Notes

- The classification JSON schema in `app/classify.py` is easy to extend — add a
  key to `CLASSIFICATION_KEYS` and mention it in the system prompt.
- To use a transactional email API (Resend/SendGrid/SES) instead of SMTP,
  replace `send_email()` in `app/digest.py` only.
