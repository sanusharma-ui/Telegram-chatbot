# bot/webhook_entry.py
import os
import asyncio
import logging
from fastapi import FastAPI, Request
from aiogram import Bot
from dotenv import load_dotenv
from backend.groq_handler import generate_response
from interaction.printer import send_human
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook_entry")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
bot = Bot(token=BOT_TOKEN)
# 🔴 THIS MUST EXIST AT TOP LEVEL
app = FastAPI()
@app.get("/")
async def health():
    return {"status": "ok"}
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        update = await request.json()
    except Exception as e:
        logger.error("Invalid JSON: %s", e)
        return {"ok": True}
    # fire-and-forget background processing
    asyncio.get_event_loop().call_soon(
        asyncio.create_task,
        process_update(update)
    )
    # IMPORTANT: immediate response
    return {"ok": True}
async def process_update(update: dict):
    try:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat_id = message["chat"]["id"]
        user_id = message["from"]["id"]
        user_text = message.get("text", "") or ""
        # handle /persona command (webhook)
        if user_text.startswith("/persona"):
            parts = user_text.split(maxsplit=1)
            if len(parts) == 2:
                persona = parts[1].strip()
                from backend.personas import PERSONAS
                if persona in PERSONAS:
                    from backend.groq_handler import set_user_persona
                    set_user_persona(str(user_id), persona)
                    await send_human(bot, chat_id, f"✅ Persona switched to *{PERSONAS[persona]['name']}*")
                    return
            await send_human(bot, chat_id, "Usage: /persona <name>\nAvailable: " + ", ".join(PERSONAS.keys()))
            return
        # typing indicator (safe)
        try:
            await bot.send_chat_action(chat_id, "typing")
        except:
            pass
        reply = await asyncio.to_thread(
            generate_response,
            user_message=user_text,
            persona_key=str(user_id),
            user_ip=str(user_id)
        )
        await send_human(bot, chat_id, reply)
    except Exception as e:
        logger.exception("process_update failed: %s", e)