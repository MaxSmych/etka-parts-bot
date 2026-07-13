# 🔧 ETKA Parts Bot

> Telegram bot that turns a **part name or an OE number** into a real, VIN-accurate
> answer for **VAG cars** (VW · Audi · Škoda · SEAT): it resolves the genuine OE
> number from **online ETKA by VIN**, pulls **cross-brand analogs and live prices**
> (Emex · Autodoc · Wildberries), and lets an **LLM rank them** by real quality —
> not by the badge on the box.

<p>
<img alt="python" src="https://img.shields.io/badge/python-3.11%2B-blue">
<img alt="aiogram" src="https://img.shields.io/badge/aiogram-3.x-2CA5E0">
<img alt="license" src="https://img.shields.io/badge/license-MIT-green">
</p>

> ⚠️ The bot talks to users in **Russian** (its market is RU: ETKA / Emex / Autodoc /
> Wildberries). This README is in English for discoverability; prompts and replies are RU.

---

## ✨ What it does

Send the bot a photo, a voice message, an OE number, or just a part name — it replies with:

- 🅾️ **ETKA catalog truth** — the real OE number for *your* VIN (current / superseded /
  cancelled), never invented by the LLM.
- 🏭 **Who actually makes it** — the Tier‑1 supplier that ships the part to the assembly
  line (MANN, Mahle/Knecht, Bosch, Valeo, TRW …). The "genuine" box is the *same part*
  with a ×3–×10 markup.
- 🔧 **Analogs by tier** — 🥇 OE‑supplier / 🥈 premium / 🥉 budget, a short curated list,
  not a 200‑brand dump.
- ⚠️ **Counterfeit hints** — what to check on this specific position (packaging, marking,
  suspiciously low price).
- 🛒 **Where to buy** — live prices from Emex (account offers), Autodoc and Wildberries,
  with direct links.
- 🖼️ **Photo check** — send a photo of the part/box, the LLM gives an original‑vs‑fake verdict.
- 🎙️ **Voice input** — dictate the request, Whisper transcribes it.

## 🧠 How it works

```
user: "cabin filter"  ──►  ETKA (by VIN)      ──►  OE number 1K1819669
        or "1K1819669"      ├ maintenance categories → LLM picks the category
                            └ detail()  → current / replacement / cancelled
                                   │
                                   ▼
        real number(s)  ──►  catalogs: Emex + Autodoc + Wildberries
                                   │  (cross-brand analogs + live prices)
                                   ▼
                            LLM ranking (tiers, actuality, counterfeit notes)
                                   │
                                   ▼
                            Telegram reply + "where to buy" block
```

The **LLM never invents an OE number** — the number always comes from ETKA. The LLM only
does semantics (matching a free‑text part name to an ETKA category) and ranking.

## 🌐 Data sources

| Source | Role | Auth |
|---|---|---|
| **online‑ETKA** (superetka) | source of truth for OE numbers, VIN‑scoped catalog | login |
| **Emex** | cross‑brand analogs + **real** account prices | login |
| **Autodoc** | brand + part name (TecDoc) | free |
| **Wildberries** | marketplace prices (best‑effort) | free |
| **Anthropic Claude** (or a compatible gateway) | the ranking/identification brain | API key |
| **OpenAI Whisper** | voice transcription (optional) | API key |

---

## 🚀 Quick start

**Prerequisites:** Python 3.11+ and [uv](https://docs.astral.sh/uv/). A Telegram bot token
from [@BotFather](https://t.me/BotFather). ETKA (superetka) and Emex accounts. An Anthropic
API key (or a compatible gateway).

```bash
git clone https://github.com/MaxSmych/etka-parts-bot.git
cd etka-parts-bot
cp .env.example .env      # then fill it in (see below)
uv sync
uv run etka-bot
```

### Configuration (`.env`)

| Variable | Required | Description |
|---|:---:|---|
| `BOT_TOKEN` | ✅ | Telegram bot token from @BotFather |
| `ANTHROPIC_API_KEY` | ✅ | Claude API key (or gateway token) |
| `ANTHROPIC_BASE_URL` | | Override for a compatible gateway (default `https://api.anthropic.com`) |
| `CLAUDE_MODEL` / `CLAUDE_MODEL_PICK` | | Ranking model / lean model for category pick |
| `CAR_VIN` | ✅* | VIN — drives the ETKA catalog and by‑name resolution |
| `CAR_ENGINE` | | Engine (type+displacement+code, e.g. `2.0 TDI CBAB`) — strongly affects part numbers |
| `CAR_PROFILE_FILE` | | Path to a factory build‑sheet (VIN‑decode with PR‑codes) for extra context |
| `SUPERETKA_LOGIN` / `SUPERETKA_PASSWORD` | ✅* | online‑ETKA account |
| `EMEX_LOGIN` / `EMEX_PASSWORD` | | Emex account — enables real prices |
| `OPENAI_API_KEY` | | Enables voice input (Whisper) |
| `TG_PROXY` | | SOCKS5 proxy for Telegram — see below |

`*` The bot runs without ETKA, but then it can't resolve numbers by VIN and falls back to
plain LLM identification ("send me the exact number").

---

## 🛰️ Telegram proxy (if `api.telegram.org` is blocked)

In some regions `api.telegram.org` is unreachable directly. Set `TG_PROXY` to route **only
the Telegram Bot API** through a SOCKS5 proxy (everything else — ETKA, Emex, Claude — goes
direct):

```dotenv
TG_PROXY=socks5://user:password@host:port
```

**Important — scheme must be `socks5://`, not `socks5h://`:**

- aiogram builds its proxy connector via [`python-socks`](https://pypi.org/project/python-socks/),
  which **does not accept the `socks5h` scheme** and will crash on startup with
  `ValueError: Invalid scheme component: socks5h`.
- You don't need `socks5h` anyway: aiogram already forces **remote DNS** (`rdns=True`), so a
  plain `socks5://` proxy resolves hostnames on the proxy side — exactly what `socks5h` means.

The proxy is applied only to the bot's Telegram session
([`build_bot`](src/etka_bot/bot.py)); outbound calls to catalogs and the LLM are not proxied.

---

## 📦 Deployment

**Docker:**

```bash
docker compose up -d --build
docker compose logs -f
```

**Windows (autostart + auto‑restart), no admin needed:**

- `run_bot.bat` — restart loop, logs to `data\bot.log` (rotates at 5 MB).
- `run_hidden.vbs` — same, but windowless.
- `install_autostart.ps1` — drops a Startup shortcut so the bot survives reboot.

The SQLite/history and logs live under `data/` (git‑ignored).

## 🗂️ Project layout

```
src/etka_bot/
├── main.py                 # entry point (asyncio + polling)
├── bot.py                  # aiogram bot/dispatcher + DI, proxy session
├── config.py               # Settings from .env
├── handlers/parts.py       # all handlers (text, voice, photo)
└── services/
    ├── superetka.py        # online-ETKA: OE number by VIN (source of truth)
    ├── parts.py            # Autodoc + Emex + Wildberries adapters
    ├── emex.py             # real Emex account prices
    ├── parts_advisor.py    # the pipeline + LLM prompts (by number / by name)
    ├── claude.py           # Anthropic Messages API client (+ gateway compatible)
    ├── openai.py           # Whisper (voice → text)
    └── audio.py            # OGG/Opus → WAV (PyAV)
```

## ⚖️ Disclaimer

Unofficial hobby project, not affiliated with Volkswagen AG, ETKA, Emex, Autodoc or
Wildberries. You need your own accounts and API keys. Use responsibly and within each
service's terms. OE numbers come from ETKA; always double‑check a part before buying.

## 📄 License

[MIT](LICENSE) © 2026 MaxSmych
