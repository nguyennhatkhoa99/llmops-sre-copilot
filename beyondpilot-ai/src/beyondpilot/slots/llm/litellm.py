"""LLM slot that talks to the LiteLLM gateway with the OpenAI-compatible API.

Routing between vLLM (Qwen3) and third-party fallbacks lives in LiteLLM's own
config, not here. The OpenAI client's built-in retries are turned off so that
``core.resilience`` stays the only retry layer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openai import AsyncOpenAI

from beyondpilot.core.contracts import LlmResult, Message
from beyondpilot.core.diagnostics import record_tokens
from beyondpilot.core.resilience import call_with_retry
from beyondpilot.core.settings import ResilienceConfig


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
        payload = [{"role": m.role, "content": m.content} for m in messages]
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
