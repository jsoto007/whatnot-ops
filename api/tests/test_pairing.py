"""Laptop pairing codes (/api/devices/pairings, /api/pair) and the extension download."""

import io
import json
import threading
import zipfile
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import db
from app.models import Device, DevicePairing
from app.services import devices

PW = "correct-horse-battery"
EXT = {"Origin": "chrome-extension://abcdefghijklmnop"}


def owner(client):
    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    client.post(
        "/api/auth/signup",
        json={"organization": "Shop", "name": "O", "email": "o@shop.test", "password": PW},
        headers=h,
    )
    return h


def new_code(client, h, name="Front laptop"):
    r = client.post("/api/devices/pairings", json={"name": name}, headers=h)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_a_code_pairs_one_laptop_once(client, app):
    h = owner(client)
    p = new_code(client, h)
    assert len(p["code"]) == 9 and p["code"][4] == "-"
    assert client.get(f"/api/devices/pairings/{p['pairingId']}").get_json()["status"] == "waiting"

    # The laptop: no session, typed in lower case without the dash.
    laptop = app.test_client()
    r = laptop.post("/api/pair", json={"code": p["code"].replace("-", "").lower()}, headers=EXT)
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["key"].startswith("wn_") and body["device"] == "Front laptop"
    assert body["organization"] == "Shop"
    assert r.headers["Access-Control-Allow-Origin"] == EXT["Origin"]
    ping = laptop.get("/api/ingest/ping", headers={"Authorization": "Bearer " + body["key"]})
    assert ping.status_code == 200 and ping.get_json()["device"] == "Front laptop"

    status = client.get(f"/api/devices/pairings/{p['pairingId']}").get_json()
    assert status["status"] == "connected" and status["device"]["name"] == "Front laptop"
    again = laptop.post("/api/pair", json={"code": p["code"]}, headers=EXT)
    assert again.status_code == 404
    with app.app_context():
        assert db.session.scalar(select(db.func.count()).select_from(Device)) == 1
        stored = db.session.scalar(select(DevicePairing))
        assert p["code"].replace("-", "") not in stored.code_hash  # only the hash is kept
    said = [e["summary"] for e in client.get("/api/audit").get_json()["entries"]]
    assert "Started pairing laptop “Front laptop”" in said
    assert "Paired laptop “Front laptop”" in said


def test_expired_wrong_and_malformed_codes_are_refused(client, app):
    h = owner(client)
    p = new_code(client, h)
    with app.app_context():
        row = db.session.get(DevicePairing, p["pairingId"])
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.session.commit()
    laptop = app.test_client()
    assert laptop.post("/api/pair", json={"code": p["code"]}, headers=EXT).status_code == 404
    assert client.get(f"/api/devices/pairings/{p['pairingId']}").get_json()["status"] == "expired"
    assert laptop.post("/api/pair", json={"code": "ABCD-EFGH"}, headers=EXT).status_code == 404
    assert laptop.post("/api/pair", json={"code": "ABC"}, headers=EXT).status_code == 400
    assert laptop.post("/api/pair", data="ABCDEFGH", headers=EXT).status_code == 400
    assert laptop.options("/api/pair", headers=EXT).status_code == 204


def test_open_codes_are_capped_and_need_a_name_and_a_manager(client, app):
    h = owner(client)
    assert client.post("/api/devices/pairings", json={"name": " "}, headers=h).status_code == 400
    for i in range(devices.MAX_OPEN_PAIRINGS):
        new_code(client, h, name=f"Laptop {i}")
    r = client.post("/api/devices/pairings", json={"name": "One too many"}, headers=h)
    assert r.status_code == 429

    token = (
        client.post("/api/team/invites", json={"email": "v@shop.test", "role": "viewer"}, headers=h)
        .get_json()["acceptUrl"]
        .rsplit("/", 1)[1]
    )
    v = app.test_client()
    hv = {"X-CSRF-Token": v.get("/api/auth/csrf").get_json()["token"]}
    v.post(
        "/api/auth/invites/accept", json={"token": token, "name": "V", "password": PW}, headers=hv
    )
    assert v.post("/api/devices/pairings", json={"name": "Mine"}, headers=hv).status_code == 403


def test_ten_laptops_racing_on_one_code_get_one_key(client, app):
    h = owner(client)
    code = new_code(client, h)["code"]
    wins: list[str] = []
    lock = threading.Lock()

    def laptop():
        with app.app_context():
            try:
                _, _, key = devices.redeem_pairing(code)
                db.session.commit()
                with lock:
                    wins.append(key)
            except devices.PairingError:
                db.session.rollback()
            finally:
                db.session.remove()

    threads = [threading.Thread(target=laptop) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1
    with app.app_context():
        assert db.session.scalar(select(db.func.count()).select_from(Device)) == 1


# ---------- extension download ----------


def test_download_bakes_this_portal_into_the_extension(client, tmp_path, monkeypatch):
    owner(client)
    monkeypatch.setenv("EXTENSION_DIST", str(tmp_path / "missing"))
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    assert client.get("/api/extension").get_json()["available"] is False
    assert client.get("/api/extension/download").status_code == 503

    dist = tmp_path / "dist"
    dist.mkdir()
    whatnot = {"matches": ["https://www.whatnot.com/*"], "js": ["content.js"]}
    (dist / "manifest.json").write_text(
        json.dumps({"version": "9.9.9", "content_scripts": [whatnot]})
    )
    (dist / "background.js").write_text("// worker")
    (dist / "defaults.json").write_text(json.dumps({"serverUrl": ""}))
    (dist / "old.zip").write_text("stale")
    monkeypatch.setenv("EXTENSION_DIST", str(dist))
    info = client.get("/api/extension").get_json()
    assert info == {"available": True, "version": "9.9.9", "serverUrl": "http://localhost"}

    r = client.get("/api/extension/download")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert "chat-scraper-9.9.9.zip" in r.headers["Content-Disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.data))
    assert sorted(z.namelist()) == ["background.js", "defaults.json", "manifest.json"]
    assert json.loads(z.read("defaults.json")) == {"serverUrl": "http://localhost"}

    monkeypatch.setenv("APP_URL", "https://ops.example.com/")
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/extension/download").data))
    assert json.loads(z.read("defaults.json")) == {"serverUrl": "https://ops.example.com"}
    # Only this portal may talk to the extension (version check, reload after an update).
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["version"] == "9.9.9"
    assert manifest["externally_connectable"] == {"matches": ["https://ops.example.com/*"]}
    assert manifest["content_scripts"] == [
        whatnot,
        {"matches": ["https://ops.example.com/*"], "js": ["portal.js"], "run_at": "document_start"},
    ]
    # The built files on the server are never changed.
    assert "externally_connectable" not in json.loads((dist / "manifest.json").read_text())


def test_portal_pattern_drops_the_port():
    from app.routes.extension import portal_pattern

    assert portal_pattern("http://127.0.0.1:5055") == "http://127.0.0.1/*"
    assert portal_pattern("https://sales-ops.example.com") == "https://sales-ops.example.com/*"
    assert portal_pattern("http://[::1]:5050") == "http://[::1]/*"


def test_download_needs_a_session(app):
    assert app.test_client().get("/api/extension/download").status_code == 401


def test_laptops_list_shows_each_extension_version(client):
    h = owner(client)
    code = new_code(client, h)["code"]
    key = client.post("/api/pair", json={"code": code}, headers=EXT).get_json()["key"]
    bearer = {**EXT, "Authorization": f"Bearer {key}"}
    [d] = client.get("/api/devices").get_json()["devices"]
    assert d["extensionVersion"] is None
    client.get("/api/ingest/ping", headers={**bearer, "X-Extension-Version": "1.5.0"})
    [d] = client.get("/api/devices").get_json()["devices"]
    assert d["extensionVersion"] == "1.5.0"
