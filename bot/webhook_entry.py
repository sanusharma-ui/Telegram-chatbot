
# import os
# import asyncio
# import logging
# from fastapi import FastAPI, Request
# from aiogram import Bot
# from dotenv import load_dotenv

# from backend.groq_handler import generate_response
# from backend.personas import PERSONAS
# from interaction.printer import send_human

# load_dotenv()
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("webhook_entry")

# BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# if not BOT_TOKEN:
#     raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

# bot = Bot(token=BOT_TOKEN)
# app = FastAPI()


# @app.get("/")
# async def health():
#     return {"status": "ok"}


# @app.post("/webhook")
# async def telegram_webhook(request: Request):
#     """
#     ⚠️ CRITICAL RULE:
#     - Telegram ko FAST response chahiye
#     - Heavy logic background me
#     """
#     try:
#         update = await request.json()
#     except Exception as e:
#         logger.error("Invalid JSON: %s", e)
#         return {"ok": True}

#     # 🚀 FIRE & FORGET — NEVER BLOCK WEBHOOK
#     loop = asyncio.get_event_loop()
#     loop.call_soon(asyncio.create_task, process_update(update))

#     # ⚡ IMMEDIATE ACK (THIS FIXES TIMEOUT)
#     return {"ok": True}


# async def process_update(update: dict):
#     try:
#         message = update.get("message") or update.get("edited_message")
#         if not message:
#             return

#         chat_id = message["chat"]["id"]
#         user_id = message["from"]["id"]

#         user_text = message.get("text", "") or message.get("caption", "")

#         # typing indicator (non-blocking)
#         try:
#             await bot.send_chat_action(chat_id, "typing")
#         except:
#             pass

#         # 🔥 HEAVY WORK OFF WEBHOOK THREAD
#         reply = await asyncio.to_thread(
#             generate_response,
#             user_message=user_text,
#             persona_key=str(user_id),
#             user_ip=str(user_id)
#         )

#         await send_human(bot, chat_id, reply)

#     except Exception as e:
#         logger.exception("process_update failed: %s", e)
# bot/polling_entry.py
import os
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv
from backend.groq_handler import generate_response, set_user_persona
from interaction.printer import send_human
from backend.personas import PERSONAS

load_dotenv()
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("polling_entry")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN missing")
session = AiohttpSession()
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

# Keep handler minimal — delegate to background task to avoid blocking polling loop
@dp.message()
async def handle_all(message):
    user_text = message.text or ""
    user_id = message.from_user.id
    chat_id = message.chat.id

    async def bg():
        try:
            # Persona switch CLI: "/persona gf"
            if user_text.startswith("/persona"):
                parts = user_text.split()
                if len(parts) == 2 and parts[1] in PERSONAS:
                    # storage key is the user id string
                    set_user_persona(str(user_id), parts[1])

                    await message.reply(
                        f"Persona switched to *{PERSONAS[parts[1]]['name']}* ✅",
                        parse_mode="Markdown"
                    )
                    return
                else:
                    await message.reply(
                        "Invalid persona.\nTry: " + ", ".join(PERSONAS.keys())
                    )
                    return

            # quick ack (we use send_human to deliver natural response)
            ack = await message.reply("Processing... ⏳")

            # generate response in thread
            reply = await asyncio.to_thread(
                generate_response,
                user_message=user_text,
                persona_key=str(user_id),   # IMPORTANT: pass user id (storage key)
                user_ip=str(user_id)
            )
        except Exception as e:
            reply = "Error generating response."
            logger.exception("bg generate error: %s", e)

        # remove ack and send humanized message
        try:
            from interaction.printer import send_human
            try:
                await bot.delete_message(chat_id, ack.message_id)
            except:
                pass
            await send_human(bot, chat_id, reply)
        except Exception:
            await send_human(bot, chat_id, reply)

    asyncio.create_task(bg())

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
