# # interaction/printer.py
# import asyncio
# import re
# import random
# async def send_human(bot, chat_id: int, text: str):
#     import random, asyncio

#     if not text:
#         return

#     chunks = re.findall(r'.{1,120}(?:[.!?]|$)', text)

#     # thinking delay
#     await bot.send_chat_action(chat_id, "typing")
#     await asyncio.sleep(random.uniform(0.6, 1.2))

#     for chunk in chunks:
#         await bot.send_chat_action(chat_id, "typing")
#         await asyncio.sleep(0.2 + len(chunk) * 0.015)

#         try:
#             await bot.send_message(chat_id, chunk.strip())
#         except:
#             pass

#         await asyncio.sleep(random.uniform(0.2, 0.5))

import asyncio
import logging
from typing import List

from aiogram import Bot
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramBadRequest,
)

logger = logging.getLogger(__name__)

TELEGRAM_TEXT_LIMIT = 4096


def _chunk_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> List[str]:
    text = text or ""
    if len(text) <= limit:
        return [text]

    chunks = []
    current = ""

    for line in text.splitlines(keepends=True):
        if len(current) + len(line) <= limit:
            current += line
        else:
            if current:
                chunks.append(current)
            if len(line) <= limit:
                current = line
            else:
                start = 0
                while start < len(line):
                    chunks.append(line[start:start + limit])
                    start += limit
                current = ""

    if current:
        chunks.append(current)

    return chunks or [""]


async def _safe_chat_action(bot: Bot, chat_id: int, action: str = "typing") -> None:
    try:
        await bot.send_chat_action(chat_id, action)
    except (TelegramNetworkError, TelegramServerError, TelegramBadRequest) as e:
        logger.warning("send_chat_action skipped: %s", e)
    except Exception as e:
        logger.warning("Unexpected send_chat_action error: %s", e)


async def _safe_send_message(bot: Bot, chat_id: int, text: str, retries: int = 2) -> bool:
    last_error = None

    for attempt in range(retries + 1):
        try:
            await bot.send_message(chat_id, text)
            return True

        except TelegramRetryAfter as e:
            wait_for = getattr(e, "retry_after", 1) or 1
            logger.warning("TelegramRetryAfter: waiting %s sec", wait_for)
            await asyncio.sleep(wait_for)

        except (TelegramNetworkError, TelegramServerError) as e:
            last_error = e
            logger.warning("Telegram send_message network/server error on attempt %s: %s", attempt + 1, e)
            await asyncio.sleep(1.5 * (attempt + 1))

        except TelegramBadRequest as e:
            logger.exception("TelegramBadRequest while sending message: %s", e)
            return False

        except Exception as e:
            last_error = e
            logger.exception("Unexpected Telegram send_message error: %s", e)
            await asyncio.sleep(1.5 * (attempt + 1))

    logger.error("send_message failed after retries: %s", last_error)
    return False


async def send_human(bot: Bot, chat_id: int, text: str, do_typing: bool = True) -> bool:
    """
    Robust Telegram sender.
    - typing indicator is best-effort only
    - message sending retries on transient Telegram network/server failures
    - long messages are chunked safely
    """
    if do_typing:
        await _safe_chat_action(bot, chat_id, "typing")

    chunks = _chunk_text(text)

    overall_ok = True
    for chunk in chunks:
        ok = await _safe_send_message(bot, chat_id, chunk)
        if not ok:
            overall_ok = False

    return overall_ok