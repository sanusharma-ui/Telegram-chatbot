# telegram_bot/bot.py
import os
import logging
from pathlib import Path
import asyncio
import sys

# aiogram imports
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineQueryResultArticle, InputTextMessageContent, InlineQuery
)

# Make project root available for imports
sys.path.append(str(Path(__file__).parent.parent))

# Import generate_response from groq handler and PERSONAS directly from personas file
from backend.groq_handler import generate_response
from backend.personas import PERSONAS

from dotenv import load_dotenv

# Load env
load_dotenv()

# Optimized Logging - Reduce to WARNING for production speed
logging.basicConfig(
    level=logging.WARNING,  # Changed from INFO to WARNING for less overhead
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("BloodByteBot")

# ========================= CONFIG =========================
# Create a reusable aiohttp session (proxy optional - disabled by default to avoid connection errors)
PROXY_URL = os.getenv("TELEGRAM_PROXY_URL")  # Set this in .env if needed, e.g., "http://proxy.example.com:8080"
session = AiohttpSession(
    proxy=PROXY_URL if PROXY_URL else None  # Disable proxy if not set in env
    # For MTProto/SOCKS: set TELEGRAM_PROXY_URL=socks5://ip:port in .env
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN missing in environment. Exiting.")
    raise SystemExit("TELEGRAM_BOT_TOKEN missing")

# Create Bot with session
BOT = Bot(token=BOT_TOKEN, session=session)

# Dispatcher with in-memory storage (good for dev)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

UPLOAD_DIR = Path("telegram_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# ========================= STATES =========================
class States(StatesGroup):
    selecting_mode = State()

# ========================= KEYBOARDS =========================
def get_mode_keyboard():
    """
    Create keyboard rows where each button text includes both the display name and the persona key.
    Example button text: "Shadowmind | shadowmind"
    This guarantees parseable key on selection and avoids matching issues caused by emojis/parentheses.
    """
    buttons = []
    row = []
    for key, info in PERSONAS.items():
        if not isinstance(info, dict):
            continue
        name = info.get("name")
        if not name:
            continue
        # show both name and key so selection is unambiguous
        label = f"{name} | {key}"
        row.append(KeyboardButton(text=label))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([KeyboardButton(text="Cancel")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)

# ========================= HANDLERS =========================

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    # NOTE: do not clear state here to avoid wiping user's selected mode unexpectedly
    name = message.from_user.first_name or "friend"
    logger.warning(f"User {user_id} started bot")  # Log as warning to reduce noise
    await state.update_data(mode="default")  # Set default mode on start
    await message.answer(
        f"Hello {name}! I am *Aisha* — your multi-persona AI.\n\n"
        "Change mode → /mode\n"
        "Quick commands → use /<mode_key> (e.g. /shadowmind /gf)\n"
        "Check mode → /current\n"
        "Send a photo → I will analyze it.\n\n"
        "Let's begin!",
        parse_mode="Markdown"
    )

@dp.message(Command("mode"))
async def cmd_mode(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    current_key = data.get("mode", "default")
    current_info = PERSONAS.get(current_key, PERSONAS.get("default", {"name": "Default"}))
    current_name = current_info.get("name", "Default")
    logger.warning(f"User {user_id} requested /mode, current: {current_key}")  # Log as warning
    await message.answer(
        f"Current Mode: *{current_name}*\n\nSelect a new mode:",
        reply_markup=get_mode_keyboard(),
        parse_mode="Markdown"
    )
    await state.set_state(States.selecting_mode)

@dp.message(States.selecting_mode)
async def process_mode_selection(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = (message.text or "").strip()
    if not text:
        await message.answer("Empty input — please choose a mode or type the mode key.")
        return

    if text.lower() == "cancel":
        await state.clear()
        logger.warning(f"User {user_id} cancelled mode selection")  # Log as warning
        return await message.answer("Mode selection cancelled.", reply_markup=ReplyKeyboardRemove())

    selected_key = None

    # 1) If user clicked button with format "Display Name | key", parse the key
    if "|" in text:
        parts = text.split("|")
        possible_key = parts[-1].strip().lower()
        if possible_key in PERSONAS:
            selected_key = possible_key

    # 2) If not found, try direct exact match on persona name (case-insensitive)
    if selected_key is None:
        for k, v in PERSONAS.items():
            if not isinstance(v, dict):
                continue
            name = v.get("name", "")
            if name and name.strip().lower() == text.strip().lower():
                selected_key = k
                break

    # 3) If still not found, try if user typed the persona key directly
    if selected_key is None:
        if text.strip().lower() in PERSONAS:
            selected_key = text.strip().lower()

    # 4) Final fuzzy fallback: check if text is substring of any name (lowercase)
    if selected_key is None:
        t = text.strip().lower()
        for k, v in PERSONAS.items():
            if not isinstance(v, dict):
                continue
            name = v.get("name", "").lower()
            if t in name or name in t:
                selected_key = k
                break

    if not selected_key:
        logger.warning(f"User {user_id} selected invalid mode: {text}")  # Log as warning
        return await message.answer("Mode not found. Try again or use /current to check active mode.", reply_markup=get_mode_keyboard())

    # Persist selected mode and reset state without clearing data to exit selecting_mode
    await state.update_data(mode=selected_key)
    await state.set_state(None)  # Set state to None to exit selecting_mode but keep the mode data intact
    updated_data = await state.get_data()
    logger.warning(f"User {user_id} selected mode: {selected_key}, verified: {updated_data.get('mode')}")  # Log as warning
    await message.answer(
        f"Mode updated! Active mode → *{PERSONAS[selected_key].get('name', selected_key)}*",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )

# /current command to check mode
@dp.message(Command("current"))
async def cmd_current(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    current_key = data.get("mode", "default")
    current_name = PERSONAS.get(current_key, {}).get("name", current_key)
    logger.warning(f"User {user_id} checked current mode: {current_key}")  # Log as warning
    await message.answer(f"Current active mode: *{current_name}* (key: {current_key})", parse_mode="Markdown")

# Quick-switch: General command handler for unknown /commands that might be persona keys
@dp.message(lambda message: message.text and message.text.startswith('/'))
async def quick_switch_or_other(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    # Extract command (first word after /)
    cmd_parts = message.text[1:].split()
    if not cmd_parts:
        return
    cmd = cmd_parts[0].strip().lower()

    # Known non-persona commands (ignore if matches)
    known_commands = {'mode', 'reset', 'current', 'start', 'broadcast', 'help'}
    if cmd in known_commands:
        return  # Let specific handlers deal with it

    # If it's a persona key (not default)
    if cmd in PERSONAS and cmd != 'default':
        await state.update_data(mode=cmd)
        updated_data = await state.get_data()
        logger.warning(f"User {user_id} quick-switched to {cmd}, verified: {updated_data.get('mode')}")  # Log as warning
        # Extract rest as message if present
        rest = ' '.join(cmd_parts[1:]).strip()
        if rest:
            await BOT.send_chat_action(message.chat.id, "typing")
            try:
                reply = await asyncio.to_thread(
                    generate_response,
                    user_message=rest,
                    persona_key=cmd,
                    user_ip=str(user_id)
                )
                await message.answer(f"*{PERSONAS[cmd].get('name', cmd)}*:\n\n{reply}", parse_mode="Markdown")
            except Exception as e:
                logger.error(f"User {user_id} quick switch response error: {e}")
                await message.answer(f"Switched to *{PERSONAS[cmd].get('name', cmd)}*, but response failed. Send a message now.", parse_mode="Markdown")
        else:
            await message.answer(f"Switched instantly → *{PERSONAS[cmd].get('name', cmd)}*", parse_mode="Markdown")
        return

    # Unknown command, ignore or reply
    logger.warning(f"User {user_id} sent unknown command: /{cmd}")  # Log as warning

@dp.message(Command("reset"))
async def cmd_reset(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    from backend.groq_handler import save_persona_memory
    save_persona_memory("default", {"user": {"name": None, "interests": [], "notes": {}}, "conversations": []})
    await state.clear()
    logger.warning(f"User {user_id} reset memory and state")  # Log as warning
    await message.answer("All memory cleared. Fresh start!")

# ========================= IMAGE HANDLER =========================
@dp.message(lambda m: m.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    mode = data.get("mode", "default")
    logger.warning(f"User {user_id} sent photo, using mode: {mode}")  # Log as warning
    caption = message.caption or "Describe this image in detail."

    await BOT.send_chat_action(message.chat.id, "typing")
    photo = message.photo[-1]

    try:
        file = await BOT.get_file(photo.file_id)
        file_path = UPLOAD_DIR / f"{photo.file_unique_id}.jpg"
        # Use faster download with chunked reading
        await BOT.download_file(file.file_path, destination=str(file_path), chunk_size=8192)  # Increased chunk size for speed
    except Exception as e:
        logger.error(f"User {user_id} file download failed: {e}")
        return await message.answer("I couldn't download that image. Try again.")

    try:
        reply = await asyncio.to_thread(
            generate_response,
            user_message=caption,
            persona_key=mode,
            image_path=str(file_path),
            user_ip=str(user_id)
        )
        persona_name = PERSONAS.get(mode, PERSONAS.get("default", {})).get("name", mode)
        await message.answer(f"*{persona_name}*:\n\n{reply}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"User {user_id} vision/generation error: {e}")
        await message.answer("I couldn't process the image. Try again.")
    finally:
        try:
            if file_path.exists():
                file_path.unlink(missing_ok=True)
        except Exception:
            pass

# ========================= TEXT HANDLER =========================
@dp.message()
async def handle_text(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    # ignore slash commands (handled above)
    if message.text and message.text.startswith("/"):
        return

    data = await state.get_data()
    mode = data.get("mode", "default")
    logger.warning(f"User {user_id} sent text, using mode: {mode}")  # Reduced log detail for speed

    await BOT.send_chat_action(message.chat.id, "typing")

    try:
        # Use a timeout for the thread to prevent hanging
        reply = await asyncio.wait_for(
            asyncio.to_thread(
                generate_response,
                user_message=message.text or "",
                persona_key=mode,
                user_ip=str(user_id)
            ),
            timeout=30.0  # 30 second timeout to prevent infinite waits
        )
        persona_name = PERSONAS.get(mode, PERSONAS.get("default", {})).get("name", mode)
        await message.answer(f"*{persona_name}*:\n\n{reply}", parse_mode="Markdown")
    except asyncio.TimeoutError:
        logger.error(f"User {user_id} response timed out after 30s")
        await message.answer("Response taking too long. Try a shorter message or switch modes.")
    except Exception as e:
        logger.error(f"User {user_id} text generation error: {e}")
        await message.answer("Something went wrong. Try again in a moment.")

# ========================= INLINE MODE =========================
@dp.inline_query()
async def inline_handler(inline_query: InlineQuery):
    query = (inline_query.query or "").strip().lower()
    results = []

    for key, info in PERSONAS.items():
        name = info.get("name") if isinstance(info, dict) else ""
        if not query or query in key.lower() or query in name.lower():
            results.append(
                InlineQueryResultArticle(
                    id=key,
                    title=f"{name}",
                    description="Click to switch to this mode",
                    input_message_content=InputTextMessageContent(message_text=f"/{key}")
                )
            )

    if not results:
        results.append(
            InlineQueryResultArticle(
                id="help",
                title="Type mode name like: shadowmind, raven_girl, etc.",
                input_message_content=InputTextMessageContent("/mode")
            )
        )

    await inline_query.answer(results, cache_time=1)

# ========================= ADMIN BROADCAST =========================
@dp.message(Command("broadcast"))
async def broadcast(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    text = (message.text or "")[10:].strip()
    if not text:
        return await message.answer("Write a message to broadcast.")
    # TODO: implement broadcast list
    await message.answer("Broadcast system ready (add user list logic).")

# ========================= MAIN =========================
async def main():
    logger.warning("BloodByte Bot Started — Multi-Persona | Vision | Inline | Fixed Modes")  # Log as warning
    # start polling
    await dp.start_polling(BOT)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.warning("Shutting down.")  # Log as warning

# ------------------ Render Free Hack (Keep Port Alive) ------------------
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import os

def keep_alive():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("", port), Handler)
    server.serve_forever()

threading.Thread(target=keep_alive, daemon=True).start()

# -----------------------------------------------------------------------