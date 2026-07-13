from __future__ import annotations

import logging
import os
import re
from io import BytesIO

from aiogram import Bot, F, Router, html
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from etka_bot.services.audio import AudioConversionError, convert_ogg_opus_to_wav
from etka_bot.services.claude import ClaudeClient, ClaudeError
from etka_bot.services.emex import EmexPriceClient
from etka_bot.services.openai import OpenAIClient, OpenAIError
from etka_bot.services.parts import (
    PartCandidate,
    PartsClient,
    is_buy_noise,
    is_quality_brand,
)
from etka_bot.services.parts_advisor import PartsAdvisor
from etka_bot.services.superetka import SuperetkaClient, get_superetka_client
from etka_bot.services.emex import get_emex_price_client

logger = logging.getLogger(__name__)
router = Router(name="parts")

# Системный промпт для анализа фото деталей
_PART_PHOTO_SYSTEM = (
    "Ты — эксперт по выявлению подделок автозапчастей в РФ. По фото детали или её "
    "упаковки оцени признаки оригинала vs подделки: качество и печать упаковки, "
    "шрифты и логотип, голограммы/QR/DataMatrix/наклейки, качество литья и "
    "поверхности, маркировку и артикул, резьбу/сварные швы. Дай вердикт "
    "(оригинал / подделка / сомнительно) с обоснованием по пунктам и что проверить "
    "дополнительно (пробить QR/DataMatrix, сверить артикул с каталогом). Если "
    "качество фото не позволяет судить — скажи, что доснять крупнее. По-русски, "
    "для Telegram: только теги <b> и <i>, списки «• », без Markdown. Кратко."
)

_PARTS_NO_BRAIN = (
    "🧠 Мозг-подборщик недоступен.\nДобавь ANTHROPIC_API_KEY в .env и перезапусти бота."
)


def _load_car_profile(car_profile_file: str) -> str | None:
    """Read the factory build-sheet (VIN.txt) used as authoritative car context."""
    try:
        with open(car_profile_file, encoding="utf-8") as fh:
            text = fh.read().strip()
    except OSError:
        return None
    return text or None


def _to_telegram_html(text: str) -> str:
    """Convert common non-Telegram HTML tags to Telegram-supported markup."""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li>", "• ", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:ul|ol|p)\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _safe_answer(message: Message, text: str) -> None:
    """Send text with HTML fallback to plain text on parse error."""
    text = _to_telegram_html(text)
    try:
        await message.answer(text)
    except TelegramBadRequest:
        await message.answer(text, parse_mode=None)


def _shop_of(url: str) -> str:
    """Shop label from the buy URL."""
    if "wildberries" in url:
        return "WB"
    if "emex" in url:
        return "Emex"
    return ""


def _buy_line(cand: PartCandidate) -> str:
    """One buy line: brand + number (+ price if known) + shop + link."""
    shop = _shop_of(cand.buy_url or "")
    tag = f" <i>({shop})</i>" if shop else ""
    num = f" <code>{cand.number}</code>" if cand.number else ""
    price = f" — {cand.price:.0f} {cand.currency}" if cand.price is not None else ""
    return (
        f"• <b>{cand.brand}</b>{num}{price}{tag} — "
        f'<a href="{cand.buy_url}">открыть</a>'
    )


def _parts_buy_block(candidates: tuple[PartCandidate, ...], limit: int = 12) -> str:
    """Where-to-buy: real manufacturers only (carmaker badges dropped), quality top."""
    analogs = [c for c in candidates if c.buy_url and not is_buy_noise(c.brand)]
    if not analogs:
        return ""
    analogs.sort(key=lambda c: (not is_quality_brand(c.brand), c.brand.lower()))
    lines = ["🔧 <b>Где купить</b> (норм. производители; цена — по ссылке):"]
    lines += [_buy_line(c) for c in analogs[:limit]]
    return "\n".join(lines)


async def _run_parts_advisor(
    message: Message,
    bot: Bot,
    claude_client: ClaudeClient,
    parts_client: PartsClient,
    superetka_client: SuperetkaClient | None,
    emex_price_client: EmexPriceClient | None,
    car_vin: str | None,
    car_engine: str | None,
    car_profile_file: str,
    claude_model_pick: str,
    query: str,
) -> None:
    """Route the query through PartsAdvisor, then append the real buy block."""
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    car_profile = _load_car_profile(car_profile_file)

    advisor = PartsAdvisor(
        claude=claude_client,
        parts=parts_client,
        superetka=superetka_client,
        emex_prices=emex_price_client,
        car_vin=car_vin,
        car_engine=car_engine,
        car_profile=car_profile,
        pick_model=claude_model_pick,
    )
    try:
        advice = await advisor.advise(query)
    except ClaudeError as error:
        logger.warning("Parts advisor failed: %s", error)
        await message.answer(
            "🫠 Мозг-подборщик сейчас не ответил (медленная сеть?).\n"
            "Попробуй ещё раз через минуту."
        )
        return

    buy_block = _parts_buy_block(advice.candidates)
    full = f"{advice.text}\n\n{buy_block}" if buy_block else advice.text
    await _safe_answer(message, full)


@router.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    """Handle the /start command."""
    user_name = message.from_user.full_name if message.from_user else "друг"
    await message.answer(
        f"👋 Привет, {html.bold(html.quote(user_name))}!\n\n"
        "🔧 <b>Бот для подбора автозапчастей</b>\n\n"
        "Что умею:\n"
        "• Пришли <b>артикул</b> — найду аналоги, проверю по ETKA, покажу цены\n"
        "• Пришли <b>название детали</b> — помогу определить нужную и подобрать номер\n"
        "• Пришли <b>фото детали/упаковки</b> — проверю на подделку\n"
        "• Надиктуй голосом — пойму и отвечу\n\n"
        "Просто напиши номер или название детали 👇"
    )


@router.message(Command("help"))
async def command_help_handler(message: Message) -> None:
    """Handle the /help command."""
    await message.answer(
        "🔧 <b>Как пользоваться ботом:</b>\n\n"
        "• <code>W712/75</code> — артикул (найду аналоги и цены)\n"
        "• <code>масляный фильтр</code> — название (подберу номер по VIN)\n"
        "• Фото упаковки/детали — проверка на подделку\n"
        "• Голосовое сообщение — работает как текст\n\n"
        "Источники: ETKA, Autodoc, Emex, Wildberries"
    )


@router.message(F.photo)
async def photo_handler(
    message: Message,
    bot: Bot,
    claude_client: ClaudeClient,
) -> None:
    """Analyze a part/box photo for counterfeit signs via Claude vision."""
    if not message.photo:
        return

    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    buffer = BytesIO()
    await bot.download(message.photo[-1].file_id, destination=buffer)

    question = (message.caption or "").strip() or (
        "Оцени деталь на фото: оригинал или подделка? Разбери по признакам."
    )
    try:
        answer = await claude_client.describe_image(
            _PART_PHOTO_SYSTEM, question, buffer.getvalue(), "image/jpeg"
        )
    except ClaudeError as error:
        logger.warning("Photo analysis failed: %s", error)
        await message.answer(
            "🫠 Не смог разобрать фото (медленная сеть?). Попробуй ещё раз."
        )
        return

    await _safe_answer(message, answer)


@router.message(F.voice)
async def voice_handler(
    message: Message,
    bot: Bot,
    claude_client: ClaudeClient,
    parts_client: PartsClient,
    superetka_client: SuperetkaClient | None,
    emex_price_client: EmexPriceClient | None,
    car_vin: str | None,
    car_engine: str | None,
    car_profile_file: str,
    claude_model_pick: str,
    stt_client: OpenAIClient | None,
) -> None:
    """Convert Telegram voice message to WAV, transcribe and pass to parts advisor."""
    if not message.voice:
        return

    if not stt_client:
        await message.answer(
            "🎙️ Голосовой ввод недоступен.\nДобавь OPENAI_API_KEY в .env."
        )
        return

    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    buffer = BytesIO()
    await bot.download(message.voice.file_id, destination=buffer)

    try:
        converted = convert_ogg_opus_to_wav(buffer.getvalue())
    except AudioConversionError as error:
        logger.warning("Failed to convert voice message: %s", error)
        await message.answer(
            "😕 Не получилось подготовить голосовое к распознаванию.\n"
            "Попробуй записать ещё раз или пришли текстом."
        )
        return

    try:
        transcript = await stt_client.transcribe_audio(
            converted.content, audio_format=converted.format
        )
    except OpenAIError as error:
        logger.warning("STT failed: %s", error)
        await message.answer(
            "😕 Не получилось распознать голосовое.\n"
            "Запиши чуть громче или пришли текстом."
        )
        return

    if not transcript.strip():
        await message.answer(
            "🎙️ Кажется, ничего не расслышал.\nПопробуй записать ещё раз."
        )
        return

    await message.answer(f'🎙️ Распознал: "{html.quote(transcript)}"')
    await _run_parts_advisor(
        message=message,
        bot=bot,
        claude_client=claude_client,
        parts_client=parts_client,
        superetka_client=superetka_client,
        emex_price_client=emex_price_client,
        car_vin=car_vin,
        car_engine=car_engine,
        car_profile_file=car_profile_file,
        claude_model_pick=claude_model_pick,
        query=transcript,
    )


@router.message(F.text)
async def text_handler(
    message: Message,
    bot: Bot,
    claude_client: ClaudeClient,
    parts_client: PartsClient,
    superetka_client: SuperetkaClient | None,
    emex_price_client: EmexPriceClient | None,
    car_vin: str | None,
    car_engine: str | None,
    car_profile_file: str,
    claude_model_pick: str,
) -> None:
    """Handle any text message as a parts query."""
    query = (message.text or "").strip()
    if not query:
        return

    await _run_parts_advisor(
        message=message,
        bot=bot,
        claude_client=claude_client,
        parts_client=parts_client,
        superetka_client=superetka_client,
        emex_price_client=emex_price_client,
        car_vin=car_vin,
        car_engine=car_engine,
        car_profile_file=car_profile_file,
        claude_model_pick=claude_model_pick,
        query=query,
    )
