"""The Chat Scraper extension, served by the portal it talks to.

GET /api/extension says whether a build is available and which version.
GET /api/extension/download zips the built extension (`extension/dist`, built
on deploy) and writes this portal's address into `defaults.json`, so a fresh
install already points at the right server and only needs a pairing code. It
also writes this portal into `manifest.json`: a small content script tells the
web app which Chat Scraper version runs on the laptop, and `externally_connectable`
lets the web app ask it to reload itself after its files were replaced (an
update in place keeps the laptop paired; removing the extension would not).
"""

import io
import json
import os
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

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


def latest_version() -> str | None:
    """The version this portal serves, or None when the extension has not been built here."""
    return _version(dist_dir())


def portal_pattern(url: str) -> str:
    """A Chrome match pattern for this portal. Ports are left out: Chrome patterns
    without one match any port, and production has none."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:  # IPv6
        host = f"[{host}]"
    return f"{parts.scheme}://{host}/*"


def manifest_for(dist: Path, url: str) -> str:
    manifest = json.loads((dist / "manifest.json").read_text())
    pattern = portal_pattern(url)
    manifest["externally_connectable"] = {"matches": [pattern]}
    scripts = [
        cs for cs in manifest.get("content_scripts", []) if "portal.js" not in cs.get("js", [])
    ]
    scripts.append({"matches": [pattern], "js": ["portal.js"], "run_at": "document_start"})
    manifest["content_scripts"] = scripts
    return json.dumps(manifest, indent=2)


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
    from flask import g

    from ..services import ratelimit

    ratelimit.per_user("extension-download", g.u.id, 20, 600)  # zipped on every call
    dist = dist_dir()
    version = _version(dist)
    if version is None:
        return jsonify({"error": "the extension has not been built on this server"}), 503
    url = server_url()
    if url is None:
        return jsonify(
            {"error": "this server does not know its public address yet; ask your developer"}
        ), 503
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(dist.rglob("*")):
            if (
                f.is_file()
                and not f.is_symlink()
                and f.name not in ("defaults.json", "manifest.json")
                and not f.name.endswith(".zip")
            ):
                z.write(f, f.relative_to(dist).as_posix())
        z.writestr("defaults.json", json.dumps({"serverUrl": url}))
        z.writestr("manifest.json", manifest_for(dist, url))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"chat-scraper-{version}.zip",
        max_age=0,
    )
