from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, replace

import httpx

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)


class PartsError(RuntimeError):
    """Raised when parts catalogs cannot return usable data."""


@dataclass(frozen=True, slots=True)
class PartCandidate:
    """One brand/number variant of a part (an OEM or an aftermarket analog)."""

    brand: str
    number: str
    name: str
    source: str  # "autodoc" | "emex"
    price: float | None = None  # best (minimal) price, Emex, in region currency
    currency: str = "₽"
    buy_url: str | None = None  # Emex product page to buy this brand/number


@dataclass(frozen=True, slots=True)
class PartsLookup:
    """Normalized result of looking up a part number across catalogs."""

    query: str
    part_name: str
    candidates: tuple[PartCandidate, ...]


@dataclass(frozen=True, slots=True)
class PartsClient:
    """Aggregates free parts catalogs (Autodoc TecDoc + Emex) by article number.

    Returns cross-brand analogs and the canonical part name. Live prices are
    gated behind site logins and are handled by a separate priced adapter.
    """

    emex_location_id: int = 61137  # Emex region id (Москва)
    autodoc_base: str = "https://webapi.autodoc.ru/api"
    emex_base: str = "https://emex.ru/api"
    timeout: float = 25.0

    async def lookup(self, number: str) -> PartsLookup:
        """Look up a part number and return de-duplicated cross-brand analogs."""
        cleaned = extract_number(number)
        if not cleaned:
            raise PartsError(
                "Не вижу номера детали в запросе. Пришли артикул, "
                "например W712/75 или 036905715G."
            )

        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        ) as client:
            autodoc, emex, wb = await asyncio.gather(
                self._autodoc(client, cleaned),
                self._emex(client, cleaned),
                self._wb(client, cleaned),
                return_exceptions=True,
            )

        candidates: list[PartCandidate] = []
        for result in (autodoc, emex, wb):
            if isinstance(result, list):
                candidates.extend(result)

        if not candidates:
            raise PartsError(
                f"Ни один каталог не ответил по номеру {cleaned}. "
                "Проверь номер или попробуй позже."
            )

        deduped = _dedupe(candidates)
        part_name = _pick_part_name(deduped)
        return PartsLookup(
            query=cleaned,
            part_name=part_name,
            candidates=tuple(deduped),
        )

    async def _autodoc(
        self, client: httpx.AsyncClient, number: str
    ) -> list[PartCandidate]:
        """Autodoc webapi: brand + part name by number (TecDoc-backed, no auth)."""
        url = f"{self.autodoc_base}/manufacturers/{number}"
        response = await client.get(url, params={"query": number})
        if response.status_code >= 400:
            return []
        data = response.json()
        if not isinstance(data, list):
            return []

        out: list[PartCandidate] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            brand = str(item.get("manufacturerName", "")).strip()
            art = str(item.get("artNumber", number)).strip() or number
            name = str(item.get("partName", "")).strip()
            if brand:
                out.append(
                    PartCandidate(brand=brand, number=art, name=name, source="autodoc")
                )
        return out

    async def _emex(
        self, client: httpx.AsyncClient, number: str
    ) -> list[PartCandidate]:
        """Emex search: cross-brand analog list by number (with optional auth)."""
        from etka_bot.services.emex import get_emex_price_client
        import logging
        logger = logging.getLogger(__name__)

        price_client = get_emex_price_client()
        cookies = None
        if price_client:
            try:
                if price_client._cookies is None:
                    await price_client._login(client)
                cookies = price_client._cookies
            except Exception as e:
                logger.warning("Emex auth search login failed, falling back to guest: %s", e)

        url = f"{self.emex_base}/search/search"
        params = {
            "detailNum": number,
            "locationId": str(self.emex_location_id),
            "make": "",
            "isHeadSearch": "true",
            "showAll": "true" if cookies else "false",
        }
        response = await client.get(url, params=params, cookies=cookies)
        if response.status_code >= 400:
            return []
        
        data = response.json()
        search_result = data.get("searchResult", {}) if isinstance(data, dict) else {}
        
        makes = search_result.get("makes", {}).get("list", [])
        originals = search_result.get("originals", [])
        analogs = search_result.get("analogs", [])
        
        out: list[PartCandidate] = []
        
        # 1. Parse makes (brands lookup, price is set to None to avoid phantom bestPrice)
        if isinstance(makes, list):
            for item in makes:
                if not isinstance(item, dict):
                    continue
                brand = str(item.get("make", "")).strip()
                art = str(item.get("num", number)).strip() or number
                name = _fix_emex_text(str(item.get("name", "")).strip())
                buy_url = f"https://emex.ru/f?detailNum={art}&make={brand}"
                if brand:
                    out.append(
                        PartCandidate(
                            brand=brand,
                            number=art,
                            name=name,
                            source="emex",
                            price=None,
                            buy_url=buy_url,
                        )
                    )
                    
        # Helper to parse offers from group lists (originals / analogs)
        def parse_groups(groups_list):
            if not isinstance(groups_list, list):
                return
            for group in groups_list:
                if not isinstance(group, dict):
                    continue
                offers = group.get("offers", [])
                if not isinstance(offers, list):
                    continue
                for offer in offers:
                    if not isinstance(offer, dict):
                        continue
                    o_data = offer.get("data", {})
                    if not isinstance(o_data, dict):
                        continue
                    
                    brand = str(o_data.get("makeName", "")).strip()
                    art = str(o_data.get("detailNum", "")).strip()
                    name = _fix_emex_text(str(o_data.get("detailName", "")).strip())
                    
                    # Extract offer price
                    display_price = offer.get("displayPrice")
                    price_val = None
                    currency_val = "₽"
                    if isinstance(display_price, dict):
                        val = display_price.get("value")
                        if val is not None:
                            try:
                                price_val = float(val)
                            except (ValueError, TypeError):
                                pass
                        symbol = display_price.get("symbol")
                        if symbol:
                            currency_val = str(symbol)
                            
                    buy_url = f"https://emex.ru/f?detailNum={art}&make={brand}"
                    if brand and art:
                        out.append(
                            PartCandidate(
                                brand=brand,
                                number=art,
                                name=name,
                                source="emex",
                                price=price_val,
                                currency=currency_val,
                                buy_url=buy_url,
                            )
                        )

        # 2. Parse originals (offers with real prices)
        parse_groups(originals)

        # 3. Parse analogs (crosses with real prices)
        parse_groups(analogs)
        
        return out

    async def _wb(self, client: httpx.AsyncClient, number: str) -> list[PartCandidate]:
        """Wildberries buyer search: marketplace offers by number (no auth)."""
        url = "https://search.wb.ru/exactmatch/ru/common/v9/search"
        params = {
            "appType": "1",
            "curr": "rub",
            "dest": "-1257786",  # Москва
            "query": number,
            "resultset": "catalog",
            "sort": "popular",
            "spp": "30",
            "lang": "ru",
        }
        headers = {
            "Origin": "https://www.wildberries.ru",
            "Referer": "https://www.wildberries.ru/",
        }
        # Best-effort: WB hard rate-limits shared IPs (429). No retry to keep
        # queries fast — WB just contributes when the IP isn't throttled.
        response = await client.get(url, params=params, headers=headers)
        if response.status_code >= 400:
            return []
        data = response.json()
        products = None
        if isinstance(data, dict):
            products = data.get("data", {}).get("products")
        if not isinstance(products, list):
            return []

        out: list[PartCandidate] = []
        for item in products[:12]:
            if not isinstance(item, dict):
                continue
            brand = str(item.get("brand", "")).strip()
            pid = item.get("id")
            price = _wb_price(item)
            if not brand or pid is None or price is None:
                continue
            out.append(
                PartCandidate(
                    brand=brand,
                    number=number,
                    name=_fix_emex_text(str(item.get("name", "")).strip()),
                    source="wb",
                    price=price,
                    currency="₽",
                    buy_url=f"https://www.wildberries.ru/catalog/{pid}/detail.aspx",
                )
            )
        return out


def _wb_price(item: dict[str, object]) -> float | None:
    """Extract WB price: sizes[0].price.product (kopecks) → rubles."""
    sizes = item.get("sizes")
    if not isinstance(sizes, list) or not sizes or not isinstance(sizes[0], dict):
        return None
    price = sizes[0].get("price")
    if not isinstance(price, dict):
        return None
    value = price.get("product") or price.get("total")
    if isinstance(value, int | float) and value > 0:
        return float(value) / 100
    return None


# Бейджи автопроизводителей/люкса: это «оригинал» с наценкой или кросс-шум —
# в список «где купить» не нужны (пользователь ищет нормального производителя).
CARMAKER_BADGES = frozenset(
    {
        "VAG",
        "VW",
        "VOLKSWAGEN",
        "AUDI",
        "SKODA",
        "SEAT",
        "CUPRA",
        "BMW",
        "MINI",
        "MERCEDES",
        "MERCEDES-BENZ",
        "PORSCHE",
        "BENTLEY",
        "LAMBORGHINI",
        "TOYOTA",
        "LEXUS",
        "HONDA",
        "ACURA",
        "NISSAN",
        "INFINITI",
        "RENAULT",
        "RENAULT-NISSAN",
        "HYUNDAI",
        "KIA",
        "FORD",
        "OPEL",
        "CHEVROLET",
        "GENERAL MOTORS",
        "ACDELCO",
        "MAZDA",
        "MITSUBISHI",
        "SUBARU",
        "SUZUKI",
        "CHERY",
        "FAW",
        "HAVAL",
        "GEELY",
        "GREAT WALL",
        "EXEED",
        "OMODA",
        "JAECOO",
        "CHANGAN",
        "LADA",
        "ГАЗ",
        "УАЗ",
        "ВАЗ",
        "DAEWOO",
        "FIAT",
        "PEUGEOT",
        "CITROEN",
        "VOLVO",
        "JAGUAR",
        "LAND ROVER",
        "JEEP",
        "DODGE",
        "CHRYSLER",
        "BENZ",
        "MAN",
        "SCANIA",
        "DAF",
        "IVECO",
        "KAMAZ",
        "КАМАЗ",
        "MAZ",
        "МАЗ",
        "RENAULT TRUCKS",
        "ISUZU",
    }
)

# Топовые Tier-1 / премиум-производители — показывать первыми.
QUALITY_BRANDS = frozenset(
    {
        "MANN",
        "MANN-FILTER",
        "MAHLE",
        "KNECHT",
        "MAHLE / KNECHT",
        "BOSCH",
        "HELLA",
        "VALEO",
        "TRW",
        "LEMFORDER",
        "LEMFÖRDER",
        "SACHS",
        "FEBI",
        "FEBI BILSTEIN",
        "ELRING",
        "NGK",
        "NTK",
        "DENSO",
        "CONTITECH",
        "CONTINENTAL",
        "GATES",
        "LUK",
        "INA",
        "FAG",
        "SCHAEFFLER",
        "SKF",
        "ATE",
        "TEXTAR",
        "FERODO",
        "BREMBO",
        "ZIMMERMANN",
        "PAGID",
        "JURID",
        "BILSTEIN",
        "KYB",
        "MONROE",
        "SANGSIN BRAKE",
        "HI-Q",
        "BLUE PRINT",
        "NIPPARTS",
        "RUVILLE",
        "SWAG",
        "VICTOR REINZ",
        "GLYCO",
        "KOLBENSCHMIDT",
        "MEYLE",
        "DELPHI",
        "NISSENS",
        "BEHR",
        "FILTRON",
        "CHAMPION",
        "MABANARO",
        "OPTIMAL",
        "STELLOX",
        "FEBEST",
        "SIDEM",
        "MOOG",
        "GKN",
        "LOBRO",
        "PIERBURG",
        "MAHLE ORIGINAL",
    }
)


def is_carmaker_badge(brand: str) -> bool:
    """True if brand is a carmaker/luxury badge (OE-markup or cross noise)."""
    return brand.strip().upper() in CARMAKER_BADGES


def is_quality_brand(brand: str) -> bool:
    """True if brand is a known Tier-1/premium manufacturer."""
    return brand.strip().upper() in QUALITY_BRANDS


# Родной OE-бренд группы VAG — единственный автопроизводитель, который в блоке
# «Купить» оставляем (реальный «оригинал» для покупки), когда аналогов нет.
# Люкс-дубли (Bentley/Lamborghini/Porsche) и чужие марки — всегда шум.
VAG_OE_BADGES = frozenset(
    {"VAG", "VW", "VOLKSWAGEN", "AUDI", "SKODA", "SEAT", "CUPRA"}
)


def is_buy_noise(brand: str) -> bool:
    """True if a carmaker/luxury badge should be dropped from the buy block.

    Keeps the car's own VAG-group OE (a real, priceable purchase) but drops the
    luxury duplicates and unrelated carmaker/truck badges.
    """
    upper = brand.strip().upper()
    return is_carmaker_badge(brand) and upper not in VAG_OE_BADGES


def _normalize_number(number: str) -> str:
    """Strip separators/whitespace and upper-case an article number."""
    return "".join(ch for ch in number.strip().upper() if ch.isalnum())


def extract_number(query: str) -> str:
    """Pick the most likely part-number token from a free-form query.

    A part number is a latin/digit token that contains digits (e.g. 'W712/75',
    '036905715G'). Ignores russian words like 'катушка зажигания' so a phrase
    'катушка зажигания BOSCH 036905715G' resolves to '036905715G'.
    """
    best = ""
    best_digits = 0
    for token in re.findall(r"[A-Za-z0-9]+(?:[./-][A-Za-z0-9]+)*", query):
        norm = _normalize_number(token)
        digits = sum(ch.isdigit() for ch in norm)
        if digits == 0 or len(norm) < 4:
            continue
        if digits > best_digits or (digits == best_digits and len(norm) > len(best)):
            best, best_digits = norm, digits
    return best


def _dedupe(candidates: list[PartCandidate]) -> list[PartCandidate]:
    """Merge duplicate brand+number pairs, keeping best name and price."""
    best: dict[tuple[str, str], PartCandidate] = {}
    for cand in candidates:
        key = (cand.brand.upper(), _normalize_number(cand.number))
        current = best.get(key)
        if current is None:
            best[key] = cand
            continue
        best[key] = replace(
            current,
            name=current.name or cand.name,
            price=current.price if current.price is not None else cand.price,
            currency=current.currency if current.price is not None else cand.currency,
            buy_url=current.buy_url or cand.buy_url,
            source=current.source if current.source == cand.source else "autodoc+emex",
        )
    return sorted(
        best.values(),
        key=lambda c: (c.price is None, c.price or 0.0, c.brand.lower()),
    )


def _pick_part_name(candidates: list[PartCandidate]) -> str:
    """Pick the most common non-empty part name across candidates."""
    counts: dict[str, int] = {}
    for cand in candidates:
        if cand.name:
            counts[cand.name] = counts.get(cand.name, 0) + 1
    if not counts:
        return ""
    return max(counts, key=lambda name: counts[name])


def _fix_emex_text(text: str) -> str:
    """Repair Emex double-encoded cyrillic (UTF-8 bytes decoded as cp1251)."""
    if not text:
        return text
    try:
        repaired = text.encode("cp1251").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    # Keep the repair only if it produced cyrillic (guards against false positives).
    if any("а" <= ch.lower() <= "я" for ch in repaired):
        return repaired
    return text
