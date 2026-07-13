from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace

import httpx

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_AUTH_URL = "https://superetka.com/ajax/ajax.php"
_CAT_URL = "https://superetka.com/etka/index.php"
# Login form marker: the catalog serves it instead of data when unauthenticated.
_LOGIN_MARKER = "authFromNew"
# A VAG-style part number: starts with a digit, ≥6 alnum chars, mostly digits.
_NUM_RE = re.compile(r"\b(\d[0-9A-Z]{5,})\b")
_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
# A maintenance-category tab: showSpareDetailsTabs("<ka>",<katalog>,<tag>,…)'>Name
_CAT_RE = re.compile(
    r"showSpareDetailsTabs\(\"([A-Z0-9]+)\",\d+,\d+[^)]*\)[^>]*>([^<]+)"
)


class SuperetkaError(RuntimeError):
    """Raised when the online-ETKA catalog cannot be reached or parsed."""


@dataclass(frozen=True, slots=True)
class EtkaEntry:
    """One part-number row from ETKA's detail view (authoritative catalog data)."""

    number: str
    name: str
    cancelled: bool = False  # отменён производителем (снят с поставки)
    cancel_date: str = ""  # дата отмены, если указана
    is_replacement: bool = False  # это актуальная замена отменённого номера


@dataclass(frozen=True, slots=True)
class EtkaCategory:
    """A maintenance-parts category for the identified car (name + ETKA `ka` code)."""

    name: str
    ka: str


@dataclass(frozen=True, slots=True)
class EtkaDetail:
    """Authoritative ETKA facts for a queried number: itself + supersessions."""

    query: str
    entries: tuple[EtkaEntry, ...]

    def numbers(self) -> list[str]:
        """Unique part numbers (queried + replacements) to price in catalogs."""
        seen: list[str] = []
        for entry in self.entries:
            if entry.number not in seen:
                seen.append(entry.number)
        return seen


class SuperetkaClient:
    """Online-ETKA (superetka.com) as the source of truth for OE numbers.

    Auth cookie is issued for domain .kolhosniki.ru (the catalog engine), so we
    capture PHPSESSID from the login response and pass it back manually — httpx's
    jar drops it on the superetka.com host. The session id is cached and re-used;
    if the catalog answers with the login form, we re-authenticate once.

    The catalog is VIN-scoped: `ajaxSelectVinModel` resolves the car's ETKA codes
    (model/year/katalog) and primes the session, after which the maintenance-parts
    tree (`ajaxSpareDetails` → `ajaxSpareDetailsCurrent`) returns numbers for THIS
    exact car — so a part name resolves to the right OE number without the LLM.
    """

    def __init__(
        self,
        login: str,
        password: str,
        vin: str | None = None,
        marke: str = "VW",
        timeout: float = 25.0,
    ) -> None:
        self._login = login
        self._password = password
        self._vin = vin
        self._marke = marke
        self._timeout = timeout
        self._sid: str | None = None
        self._categories: tuple[EtkaCategory, ...] | None = None

    async def detail(self, number: str) -> EtkaDetail:
        """Look up a part number in ETKA and return it plus any supersessions."""
        async with self._session() as client:
            html = await self._get(
                client,
                {
                    "cat": "ajaxDetailMain",
                    "marke": self._marke,
                    "lang": "RU",
                    "detail": number,
                    "cnt": "1",
                    "modal": "1",
                    "vin": self._vin or "",
                },
            )
        return EtkaDetail(query=number, entries=_parse_detail(html))

    async def maintenance_categories(self) -> tuple[EtkaCategory, ...]:
        """Maintenance-parts categories for the car (cached; VIN-scoped)."""
        if self._categories is not None:
            return self._categories
        async with self._session() as client:
            codes = await self._resolve_vin(client)
            html = await self._get(
                client,
                {
                    "cat": "ajaxSpareDetails",
                    "marke": codes.get("marke", self._marke),
                    "lang": "RU",
                    "model": codes.get("model", ""),
                    "year": codes.get("year", ""),
                    "vin": self._vin or "",
                },
            )
        self._categories = _parse_categories(html)
        return self._categories

    async def category_numbers(self, ka: str) -> tuple[EtkaEntry, ...]:
        """OE numbers under one maintenance category (`ka`) for this car."""
        async with self._session() as client:
            codes = await self._resolve_vin(client)
            html = await self._get(
                client,
                {
                    "cat": "ajaxSpareDetailsCurrent",
                    "marke": codes.get("marke", self._marke),
                    "lang": "RU",
                    "model": codes.get("model", ""),
                    "year": codes.get("year", ""),
                    "katalog": codes.get("kat", ""),
                    "ka": ka,
                    "tag": "0",
                    "vin": self._vin or "",
                },
            )
        return _parse_spare_numbers(html)

    def _session(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": _UA})

    async def _resolve_vin(self, client: httpx.AsyncClient) -> dict[str, str]:
        """Feed the VIN → resolve ETKA codes and prime the VIN-scoped session."""
        text = await self._get(
            client, {"cat": "ajaxSelectVinModel", "vin": self._vin or "", "r": "0.1"}
        )
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError) as error:
            raise SuperetkaError(f"ETKA VIN resolve failed: {text[:80]!r}") from error
        return {k: str(v) for k, v in data.items()}

    async def _get(self, client: httpx.AsyncClient, params: dict[str, str]) -> str:
        """GET the catalog with the session cookie; re-authenticate once if needed."""
        if not self._sid:
            await self._authenticate(client)
        text = await self._raw_get(client, params)
        if _LOGIN_MARKER in text or not text.strip():
            self._sid = None
            await self._authenticate(client)
            text = await self._raw_get(client, params)
        return text

    async def _raw_get(self, client: httpx.AsyncClient, params: dict[str, str]) -> str:
        response = await client.get(
            _CAT_URL,
            params=params,
            headers={
                "Cookie": f"PHPSESSID={self._sid}",
                "Referer": "https://superetka.com/etka/",
            },
        )
        if response.status_code >= 400:
            raise SuperetkaError(f"ETKA http {response.status_code}")
        return response.text

    async def _authenticate(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            _AUTH_URL,
            data={
                "cat": "auth",
                "lang": "EN",
                "lgn": self._login,
                "pwd": self._password,
                "tkn": "",
            },
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://superetka.com",
                "Referer": "https://superetka.com/etka/",
            },
        )
        body = response.text.strip()
        sid = None
        for cookie in response.headers.get_list("set-cookie"):
            match = re.search(r"PHPSESSID=([a-f0-9]+)", cookie)
            if match:
                sid = match.group(1)
                break
        if body != "1" or not sid:
            raise SuperetkaError(f"ETKA auth failed (body={body!r}, sid={sid!r})")
        self._sid = sid


def _clean(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()


def _parse_detail(html: str) -> tuple[EtkaEntry, ...]:
    """Parse ETKA's ajaxDetailMain rows into ordered, de-duplicated entries.

    Only the first market block (the identified car's «Local» catalog) is read;
    later blocks repeat the data per foreign market and would add noise.
    """
    cut = html.find("adInfoElsa")
    block = html[:cut] if cut > 0 else html

    entries: list[EtkaEntry] = []
    seen: set[str] = set()
    expect_replacement = False
    for raw_row in _ROW_RE.findall(block):
        text = _clean(raw_row)
        if not text:
            continue
        is_note = "Отмена" in text or "используйте замену" in text
        if is_note:
            if entries:
                date = _DATE_RE.search(text)
                entries[-1] = replace(
                    entries[-1],
                    cancelled=True,
                    cancel_date=date.group(1) if date else "",
                )
            expect_replacement = True
            continue
        parsed = _parse_number_row(text)
        if not parsed:
            continue
        number, name = parsed
        if number in seen:
            expect_replacement = False
            continue
        seen.add(number)
        entries.append(
            EtkaEntry(number=number, name=name, is_replacement=expect_replacement)
        )
        expect_replacement = False
    return tuple(entries[:6])


def _parse_number_row(text: str) -> tuple[str, str] | None:
    """Extract (number, name) from a detail row; None if it holds no part number."""
    match = _NUM_RE.search(text)
    if not match:
        return None
    number = match.group(1)
    if sum(ch.isdigit() for ch in number) < 4:
        return None  # guard against stray tokens like a year
    rest = text[match.end() :].strip(" *")
    # Name ends at the price-column placeholder or a trailing quantity digit.
    rest = rest.split("Тут может")[0].strip()
    rest = re.sub(r"\s+\d+$", "", rest).strip()
    return number, rest


def _parse_categories(html: str) -> tuple[EtkaCategory, ...]:
    """Parse maintenance-category tabs (name + `ka`) from ajaxSpareDetails."""
    out: list[EtkaCategory] = []
    seen: set[str] = set()
    for ka, name in _CAT_RE.findall(html):
        clean = _clean(name)
        if not clean or ka in seen:
            continue
        seen.add(ka)
        out.append(EtkaCategory(name=clean, ka=ka))
    return tuple(out)


def _parse_spare_numbers(html: str) -> tuple[EtkaEntry, ...]:
    """Parse OE numbers under a maintenance category (ajaxSpareDetailsCurrent)."""
    out: list[EtkaEntry] = []
    seen: set[str] = set()
    for raw_row in _ROW_RE.findall(html) or [html]:
        text = _clean(raw_row)
        match = _NUM_RE.search(text)
        if not match:
            continue
        number = match.group(1)
        if sum(ch.isdigit() for ch in number) < 4 or number in seen:
            continue
        seen.add(number)
        rest = text[match.end() :].strip(" *")
        # Name ends before the quantity column (a standalone digit).
        rest = re.split(r"\s+\d+\s", rest)[0].strip()
        out.append(EtkaEntry(number=number, name=rest))
    return tuple(out[:6])


_client: SuperetkaClient | None = None
_built = False


def get_superetka_client() -> SuperetkaClient | None:
    """Return a cached ETKA client, or None if credentials are absent."""
    global _client, _built
    if _built:
        return _client
    _built = True
    login = os.getenv("SUPERETKA_LOGIN")
    password = os.getenv("SUPERETKA_PASSWORD")
    if login and password:
        _client = SuperetkaClient(
            login=login,
            password=password,
            vin=os.getenv("CAR_VIN") or None,
        )
    return _client
