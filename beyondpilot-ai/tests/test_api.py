from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from beyondpilot.api.app import create_app
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.settings import parse_settings


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        yield c


def test_health_needs_no_key(client):
    r = client.get("/internal/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_ready_reports_slots_and_safety(client):
    body = client.get("/internal/v1/ready").json()
    assert body["status"] == "ready" and body["slots"] == 1
    assert body["safety"]["injection"] in ("regex", "llm_guard")


def test_missing_key(client):
    r = client.get("/api/v1/ping")
    assert r.status_code == 401
    assert r.json() == {"code": "UNAUTHENTICATED", "message": "Thiếu API key."}


def test_wrong_key(client):
    r = client.get("/api/v1/ping", headers={"X-API-Key": "key-on"})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("key", ["key-one", "key-two"])
def test_valid_keys(client, key):
    r = client.get("/api/v1/ping", headers={"X-API-Key": key})
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_auth_disabled_for_local_dev():
    app = create_app(parse_settings({"auth_disabled": True}, env={}))
    with TestClient(app) as c:
        assert c.get("/api/v1/ping").status_code == 200


def test_unexpected_errors_are_wrapped(settings):
    app = create_app(settings)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("stack trace with secrets")

    @app.get("/app-error")
    async def app_error():
        raise AppError(ErrorCode.UPSTREAM_TIMEOUT, "litellm at 10.0.0.9 timed out")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom")
        assert r.status_code == 500 and r.json()["code"] == "INTERNAL"
        assert "secrets" not in r.text
        r = c.get("/app-error")
        assert r.status_code == 504 and "10.0.0.9" not in r.text


def test_bad_slot_config_fails_at_startup():
    settings = parse_settings(
        {"auth_disabled": True, "modules": {"rag": {"slots": {"llm": {"type": "missing"}}}}}, env={}
    )
    with pytest.raises(AppError) as info:
        with TestClient(create_app(settings)):
            pass
    assert info.value.code is ErrorCode.SLOT_NOT_FOUND
