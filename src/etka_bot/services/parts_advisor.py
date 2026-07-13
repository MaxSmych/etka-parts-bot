from __future__ import annotations

import re
from dataclasses import dataclass, replace

import httpx

from etka_bot.services.claude import ClaudeClient
from etka_bot.services.emex import EmexError, EmexPriceClient
from etka_bot.services.parts import (
    PartCandidate,
    PartsClient,
    PartsError,
    PartsLookup,
    extract_number,
)
from etka_bot.services.superetka import (
    EtkaCategory,
    EtkaDetail,
    SuperetkaClient,
    SuperetkaError,
)

# --- Ветка A: точный артикул есть; номер сверяем по ETKA, цены/аналоги из каталогов ---
RANK_SYSTEM = (
    "Ты — независимый эксперт по автозапчастям в РФ. Каталожный номер сверен по "
    "ETKA (данные показаны пользователю отдельным блоком). Тебе дают номер, факты "
    "ETKA и список брендов-аналогов из каталогов. Приоритет — КАЧЕСТВО без переплаты "
    "за шильдик. Ответь СТРОГО по структуре:\n\n"
    "🏭 <b>Кто на конвейере</b> — назови настоящего завода-производителя (Tier-1: "
    "MANN, Mahle/Knecht, Bosch, Hella, Valeo, TRW, Lemförder, SKF, Febi, NGK, Sachs, "
    "Contitech, Elring…), который поставляет эту деталь на сборку. «Оригинал» в "
    "коробке автопроизводителя = его же деталь с наценкой ×3–×10, "
    "качество идентично.\n\n"
    "🅾️ <b>Актуальность</b> — бери ТОЛЬКО из фактов ETKA выше (действующий номер / "
    "снят и заменён). Свои OE-номера НЕ выдумывай: ETKA — единственный источник "
    "номера. Отменённый номер мог остаться на складах — так и скажи, не отговаривай "
    "жёстко.\n\n"
    "🔧 <b>Аналоги</b> по тирам: 🥇 OE-поставщик, 🥈 премиум, 🥉 бюджет/риск. "
    "Советуй бренд настоящего производителя (то же качество, что оригинал, без "
    "наценки). No-name — «на свой риск», не как баланс. В КАЖДОМ тире — только "
    "несколько САМЫХ ходовых/узнаваемых брендов (🥇 2–4, 🥈 3–5, 🥉 5–8). НЕ "
    "вываливай весь каталог — короткая выжимка, а не перечень всех ноунеймов.\n\n"
    "⚠️ <b>Подделки</b> — на что смотреть по этой позиции (упаковка, маркировка, "
    "аномально низкая цена премиум-бренда).\n\n"
    "ВАЖНО: бренды бери ТОЛЬКО из списка, ничего не выдумывай. ЦЕНЫ НЕ приводи — "
    "они у каждого продавца свои, пользователь смотрит их по ссылкам в блоке «Где "
    "купить» ниже (его добавляют автоматически, ты его НЕ пиши).\n"
    "НЕ упоминай бренды автопроизводителей и люксовые бейджи (VW, Audi, Skoda, SEAT, "
    "VAG, Bentley, Lamborghini, Porsche…) и кросс по ним — пользователю нужен ТОЛЬКО "
    "реальный производитель детали. Никаких «единый номер для VAG Group, включая "
    "Bentley».\n"
    "Формат Telegram: только теги <b> и <i>, списки «• », без Markdown. Кратко."
)

# --- Ветки B (название/машина) и C (симптом): точного номера нет ---
# Claude НЕ выдумывает артикулы — номер даёт только ETKA по точному артикулу.
IDENTIFY_SYSTEM = (
    "Ты — автоэксперт-диагност по подбору автозапчастей в РФ. Пользователь описал "
    "деталь словами, назвал машину или описал симптом — точного артикула нет.\n"
    "Задача:\n"
    "• НАЗВАНИЕ детали (± марка/модель/двигатель) — определи, ЧТО именно за узел/"
    "деталь нужна, какие бывают исполнения под эту машину, на что смотреть.\n"
    "• СИМПТОМ/проблема — коротко назови вероятные причины и какие узлы/детали "
    "проверить или заменить в первую очередь.\n"
    "СТРОГО ЗАПРЕЩЕНО придумывать каталожные (OE) артикулы — ты часто ошибаешься, "
    "а неверный номер обходится дорого. Номер даёт ТОЛЬКО каталог. В конце попроси "
    "пользователя прислать точный артикул из ETKA — бот сверит его, покажет "
    "актуальность/замену и подберёт производителя и аналоги.\n"
    "Данные машины (VIN, двигатель, заводской build-sheet с PR-кодами) — ФАКТ "
    "владельца, АВТОРИТЕТНЫ. Бери из них марку/модель/год/кузов/мотор/КПП как есть, "
    "НЕ переспрашивай. СТРОГО ЗАПРЕЩЕНО выдумывать характеристики мотора "
    "(мощность/объём) — они уже даны.\n"
    "Формат Telegram: только теги <b> и <i>, списки «• », без Markdown, кратко."
)


# --- Выбор категории ETKA под запрос (LLM делает семантику, не выдумывает номер) ---
CATEGORY_SYSTEM = (
    "Пользователь описал нужную деталь своими словами. Ниже — нумерованный список "
    "категорий деталей обслуживания из каталога ETKA для его машины. Верни ТОЛЬКО "
    "номер той категории, что точно соответствует запросу (одно число). Учитывай "
    "склонения, синонимы, язык. Если ни одна не подходит — верни 0. Больше ничего."
)


@dataclass(frozen=True, slots=True)
class Advice:
    """Result of the parts advisor: ready text + candidates for the buy block."""

    text: str
    candidates: tuple[PartCandidate, ...]


@dataclass(frozen=True, slots=True)
class PartsAdvisor:
    """Routes a parts query into the right branch with a tailored Claude prompt."""

    claude: ClaudeClient
    parts: PartsClient
    superetka: SuperetkaClient | None = None  # ETKA — источник истины по OE-номеру
    emex_prices: EmexPriceClient | None = None  # реальные цены по логину Emex
    car_vin: str | None = None  # user's car, injected as context for name/symptom
    car_engine: str | None = None  # engine (not in VIN for VW), changes article a lot
    car_profile: str | None = None  # full factory build-sheet (ETKA/VIN) with PR-codes
    # Lean model for the trivial category pick (Opus is leanest on the datakey
    # gateway — it doesn't force heavy thinking like Sonnet/Haiku do there).
    pick_model: str | None = None

    async def advise(self, query: str) -> Advice:
        """Branch on whether the query carries an exact article number."""
        number = extract_number(query)
        if number:
            try:
                return await self._by_number(number)
            except PartsError:
                pass  # number not found anywhere → treat as description
        return await self._by_description(query)

    async def _by_number(self, number: str) -> Advice:
        etka = await self._etka_detail(number)
        numbers = etka.numbers() if etka and etka.entries else [number]
        part_name, candidates = await self._lookup_numbers(numbers)

        if not (etka and etka.entries) and not candidates:
            raise PartsError(f"Номер {number} не найден ни в ETKA, ни в каталогах.")

        etka_block = _render_etka(etka)
        if not candidates:
            note = (
                "\n\nПо этим номерам аналоги в каталогах не нашлись — "
                "пришли номер ещё раз позже или проверь его."
            )
            return Advice(text=(etka_block + note).strip(), candidates=())

        lookup = PartsLookup(query=number, part_name=part_name, candidates=candidates)
        prompt = (
            f"Деталь: {lookup.part_name or '—'}\n"
            f"Запрошенный номер: {number}\n"
            f"{_etka_prompt(etka)}"
            f"Бренды-аналоги из каталогов:\n{_catalog_lines(lookup)}"
        )
        analysis = await self.claude.complete(RANK_SYSTEM, prompt, max_tokens=1800)
        text = f"{etka_block}\n\n{analysis}" if etka_block else analysis
        return Advice(text=text, candidates=candidates)

    async def _by_description(self, query: str) -> Advice:
        resolved = await self._resolve_by_name(query)
        if resolved:
            number, category = resolved
            try:
                advice = await self._by_number(number)
            except PartsError:
                pass
            else:
                head = (
                    f"🔎 «{query.strip()}» → категория ETKA «{category}» → номер "
                    f"<code>{number}</code> (подобран по VIN, не выдуман).\n\n"
                )
                return replace(advice, text=head + advice.text)

        prompt = query
        if self.car_profile:
            prompt = (
                "Заводской build-sheet машины пользователя (ETKA/VIN-декод) — "
                "АБСОЛЮТНЫЙ факт. Используй марку/модель/мотор/КПП и PR-коды для "
                f"выбора ТОЧНОГО варианта детали:\n{self.car_profile}\n\n"
                f"Запрос: {query}"
            )
        else:
            car: list[str] = []
            if self.car_vin:
                car.append(f"VIN: {self.car_vin}")
            if self.car_engine:
                car.append(f"Двигатель: {self.car_engine}")
            if car:
                prompt = "Машина — " + "; ".join(car) + f".\nЗапрос: {query}"
        text = await self.claude.complete(IDENTIFY_SYSTEM, prompt, max_tokens=1500)
        return Advice(text=text.strip(), candidates=())

    async def _resolve_by_name(self, query: str) -> tuple[str, str] | None:
        """Turn a part NAME into a real OE number via ETKA's VIN-scoped catalog.

        ETKA lists the car's maintenance categories; Claude picks the matching one
        (semantic match — handles declensions/synonyms/language), and the number
        comes from the catalog. Returns (number, category_name) or None.
        """
        if not self.superetka:
            return None
        try:
            categories = await self.superetka.maintenance_categories()
        except (SuperetkaError, httpx.HTTPError, OSError):
            return None
        if not categories:
            return None
        ka = await self._pick_category(query, categories)
        if not ka:
            return None
        try:
            entries = await self.superetka.category_numbers(ka)
        except (SuperetkaError, httpx.HTTPError, OSError):
            return None
        if not entries:
            return None
        name = next((c.name for c in categories if c.ka == ka), "")
        return entries[0].number, name

    async def _pick_category(
        self, query: str, categories: tuple[EtkaCategory, ...]
    ) -> str | None:
        """Ask Claude which ETKA category matches the query; return its `ka`."""
        listing = "\n".join(f"{i}. {c.name}" for i, c in enumerate(categories, 1))
        answer = await self.claude.complete(
            CATEGORY_SYSTEM,
            f"Запрос: {query}\n\nКатегории:\n{listing}",
            max_tokens=8,
            model=self.pick_model,
        )
        match = re.search(r"\d+", answer)
        if not match:
            return None
        index = int(match.group())
        if 1 <= index <= len(categories):
            return categories[index - 1].ka
        return None

    async def _etka_detail(self, number: str) -> EtkaDetail | None:
        """Fetch authoritative ETKA facts for a number; None on any failure."""
        if not self.superetka:
            return None
        try:
            return await self.superetka.detail(number)
        except (SuperetkaError, httpx.HTTPError, OSError):
            return None

    async def _lookup_numbers(
        self, numbers: list[str]
    ) -> tuple[str, tuple[PartCandidate, ...]]:
        """Look up each number in catalogs and merge de-duplicated candidates."""
        merged: list[PartCandidate] = []
        seen: set[tuple[str, str]] = set()
        part_name = ""
        for number in numbers:
            try:
                lookup = await self.parts.lookup(number)
            except PartsError:
                continue
            part_name = part_name or lookup.part_name
            for cand in lookup.candidates:
                key = (cand.brand.upper(), cand.number.upper())
                if key in seen:
                    continue
                seen.add(key)
                merged.append(cand)
        merged = await self._enrich_prices(merged)
        merged.sort(key=lambda c: (c.price is None, c.price or 0.0, c.brand.lower()))
        return part_name, tuple(merged)

    async def _enrich_prices(
        self, candidates: list[PartCandidate]
    ) -> list[PartCandidate]:
        """Attach real Emex account prices to Emex-sourced candidates."""
        if not self.emex_prices or not candidates:
            return candidates
        details = [(c.number, c.brand) for c in candidates if "emex" in c.source]
        if not details:
            return candidates
        try:
            offers = await self.emex_prices.prices(details)
        except (EmexError, httpx.HTTPError, OSError):
            return candidates
        out: list[PartCandidate] = []
        for cand in candidates:
            offer = offers.get((cand.number.upper(), cand.brand.upper()))
            if offer and cand.price is None:
                out.append(replace(cand, price=offer.price, currency=offer.currency))
            else:
                out.append(cand)
        return out


def _render_etka(etka: EtkaDetail | None) -> str:
    """User-facing ETKA facts block: every number with a status comment."""
    if not etka or not etka.entries:
        return ""
    lines = ["🅾️ <b>Каталог ETKA</b> (источник истины по номеру):"]
    for entry in etka.entries:
        if entry.cancelled:
            date = f" {entry.cancel_date}" if entry.cancel_date else ""
            note = (
                f"❗ снят{date}, есть замена ниже — "
                "но старые склады могут ещё торговать"
            )
        elif entry.is_replacement:
            note = "✅ актуальная замена"
        else:
            note = "✅ действующий"
        name = f" {entry.name}" if entry.name else ""
        lines.append(f"• <code>{entry.number}</code>{name} — {note}")
    return "\n".join(lines)


def _etka_prompt(etka: EtkaDetail | None) -> str:
    """Compact ETKA facts for Claude's prompt (actuality without inventing)."""
    if not etka or not etka.entries:
        return ""
    parts = []
    for entry in etka.entries:
        if entry.cancelled:
            status = f"снят {entry.cancel_date}".strip()
        elif entry.is_replacement:
            status = "актуальная замена"
        else:
            status = "действующий"
        parts.append(f"{entry.number} — {entry.name} ({status})")
    return "Факты ETKA (актуальность бери отсюда):\n" + "\n".join(parts) + "\n"


def _catalog_lines(lookup: PartsLookup) -> str:
    lines = []
    for cand in lookup.candidates:
        if cand.source == "wb":
            continue  # WB brand names are noisy — keep them out of tier analysis
        suffix = f" ({cand.name})" if cand.name else ""
        lines.append(f"• {cand.brand}: {cand.number}{suffix}")
    return "\n".join(lines)
