
# # polling_entry.py
# import os
# import asyncio
# import logging
# from aiogram import Bot, Dispatcher
# from aiogram.client.session.aiohttp import AiohttpSession
# from aiogram.types import (
#     Message,
#     InlineKeyboardMarkup,
#     InlineKeyboardButton,
#     CallbackQuery
# )
# from dotenv import load_dotenv

# from backend.groq_handler import generate_response, set_user_persona
# from interaction.printer import send_human
# from backend.personas import PERSONAS
# from backend.gmail_integration import (
#     get_auth_url_for_user,
#     gmail_summary,
#     gmail_smart_summary,  # smart inbox hook
#     create_draft,
#     send_message_from_draft,
#     disconnect_user
# )

# load_dotenv()

# logging.basicConfig(level=logging.WARNING)
# logger = logging.getLogger("polling_entry")

# BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# if not BOT_TOKEN:
#     raise SystemExit("BOT_TOKEN missing")

# # Create a single shared aiohttp session and the Bot that uses it
# session = AiohttpSession()
# bot = Bot(token=BOT_TOKEN, session=session)
# dp = Dispatcher()

# # ────────────────────────────────────────────────
# #           PERSONA KEYBOARD HELPER
# # ────────────────────────────────────────────────
# PERSONA_COLUMNS = 3
# TELEGRAM_BUTTON_LIMIT = 100  # Telegram absolute limit for inline keyboard buttons
# SAFETY_BUTTON_LIMIT = 90     # keep UX safe; trim if more

# def build_persona_keyboard() -> InlineKeyboardMarkup:
#     buttons = []
#     row = []

#     # convert to list so we can slice if it's huge
#     persona_items = list(PERSONAS.items())
#     total_buttons = len(persona_items)

#     if total_buttons > SAFETY_BUTTON_LIMIT:
#         logger.warning(
#             "Too many personas (%d) — trimming keyboard to %d buttons.",
#             total_buttons,
#             SAFETY_BUTTON_LIMIT
#         )
#         persona_items = persona_items[:SAFETY_BUTTON_LIMIT - 1]  # leave room for a "More..." button

#     for key, data in persona_items:
#         # ensure valid shape (defensive)
#         name = data.get("name") if isinstance(data, dict) else None
#         if not name:
#             # skip malformed persona entries
#             continue

#         row.append(
#             InlineKeyboardButton(
#                 text=name,
#                 callback_data=f"persona:{key}"
#             )
#         )
#         if len(row) == PERSONA_COLUMNS:
#             buttons.append(row)
#             row = []
#     if row:
#         buttons.append(row)

#     # if we trimmed earlier, add a "More..." button (fallback/paging placeholder)
#     if total_buttons > SAFETY_BUTTON_LIMIT:
#         buttons.append([InlineKeyboardButton(text="More...", callback_data="persona:more")])

#     return InlineKeyboardMarkup(inline_keyboard=buttons)


# # ────────────────────────────────────────────────
# #                  MESSAGE HANDLER
# # ────────────────────────────────────────────────
# @dp.message()
# async def handle_all(message: Message):
#     user_text_raw = message.text or ""
#     user_text = user_text_raw.strip()
#     if not user_text:
#         return

#     user_id = str(message.from_user.id)
#     chat_id = message.chat.id

#     # Normalize first token (handles /command@BotName)
#     tokens = user_text.split()
#     first_token = tokens[0] if tokens else ""
#     base_cmd = first_token.split("@", 1)[0] if first_token else ""

#     # ── START COMMAND (explicit, before other handlers) ───────────────────────────────
#     if base_cmd == "/start":
#         await message.reply(
#             "👋 Bot ready.\n\n"
#             "Commands:\n"
#             "/persona – choose persona\n"
#             "/gmail – gmail tools\n\n"
#             "Type anything to chat."
#         )
#         return

#     # ── PERSONA selection ───────────────────────────────────────────────
#     # Accept: "/persona", "/persona@BotName", "/persona NAME", "/persona@BotName NAME"
#     if base_cmd == "/persona":
#         # tokens: first token may be "/persona" or "/persona@BotName"
#         # if only "/persona"
#         if len(tokens) == 1:
#             kb = build_persona_keyboard()
#             # send plain text (avoid parse_mode + reply_markup issues)
#             await message.reply("🎭 Choose your persona:", reply_markup=kb)
#             return

#         # if "/persona NAME"
#         if len(tokens) >= 2:
#             persona = tokens[1].strip()
#             if persona in PERSONAS:
#                 set_user_persona(user_id, persona)
#                 # markdown for single-line reply is fine here (not with keyboard)
#                 await message.reply(f"✅ Switched to *{PERSONAS[persona]['name']}* 🔥", parse_mode="Markdown")
#                 return
#             else:
#                 await message.reply("❌ Unknown persona. Use /persona to open the selector or /persona <name> to switch.")
#                 return

#     # ── GMAIL / HELP commands (robust to @BotName) ───────────────────────────────
#     if base_cmd in ("/gmail", "/help"):
#         # interpret tokens after the command
#         # tokens[0] = '/gmail' or '/gmail@Bot', tokens[1] = subcommand (if exists)
#         cmd = tokens[1].lower() if len(tokens) > 1 else ""
#         subcmd = tokens[2].lower() if len(tokens) > 2 else ""

#         # connect
#         if cmd == "connect":
#             try:
#                 url = get_auth_url_for_user(user_id, need_send=True)
#                 kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔐 Connect Gmail", url=url)]])
#                 await bot.send_message(chat_id, "Click below to securely connect Gmail:", reply_markup=kb, disable_web_page_preview=True)
#             except Exception as e:
#                 logger.exception("gmail connect error: %s", e)
#                 await message.reply("❌ Failed to build OAuth link.")
#             return

#         # inbox (plain)
#         if cmd == "inbox" and subcmd == "":
#             try:
#                 summary = gmail_summary(user_id)
#                 await message.reply(summary or "No recent emails or not connected.")
#             except Exception as e:
#                 logger.exception("gmail inbox error: %s", e)
#                 await message.reply("❌ Error fetching inbox.")
#             return

#         # inbox smart (subcommand)
#         if cmd == "inbox" and subcmd == "smart":
#             try:
#                 summary = gmail_smart_summary(user_id)
#                 await message.reply(summary or "No recent emails or not connected.")
#             except Exception as e:
#                 logger.exception("gmail smart inbox error: %s", e)
#                 await message.reply("❌ Error fetching smart inbox.")
#             return

#         # disconnect
#         if cmd == "disconnect":
#             try:
#                 disconnect_user(user_id)
#                 await message.reply("✅ Gmail disconnected.")
#             except Exception as e:
#                 logger.exception("gmail disconnect error: %s", e)
#                 await message.reply("❌ Error disconnecting.")
#             return

#         # send <draft_id>
#         if cmd == "send":
#             draft_id = tokens[2].strip() if len(tokens) > 2 else ""
#             if not draft_id:
#                 await message.reply("Usage: /gmail send <draft_id>")
#                 return
#             try:
#                 ok = send_message_from_draft(user_id, draft_id)
#                 await message.reply("✅ Sent!" if ok else "❌ Failed to send.")
#             except Exception as e:
#                 logger.exception("gmail send error: %s", e)
#                 await message.reply("❌ Error sending draft.")
#             return

#         # draft <to> | <subject> | <instructions>
#         if cmd == "draft":
#             # rebuild payload preserving separators (we expect everything after "draft")
#             # use partition to preserve content where 'draft' appears (safer than split)
#             payload = user_text.partition("draft")[2].strip()
#             if not payload:
#                 await message.reply("Usage: /gmail draft <to> | <subject> | <instructions>")
#                 return

#             try:
#                 parts_payload = [p.strip() for p in payload.split("|")]
#                 if len(parts_payload) < 1 or "@" not in parts_payload[0]:
#                     await message.reply("Please provide valid recipient email first.")
#                     return

#                 to_addr = parts_payload[0]
#                 subject = parts_payload[1] if len(parts_payload) > 1 else "(no subject)"
#                 instructions = parts_payload[2] if len(parts_payload) > 2 else "Short professional email please."

#                 ai_prompt = (
#                     f"GENERATE_EMAIL_BODY_ONLY:\n"
#                     f"Recipient: {to_addr}\nSubject: {subject}\n"
#                     f"Context/Instruction: {instructions}\n\n"
#                     "Output ONLY the email body text. No emojis, no signature."
#                 )

#                 email_body = await asyncio.to_thread(
#                     generate_response,
#                     user_message=ai_prompt,
#                     persona_key=user_id,
#                     user_ip=user_id
#                 )

#                 email_body = (email_body or "").strip().replace("😎", "").replace("☕", "").strip()

#                 if not email_body:
#                     await message.reply("❌ Couldn't generate email body. Try different instructions.")
#                     return

#                 created = create_draft(user_id, to_addr, subject, email_body)
#                 if created and isinstance(created, dict) and created.get("id"):
#                     draft_id = created["id"]
#                     preview = email_body[:800] + ("..." if len(email_body) > 800 else "")
#                     reply_text = (
#                         "✅ **Draft created!**\n\n"
#                         f"To: {to_addr}\n"
#                         f"Subject: {subject}\n\n"
#                         f"```\n{preview}\n```\n\n"
#                         f"→ Use `/gmail send {draft_id}` to send\n"
#                         f"→ `/gmail disconnect` to revoke access"
#                     )
#                     await message.reply(reply_text, parse_mode="Markdown")
#                 else:
#                     await message.reply("❌ Failed to create draft.")
#             except Exception as e:
#                 logger.exception("gmail draft error: %s", e)
#                 await message.reply("❌ Error creating draft.")
#             return

#         # help / default gmail message
#         await message.reply(
#             "📧 *Gmail commands:*\n\n"
#             "/gmail connect       → link your Gmail\n"
#             "/gmail inbox         → show recent emails\n"
#             "/gmail inbox smart   → AI summarized inbox\n"
#             "/gmail draft to@... | Subject | instructions\n"
#             "/gmail send <draft_id>\n"
#             "/gmail disconnect    → revoke access",
#             parse_mode="Markdown"
#         )
#         return

#     # ── Normal conversation ─────────────────────────────
#     async def bg_task():
#         try:
#             # Better UX: use Telegram "typing" action instead of a temporary message
#             try:
#                 await bot.send_chat_action(chat_id, "typing")
#             except Exception:
#                 # non-fatal; continue
#                 pass

#             reply_text = await asyncio.to_thread(
#                 generate_response,
#                 user_message=user_text,
#                 persona_key=user_id,
#                 user_ip=user_id
#             )
#             await send_human(bot, chat_id, reply_text)
#         except Exception as e:
#             logger.exception("background generate failed", exc_info=True)
#             await send_human(bot, chat_id, "Sorry, something broke on my side... 😔")

#     # schedule background generation (keeps handler fast)
#     asyncio.create_task(bg_task())


# # ────────────────────────────────────────────────
# #             PERSONA CALLBACK HANDLER
# # ────────────────────────────────────────────────
# @dp.callback_query(lambda c: c.data.startswith("persona:"))
# async def persona_callback(callback: CallbackQuery):
#     try:
#         persona_key = callback.data.split(":", 1)[1]
#         user_id = str(callback.from_user.id)

#         # simple paging fallback
#         if persona_key == "more":
#             await callback.answer()
#             await callback.message.reply("There are many personas — I'll add paging/filters soon. Use /persona <name> for now.")
#             return

#         if persona_key in PERSONAS:
#             set_user_persona(user_id, persona_key)
#             # safer edit_text with fallback
#             try:
#                 await callback.message.edit_text(
#                     f"✅ Now talking with *{PERSONAS[persona_key]['name']}* 🔥",
#                     parse_mode="Markdown"
#                 )
#             except Exception:
#                 try:
#                     await callback.message.reply(
#                         f"✅ Now talking with *{PERSONAS[persona_key]['name']}* 🔥",
#                         parse_mode="Markdown"
#                     )
#                 except Exception:
#                     # last resort: log and ignore
#                     logger.exception("Couldn't notify user about persona change.")
#         else:
#             try:
#                 await callback.message.edit_text("❌ Invalid persona selected.")
#             except Exception:
#                 await callback.message.reply("❌ Invalid persona selected.")

#         await callback.answer()
#     except Exception as e:
#         logger.exception("persona callback error", exc_info=True)
#         try:
#             await callback.answer("Something went wrong...", show_alert=True)
#         except Exception:
#             pass


# # ────────────────────────────────────────────────
# #                      MAIN
# # ────────────────────────────────────────────────
# async def main():
#     print("🤖 Bot is starting (polling mode)...")
#     try:
#         # allowed_updates explicit for safety
#         await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
#     finally:
#         # ensure the aiohttp session is closed to avoid leaks
#         try:
#             # prefer closing bot.session (the same AiohttpSession instance)
#             if getattr(bot, "session", None):
#                 await bot.session.close()
#         except Exception:
#             logger.exception("Error closing bot.session; trying session.close() directly")

#         try:
#             if session:
#                 await session.close()
#         except Exception:
#             logger.exception("Error closing global aiohttp session")


# if __name__ == "__main__":
#     asyncio.run(main())
# polling_entry.py  (changes in /gmail draft handler for validation)

import os
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from aiogram.filters import Command
from dotenv import load_dotenv

from backend.groq_handler import generate_response, set_user_persona
from interaction.printer import send_human
from backend.personas import PERSONAS
from backend.gmail_integration import (
    get_auth_url_for_user,
    gmail_summary,
    gmail_smart_summary,
    create_draft,
    send_message_from_draft,
    disconnect_user
)

load_dotenv()

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("polling_entry")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN missing")

session = AiohttpSession()
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

# ────────────────────────────────────────────────
#           PERSONA KEYBOARD HELPER
# ────────────────────────────────────────────────
PERSONA_COLUMNS = 3
TELEGRAM_BUTTON_LIMIT = 100
SAFETY_BUTTON_LIMIT = 90

def build_persona_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    row = []

    persona_items = list(PERSONAS.items())
    total_buttons = len(persona_items)

    if total_buttons > SAFETY_BUTTON_LIMIT:
        logger.warning(
            "Too many personas (%d) — trimming to %d buttons.",
            total_buttons, SAFETY_BUTTON_LIMIT
        )
        persona_items = persona_items[:SAFETY_BUTTON_LIMIT - 1]

    for key, data in persona_items:
        name = data.get("name") if isinstance(data, dict) else None
        if not name:
            continue

        row.append(
            InlineKeyboardButton(
                text=name,
                callback_data=f"persona:{key}"
            )
        )
        if len(row) == PERSONA_COLUMNS:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if total_buttons > SAFETY_BUTTON_LIMIT:
        buttons.append([InlineKeyboardButton(text="More...", callback_data="persona:more")])

    # Debug print
    print(f"[DEBUG] Personas loaded: {len(persona_items)}")
    print(f"[DEBUG] Buttons rows: {len(buttons)}")
    if buttons:
        print(f"[DEBUG] First row example: {buttons[0]}")

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ────────────────────────────────────────────────
#                  START COMMAND
# ────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.reply(
        "👋 Bot ready.\n\n"
        "Commands:\n"
        "/persona – choose persona\n"
        "/gmail – gmail tools\n\n"
        "Type anything to chat."
    )


# ────────────────────────────────────────────────
#                  MAIN MESSAGE HANDLER
# ────────────────────────────────────────────────
@dp.message()
async def handle_all(message: Message):
    user_text_raw = message.text or ""
    user_text = user_text_raw.strip()
    if not user_text:
        return

    user_id = str(message.from_user.id)
    chat_id = message.chat.id

    # Normalize first token (handles /command@BotName)
    tokens = user_text.split()
    first_token = tokens[0] if tokens else ""
    base_cmd = first_token.split("@", 1)[0] if first_token else ""

    # ── PERSONA selection ───────────────────────────────────────────────
    if base_cmd == "/persona":
        if len(tokens) == 1:
            kb = build_persona_keyboard()
            if not kb.inline_keyboard:
                await message.reply("No personas available right now 😔")
                return
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text="🎭 Choose your persona:",
                    reply_markup=kb
                )
            except Exception as e:
                logger.exception("Persona keyboard failed: %s", e)
                await message.reply("❌ Could not show persona list right now. Try /persona <name> directly.")
            return

        if len(tokens) >= 2:
            persona = tokens[1].strip()
            if persona in PERSONAS:
                set_user_persona(user_id, persona)
                await message.reply(f"✅ Switched to *{PERSONAS[persona]['name']}* 🔥", parse_mode="Markdown")
                return
            else:
                await message.reply("❌ Unknown persona. Use /persona to open the selector or /persona <name> to switch.")
                return

    # ── GMAIL / HELP commands ───────────────────────────────
    if base_cmd in ("/gmail", "/help"):
        cmd = tokens[1].lower() if len(tokens) > 1 else ""
        subcmd = tokens[2].lower() if len(tokens) > 2 else ""

        if cmd == "connect":
            try:
                url = get_auth_url_for_user(user_id, need_send=True)
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔐 Connect Gmail", url=url)]])
                await bot.send_message(chat_id, "Click below to securely connect Gmail:", reply_markup=kb, disable_web_page_preview=True)
            except Exception as e:
                logger.exception("gmail connect error: %s", e)
                await message.reply("❌ Failed to build OAuth link.")
            return

        if cmd == "inbox" and subcmd == "":
            try:
                summary = gmail_summary(user_id)
                await message.reply(summary or "No recent emails or not connected.")
            except Exception as e:
                logger.exception("gmail inbox error: %s", e)
                await message.reply("❌ Error fetching inbox.")
            return

        if cmd == "inbox" and subcmd == "smart":
            try:
                summary = gmail_smart_summary(user_id)
                await message.reply(summary or "No recent emails or not connected.")
            except Exception as e:
                logger.exception("gmail smart inbox error: %s", e)
                await message.reply("❌ Error fetching smart inbox.")
            return

        if cmd == "disconnect":
            try:
                disconnect_user(user_id)
                await message.reply("✅ Gmail disconnected.")
            except Exception as e:
                logger.exception("gmail disconnect error: %s", e)
                await message.reply("❌ Error disconnecting.")
            return

        if cmd == "send":
            draft_id = tokens[2].strip() if len(tokens) > 2 else ""
            if not draft_id:
                await message.reply("Usage: /gmail send <draft_id>")
                return
            try:
                ok = send_message_from_draft(user_id, draft_id)
                await message.reply("✅ Sent!" if ok else "❌ Failed to send.")
            except Exception as e:
                logger.exception("gmail send error: %s", e)
                await message.reply("❌ Error sending draft.")
            return

        # ── Draft command with fixes ───────────────────────────────
        if cmd == "draft":
            payload = user_text.partition("draft")[2].strip()
            if not payload:
                await message.reply("Usage: /gmail draft <to> | <subject> | <instructions>\nExample: /gmail draft friend@gmail.com | Meeting Reminder | Professional reminder for tomorrow's call")
                return

            try:
                parts = [p.strip() for p in payload.split("|")]
                if len(parts) < 1:
                    await message.reply("❌ At least provide recipient email.")
                    return

                to_addr = parts[0].strip()
                
                # NEW VALIDATION
                import re
                if not re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', to_addr):
                    await message.reply(f"❌ Invalid email address: '{to_addr}'\nPlease use a valid format like name@example.com")
                    return

                subject = parts[1].strip() if len(parts) > 1 else "(no subject)"
                instructions = parts[2].strip() if len(parts) > 2 else "Short professional email please."

                ai_prompt = (
                    f"GENERATE_EMAIL_BODY_ONLY:\n"
                    f"Recipient: {to_addr}\nSubject: {subject}\n"
                    f"Context/Instruction: {instructions}\n\n"
                    "Output ONLY the email body text. No emojis, no signature."
                )

                email_body = await asyncio.to_thread(
                    generate_response,
                    user_message=ai_prompt,
                    persona_key=user_id,
                    user_ip=user_id
                )

                email_body = (email_body or "").strip().replace("😎", "").replace("☕", "").strip()

                if not email_body:
                    await message.reply("❌ Couldn't generate email body. Try different instructions.")
                    return

                created = create_draft(user_id, to_addr, subject, email_body)
                if created and isinstance(created, dict) and created.get("id"):
                    draft_id = created["id"]
                    preview = email_body[:800] + ("..." if len(email_body) > 800 else "")
                    reply_text = (
                        "✅ **Draft created!**\n\n"
                        f"To: {to_addr}\n"
                        f"Subject: {subject}\n\n"
                        f"```\n{preview}\n```\n\n"
                        f"→ Use `/gmail send {draft_id}` to send\n"
                        f"→ `/gmail disconnect` to revoke access"
                    )
                    await message.reply(reply_text, parse_mode="Markdown")
                else:
                    await message.reply("❌ Failed to create draft. Check if Gmail is connected properly.")
            except Exception as e:
                logger.exception("gmail draft error: %s", e)
                await message.reply(f"❌ Error creating draft: {str(e)}")
            return

        # help / default
        await message.reply(
            "📧 *Gmail commands:*\n\n"
            "/gmail connect       → link your Gmail\n"
            "/gmail inbox         → show recent emails\n"
            "/gmail inbox smart   → AI summarized inbox\n"
            "/gmail draft to@... | Subject | instructions\n"
            "/gmail send <draft_id>\n"
            "/gmail disconnect    → revoke access",
            parse_mode="Markdown"
        )
        return

    # ── Normal conversation ─────────────────────────────
    async def bg_task():
        try:
            await bot.send_chat_action(chat_id, "typing")
            reply_text = await asyncio.to_thread(
                generate_response,
                user_message=user_text,
                persona_key=user_id,
                user_ip=user_id
            )
            await send_human(bot, chat_id, reply_text)
        except Exception as e:
            logger.exception("background generate failed", exc_info=True)
            await send_human(bot, chat_id, "Sorry, something broke on my side... 😔")

    asyncio.create_task(bg_task())


# ────────────────────────────────────────────────
#             PERSONA CALLBACK HANDLER
# ────────────────────────────────────────────────
@dp.callback_query(lambda c: c.data.startswith("persona:"))
async def persona_callback(callback: CallbackQuery):
    try:
        persona_key = callback.data.split(":", 1)[1]
        user_id = str(callback.from_user.id)

        if persona_key == "more":
            await callback.answer()
            await callback.message.reply("There are many personas — I'll add paging/filters soon. Use /persona <name> for now.")
            return

        if persona_key in PERSONAS:
            set_user_persona(user_id, persona_key)
            try:
                await callback.message.edit_text(
                    f"✅ Now talking with *{PERSONAS[persona_key]['name']}* 🔥",
                    parse_mode="Markdown"
                )
            except Exception:
                await callback.message.reply(
                    f"✅ Now talking with *{PERSONAS[persona_key]['name']}* 🔥",
                    parse_mode="Markdown"
                )
        else:
            try:
                await callback.message.edit_text("❌ Invalid persona selected.")
            except Exception:
                await callback.message.reply("❌ Invalid persona selected.")

        await callback.answer()
    except Exception as e:
        logger.exception("persona callback error", exc_info=True)
        try:
            await callback.answer("Something went wrong...", show_alert=True)
        except Exception:
            pass


# ────────────────────────────────────────────────
#                      MAIN
# ────────────────────────────────────────────────
async def main():
    print("🤖 Bot is starting (polling mode)...")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        try:
            if getattr(bot, "session", None):
                await bot.session.close()
        except Exception:
            logger.exception("Error closing bot.session")

        try:
            if session:
                await session.close()
        except Exception:
            logger.exception("Error closing global aiohttp session")


if __name__ == "__main__":
    asyncio.run(main())