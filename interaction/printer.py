# interaction/printer.py
import asyncio
import re
import random
async def send_human(bot, chat_id: int, text: str):
    import random, asyncio

    if not text:
        return

    chunks = re.findall(r'.{1,120}(?:[.!?]|$)', text)

    # thinking delay
    await bot.send_chat_action(chat_id, "typing")
    await asyncio.sleep(random.uniform(0.6, 1.2))

    for chunk in chunks:
        await bot.send_chat_action(chat_id, "typing")
        await asyncio.sleep(0.2 + len(chunk) * 0.015)

        try:
            await bot.send_message(chat_id, chunk.strip())
        except:
            pass

        await asyncio.sleep(random.uniform(0.2, 0.5))