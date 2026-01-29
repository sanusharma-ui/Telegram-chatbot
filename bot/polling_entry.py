# # bot/polling_entry.py
# import os
# import asyncio
# import logging
# from aiogram import Bot, Dispatcher
# from aiogram.client.session.aiohttp import AiohttpSession
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
# # Keep handler minimal — delegate to background task to avoid blocking polling loop
# @dp.message()
# async def handle_all(message):
#     user_text = (message.text or "").strip()
#     user_id = message.from_user.id
#     chat_id = message.chat.id

#     # --- Immediate /persona handling (synchronous; no background task) ---
#     if user_text.startswith("/persona"):
#         parts = user_text.split(maxsplit=1)
#         if len(parts) == 2:
#             persona = parts[1].strip()
#             from backend.personas import PERSONAS
#             if persona in PERSONAS:
#                 from backend.groq_handler import set_user_persona
#                 set_user_persona(str(user_id), persona)
#                 await message.reply(
#                     f"✅ Persona switched to *{PERSONAS[persona]['name']}* 🔥",
#                     parse_mode="Markdown"
#                 )
#                 return
#         await message.reply("Usage: /persona <name>\nAvailable: " + ", ".join(PERSONAS.keys()))
#         return

#     # --- existing background flow (unchanged) ---
#     async def bg():
#         try:
#             ack = await message.reply("Processing... ⏳")
#             reply = await asyncio.to_thread(generate_response,
#                 user_message=user_text,
#                 persona_key=str(user_id),
#                 user_ip=str(user_id)
#             )
#         except Exception as e:
#             reply = "Error generating response."
#             logger.exception("bg generate error: %s", e)
#         # edit ack
#         try:
#             from interaction.printer import send_human
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


import os
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv
from backend.groq_handler import generate_response
from interaction.printer import send_human
from backend.personas import PERSONAS

# Gmail helpers will be imported lazily inside handler to avoid startup deps
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
    user_text = (message.text or "").strip()
    user_id = str(message.from_user.id)
    chat_id = message.chat.id

    # ---- 1) Persona switch (system-level) ----
    if user_text.startswith("/persona"):
        parts = user_text.split(maxsplit=1)
        if len(parts) == 2:
            persona = parts[1].strip()
            from backend.personas import PERSONAS
            if persona in PERSONAS:
                from backend.groq_handler import set_user_persona
                set_user_persona(user_id, persona)
                await message.reply(
                    f"✅ Persona switched to *{PERSONAS[persona]['name']}* 🔥",
                    parse_mode="Markdown"
                )
                return
        await message.reply("Usage: /persona <name>\nAvailable: " + ", ".join(PERSONAS.keys()))
        return

    # ---- 2) System-level command router (commands never reach the LLM) ----
    if user_text.startswith("/gmail") or user_text.startswith("/help") or user_text.startswith("/start"):
        # Lazy import for Gmail integration utilities
        from backend.gmail_integration import (
            get_auth_url_for_user,
            gmail_summary,
            create_draft,
            send_message_from_draft,
            disconnect_user
        )

        parts = user_text.split(maxsplit=1)
        cmd = parts[1].strip() if len(parts) > 1 else ""

        # /gmail connect
        if cmd == "connect":
            try:
                url = get_auth_url_for_user(user_id, need_send=True)
                await message.reply(f"🔐 Connect Gmail securely:\n{url}", disable_web_page_preview=True)
            except Exception as e:
                logger.exception("gmail connect error: %s", e)
                await message.reply("❌ Failed to build OAuth link. Check server logs.")
            return

        # /gmail inbox
        if cmd == "inbox":
            try:
                summary = gmail_summary(user_id)
                await message.reply(summary or "No recent emails or Gmail not connected.")
            except Exception as e:
                logger.exception("gmail inbox error: %s", e)
                await message.reply("❌ Error fetching inbox. Check logs.")
            return

        # /gmail disconnect
        if cmd == "disconnect":
            try:
                disconnect_user(user_id)
                await message.reply("✅ Gmail disconnected safely.")
            except Exception as e:
                logger.exception("gmail disconnect error: %s", e)
                await message.reply("❌ Error disconnecting. Check logs.")
            return

        # /gmail send <draft_id>
        if cmd.startswith("send"):
            sub = cmd.split(maxsplit=1)
            if len(sub) < 2:
                await message.reply("Usage: /gmail send <draft_id>")
                return
            draft_id = sub[1].strip()
            try:
                ok = send_message_from_draft(user_id, draft_id)
                await message.reply("✅ Mail sent." if ok else "❌ Failed to send draft. Check logs.")
            except Exception as e:
                logger.exception("gmail send error: %s", e)
                await message.reply("❌ Error sending draft. Check logs.")
            return

        # /gmail draft to | subject | instructions
        # Example:
        # /gmail draft hr@company.com | Leave Application | Please write a polite leave request for tomorrow.
        if cmd.startswith("draft"):
            payload = cmd[len("draft"):].strip()
            if not payload:
                await message.reply("Usage: /gmail draft <to> | <subject> | <instructions>")
                return
            try:
                # split on '|' to allow natural language body instruction
                parts = [p.strip() for p in payload.split("|")]
                # expected: parts = [to, subject, instructions]
                to_addr = parts[0] if len(parts) >= 1 else None
                subject = parts[1] if len(parts) >= 2 else "(no subject)"
                instructions = parts[2] if len(parts) >= 3 else "Please draft a short professional email."
                if not to_addr or "@" not in to_addr:
                    await message.reply("Please provide a valid recipient email (e.g. hr@company.com).")
                    return

                # Ask the LLM to generate only the email body (system-level control).
                # We demand plain text, no emojis, no signatures, max 300 words.
                ai_prompt = (
                    f"GENERATE_EMAIL_BODY_ONLY:\n"
                    f"Recipient: {to_addr}\nSubject: {subject}\nContext/Instruction: {instructions}\n\n"
                    "Output: Provide only the email body as plain text. Do not include emojis, greetings beyond a one-line salutation if needed, or any signature."
                )

                # Use the existing generate_response but ensure it's called in a thread (sync wrapper)
                # This will still honor persona-driven memory, but the prompt forces plain output.
                email_body = await asyncio.to_thread(
                    generate_response,
                    user_message=ai_prompt,
                    persona_key=user_id,
                    user_ip=user_id
                )

                # Post-process: trim and ensure we have reasonable content
                email_body = (email_body or "").strip()
                # If polish_reply added emoji (rare), remove common emoji characters
                email_body = email_body.replace("😎", "").replace("☕", "").strip()
                if not email_body:
                    await message.reply("❌ AI failed to generate the email body. Try rewording the instructions.")
                    return

                # Create draft via Gmail API (system action)
                created = create_draft(user_id, to_addr, subject, email_body)
                if created and isinstance(created, dict) and created.get("id"):
                    draft_id = created.get("id")
                    reply_text = (
                        "✅ Draft created.\n\n"
                        f"To: {to_addr}\nSubject: {subject}\n\n"
                        f"---\n{email_body[:1000]}\n---\n\n"
                        f"Use `/gmail send {draft_id}` to send this draft, or `/gmail disconnect` to revoke access."
                    )
                    await message.reply(reply_text)
                else:
                    await message.reply("❌ Failed to create draft. Check server logs.")
            except Exception as e:
                logger.exception("gmail draft error: %s", e)
                await message.reply("❌ Error while creating draft. Check logs.")
            return

        # Fallback help for /gmail
        await message.reply(
            "Gmail commands:\n"
            "/gmail connect\n"
            "/gmail inbox\n"
            "/gmail draft to@example.com | Subject | Instructions\n"
            "/gmail send <draft_id>\n"
            "/gmail disconnect"
        )
        return

    # ---- 3) Non-command -> forward to LLM in background (unchanged behaviour) ----
    async def bg():
        try:
            ack = await message.reply("Processing... ⏳")
            reply = await asyncio.to_thread(generate_response,
                user_message=user_text,
                persona_key=user_id,
                user_ip=user_id
            )
        except Exception as e:
            reply = "Error generating response."
            logger.exception("bg generate error: %s", e)
        # edit ack and send in human-like chunks
        try:
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
