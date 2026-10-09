"""Small Anthropic adapter; no business-module database access."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Generation:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


class AnthropicProvider:
    def __init__(self, api_key: str, model: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.transport = transport

    def generate(self, system: str, context: str) -> Generation:
        if not self.api_key:
            raise RuntimeError("AI provider is not configured")
        with httpx.Client(timeout=60, transport=self.transport) as client:
            response = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 500,
                    "system": system,
                    "messages": [{"role": "user", "content": context}],
                },
            )
            response.raise_for_status()
            data = response.json()
        usage = data["usage"]
        blocks = data["content"]
        text = "\n".join(block["text"] for block in blocks if block.get("type") == "text")
        # A response with usage must still enter the outbox, including a refusal or empty text.
        if not text:
            text = "模型未生成可显示的文本。"
        return Generation(
            text=text,
            model=data["model"],
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens") or 0,
            cache_read_input_tokens=usage.get("cache_read_input_tokens") or 0,
        )
