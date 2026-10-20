"""The Chat Scraper extension, served by the portal it talks to.

GET /api/extension says whether a build is available and which version.
GET /api/extension/download zips the built extension (`extension/dist`, built
on deploy) and writes this portal's address into `defaults.json`, so a fresh
install already points at the right server and only needs a pairing code.
"""

import io
import json
import os
import zipfile
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file

from ..auth import require
from ..config import is_dev

bp = Blueprint("extension", __name__)


def dist_dir() -> Path:
    configured = os.environ.get("EXTENSION_DIST")
    if configured:
        return Path(configured)
    return Path(current_app.root_path).parents[1] / "extension" / "dist"


def _version(dist: Path) -> str | None:
    try:
        return json.loads((dist / "manifest.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        return None


def server_url() -> str | None:
    """The address the extension should upload to: this portal. In production it must
    come from configuration, never from the request's Host header."""
    base = os.environ.get("APP_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not base:
        if not is_dev(current_app.config.get("APP_ENV")):
            return None
        base = request.url_root
    return base.rstrip("/")


@bp.get("")
@require("shows.view")
def info():
    dist = dist_dir()
    version = _version(dist)
    url = server_url()
    return jsonify(
        {"available": version is not None and url is not None, "version": version, "serverUrl": url}
    )


@bp.get("/download")
@require("shows.view")
def download():
    dist = dist_dir()
    version = _version(dist)
    if version is None:
        return jsonify({"error": "the extension has not been built on this server"}), 503
    url = server_url()
    if url is None:
        return jsonify({"error": "set APP_URL so the extension knows where to upload"}), 503
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(dist.rglob("*")):
            if (
                f.is_file()
                and not f.is_symlink()
                and f.name != "defaults.json"
                and not f.name.endswith(".zip")
            ):
                z.write(f, f.relative_to(dist).as_posix())
        z.writestr("defaults.json", json.dumps({"serverUrl": url}))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"chat-scraper-{version}.zip",
        max_age=0,
    )
