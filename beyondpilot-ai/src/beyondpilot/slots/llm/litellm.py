"""LLM slot that talks to the LiteLLM gateway with the OpenAI-compatible API.

Routing between vLLM (Qwen3) and third-party fallbacks lives in LiteLLM's own
config, not here. The OpenAI client's built-in retries are turned off so that
``core.resilience`` stays the only retry layer.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

from openai import AsyncOpenAI

from beyondpilot.core.contracts import LlmResult, Message
from beyondpilot.core.diagnostics import record_tokens
from beyondpilot.core.resilience import call_with_retry
from beyondpilot.core.settings import ResilienceConfig


def _message(m: Message) -> dict[str, Any]:
    """OpenAI chat format; images become data-URI parts for vision models."""
    if not m.images:
        return {"role": m.role, "content": m.content}
    parts: list[dict[str, Any]] = [{"type": "text", "text": m.content}]
    for image in m.images:
        url = "data:" + sniff_mime(image) + ";base64," + base64.b64encode(image).decode()
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": m.role, "content": parts}


def sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


class Impl:
    accepts_resilience = True

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        resilience: ResilienceConfig,
        temperature: float = 0.2,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.resilience = resilience
        self.client = client or AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=0)

    async def complete(self, messages: Sequence[Message], **options: Any) -> LlmResult:
        payload = [_message(m) for m in messages]
        params = {"temperature": self.temperature, **options}
        model = params.pop("model", self.model)

        async def call() -> Any:
            return await self.client.chat.completions.create(
                model=model, messages=payload, **params
            )

        response = await call_with_retry(call, config=self.resilience, op="llm.litellm")
        usage = getattr(response, "usage", None)
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        record_tokens(prompt, completion, model)
        text = (response.choices[0].message.content or "").strip()
        return LlmResult(text=text, model=model, prompt_tokens=prompt, completion_tokens=completion)
