"""End-to-end run: fetch new tweets → classify → store → email digest.

This is what the manual /run endpoint and the daily scheduled job both call.
Sending is idempotent-ish: a tweet is emailed at most once (emailed flag), so
running /run more than once a day just picks up whatever is new since last time.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from . import db, digest, x_client
from .classify import classify


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def run(send: bool = True) -> dict[str, Any]:
    db.init_db()

    tweets = x_client.fetch_new_tweets()

    classified_hiring = 0
    for tw in tweets:
        result = classify(tw)
        result["author"] = tw.get("author")
        result["text"] = tw.get("text")
        db.upsert_tweet(
            tweet_id=tw["id"],
            created_at=tw.get("created_at", ""),
            is_hiring=result["is_hiring"],
            data=result,
        )
        if result["is_hiring"]:
            classified_hiring += 1

    pending = db.pending_digest()

    emailed = 0
    if send and pending and _emailing_on():
        subject = f"Hiring digest — {len(pending)} new · {_today()}"
        digest.send_email(subject, digest.build_html(pending), digest.build_text(pending))
        db.mark_emailed([r["id"] for r in pending])
        emailed = len(pending)

    return {
        "fetched": len(tweets),
        "new_hiring": classified_hiring,
        "pending_before_email": len(pending),
        "emailed": emailed,
        "sent": bool(emailed),
    }


def _emailing_on() -> bool:
    from .config import config
    return config.email_enabled
