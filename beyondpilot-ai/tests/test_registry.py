from __future__ import annotations

import pytest

from beyondpilot.core.contracts import Llm, Message
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.registry import Registry, build_slot, resolve_class
from beyondpilot.core.settings import ResilienceConfig, SlotConfig, parse_settings


async def test_registry_builds_configured_slots(settings):
    registry = Registry(settings)
    llm = registry.get("demo", "llm")
    assert isinstance(llm, Llm)
    result = await llm.complete([Message("user", "xin chào")])
    assert result.text == "> xin chào"
    assert registry.slots() == [("demo", "llm")]


def test_type_name_is_file_name():
    from beyondpilot.slots.llm import echo

    assert resolve_class("llm", "echo") is echo.Impl


@pytest.mark.parametrize(
    ("kind", "type_name", "code"),
    [
        ("llm", "does_not_exist", ErrorCode.SLOT_NOT_FOUND),
        ("nope", "echo", ErrorCode.CONFIG_INVALID),
        ("llm", "../evil", ErrorCode.CONFIG_INVALID),
        ("llm", "Echo", ErrorCode.CONFIG_INVALID),
    ],
)
def test_resolve_errors(kind, type_name, code):
    with pytest.raises(AppError) as info:
        resolve_class(kind, type_name)
    assert info.value.code is code


def test_contract_mismatch_is_rejected():
    # The echo LLM does not satisfy the embedder contract.
    cfg = SlotConfig(type="echo", kind="embedder")
    with pytest.raises(AppError) as info:
        build_slot("emb", cfg, ResilienceConfig())
    assert info.value.code in (ErrorCode.SLOT_NOT_FOUND, ErrorCode.CONFIG_INVALID)


def test_bad_params_are_config_errors():
    cfg = SlotConfig(type="echo", params={"unknown": 1})
    with pytest.raises(AppError) as info:
        build_slot("llm", cfg, ResilienceConfig())
    assert info.value.code is ErrorCode.CONFIG_INVALID


def test_unconfigured_slot():
    registry = Registry(parse_settings({"auth_disabled": True}, env={}))
    with pytest.raises(AppError):
        registry.get("rag", "llm")


def test_litellm_slot_gets_resilience_and_no_client_retries():
    settings = parse_settings(
        {
            "auth_disabled": True,
            "resilience": {"attempts": 4},
            "modules": {
                "rag": {
                    "slots": {
                        "llm": {
                            "type": "litellm",
                            "params": {
                                "base_url": "http://localhost:4000/v1",
                                "api_key": "x",
                                "model": "qwen3",
                            },
                        }
                    }
                }
            },
        },
        env={},
    )
    llm = Registry(settings).get("rag", "llm")
    assert llm.resilience.attempts == 4
    assert llm.client.max_retries == 0
