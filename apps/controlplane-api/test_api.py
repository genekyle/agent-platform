from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def _svc(service_id: str, status: str, required: bool = True):
    return {
        "id": service_id,
        "label": service_id,
        "kind": "test",
        "status": status,
        "reachable": status == "healthy",
        "required_for_training": required,
        "endpoint_or_target": service_id,
        "message": status,
        "details": {},
    }


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True


def test_system_status_all_healthy(monkeypatch):
    services = [
        _svc("controlplane_api", "healthy"),
        _svc("capture_server", "healthy"),
        _svc("chrome_cdp", "healthy"),
        _svc("postgres", "healthy"),
        _svc("redis", "healthy", required=False),
        _svc("artifacts_dir", "healthy"),
    ]
    monkeypatch.setattr(main, "collect_system_services", lambda: services)

    response = client.get("/api/system/status")

    assert response.status_code == 200
    body = response.json()
    assert body["overall_status"] == "healthy"
    assert [service["id"] for service in body["services"]] == [service["id"] for service in services]


def test_system_status_capture_server_down(monkeypatch):
    services = [
        _svc("controlplane_api", "healthy"),
        _svc("capture_server", "down"),
        _svc("chrome_cdp", "healthy"),
        _svc("postgres", "healthy"),
        _svc("redis", "healthy", required=False),
        _svc("artifacts_dir", "healthy"),
    ]
    monkeypatch.setattr(main, "collect_system_services", lambda: services)

    response = client.get("/api/system/status")

    assert response.status_code == 200
    assert response.json()["overall_status"] == "down"


def test_system_status_chrome_down(monkeypatch):
    services = [
        _svc("controlplane_api", "healthy"),
        _svc("capture_server", "healthy"),
        _svc("chrome_cdp", "down"),
        _svc("postgres", "healthy"),
        _svc("redis", "healthy", required=False),
        _svc("artifacts_dir", "healthy"),
    ]
    monkeypatch.setattr(main, "collect_system_services", lambda: services)

    response = client.get("/api/system/status")

    assert response.status_code == 200
    assert response.json()["overall_status"] == "down"


def test_system_status_database_unavailable(monkeypatch):
    services = [
        _svc("controlplane_api", "healthy"),
        _svc("capture_server", "healthy"),
        _svc("chrome_cdp", "healthy"),
        _svc("postgres", "down"),
        _svc("redis", "healthy", required=False),
        _svc("artifacts_dir", "healthy"),
    ]
    monkeypatch.setattr(main, "collect_system_services", lambda: services)

    response = client.get("/api/system/status")

    assert response.status_code == 200
    assert response.json()["overall_status"] == "down"


def test_system_status_artifacts_dir_missing(monkeypatch):
    services = [
        _svc("controlplane_api", "healthy"),
        _svc("capture_server", "healthy"),
        _svc("chrome_cdp", "healthy"),
        _svc("postgres", "healthy"),
        _svc("redis", "healthy", required=False),
        _svc("artifacts_dir", "down"),
    ]
    monkeypatch.setattr(main, "collect_system_services", lambda: services)

    response = client.get("/api/system/status")

    assert response.status_code == 200
    assert response.json()["overall_status"] == "down"


def test_system_status_mixed_state_overall_is_degraded(monkeypatch):
    services = [
        _svc("controlplane_api", "healthy"),
        _svc("capture_server", "healthy"),
        _svc("chrome_cdp", "healthy"),
        _svc("postgres", "healthy"),
        _svc("redis", "down", required=False),
        _svc("artifacts_dir", "degraded"),
    ]
    monkeypatch.setattr(main, "collect_system_services", lambda: services)

    response = client.get("/api/system/status")

    assert response.status_code == 200
    assert response.json()["overall_status"] == "degraded"
