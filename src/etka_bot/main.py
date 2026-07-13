from __future__ import annotations

import asyncio
import logging
import sys

from dotenv import load_dotenv

from etka_bot.bot import (
    build_bot,
    build_claude_client,
    build_dispatcher,
    build_emex_price_client,
    build_parts_client,
    build_stt_client,
    build_superetka_client,
)
from etka_bot.config import load_settings


async def main() -> None:
    """Start bot long polling."""
    load_dotenv()
    settings = load_settings()

    bot = build_bot(settings)
    claude_client = build_claude_client(settings)
    parts_client = build_parts_client()
    stt_client = build_stt_client(settings)
    superetka_client = build_superetka_client(settings)
    emex_price_client = build_emex_price_client(settings)

    dispatcher = build_dispatcher(
        claude_client=claude_client,
        parts_client=parts_client,
        stt_client=stt_client,
        superetka_client=superetka_client,
        emex_price_client=emex_price_client,
        settings=settings,
    )

    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


def run() -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())


if __name__ == "__main__":
    run()
