# Telegram AI Companion Bot

A powerful, persona-driven Telegram companion bot built for safe, emotional, and practical interactions. Uses Groq (Llama family) models with a strict safety layer, per-user memory, Gmail integration, and fast production-ready defaults.

**Last updated:** February 2026

---

## Key Features

* **Multi‑Persona System** — Switch between different AI personalities (girlfriend, therapist, anime characters, yandere, etc.). Each persona has its own system prompt and a "soul" backstory.
* **Persistent User Memory** — Per-user JSON memory stored at `memory/<user_id>.json` that tracks bond & trust levels, recent moods, short conversation history (last 60 turns), last seen timestamp, and silence detection.
* **Gmail Integration (OAuth2)** — Secure Gmail features: connect, list inbox, search messages, generate drafts, and send drafts.
* **Advanced Safety Engine** — Fast keyword pre-filter + regex intent detection for suicide/self-harm, violence, sexual crimes, terrorism, jailbreak attempts, abusive language, and emotional dependency. Crisis deflection wired to Indian helplines.
* **Production-Focused Performance** — Redis (Upstash) caching, LRU fallback, rate limits, and a model-fallback chain for reliability.
* **Image & Multimodal Support** — Base64 images, auto-resize and model-ready preprocessing.
* **Rate Limiting & Throttling** — Global and per-user rate limits to prevent abuse.

---

## Project Layout

```
project/
├── backend/
│   ├── groq_handler.py          # Core Groq interaction, prompt handling, and model fallback
│   ├── safety_engine.py         # All detection functions (mood, harm, jailbreak etc.)
│   ├── personas.py              # PERSONAS dict with names, system prompts and metadata
│   ├── gmail_integration.py     # OAuth helpers, list, draft, send utilities
│   ├── gmail_search.py          # Gmail message search helpers
│   └── memory/                  # Per-user JSON files (gitignored)
├── interaction/
│   └── printer.py               # send_human() wrapper for uniform Telegram responses
├── webhook_entry.py             # FastAPI webhook for Telegram
├── .env                         # BOT_TOKEN, GROQ_API_KEY, REDIS_URL, etc.
└── README.md
```

---

## Quickstart — Development (webhook)

1. Clone the repository

```bash
git clone https://github.com/sanusharma-ui/Telegram-chatbot.git
cd Telegram-chatbot
```

2. Install dependencies

```bash
pip install -r requirements.txt
# or manually:
# pip install fastapi uvicorn aiogram python-dotenv groq redis ratelimit tenacity pillow
```

3. Create `.env` (example values)

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
GROQ_API_KEY=gq-XXXXXXXXXXXXXXXXXXXXXXXX
REDIS_URL=redis://default:password@upstash-host:port
WEBHOOK_SECRET_TOKEN=some_random_secret_for_security
HIGH_TRAFFIC=false   # optional: enables light throttling backoff
```

4. Run the app (webhook mode)

```bash
uvicorn webhook_entry:app --host 0.0.0.0 --port 8000
# Then configure Telegram webhook with BotFather or a curl call:
# https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://your-domain.com/webhook
```

> For local testing without HTTPS, use `ngrok` or run the project in polling mode (small code change in `webhook_entry.py`).

---

## Commands (Telegram)

* `/start` or `/help` — show help and Gmail quick links
* `/persona <name>` — switch persona (e.g. `/persona gf`, `/persona therapist`)
* `/gmail connect` — generate a secure OAuth link for Gmail access
* `/gmail inbox` — list recent emails (summary)
* `/gmail search <query>` — search inbox with a natural-language query
* `/gmail draft <to> | <subject> | <instructions>` — creates a draft
* `/gmail send <draft_id>` — send an existing draft created by the bot
* `/gmail disconnect` — revoke Gmail access for this user

---

## Gmail Integration Notes

* Uses OAuth2 for security: no passwords stored. Only the refresh token (encrypted) is kept if the user consents.
* `gmail_integration.py` provides helper wrappers for listing, searching, drafting and sending.
* Drafts created by `/gmail draft` are stored with metadata so `/gmail send` can reference them safely.

---

## Safety & Crisis Handling

The bot contains multiple defensive layers:

1. **Fast Keyword Filter** — instant reject for high-risk inputs.
2. **Regex Intent Detection** — higher-precision classifiers for self-harm, violence, sexual crimes, terrorism, and jailbreak attempts.
3. **Persona-aware Deflection** — when jailbreak or prompt-leak attempts are detected, the bot responds with a persona-consistent deflection.
4. **Crisis Support** — for Indian users, the bot offers helpline information (KIRAN, AASRA, etc.) and avoids giving instructions that could cause harm.

Example helplines (display-only):

* KIRAN (24/7): 9152987821
* AASRA: 022-27546669

---

## Memory Model

* Per-user JSON stored at `backend/memory/<user_id>.json` (encrypted at rest recommended for production).
* Tracks: `bond_level`, `trust_level`, `recent_moods[]`, `last_60_turns[]`, `last_seen`, `silence_flags` (2d, 5d).
* Memory size is limited and periodically summarized to keep prompt costs manageable.

---

## Performance & Reliability

* **Caching**: Redis for persona prompts + per-message caching. LRU fallback for degraded Redis.
* **Rate Limits**: Global and per-user to avoid abuse. Default: 25 calls/min global, 20 messages/60s per-user.
* **Model Fallback Chain**: `llama-3.3-70b` → `llama-4-scout` → `llama-3.1-8b` (configurable).
* **Throttling**: Optional `HIGH_TRAFFIC` mode adds minor per-request sleep to smooth spikes.

---

## Extensibility & Roadmap

Suggested next steps if you want to iterate:

* Add long-form conversation summarization to keep memory compact.
* Add voice message support (transcribe → respond) and TTS.
* Add inline action buttons for quick persona switching and Gmail shortcuts.
* Add AES‑256 encryption for memory files and tokens at rest.
* Improve persona editor UI so non-devs can add personas safely.

---

## Contributing

Contributions are welcome. Please open issues or PRs for:

* Bug fixes
* New persona templates
* Safety-rule improvements
* Performance tuning

**Before submitting PRs:**

* Run tests (if present) and linting
* Keep secrets out of commits
* Add changelog entry for behavior or safety changes

---

## License

MIT — feel free to fork and adapt for personal or internal use. If you republish, please keep the original credit.

---

## Contact

If you want help improving the repo layout, persona prompts, safety rules, or Gmail UX, open an issue or DM the repo owner.

Made with ❤️ for meaningful, safe AI companionship.
