from __future__ import annotations

from dataclasses import dataclass

import httpx


class OpenAIError(RuntimeError):
    """Raised when OpenAI cannot return a usable answer."""


@dataclass(frozen=True, slots=True)
class OpenAIClient:
    """Minimal async OpenAI client — only STT (Whisper) for this bot."""

    api_key: str
    stt_model: str
    base_url: str = "https://api.openai.com/v1"

    async def transcribe_audio(self, audio: bytes, audio_format: str) -> str:
        """Transcribe audio bytes using Whisper."""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {
            "file": (f"audio.{audio_format}", audio, f"audio/{audio_format}"),
        }
        data = {"model": self.stt_model}

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/audio/transcriptions",
                data=data,
                files=files,
                headers=headers,
            )

        if response.status_code >= 400:
            msg = (
                f"OpenAI STT request failed with status {response.status_code}: "
                f"{response.text[:500]}"
            )
            raise OpenAIError(msg)

        result = response.json()
        if not isinstance(result, dict):
            raise OpenAIError("OpenAI STT response has unexpected format.")

        text = result.get("text")
        if not isinstance(text, str):
            raise OpenAIError("OpenAI STT response does not contain text string.")

        return text.strip()
