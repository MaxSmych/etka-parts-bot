from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from etka_bot.config import Settings
from etka_bot.handlers import parts_router
from etka_bot.services.claude import ClaudeClient
from etka_bot.services.emex import EmexPriceClient
from etka_bot.services.openai import OpenAIClient
from etka_bot.services.parts import PartsClient
from etka_bot.services.superetka import SuperetkaClient


def build_bot(settings: Settings) -> Bot:
    """Create configured aiogram bot."""
    session = AiohttpSession(proxy=settings.tg_proxy) if settings.tg_proxy else None

    return Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_claude_client(settings: Settings) -> ClaudeClient:
    """Create configured Claude client."""
    return ClaudeClient(
        api_key=settings.anthropic_api_key,
        model=settings.claude_model,
        base_url=settings.anthropic_base_url,
        proxy=settings.tg_proxy,
    )


def build_parts_client() -> PartsClient:
    """Create parts catalog client (Autodoc + Emex + WB, no auth needed)."""
    return PartsClient()


def build_stt_client(settings: Settings) -> OpenAIClient | None:
    """Create STT (Whisper) client if OpenAI key is set."""
    if not settings.openai_api_key:
        return None
    return OpenAIClient(
        api_key=settings.openai_api_key,
        stt_model=settings.openai_stt_model,
    )


def build_superetka_client(settings: Settings) -> SuperetkaClient | None:
    """Create ETKA client if credentials are set."""
    if not settings.superetka_login or not settings.superetka_password:
        return None
    return SuperetkaClient(
        login=settings.superetka_login,
        password=settings.superetka_password,
        vin=settings.car_vin,
    )


def build_emex_price_client(settings: Settings) -> EmexPriceClient | None:
    """Create Emex price client if credentials are set."""
    if not settings.emex_login or not settings.emex_password:
        return None
    return EmexPriceClient(
        login=settings.emex_login,
        password=settings.emex_password,
    )


def build_dispatcher(
    claude_client: ClaudeClient,
    parts_client: PartsClient,
    stt_client: OpenAIClient | None,
    superetka_client: SuperetkaClient | None,
    emex_price_client: EmexPriceClient | None,
    settings: Settings,
) -> Dispatcher:
    """Create dispatcher and register routers."""
    dispatcher = Dispatcher(
        claude_client=claude_client,
        parts_client=parts_client,
        stt_client=stt_client,
        superetka_client=superetka_client,
        emex_price_client=emex_price_client,
        car_vin=settings.car_vin,
        car_engine=settings.car_engine,
        car_profile_file=settings.car_profile_file,
        claude_model_pick=settings.claude_model_pick,
    )
    dispatcher.include_router(parts_router)
    return dispatcher
