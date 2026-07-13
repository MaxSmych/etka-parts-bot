from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx


class ClaudeError(RuntimeError):
    """Raised when Claude cannot return a usable answer."""


@dataclass(frozen=True, slots=True)
class ClaudeClient:
    """Minimal async client for the Anthropic Messages API.

    Works both against api.anthropic.com and Anthropic-compatible gateways
    (e.g. datakey.one) via ``base_url``. Sends the token in both the Bearer
    and x-api-key headers so either accepting scheme is satisfied.
    """

    api_key: str
    model: str
    base_url: str = "https://api.anthropic.com"
    timeout: float = 120.0
    proxy: str | None = None

    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
        model: str | None = None,
    ) -> str:
        """Send a single-turn request and return the concatenated text.

        ``model`` overrides the default per call (e.g. a cheaper/leaner model for
        a trivial classification), otherwise the client's configured model is used.
        """
        payload: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        url = f"{self.base_url.rstrip('/')}/v1/messages"
        async with httpx.AsyncClient(timeout=self.timeout, proxy=self.proxy) as client:
            response = await client.post(url, json=payload, headers=headers)

        if response.status_code >= 400:
            raise ClaudeError(
                f"Claude request failed with status {response.status_code}: "
                f"{response.text[:300]}"
            )

        return self._extract_text(response.json())

    async def describe_image(
        self,
        system: str,
        text: str,
        image: bytes,
        media_type: str = "image/jpeg",
        max_tokens: int = 1200,
    ) -> str:
        """Send an image + question (vision) and return the text answer."""
        b64 = base64.standard_b64encode(image).decode("ascii")
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": text},
                    ],
                }
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        url = f"{self.base_url.rstrip('/')}/v1/messages"
        async with httpx.AsyncClient(timeout=self.timeout, proxy=self.proxy) as client:
            response = await client.post(url, json=payload, headers=headers)

        if response.status_code >= 400:
            raise ClaudeError(
                f"Claude vision request failed with status "
                f"{response.status_code}: {response.text[:300]}"
            )

        return self._extract_text(response.json())

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        blocks = data.get("content")
        if not isinstance(blocks, list) or not blocks:
            raise ClaudeError("Claude response does not contain content.")

        parts = [
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        text = "".join(parts).strip()
        if not text:
            raise ClaudeError("Claude response is empty.")

        return text
