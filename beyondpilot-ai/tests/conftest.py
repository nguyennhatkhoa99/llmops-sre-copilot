from __future__ import annotations

import pytest

from beyondpilot.core.settings import Settings, parse_settings


@pytest.fixture
def settings() -> Settings:
    return parse_settings(
        {
            "api_keys": ["key-one", "key-two"],
            "resilience": {"attempts": 3, "base_delay": 0.1, "max_delay": 1, "timeout": 2},
            "modules": {"demo": {"slots": {"llm": {"type": "echo", "params": {"prefix": "> "}}}}},
        },
        env={},
    )
