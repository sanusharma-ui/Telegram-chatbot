# interaction/printer.py
import asyncio
import re
import random

_SENTENCE_RE = re.compile(r'(.{30,200}?[\.\!\?]|[\n]+|.{1,200})', re.S)

async def send_human(bot, chat_id: int, text: str, typing_base: float = 0.5):
    """
    Send text in human-like chunks. Use bot.send_chat_action and delays.
    """
    if not text:
        return
    # sanitize whitespace
    text = text.strip()
    # split into chunks
    chunks = _SENTENCE_RE.findall(text)
    if not chunks:
        chunks = [text]
    # start typing indicator
    try:
        await bot.send_chat_action(chat_id, "typing")
    except Exception:
        pass
    for chunk in chunks:
        # small random thinking pause
        await asyncio.sleep(random.uniform(0.2, typing_base))
        try:
            await bot.send_message(chat_id, chunk.strip())
        except Exception:
            # last-resort: direct edit not implemented here
            try:
                await bot.send_message(chat_id, chunk.strip())
            except Exception:
                pass
