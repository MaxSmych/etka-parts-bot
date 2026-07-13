from __future__ import annotations

from dataclasses import dataclass
from os import getenv
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    """Application settings loaded from environment."""

    bot_token: str
    tg_proxy: str | None
    # Claude (мозг-подборщик запчастей)
    anthropic_api_key: str
    anthropic_base_url: str
    claude_model: str
    claude_model_pick: str
    # OpenAI Whisper (STT для голосового ввода)
    openai_api_key: str | None
    openai_stt_model: str
    # Машина пользователя (контекст для подбора)
    car_vin: str | None
    car_engine: str | None
    car_profile_file: str
    # Superetka (онлайн-ETKA)
    superetka_login: str | None
    superetka_password: str | None
    # Emex (реальные цены по аккаунту)
    emex_login: str | None
    emex_password: str | None


def load_settings() -> Settings:
    """Load and validate application settings."""
    bot_token = getenv("BOT_TOKEN")
    if not bot_token:
        msg = "BOT_TOKEN is required. Put it into .env or environment variables."
        raise RuntimeError(msg)

    anthropic_api_key = getenv("ANTHROPIC_API_KEY")
    if not anthropic_api_key:
        msg = "ANTHROPIC_API_KEY is required (мозг-подборщик). Put it into .env."
        raise RuntimeError(msg)

    return Settings(
        bot_token=bot_token,
        tg_proxy=getenv("TG_PROXY"),
        anthropic_api_key=anthropic_api_key,
        anthropic_base_url=getenv(
            "ANTHROPIC_BASE_URL",
            "https://api.anthropic.com",
        ),
        claude_model=getenv("CLAUDE_MODEL", "claude-opus-4-8"),
        claude_model_pick=getenv("CLAUDE_MODEL_PICK", "claude-opus-4-8"),
        openai_api_key=getenv("OPENAI_API_KEY"),
        openai_stt_model=getenv("OPENAI_STT_MODEL", "whisper-1"),
        car_vin=getenv("CAR_VIN"),
        car_engine=getenv("CAR_ENGINE"),
        car_profile_file=getenv("CAR_PROFILE_FILE", "VIN.txt"),
        superetka_login=getenv("SUPERETKA_LOGIN"),
        superetka_password=getenv("SUPERETKA_PASSWORD"),
        emex_login=getenv("EMEX_LOGIN"),
        emex_password=getenv("EMEX_PASSWORD"),
    )
