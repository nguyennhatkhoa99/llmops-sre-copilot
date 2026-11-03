"""Shared core. Modules A–H depend on this package; it never imports a web framework."""

from beyondpilot.core.contracts import (
    CONTRACTS,
    AccessFilter,
    Chunk,
    Document,
    Hit,
    LlmResult,
    Message,
)
from beyondpilot.core.errors import CATALOGUE, AppError, ErrorCode
from beyondpilot.core.registry import Registry
from beyondpilot.core.resilience import RetryableError, call_with_retry
from beyondpilot.core.safety import GuardrailResult, SafetyEngine
from beyondpilot.core.settings import Settings, load_settings, parse_settings
from beyondpilot.core.validator import generate_validated, validate

__all__ = [
    "CATALOGUE",
    "CONTRACTS",
    "AccessFilter",
    "AppError",
    "Chunk",
    "Document",
    "ErrorCode",
    "GuardrailResult",
    "Hit",
    "LlmResult",
    "Message",
    "Registry",
    "RetryableError",
    "SafetyEngine",
    "Settings",
    "call_with_retry",
    "generate_validated",
    "load_settings",
    "parse_settings",
    "validate",
]
