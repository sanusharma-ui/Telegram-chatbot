# Telegram AI Companion Bot with Gmail Integration

A powerful, persona-driven Telegram bot powered by **Groq (Llama models)** with strong safety mechanisms, emotional memory, Gmail integration, and multi-persona support.

## Features

- **Persona System**  
  Switch between different AI personalities (e.g. girlfriend, therapist, anime characters, yandere, etc.)  
  Each persona has its own system prompt + soul backstory

- **User Memory & Evolution**  
  Per-user persistent memory stored in `memory/<user_id>.json`  
  Tracks:  
  - Bond & trust levels (evolves over time)  
  - Recent moods (positive/negative/neutral)  
  - Conversation history (last 60 turns)  
  - Last seen timestamp + silence detection (2d, 5d absence)

- **Advanced Safety Layer** (very strict & production-grade)
  - Fast keyword pre-filter for extreme harm
  - Regex-based intent detection for:
    - Suicide / self-harm
    - Violence
    - Sexual crimes
    - Terrorism
    - Emotional dependency
  - Jailbreak / OOC / prompt leaking attempts detection
  - Abusive language filter (Hindi + English slurs)
  - Mood-killer phrase removal (no "I am an AI" breaks immersion)
  - Crisis deflection with Indian helpline numbers

- **Gmail Integration** (via OAuth2)
  Commands:
  - `/gmail connect` → secure OAuth link
  - `/gmail inbox` → recent emails summary
  - `/gmail search <query>`
  - `/gmail draft <to> | <subject> | <instructions>` → AI-generated draft
  - `/gmail send <draft_id>`
  - `/gmail disconnect`

- **Performance Optimizations**
  - Redis caching (Upstash compatible) + LRU fallback
  - Per-message + persona caching
  - Global rate limit: 25 calls/min
  - Per-user rate limit: 20 msg/60s
  - Model fallback chain: llama-3.3-70b → llama-4-scout → llama-3.1-8b

- **Image Support** (vision ready)
  - Base64 encoding + auto-resize
  - Ready for multimodal models

## Project Structure (important files)
project/
├── backend/
│   ├── groq_handler.py          # Core Groq + safety + memory logic
│   ├── safety_engine.py         # All detection functions (mood, harm, jailbreak etc.)
│   ├── personas.py              # PERSONAS dict with names & prompts
│   ├── gmail_integration.py     # OAuth, draft, send, summary etc.
│   ├── gmail_search.py          # Gmail message search
│   └── memory/                  # Per-user JSON files (git ignored)
├── interaction/
│   └── printer.py               # send_human() wrapper
├── webhook_entry.py             # FastAPI webhook for Telegram
├── .env                         # BOT_TOKEN, GROQ_API_KEY, REDIS_URL etc.
└── README.md
text## Setup Instructions

1. Clone the repo
   ```bash
   git clone https://github.com/sanusharma-ui/Telegram-chatbot
  
Install dependenciesBashpip install -r requirements.txt
# or manually:
pip install fastapi uvicorn aiogram python-dotenv groq redis ratelimit tenacity pillow
Create .env fileenvTELEGRAM_BOT_TOKEN=your_bot_token_here
GROQ_API_KEY=gq-XXXXXXXXXXXXXXXXXXXXXXXX
REDIS_URL=redis://default:password@upstash-host:port
WEBHOOK_SECRET_TOKEN=some_random_secret_for_security
# Optional: HIGH_TRAFFIC=true  (adds small sleep for throttling)
Run the bot (webhook mode)Bashuvicorn webhook_entry:app --host 0.0.0.0 --port 8000Then set webhook via Telegram BotFather or curl:texthttps://api.telegram.org/bot<TOKEN>/setWebhook?url=https://your-domain.com/webhook

Available Commands

/start or /help → shows Gmail help
/persona <name> → switch persona (e.g. /persona gf, /persona therapist)
/gmail connect
/gmail inbox
/gmail search meeting tomorrow
/gmail draft friend@gmail.com | Project Update | remind about Friday deadline
/gmail send <draft_id>
/gmail disconnect

Safety & Crisis Handling
When harmful content is detected:

CategoryResponse BehaviorHelpline (India)Immediate suicideUrgent message + KIRAN 91529878219152987821 (24/7)General self-harmSupportive + AASRA / KIRAN numbers022-27546669Violence / crimeRefusal + constructive redirect—Jailbreak attemptDeflection (persona-specific witty reply)—Abusive languagePolite refusal—
Tech Stack

Backend: FastAPI + aiogram (Telegram)
LLM: Groq API (Llama 3.3 70B, Scout, 8B fallback)
Cache: Redis (Upstash) + lru_cache
Storage: JSON files per user
Safety: Custom regex + keyword engines
Auth: Google OAuth2 for Gmail

Contributing / Improving
Possible next steps:

Add conversation summarization for long histories
Dynamic temperature / max_tokens per persona
Voice message support (transcribe → respond)
Inline buttons for quick actions
Better error reporting to admin chat
Encrypt memory files (if multi-tenant server)

License
MIT (feel free to fork & modify)
Made with ❤️ for meaningful & safe AI companionship.
## Support
If you like this project, please ⭐ the repo!
Last updated: February 2026
