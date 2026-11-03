"""Turn ``type:`` in config into slot instances.

Rule: the type name is the file name. ``kind: llm, type: litellm`` loads the
class ``Impl`` from ``beyondpilot.slots.llm.litellm``. The instance must satisfy
the contract for its kind, otherwise start-up stops with ``CONFIG_INVALID``.
"""

from __future__ import annotations

import importlib
import re
from typing import Any

from beyondpilot.core.contracts import CONTRACTS
from beyondpilot.core.errors import AppError, ErrorCode
from beyondpilot.core.settings import ResilienceConfig, Settings, SlotConfig

SLOT_PACKAGE = "beyondpilot.slots"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def resolve_class(kind: str, type_name: str) -> type:
    if kind not in CONTRACTS:
        raise AppError(ErrorCode.CONFIG_INVALID, f"unknown slot kind {kind!r}")
    if not _NAME_RE.match(type_name):
        raise AppError(ErrorCode.CONFIG_INVALID, f"invalid slot type {type_name!r}")
    module_name = f"{SLOT_PACKAGE}.{kind}.{type_name}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            raise AppError(ErrorCode.SLOT_NOT_FOUND, f"no implementation {module_name}") from exc
        # The implementation exists but one of its dependencies is not installed.
        raise AppError(
            ErrorCode.CONFIG_INVALID, f"{module_name} needs missing package {exc.name}"
        ) from exc
    cls = getattr(module, "Impl", None)
    if not isinstance(cls, type):
        raise AppError(ErrorCode.CONFIG_INVALID, f"{module_name} has no class Impl")
    return cls


def build_slot(name: str, cfg: SlotConfig, resilience: ResilienceConfig) -> Any:
    kind = cfg.kind or name
    cls = resolve_class(kind, cfg.type)
    params = dict(cfg.params)
    if getattr(cls, "accepts_resilience", False):
        params.setdefault("resilience", resilience)
    try:
        instance = cls(**params)
    except TypeError as exc:
        raise AppError(
            ErrorCode.CONFIG_INVALID, f"bad params for {kind}.{cfg.type}: {exc}"
        ) from exc
    if not isinstance(instance, CONTRACTS[kind]):
        raise AppError(
            ErrorCode.CONFIG_INVALID, f"{kind}.{cfg.type} does not satisfy the {kind} contract"
        )
    return instance


class Registry:
    """Every slot of every module, built once at start-up."""

    def __init__(self, settings: Settings) -> None:
        self._slots: dict[tuple[str, str], Any] = {}
        for module, mcfg in settings.modules.items():
            for name, scfg in mcfg.slots.items():
                self._slots[(module, name)] = build_slot(name, scfg, settings.resilience)

    def get(self, module: str, slot: str) -> Any:
        try:
            return self._slots[(module, slot)]
        except KeyError as exc:
            raise AppError(
                ErrorCode.CONFIG_INVALID, f"slot {module}.{slot} is not configured"
            ) from exc

    def slots(self) -> list[tuple[str, str]]:
        return sorted(self._slots)
