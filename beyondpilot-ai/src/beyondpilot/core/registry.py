"""Turn ``type:`` in config into slot instances.

Rule: the type name is the file name. ``kind: llm, type: litellm`` loads the
class ``Impl`` from ``beyondpilot.slots.llm.litellm``. The instance must satisfy
the contract for its kind, otherwise start-up stops with ``CONFIG_INVALID``.

A param written as ``"@name"`` is replaced by the slot ``name`` of the same
module; ``"@module.name"`` points to a slot of another module (for example
module D writing into module B's store with ``"@rag.store"``). Slots are built
in dependency order; unknown or circular references stop start-up.
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
_REF_RE = re.compile(r"^@([a-z][a-z0-9_]*)(?:\.([a-z][a-z0-9_]*))?$")

SlotId = tuple[str, str]  # (module, slot)


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


def parse_ref(value: Any, module: str) -> SlotId | None:
    if not isinstance(value, str):
        return None
    match = _REF_RE.match(value)
    if not match:
        return None
    first, second = match.groups()
    return (first, second) if second else (module, first)


def slot_refs(cfg: SlotConfig, module: str = "") -> set[SlotId]:
    """Slots this slot depends on, from ``"@name"`` / ``"@module.name"`` params."""
    return {ref for v in cfg.params.values() if (ref := parse_ref(v, module)) is not None}


def build_slot(
    name: str,
    cfg: SlotConfig,
    resilience: ResilienceConfig,
    built: dict[SlotId, Any] | None = None,
    module: str = "",
) -> Any:
    kind = cfg.kind or name
    cls = resolve_class(kind, cfg.type)
    built = built or {}
    params: dict[str, Any] = {}
    for key, value in cfg.params.items():
        ref = parse_ref(value, module)
        params[key] = built[ref] if ref is not None and ref in built else value
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
        pending: dict[SlotId, SlotConfig] = {
            (module, name): scfg
            for module, mcfg in settings.modules.items()
            for name, scfg in mcfg.slots.items()
        }
        deps = {sid: slot_refs(cfg, sid[0]) for sid, cfg in pending.items()}
        for sid, refs in deps.items():
            unknown = refs - pending.keys()
            if unknown:
                names = sorted(f"{m}.{s}" for m, s in unknown)
                raise AppError(
                    ErrorCode.CONFIG_INVALID,
                    f"{sid[0]}.{sid[1]} refers to unknown slot(s) {names}",
                )
        self._slots: dict[SlotId, Any] = {}
        # Build in dependency order: a slot waits until the slots it refers to exist.
        while pending:
            ready = [sid for sid in pending if deps[sid] <= self._slots.keys()]
            if not ready:
                names = sorted(f"{m}.{s}" for m, s in pending)
                raise AppError(ErrorCode.CONFIG_INVALID, f"circular slot references in {names}")
            for sid in ready:
                cfg = pending.pop(sid)
                self._slots[sid] = build_slot(
                    sid[1], cfg, settings.resilience, self._slots, module=sid[0]
                )

    def get(self, module: str, slot: str) -> Any:
        try:
            return self._slots[(module, slot)]
        except KeyError as exc:
            raise AppError(
                ErrorCode.CONFIG_INVALID, f"slot {module}.{slot} is not configured"
            ) from exc

    def optional(self, module: str, slot: str) -> Any | None:
        return self._slots.get((module, slot))

    def has(self, module: str, slot: str) -> bool:
        return (module, slot) in self._slots

    def slots(self) -> list[SlotId]:
        return sorted(self._slots)

    def replace(self, module: str, slot: str, instance: Any) -> None:
        """Swap a slot after start-up. For tests only."""
        self._slots[(module, slot)] = instance
