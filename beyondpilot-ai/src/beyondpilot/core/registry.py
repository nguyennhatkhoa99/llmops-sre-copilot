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


def slot_refs(cfg: SlotConfig) -> set[str]:
    """Names of other slots this slot depends on, written as ``"@name"`` in params."""
    return {
        v[1:]
        for v in cfg.params.values()
        if isinstance(v, str) and v.startswith("@") and _NAME_RE.match(v[1:])
    }


def build_slot(
    name: str,
    cfg: SlotConfig,
    resilience: ResilienceConfig,
    built: dict[str, Any] | None = None,
) -> Any:
    kind = cfg.kind or name
    cls = resolve_class(kind, cfg.type)
    built = built or {}
    params = {
        k: built[v[1:]] if isinstance(v, str) and v[1:] in built and v.startswith("@") else v
        for k, v in cfg.params.items()
    }
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
            built: dict[str, Any] = {}
            pending = dict(mcfg.slots)
            for name, scfg in pending.items():
                unknown = slot_refs(scfg) - set(mcfg.slots)
                if unknown:
                    raise AppError(
                        ErrorCode.CONFIG_INVALID,
                        f"{module}.{name} refers to unknown slot(s) {sorted(unknown)}",
                    )
            # Build in dependency order: a slot waits until the slots it refers to exist.
            while pending:
                ready = [n for n, c in pending.items() if slot_refs(c) <= built.keys()]
                if not ready:
                    raise AppError(ErrorCode.CONFIG_INVALID, f"{module}: circular slot references")
                for name in ready:
                    built[name] = build_slot(name, pending.pop(name), settings.resilience, built)
            for name, instance in built.items():
                self._slots[(module, name)] = instance

    def get(self, module: str, slot: str) -> Any:
        try:
            return self._slots[(module, slot)]
        except KeyError as exc:
            raise AppError(
                ErrorCode.CONFIG_INVALID, f"slot {module}.{slot} is not configured"
            ) from exc

    def slots(self) -> list[tuple[str, str]]:
        return sorted(self._slots)

    def has(self, module: str, slot: str) -> bool:
        return (module, slot) in self._slots
