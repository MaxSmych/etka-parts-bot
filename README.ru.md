# 🔧 ETKA Parts Bot

**Русский** · [English](README.md)

> Telegram-бот, который по **названию детали или OE-номеру** даёт точный ответ под
> **VAG (VW · Audi · Škoda · SEAT)**: берёт настоящий OE-номер из **онлайн-ETKA по VIN**,
> подтягивает **кросс-бренд аналоги и живые цены** (Emex · Autodoc · Wildberries) и
> **ранжирует их LLM-ом** по реальному качеству — а не по бейджу на коробке.

<p>
<img alt="python" src="https://img.shields.io/badge/python-3.11%2B-blue">
<img alt="aiogram" src="https://img.shields.io/badge/aiogram-3.x-2CA5E0">
<img alt="license" src="https://img.shields.io/badge/license-MIT-green">
</p>

---

## ✨ Что умеет

Пришли фото, голосовое, OE-номер или просто название детали — бот ответит:

- 🅾️ **Правда из каталога ETKA** — реальный OE-номер под *твой* VIN (действующий /
  заменён / снят), номер LLM НЕ выдумывает.
- 🏭 **Кто реально производит** — завод-поставщик на конвейер (MANN, Mahle/Knecht, Bosch,
  Valeo, TRW…). «Оригинал» в коробке автопроизводителя — та же деталь с наценкой ×3–×10.
- 🔧 **Аналоги по тирам** — 🥇 OE-поставщик / 🥈 премиум / 🥉 бюджет: короткая выжимка
  ходовых брендов, а не свалка из сотни ноунеймов.
- ⚠️ **Признаки подделки** — на что смотреть по этой позиции (упаковка, маркировка, цена).
- 🛒 **Где купить** — живые цены из Emex (по аккаунту), Autodoc и Wildberries со ссылками.
- 🖼️ **Сверка фото** — пришли фото детали/упаковки, LLM даст вердикт оригинал/подделка.
- 🎙️ **Голосовой ввод** — надиктуй запрос, Whisper распознает.

## 🧠 Как это работает

```
«салонный фильтр»  ──►  ETKA (по VIN)        ──►  OE-номер 1K1819669
   или «1K1819669»      ├ категории обслуживания → LLM выбирает категорию
                        └ detail()  → действующий / замена / снят
                               │
                               ▼
     реальные номера  ──►  каталоги: Emex + Autodoc + Wildberries
                               │  (кросс-бренд аналоги + живые цены)
                               ▼
                        ранжирование LLM (тиры, актуальность, подделки)
                               │
                               ▼
                        ответ в Telegram + блок «Где купить»
```

**OE-номер LLM никогда не выдумывает** — номер всегда из ETKA. LLM делает только семантику
(сопоставляет название детали с категорией ETKA) и ранжирование.

## 🌐 Источники данных

| Источник | Роль | Доступ |
|---|---|---|
| **онлайн-ETKA** (superetka) | источник истины по OE-номеру, каталог по VIN | логин |
| **Emex** | кросс-бренд аналоги + **реальные** цены по аккаунту | логин |
| **Autodoc** | бренд + название детали (TecDoc) | бесплатно |
| **Wildberries** | цены маркетплейса (best-effort) | бесплатно |
| **Anthropic Claude** (или совместимый шлюз) | мозг-подборщик/ранжировщик | API-ключ |
| **OpenAI Whisper** | распознавание голоса (опц.) | API-ключ |

---

## 🚀 Быстрый старт

**Нужно:** Python 3.11+ и [uv](https://docs.astral.sh/uv/). Токен бота от
[@BotFather](https://t.me/BotFather). Аккаунты ETKA (superetka) и Emex. Ключ Anthropic
(или совместимый шлюз).

```bash
git clone https://github.com/MaxSmych/etka-parts-bot.git
cd etka-parts-bot
cp .env.example .env      # затем заполни (см. ниже)
uv sync
uv run etka-bot
```

### Конфигурация (`.env`)

| Переменная | Обяз. | Описание |
|---|:---:|---|
| `BOT_TOKEN` | ✅ | Токен бота от @BotFather |
| `ANTHROPIC_API_KEY` | ✅ | Ключ Claude (или токен шлюза) |
| `ANTHROPIC_BASE_URL` | | Свой совместимый шлюз (по умолч. `https://api.anthropic.com`) |
| `CLAUDE_MODEL` / `CLAUDE_MODEL_PICK` | | Модель ранжирования / лёгкая модель для выбора категории |
| `CAR_VIN` | ✅* | VIN — задаёт каталог ETKA и подбор по названию |
| `CAR_ENGINE` | | Двигатель (тип+объём+код, напр. `2.0 TDI CBAB`) — сильно влияет на артикулы |
| `CAR_PROFILE_FILE` | | Файл с заводским build-sheet (VIN-декод с PR-кодами) для контекста |
| `SUPERETKA_LOGIN` / `SUPERETKA_PASSWORD` | ✅* | Аккаунт онлайн-ETKA |
| `EMEX_LOGIN` / `EMEX_PASSWORD` | | Аккаунт Emex — включает реальные цены |
| `OPENAI_API_KEY` | | Включает голосовой ввод (Whisper) |
| `TG_PROXY` | | SOCKS5-прокси для Telegram — см. ниже |

`*` Без ETKA бот запустится, но не сможет резолвить номера по VIN и свалится в обычную
LLM-диагностику («пришли точный номер»).

---

## 🛰️ Прокси для Telegram (если `api.telegram.org` недоступен)

В некоторых сетях `api.telegram.org` недоступен напрямую. Задай `TG_PROXY`, чтобы гнать
через SOCKS5-прокси **только Telegram Bot API** (всё остальное — ETKA, Emex, Claude — идёт
напрямую):

```dotenv
TG_PROXY=socks5://user:password@host:port
```

**Важно — схема ТОЛЬКО `socks5://`, не `socks5h://`:**

- aiogram строит прокси-коннектор через [`python-socks`](https://pypi.org/project/python-socks/),
  который **не понимает схему `socks5h`** и падает на старте с ошибкой
  `ValueError: Invalid scheme component: socks5h`.
- `socks5h` и не нужен: aiogram уже форсит **удалённый DNS** (`rdns=True`), т.е. обычный
  `socks5://` резолвит хосты на стороне прокси — ровно то, что значит `socks5h`.

Прокси применяется только к Telegram-сессии бота ([`build_bot`](src/etka_bot/bot.py));
запросы к каталогам и LLM не проксируются.

---

## 📦 Развёртывание

**Docker:**

```bash
docker compose up -d --build
docker compose logs -f
```

**Windows (автозапуск + авто-рестарт), без прав админа:**

- `run_bot.bat` — петля рестарта, логи в `data\bot.log` (ротация при 5 МБ).
- `run_hidden.vbs` — то же, но без окна консоли.
- `install_autostart.ps1` — ярлык в автозагрузке, бот переживает перезагрузку.

SQLite/история и логи лежат в `data/` (в git не попадает).

## 🗂️ Структура

```
src/etka_bot/
├── main.py                 # точка входа (asyncio + polling)
├── bot.py                  # aiogram bot/dispatcher + DI, прокси-сессия
├── config.py               # настройки из .env
├── handlers/parts.py       # все хендлеры (текст, голос, фото)
└── services/
    ├── superetka.py        # онлайн-ETKA: OE-номер по VIN (источник истины)
    ├── parts.py            # адаптеры Autodoc + Emex + Wildberries
    ├── emex.py             # реальные цены по аккаунту Emex
    ├── parts_advisor.py    # пайплайн + промпты LLM (по номеру / по названию)
    ├── claude.py           # клиент Anthropic Messages API (+ шлюзы)
    ├── openai.py           # Whisper (голос → текст)
    └── audio.py            # OGG/Opus → WAV (PyAV)
```

## ⚖️ Дисклеймер

Неофициальный хобби-проект, не связан с Volkswagen AG, ETKA, Emex, Autodoc или Wildberries.
Нужны свои аккаунты и API-ключи. Используй ответственно и в рамках правил каждого сервиса.
OE-номера берутся из ETKA — всё равно перепроверяй деталь перед покупкой.

## 📄 Лицензия

[MIT](LICENSE) © 2026 MaxSmych
