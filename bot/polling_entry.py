# bot/polling_entry.py
import os
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv
from backend.groq_handler import generate_response
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
            if user_text.startswith("/persona"):
                parts = user_text.split()
                if len(parts) == 2 and parts[1] in PERSONAS:
                    from backend.groq_handler import set_user_persona
                    set_user_persona(str(user_id), parts[1])

                    await message.reply(
                        f"Persona switched to *{PERSONAS[parts[1]]['name']}* 🔥",
                        parse_mode="Markdown"
                    )
                    return
                else:
                    await message.reply(
                        "Invalid persona.\nTry: " + ", ".join(PERSONAS.keys())
                    )
                    return
            ack = await message.reply("Processing... ⏳")
            reply = await asyncio.to_thread(generate_response,
                user_message=user_text,
                persona_key=str(user_id),
                user_ip=str(user_id)
            )
        except Exception as e:
            reply = "Error generating response."
            logger.exception("bg generate error: %s", e)
        # edit ack
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