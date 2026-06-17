"""Tiny HTTP API exposing the manual D1 sync.

Only called server-to-server, by the Cloudflare Pages Function behind the
dashboard's Refresh button (frontend/functions/api/sync.js) — never
directly from the browser, so the shared secret stays off the client.

Run via:
    .venv/bin/python -m api.server
or as the systemd unit in deploy/sync-api.service.
"""
from __future__ import annotations

from flask import Flask, jsonify, request

from config.settings import settings
from storage.db import log_error
from sync.d1_sync import D1SyncError, sync_to_d1

app = Flask(__name__)


def _authorized(req) -> bool:
    if not settings.sync_api_token:
        return False
    auth = req.headers.get("Authorization", "")
    return auth == f"Bearer {settings.sync_api_token}"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/sync", methods=["POST"])
def sync():
    if not _authorized(request):
        return jsonify({"error": "unauthorized"}), 401

    try:
        counts = sync_to_d1()
    except D1SyncError as exc:
        log_error("d1_sync", str(exc))
        return jsonify({"error": str(exc)}), 502

    return jsonify({"status": "ok", "counts": counts})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=settings.sync_api_port)
