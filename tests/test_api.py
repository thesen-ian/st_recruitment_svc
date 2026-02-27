from typing import Any, Dict

import pytest


def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    json_body = resp.json()
    assert isinstance(json_body, dict)
    assert json_body.get("status") == "ok"


def test_validation_error_envelope(client):
    # Missing required_field will trigger validation error
    resp = client.post("/api/test/validate", json={})
    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body
    err = body["error"]
    assert err.get("type") == "validation_error"
    assert isinstance(err.get("message"), str)
    assert isinstance(err.get("details"), list)
    # details should contain at least one validation issue for required_field
    assert any(d.get("loc") for d in err.get("details"))


@pytest.mark.parametrize(
    "path, expected_type, expected_status",
    [
        ("/api/test/unauthorized", "unauthorized", 401),
        ("/api/test/forbidden", "forbidden", 403),
    ],
)
def test_auth_http_exceptions_envelope(client, path: str, expected_type: str, expected_status: int):
    resp = client.get(path)
    assert resp.status_code == expected_status
    body = resp.json()
    assert "error" in body
    err = body["error"]
    assert err.get("type") == expected_type
    assert isinstance(err.get("message"), str)
    # details should not be present for pure HTTP errors
    assert "details" not in err
