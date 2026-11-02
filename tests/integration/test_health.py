def test_health_reports_dependencies(anon):
    response = anon.get("/health")
    assert response.status_code == 200
    assert response.json() == {"mysql": "ok", "redis": "ok"}
