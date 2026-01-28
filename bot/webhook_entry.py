# # bot/webhook_entry.py
# import os
# import asyncio
# import logging
# from fastapi import FastAPI, Request, HTTPException
# from pydantic import BaseModel
# from aiogram import Bot
# from backend.groq_handler import generate_response
# from interaction.printer import send_human
# from backend.personas import PERSONAS
# from dotenv import load_dotenv

# load_dotenv()
# logging.basicConfig(level=logging.WARNING)
# logger = logging.getLogger("webhook_entry")

# BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# if not BOT_TOKEN:
#     logger.error("TELEGRAM_BOT_TOKEN not set in environment")
#     raise SystemExit("Missing BOT_TOKEN")
# bot = Bot(token=BOT_TOKEN)

# app = FastAPI()

# class UpdateModel(BaseModel):
#     update_id: int

# @app.get("/")
# async def health():
#     return {"status": "ok"}

# @app.post("/webhook")
# async def telegram_webhook(request: Request):
#     """
#     Webhook receiver for Telegram updates.
#     Quickly ACK then process in background to avoid timeouts.
#     """
#     try:
#         update = await request.json()
#     except Exception as e:
#         logger.error("Invalid JSON in webhook: %s", e)
#         raise HTTPException(status_code=400, detail="Invalid JSON")

#     # Quick validation and ACK
#     # Fire-and-forget background processing
#     asyncio.create_task(_process_update_async(update))
#     return {"ok": True}

# async def _process_update_async(update: dict):
#     """
#     Background worker: parse update and respond.
#     Keep everything guarded with timeouts and try/except.
#     """
#     try:
#         # message path (text)
#         message = update.get("message") or update.get("edited_message")
#         if not message:
#             # Not a standard message (callback query, etc.) — ignore for now
#             return

#         chat_id = message["chat"]["id"]
#         user_id = message["from"]["id"]
#         # handle photo if present
#         if "photo" in message:
#             # Use caption as prompt if present
#             user_text = message.get("caption", "Describe this image")
#             # Downloading file and passing path requires more code; fallback to passing no image path
#             image_path = None
#         else:
#             user_text = message.get("text", "")

#         # Send quick typing indicator + ephemeral ack message
#         try:
#             await bot.send_chat_action(chat_id, "typing")
#             ack = await bot.send_message(chat_id, "Processing... ⏳")
#             ack_msg_id = ack.message_id
#         except Exception:
#             ack_msg_id = None

#         # Generate in a thread with timeout (protect main loop)
#         try:
#             # run generate_response in thread pool
#             raw_reply = await asyncio.wait_for(
#                 asyncio.to_thread(generate_response, user_message=user_text or "", persona_key="default", user_ip=str(user_id)),
#                 timeout=30.0
#             )
#         except asyncio.TimeoutError:
#             raw_reply = "Response taking too long. Try again later."
#         except Exception as e:
#             logger.exception("Generation failed: %s", e)
#             raw_reply = "Sorry, I couldn't process that. Try again."

#         # Use human sender to split and send (leave ack message as edited to final)
#         persona_name = PERSONAS.get("default", {}).get("name", "Bot")
#         full_text = f"*{persona_name}*:\n\n{raw_reply}"
#         # If we have an ack message, edit it to avoid spam; otherwise, send new
#         try:
#             if ack_msg_id:
#                 # edit ack message to final (fast path)
#                 await bot.edit_message_text(chat_id=chat_id, message_id=ack_msg_id, text=full_text, parse_mode="Markdown")
#             else:
#                 # fallback: send humanized sequence
#                 await send_human(bot, chat_id, raw_reply)
#         except Exception as e:
#             logger.exception("Failed to deliver reply: %s", e)
#     except Exception as e:
#         logger.exception("Unhandled error in _process_update_async: %s", e)
import os
import asyncio
import logging
from fastapi import FastAPI, Request
from aiogram import Bot
from dotenv import load_dotenv

from backend.groq_handler import generate_response
from backend.personas import PERSONAS
from interaction.printer import send_human

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook_entry")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

bot = Bot(token=BOT_TOKEN)
app = FastAPI()


@app.get("/")
async def health():
    return {"status": "ok"}


@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    ⚠️ CRITICAL RULE:
    - Telegram ko FAST response chahiye
    - Heavy logic background me
    """
    try:
        update = await request.json()
    except Exception as e:
        logger.error("Invalid JSON: %s", e)
        return {"ok": True}

    # 🚀 FIRE & FORGET — NEVER BLOCK WEBHOOK
    loop = asyncio.get_event_loop()
    loop.call_soon(asyncio.create_task, process_update(update))

    # ⚡ IMMEDIATE ACK (THIS FIXES TIMEOUT)
    return {"ok": True}


async def process_update(update: dict):
    try:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat_id = message["chat"]["id"]
        user_id = message["from"]["id"]

        user_text = message.get("text", "") or message.get("caption", "")

        # typing indicator (non-blocking)
        try:
            await bot.send_chat_action(chat_id, "typing")
        except:
            pass

        # 🔥 HEAVY WORK OFF WEBHOOK THREAD
        reply = await asyncio.to_thread(
            generate_response,
            user_message=user_text,
            persona_key=str(user_id),
            user_ip=str(user_id)
        )

        await send_human(bot, chat_id, reply)

    except Exception as e:
        logger.exception("process_update failed: %s", e)
