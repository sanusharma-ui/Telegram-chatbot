# # polling_entry.py
# import os
# import asyncio
# import logging
# from aiogram import Bot, Dispatcher
# from aiogram.client.session.aiohttp import AiohttpSession
# from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
# from dotenv import load_dotenv
# from backend.groq_handler import generate_response
# from interaction.printer import send_human
# from backend.personas import PERSONAS

# load_dotenv()
# logging.basicConfig(level=logging.WARNING)
# logger = logging.getLogger("polling_entry")
# BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# if not BOT_TOKEN:
#     raise SystemExit("BOT_TOKEN missing")
# session = AiohttpSession()
# bot = Bot(token=BOT_TOKEN, session=session)
# dp = Dispatcher()

# @dp.message()
# async def handle_all(message):
#     user_text = (message.text or "").strip()
#     user_id = str(message.from_user.id)
#     chat_id = message.chat.id

#     if user_text.startswith("/persona"):
#         parts = user_text.split(maxsplit=1)
#         if len(parts) == 2:
#             persona = parts[1].strip()
#             from backend.personas import PERSONAS
#             if persona in PERSONAS:
#                 from backend.groq_handler import set_user_persona
#                 set_user_persona(user_id, persona)
#                 await message.reply(
#                     f"✅ Persona switched to *{PERSONAS[persona]['name']}* 🔥",
#                     parse_mode="Markdown"
#                 )
#                 return
#         await message.reply("Usage: /persona <name>\nAvailable: " + ", ".join(PERSONAS.keys()))
#         return

#     if user_text.startswith("/gmail") or user_text.startswith("/help") or user_text.startswith("/start"):
#         from backend.gmail_integration import (
#             get_auth_url_for_user,
#             gmail_summary,
#             create_draft,
#             send_message_from_draft,
#             disconnect_user
#         )

#         parts = user_text.split(maxsplit=1)
#         cmd = parts[1].strip() if len(parts) > 1 else ""

#         if cmd == "connect":
#             try:
#                 url = get_auth_url_for_user(user_id, need_send=True)
#                 kb = InlineKeyboardMarkup(inline_keyboard=[
#                     [InlineKeyboardButton(text="🔐 Connect Gmail", url=url)]
#                 ])
#                 await bot.send_message(chat_id, "Click below to securely connect Gmail:", reply_markup=kb, disable_web_page_preview=True)
#             except Exception as e:
#                 logger.exception("gmail connect error: %s", e)
#                 await message.reply("❌ Failed to build OAuth link. Check server logs.")
#             return

#         if cmd == "inbox":
#             try:
#                 summary = gmail_summary(user_id)
#                 await message.reply(summary or "No recent emails or Gmail not connected.")
#             except Exception as e:
#                 logger.exception("gmail inbox error: %s", e)
#                 await message.reply("❌ Error fetching inbox. Check logs.")
#             return

#         if cmd == "disconnect":
#             try:
#                 disconnect_user(user_id)
#                 await message.reply("✅ Gmail disconnected safely.")
#             except Exception as e:
#                 logger.exception("gmail disconnect error: %s", e)
#                 await message.reply("❌ Error disconnecting. Check logs.")
#             return

#         if cmd.startswith("send"):
#             sub = cmd.split(maxsplit=1)
#             if len(sub) < 2:
#                 await message.reply("Usage: /gmail send <draft_id>")
#                 return
#             draft_id = sub[1].strip()
#             try:
#                 ok = send_message_from_draft(user_id, draft_id)
#                 await message.reply("✅ Mail sent." if ok else "❌ Failed to send draft. Check logs.")
#             except Exception as e:
#                 logger.exception("gmail send error: %s", e)
#                 await message.reply("❌ Error sending draft. Check logs.")
#             return

#         if cmd.startswith("draft"):
#             payload = cmd[len("draft"):].strip()
#             if not payload:
#                 await message.reply("Usage: /gmail draft <to> | <subject> | <instructions>")
#                 return
#             try:
#                 parts = [p.strip() for p in payload.split("|")]
#                 to_addr = parts[0] if len(parts) >= 1 else None
#                 subject = parts[1] if len(parts) >= 2 else "(no subject)"
#                 instructions = parts[2] if len(parts) >= 3 else "Please draft a short professional email."
#                 if not to_addr or "@" not in to_addr:
#                     await message.reply("Please provide a valid recipient email (e.g. hr@company.com).")
#                     return

#                 ai_prompt = (
#                     f"GENERATE_EMAIL_BODY_ONLY:\n"
#                     f"Recipient: {to_addr}\nSubject: {subject}\nContext/Instruction: {instructions}\n\n"
#                     "Output: Provide only the email body as plain text. Do not include emojis, greetings beyond a one-line salutation if needed, or any signature."
#                 )

#                 email_body = await asyncio.to_thread(
#                     generate_response,
#                     user_message=ai_prompt,
#                     persona_key=user_id,
#                     user_ip=user_id
#                 )

#                 email_body = (email_body or "").strip()
#                 email_body = email_body.replace("😎", "").replace("☕", "").strip()
#                 if not email_body:
#                     await message.reply("❌ AI failed to generate the email body. Try rewording the instructions.")
#                     return

#                 created = create_draft(user_id, to_addr, subject, email_body)
#                 if created and isinstance(created, dict) and created.get("id"):
#                     draft_id = created.get("id")
#                     reply_text = (
#                         "✅ Draft created.\n\n"
#                         f"To: {to_addr}\nSubject: {subject}\n\n"
#                         f"---\n{email_body[:1000]}\n---\n\n"
#                         f"Use `/gmail send {draft_id}` to send this draft, or `/gmail disconnect` to revoke access."
#                     )
#                     await message.reply(reply_text)
#                 else:
#                     await message.reply("❌ Failed to create draft. Check server logs.")
#             except Exception as e:
#                 logger.exception("gmail draft error: %s", e)
#                 await message.reply("❌ Error while creating draft. Check logs.")
#             return

#         await message.reply(
#             "Gmail commands:\n"
#             "/gmail connect\n"
#             "/gmail inbox\n"
#             "/gmail draft to@example.com | Subject | Instructions\n"
#             "/gmail send <draft_id>\n"
#             "/gmail disconnect"
#         )
#         return

#     async def bg():
#         try:
#             ack = await message.reply("Processing... ⏳")
#             reply = await asyncio.to_thread(generate_response,
#                 user_message=user_text,
#                 persona_key=user_id,
#                 user_ip=user_id
#             )
#         except Exception as e:
#             reply = "Error generating response."
#             logger.exception("bg generate error: %s", e)
#         try:
#             try:
#                 await bot.delete_message(chat_id, ack.message_id)
#             except:
#                 pass
#             await send_human(bot, chat_id, reply)
#         except Exception:
#             await send_human(bot, chat_id, reply)

#     asyncio.create_task(bg())

# async def main():
#     await dp.start_polling(bot)

# if __name__ == "__main__":
#     asyncio.run(main())


# polling_entry.py
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
from dotenv import load_dotenv

from backend.groq_handler import generate_response, set_user_persona
from interaction.printer import send_human
from backend.personas import PERSONAS
from backend.gmail_integration import (
    get_auth_url_for_user,
    gmail_summary,
    gmail_smart_summary,  # <-- new: smart inbox hook
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

# Create a single shared aiohttp session and the Bot that uses it
session = AiohttpSession()
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

# ────────────────────────────────────────────────
#           PERSONA KEYBOARD HELPER
# ────────────────────────────────────────────────
PERSONA_COLUMNS = 3
TELEGRAM_BUTTON_LIMIT = 100  # Telegram absolute limit for inline keyboard buttons
SAFETY_BUTTON_LIMIT = 90     # keep UX safe; trim if more

def build_persona_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    row = []

    # convert to list so we can slice if it's huge
    persona_items = list(PERSONAS.items())
    total_buttons = len(persona_items)

    if total_buttons > SAFETY_BUTTON_LIMIT:
        logger.warning("Too many personas (%d) — trimming keyboard to %d buttons.", total_buttons, SAFETY_BUTTON_LIMIT)
        persona_items = persona_items[:SAFETY_BUTTON_LIMIT - 1]  # leave room for a "More..." button

    for key, data in persona_items:
        row.append(
            InlineKeyboardButton(
                text=data["name"],
                callback_data=f"persona:{key}"
            )
        )
        if len(row) == PERSONA_COLUMNS:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # if we trimmed earlier, add a "More..." button (fallback/paging placeholder)
    if total_buttons > SAFETY_BUTTON_LIMIT:
        buttons.append([InlineKeyboardButton(text="More...", callback_data="persona:more")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ────────────────────────────────────────────────
#                  MESSAGE HANDLER
# ────────────────────────────────────────────────
@dp.message()
async def handle_all(message: Message):
    user_text = (message.text or "").strip()
    if not user_text:
        return

    user_id = str(message.from_user.id)
    chat_id = message.chat.id

    # ── Persona selection ───────────────────────────────
    if user_text == "/persona":
        kb = build_persona_keyboard()
        await message.reply(
            "🎭 *Choose your persona:*",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    if user_text.startswith("/persona "):
        # legacy text command support (optional)
        parts = user_text.split(maxsplit=1)
        if len(parts) == 2:
            persona = parts[1].strip()
            if persona in PERSONAS:
                set_user_persona(user_id, persona)
                await message.reply(
                    f"✅ Switched to *{PERSONAS[persona]['name']}* 🔥",
                    parse_mode="Markdown"
                )
                return
        await message.reply("Use /persona to see the menu or /persona <name>")
        return

    # ── Gmail commands ──────────────────────────────────
    if user_text.startswith(("/gmail", "/help", "/start")):
        parts = user_text.split(maxsplit=1)
        cmd = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "connect":
            try:
                url = get_auth_url_for_user(user_id, need_send=True)
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🔐 Connect Gmail", url=url)
                ]])
                await bot.send_message(
                    chat_id,
                    "Click below to securely connect Gmail:",
                    reply_markup=kb,
                    disable_web_page_preview=True
                )
            except Exception as e:
                logger.exception("gmail connect error: %s", e)
                await message.reply("❌ Failed to build OAuth link.")
            return

        if cmd == "inbox":
            try:
                summary = gmail_summary(user_id)
                await message.reply(summary or "No recent emails or not connected.")
            except Exception as e:
                logger.exception("gmail inbox error: %s", e)
                await message.reply("❌ Error fetching inbox.")
            return

        # new smart inbox shortcut
        if cmd == "inbox smart":
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

        if cmd.startswith("send"):
            sub = cmd.split(maxsplit=1)
            if len(sub) < 2:
                await message.reply("Usage: /gmail send <draft_id>")
                return
            draft_id = sub[1].strip()
            try:
                ok = send_message_from_draft(user_id, draft_id)
                await message.reply("✅ Sent!" if ok else "❌ Failed to send.")
            except Exception as e:
                logger.exception("gmail send error: %s", e)
                await message.reply("❌ Error sending draft.")
            return

        if cmd.startswith("draft"):
            payload = cmd[len("draft"):].strip()
            if not payload:
                await message.reply("Usage: /gmail draft <to> | <subject> | <instructions>")
                return

            try:
                parts = [p.strip() for p in payload.split("|")]
                if len(parts) < 1 or "@" not in parts[0]:
                    await message.reply("Please provide valid recipient email first.")
                    return

                to_addr = parts[0]
                subject = parts[1] if len(parts) > 1 else "(no subject)"
                instructions = parts[2] if len(parts) > 2 else "Short professional email please."

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
                    await message.reply("❌ Failed to create draft.")
            except Exception as e:
                logger.exception("gmail draft error: %s", e)
                await message.reply("❌ Error creating draft.")
            return

        # help / default gmail message
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
            # Better UX: use Telegram "typing" action instead of a temporary message
            try:
                await bot.send_chat_action(chat_id, "typing")
            except Exception:
                # non-fatal; continue
                pass

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

    # schedule background generation (keeps handler fast)
    asyncio.create_task(bg_task())


# ────────────────────────────────────────────────
#             PERSONA CALLBACK HANDLER
# ────────────────────────────────────────────────
@dp.callback_query(lambda c: c.data.startswith("persona:"))
async def persona_callback(callback: CallbackQuery):
    try:
        persona_key = callback.data.split(":", 1)[1]
        user_id = str(callback.from_user.id)

        # simple paging fallback
        if persona_key == "more":
            await callback.answer()
            await callback.message.reply("There are many personas — I'll add paging/filters soon. Use /persona <name> for now.")
            return

        if persona_key in PERSONAS:
            set_user_persona(user_id, persona_key)
            # safer edit_text with fallback
            try:
                await callback.message.edit_text(
                    f"✅ Now talking with *{PERSONAS[persona_key]['name']}* 🔥",
                    parse_mode="Markdown"
                )
            except Exception:
                try:
                    await callback.message.reply(
                        f"✅ Now talking with *{PERSONAS[persona_key]['name']}* 🔥",
                        parse_mode="Markdown"
                    )
                except Exception:
                    # last resort: log and ignore
                    logger.exception("Couldn't notify user about persona change.")
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
        # allowed_updates explicit for safety
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        # ensure the aiohttp session is closed to avoid leaks
        try:
            # prefer closing bot.session (the same AiohttpSession instance)
            if getattr(bot, "session", None):
                await bot.session.close()
        except Exception:
            logger.exception("Error closing bot.session; trying session.close() directly")

        try:
            if session:
                await session.close()
        except Exception:
            logger.exception("Error closing global aiohttp session")


if __name__ == "__main__":
    asyncio.run(main())
