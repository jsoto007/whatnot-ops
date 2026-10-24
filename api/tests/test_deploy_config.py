"""render.yaml is production config: check the things a typo would break silently."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_render_blueprint_shape():
    cfg = yaml.safe_load((ROOT / "render.yaml").read_text())
    services = {s["name"]: s for s in cfg["services"]}
    web, worker = services["sales-ops"], services["sales-ops-worker"]
    assert web["type"] == "web" and worker["type"] == "worker"
    assert web["healthCheckPath"] == "/api/health"
    assert "db upgrade" in web["startCommand"] and "gunicorn wsgi:app" in web["startCommand"]
    assert web["startCommand"].index("ensure-support") < web["startCommand"].index("gunicorn")
    assert "--bind 0.0.0.0:$PORT" in web["startCommand"] and "--chdir" not in web["startCommand"]
    assert "npm run build" in web["buildCommand"]
    assert worker["startCommand"].strip() == "cd api && python -m app.worker"
    for svc in (web, worker):
        env = {e["key"]: e for e in svc["envVars"]}
        assert env["APP_ENV"]["value"] == "production"
        assert env["DATABASE_URL"]["fromDatabase"]["name"] == "whatnot-ops-db"
        assert "JOBS_INLINE" not in env and "AI_PROVIDER" not in env  # never committed; never fake
    assert cfg["databases"][0]["plan"] != "free"
    assert (ROOT / ".python-version").read_text().strip() == web["envVars"][0]["value"]


def test_version_pins_agree():
    node = (ROOT / ".node-version").read_text().strip()
    cfg = yaml.safe_load((ROOT / "render.yaml").read_text())
    web = next(s for s in cfg["services"] if s["name"] == "sales-ops")
    assert {e["key"]: e["value"] for e in web["envVars"] if "value" in e}["NODE_VERSION"] == node
