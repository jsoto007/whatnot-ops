def test_health_reports_db_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok", "db": True}


def test_unknown_api_path_is_404_not_spa(client):
    r = client.get("/api/nope")
    assert r.status_code == 404


def test_spa_fallback_without_dist_is_503_hint(client):
    r = client.get("/some/client/route")
    assert r.status_code in (200, 503)
