"""Cron entrypoint — POST to the running web service's /run endpoint.

On Render a persistent Disk attaches to exactly ONE service, so the daily job
can't mount the web service's SQLite disk directly. Instead it drives the run
over HTTP (same as the Railway cron did with curl). Set WEB_URL to your web
service's public URL, e.g. https://twitter-scrapper.onrender.com
"""
import os
import sys
import urllib.request

base = os.environ.get("WEB_URL", "").rstrip("/")
if not base:
    sys.exit("Set WEB_URL to your Render web service URL (e.g. https://<app>.onrender.com)")

req = urllib.request.Request(base + "/run", method="POST")
try:
    with urllib.request.urlopen(req, timeout=600) as resp:
        print(resp.status, resp.read().decode("utf-8")[:1000])
except Exception as exc:  # surface a non-zero exit so Render marks the run failed
    sys.exit(f"trigger failed: {exc}")
