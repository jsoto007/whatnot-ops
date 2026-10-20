"""Serve the built React app (web/dist) from Flask in production.

Any path that is not /api/* returns index.html so the SPA router can take
over. In development Vite serves the app itself and proxies /api here, so a
missing dist just yields a short hint.
"""

import os

from flask import Flask, abort, send_from_directory
from werkzeug.security import safe_join

DIST = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "web", "dist"))


def register_spa(app: Flask) -> None:
    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def spa(path: str):
        if path.startswith("api/") or path.endswith(".map"):
            abort(404)  # source maps are not published
        full = safe_join(DIST, path) if path else None  # None for anything outside dist
        if full and os.path.isfile(full):
            return send_from_directory(DIST, path)
        index = os.path.join(DIST, "index.html")
        if not os.path.isfile(index):
            return "web/dist not built. Run `make web` for dev or `npm run build` in web/.", 503
        return send_from_directory(DIST, "index.html")
