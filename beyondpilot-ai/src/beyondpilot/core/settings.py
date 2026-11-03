"""Load ``config.yaml`` and resolve secrets from the environment.

Secrets never live in the file. The file references them as ``${NAME}`` or
``${NAME:-default}``; in production OpenBao injects them as environment variables.
A missing variable without a default stops start-up with ``CONFIG_INVALID``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, ValidationError, model_validator

from beyondpilot.core.errors import AppError, ErrorCode

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class SlotConfig(BaseModel):
    """One slot of a module: which implementation to load and its parameters."""

    type: str
    kind: str | None = None  # contract name; defaults to the slot's key in the module
    params: dict[str, Any] = Field(default_factory=dict)


class ModuleConfig(BaseModel):
    slots: dict[str, SlotConfig] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)  # module-specific knobs, e.g. top_k


class ResilienceConfig(BaseModel):
    attempts: int = Field(3, ge=1, le=10)
    base_delay: float = Field(0.5, ge=0)
    max_delay: float = Field(8.0, ge=0)
    timeout: float = Field(30.0, gt=0)


class ValidatorConfig(BaseModel):
    max_retries: int = Field(2, ge=0, le=5)
    tolerance: float = Field(0.005, ge=0)  # relative tolerance when matching numbers


class SafetyConfig(BaseModel):
    block_on_pii: bool = False  # mask PII and continue by default; injection always blocks


class Settings(BaseModel):
    api_keys: list[SecretStr] = Field(default_factory=list)
    api_key_header: str = "X-API-Key"
    auth_disabled: bool = False  # local development only
    resilience: ResilienceConfig = Field(default_factory=ResilienceConfig)
    validator: ValidatorConfig = Field(default_factory=ValidatorConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    modules: dict[str, ModuleConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_keys(self) -> Settings:
        if not self.auth_disabled and not [k for k in self.api_keys if k.get_secret_value()]:
            raise ValueError("api_keys must not be empty unless auth_disabled is true")
        return self


def _expand(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, str):

        def sub(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in env:
                return env[name]
            if default is not None:
                return default
            raise AppError(ErrorCode.CONFIG_INVALID, f"missing environment variable {name}")

        return _ENV_RE.sub(sub, value)
    if isinstance(value, list):
        return [_expand(v, env) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, env) for k, v in value.items()}
    return value


def parse_settings(raw: Mapping[str, Any], env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    try:
        return Settings.model_validate(_expand(dict(raw), env))
    except ValidationError as exc:
        fields = ", ".join(".".join(str(p) for p in e["loc"]) or "<root>" for e in exc.errors())
        raise AppError(ErrorCode.CONFIG_INVALID, f"invalid fields: {fields}") from exc


def load_settings(path: str | Path | None = None, env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    path = Path(path or env.get("BEYONDPILOT_CONFIG", "config.yaml"))
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise AppError(ErrorCode.CONFIG_INVALID, f"config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise AppError(ErrorCode.CONFIG_INVALID, f"config file is not valid YAML: {path}") from exc
    if not isinstance(raw, dict):
        raise AppError(ErrorCode.CONFIG_INVALID, "config root must be a mapping")
    return parse_settings(raw, env)
