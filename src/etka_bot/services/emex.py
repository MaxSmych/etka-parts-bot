from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_LOGIN_URL = "https://emex.ru/api/account/login"
_PRICES_URL = "https://emex.ru/api/search/bestOffers/prices"


class EmexError(RuntimeError):
    """Raised when Emex cannot authenticate or return prices."""


@dataclass(frozen=True, slots=True)
class EmexOffer:
    """Best-offer price for one brand/number under the user's Emex account."""

    number: str
    make: str
    price: float
    currency: str


class EmexPriceClient:
    """Real Emex prices via the user's account (guest search shows a phantom min).

    Logs in through `api/account/login` (cookies live on emex.ru, no cross-domain
    trick needed) and queries `api/search/bestOffers/prices` for a batch of
    brand/number pairs — the same figures the logged-in site shows the user.
    """

    def __init__(
        self,
        login: str,
        password: str,
        location_id: int = 61137,  # Москва; сервер всё равно берёт регион аккаунта
        latitude: float = 55.75,
        longitude: float = 37.62,
        timeout: float = 20.0,
    ) -> None:
        self._login_value = login
        self._password = password
        self._location_id = location_id
        self._latitude = latitude
        self._longitude = longitude
        self._timeout = timeout
        self._cookies: httpx.Cookies | None = None

    async def prices(
        self, details: list[tuple[str, str]]
    ) -> dict[tuple[str, str], EmexOffer]:
        """Return {(NUMBER, MAKE): EmexOffer} for the given brand/number pairs."""
        if not details:
            return {}
        payload = {
            "details": [{"num": num, "make": make} for num, make in details],
            "latitude": self._latitude,
            "longitude": self._longitude,
            "locationId": self._location_id,
            "dynamicBestOffers": True,
        }
        for attempt in (1, 2):
            async with httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": _UA, "Origin": "https://emex.ru"},
                cookies=self._cookies,
            ) as client:
                if self._cookies is None:
                    await self._login(client)
                response = await client.post(
                    _PRICES_URL,
                    json=payload,
                    headers={
                        "Referer": "https://emex.ru/",
                        "Content-Type": "application/json",
                    },
                )
                if response.status_code in (401, 403) and attempt == 1:
                    self._cookies = None  # stale session → re-login once
                    continue
                if response.status_code >= 400:
                    raise EmexError(f"Emex prices http {response.status_code}")
                return _parse_offers(response.json())
        return {}

    async def _login(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            _LOGIN_URL,
            json={
                "login": self._login_value,
                "password": self._password,
                "t": int(time.time() * 1000),
            },
            headers={
                "Referer": "https://emex.ru/",
                "Content-Type": "application/json",
            },
        )
        if response.status_code >= 400:
            raise EmexError(f"Emex login http {response.status_code}")
        data = response.json()
        if not isinstance(data, dict) or not data.get("userId"):
            raise EmexError("Emex login rejected (no userId).")
        self._cookies = client.cookies


def _parse_offers(data: object) -> dict[tuple[str, str], EmexOffer]:
    offers: dict[tuple[str, str], EmexOffer] = {}
    items = data.get("offers") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return offers
    for item in items:
        if not isinstance(item, dict):
            continue
        detail = item.get("detail") or {}
        price = item.get("displayPrice") or {}
        num = str(detail.get("num", "")).strip()
        make = str(detail.get("make", "")).strip()
        value = price.get("value")
        if not num or not make or not isinstance(value, int | float) or value <= 0:
            continue
        symbol = str(price.get("symbol") or "₽")
        offers[(num.upper(), make.upper())] = EmexOffer(
            number=num, make=make, price=float(value), currency=symbol
        )
    return offers


_client: EmexPriceClient | None = None
_built = False


def get_emex_price_client() -> EmexPriceClient | None:
    """Return a cached Emex price client, or None if credentials are absent."""
    global _client, _built
    if _built:
        return _client
    _built = True
    login = os.getenv("EMEX_LOGIN")
    password = os.getenv("EMEX_PASSWORD")
    if login and password:
        _client = EmexPriceClient(login=login, password=password)
    return _client
