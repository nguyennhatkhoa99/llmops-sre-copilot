from __future__ import annotations

import pytest

from beyondpilot.core.errors import CATALOGUE, AppError, ErrorCode
from beyondpilot.core.settings import load_settings, parse_settings


def test_every_code_has_catalogue_entry():
    assert set(CATALOGUE) == set(ErrorCode)


def test_app_error_hides_detail_from_client():
    err = AppError(ErrorCode.UPSTREAM_ERROR, "secret host 10.0.0.5 said 500")
    assert err.status == 502
    assert err.to_dict() == {
        "code": "UPSTREAM_ERROR",
        "message": CATALOGUE[ErrorCode.UPSTREAM_ERROR].message,
    }
    assert "10.0.0.5" not in str(err.to_dict())


def test_env_substitution_and_defaults():
    s = parse_settings(
        {
            "api_keys": ["${KEY}"],
            "modules": {
                "rag": {
                    "slots": {
                        "llm": {
                            "type": "litellm",
                            "params": {
                                "base_url": "${URL:-http://litellm:4000/v1}",
                                "api_key": "${KEY}",
                                "model": "qwen3",
                            },
                        }
                    }
                }
            },
        },
        env={"KEY": "abc"},
    )
    assert s.api_keys[0].get_secret_value() == "abc"
    params = s.modules["rag"].slots["llm"].params
    assert params["base_url"] == "http://litellm:4000/v1"
    assert params["api_key"] == "abc"


def test_missing_env_stops_startup():
    with pytest.raises(AppError) as info:
        parse_settings({"api_keys": ["${NOPE}"]}, env={})
    assert info.value.code is ErrorCode.CONFIG_INVALID
    assert "NOPE" in info.value.detail


def test_api_keys_required_unless_auth_disabled():
    with pytest.raises(AppError):
        parse_settings({"api_keys": []}, env={})
    assert parse_settings({"auth_disabled": True}, env={}).auth_disabled


def test_secret_not_in_repr():
    s = parse_settings({"api_keys": ["super-secret"]}, env={})
    assert "super-secret" not in repr(s)


def test_load_settings_from_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("api_keys: ['${K}']\nresilience: {attempts: 5}\n", encoding="utf-8")
    s = load_settings(path, env={"K": "x"})
    assert s.resilience.attempts == 5


def test_load_settings_errors(tmp_path):
    with pytest.raises(AppError):
        load_settings(tmp_path / "missing.yaml", env={})
    bad = tmp_path / "bad.yaml"
    bad.write_text("api_keys: [unclosed", encoding="utf-8")
    with pytest.raises(AppError):
        load_settings(bad, env={})


def test_template_file_is_valid():
    from pathlib import Path

    template = Path(__file__).resolve().parent.parent / "config.yaml.template"
    s = load_settings(
        template,
        env={
            "BEYONDPILOT_API_KEY": "k",
            "LITELLM_API_KEY": "l",
            "PG_DSN": "postgresql://x",
            "BACKEND_API_KEY": "b",
        },
    )
    assert s.modules["rag"].slots["llm"].type == "litellm"
    assert set(s.modules) == {
        "rag",
        "report",
        "content_check",
        "documents",
        "vision",
        "forecast",
        "ot",
        "radar",
    }
    assert s.modules["content_check"].slots["review"].params["api_key"] == "b"
