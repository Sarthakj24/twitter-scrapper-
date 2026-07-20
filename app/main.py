"""Tiny FastAPI surface:
  GET  /health  — liveness probe (also confirms the DB is reachable).
  POST /run     — run the full pipeline now (fetch → classify → store → email).
  GET  /run     — same, convenient for a cron `curl` (see README).

The heavy lifting lives in app.pipeline; this file is deliberately thin.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query

from . import db, pipeline
from .config import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("twitter-scrapper")

app = FastAPI(title="twitter-scrapper hiring digest", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    log.info("DB initialized at %s", config.db_path)


@app.get("/health")
def health() -> dict[str, object]:
    # Touch the DB so the probe fails loudly if the volume isn't mounted.
    try:
        db.init_db()
        return {"status": "ok", "db": config.db_path}
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=503, detail=f"db unavailable: {exc}")


def _run(send: bool) -> dict[str, object]:
    try:
        config.require_worker()
        result = pipeline.run(send=send)
        log.info("run complete: %s", result)
        return result
    except Exception as exc:
        log.exception("run failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/run")
def run_post(send: bool = Query(True, description="Set false for a dry run (no email).")):
    return _run(send)


@app.get("/run")
def run_get(send: bool = Query(True, description="Set false for a dry run (no email).")):
    return _run(send)
