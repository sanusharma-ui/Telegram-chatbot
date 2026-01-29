# # bot/webhook_entry.py
# import os
# import asyncio
# import logging
# from fastapi import FastAPI, Request
# from aiogram import Bot
# from dotenv import load_dotenv
# from backend.groq_handler import generate_response
# from interaction.printer import send_human
# load_dotenv()
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("webhook_entry")
# BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# if not BOT_TOKEN:
#     raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
# bot = Bot(token=BOT_TOKEN)
# # 🔴 THIS MUST EXIST AT TOP LEVEL
# app = FastAPI()
# @app.get("/")
# async def health():
#     return {"status": "ok"}
# @app.post("/webhook")
# async def telegram_webhook(request: Request):
#     try:
#         update = await request.json()
#     except Exception as e:
#         logger.error("Invalid JSON: %s", e)
#         return {"ok": True}
#     # fire-and-forget background processing
#     asyncio.get_event_loop().call_soon(
#         asyncio.create_task,
#         process_update(update)
#     )
#     # IMPORTANT: immediate response
#     return {"ok": True}
# async def process_update(update: dict):
#     try:
#         message = update.get("message") or update.get("edited_message")
#         if not message:
#             return
#         chat_id = message["chat"]["id"]
#         user_id = message["from"]["id"]
#         user_text = message.get("text", "") or ""
#         # handle /persona command (webhook)
#         if user_text.startswith("/persona"):
#             parts = user_text.split(maxsplit=1)
#             if len(parts) == 2:
#                 persona = parts[1].strip()
#                 from backend.personas import PERSONAS
#                 if persona in PERSONAS:
#                     from backend.groq_handler import set_user_persona
#                     set_user_persona(str(user_id), persona)
#                     await send_human(bot, chat_id, f"✅ Persona switched to *{PERSONAS[persona]['name']}*")
#                     return
#             await send_human(bot, chat_id, "Usage: /persona <name>\nAvailable: " + ", ".join(PERSONAS.keys()))
#             return
#         # typing indicator (safe)
#         try:
#             await bot.send_chat_action(chat_id, "typing")
#         except:
#             pass
#         reply = await asyncio.to_thread(
#             generate_response,
#             user_message=user_text,
#             persona_key=str(user_id),
#             user_ip=str(user_id)
#         )
#         await send_human(bot, chat_id, reply)
#     except Exception as e:
#         logger.exception("process_update failed: %s", e)

import os
import asyncio
import logging
from fastapi import FastAPI, Request, Response, Query
from aiogram import Bot
from dotenv import load_dotenv
from backend.groq_handler import generate_response
from interaction.printer import send_human

# Lazy imports for Gmail handlers used in the router
# from backend.gmail_integration import handle_oauth_callback, get_auth_url_for_user, gmail_summary, create_draft, send_message_from_draft, disconnect_user

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook_entry")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

# Optional webhook secret token to validate incoming requests
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN")

bot = Bot(token=BOT_TOKEN)

# 🔴 THIS MUST EXIST AT TOP LEVEL
app = FastAPI()

@app.get("/")
async def health():
    return {"status": "ok"}

# OAuth callback endpoint for Google to redirect to after consent
# Example redirect URI: https://your-domain.com/gmail/callback?state=...&code=...
@app.get("/gmail/callback")
async def gmail_callback(state: str = Query(None), code: str = Query(None)):
    try:
        if not state or not code:
            return Response("Missing state or code", status_code=400)
        # Lazy import to avoid startup-time deps
        from backend.gmail_integration import handle_oauth_callback
        user_id = handle_oauth_callback(state, code)
        if not user_id:
            return Response("Authorization failed or state expired. You may close this window.", status_code=200)
        # Simple success page (user returns to Telegram)
        return Response("Gmail connected successfully. You may close this window and return to Telegram.", media_type="text/plain")
    except Exception as e:
        logger.exception("gmail callback failed: %s", e)
        return Response("Server error during OAuth callback. Check logs.", status_code=500)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    # Optional verification of Telegram secret token (recommended)
    if WEBHOOK_SECRET_TOKEN:
        header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if header_token != WEBHOOK_SECRET_TOKEN:
            logger.warning("Invalid webhook secret token")
            return {"ok": True}

    try:
        update = await request.json()
    except Exception as e:
        logger.error("Invalid JSON: %s", e)
        return {"ok": True}

    # Queue background processing
    asyncio.get_event_loop().call_soon(
        asyncio.create_task,
        process_update(update)
    )
    return {"ok": True}

async def process_update(update: dict):
    try:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        chat_id = message["chat"]["id"]
        user_id = str(message["from"]["id"])
        user_text = (message.get("text", "") or "").strip()

        # ---- 1) Persona switch (system-level) ----
        if user_text.startswith("/persona"):
            parts = user_text.split(maxsplit=1)
            if len(parts) == 2:
                persona = parts[1].strip()
                from backend.personas import PERSONAS
                if persona in PERSONAS:
                    from backend.groq_handler import set_user_persona
                    set_user_persona(user_id, persona)
                    await send_human(bot, chat_id, f"✅ Persona switched to *{PERSONAS[persona]['name']}*")
                    return
            await send_human(bot, chat_id, "Usage: /persona <name>\nAvailable: " + ", ".join(PERSONAS.keys()))
            return

        # ---- 2) System-level command router (commands never reach the LLM) ----
        if user_text.startswith("/gmail") or user_text.startswith("/help") or user_text.startswith("/start"):
            # Lazy import Gmail utilities
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
                    await send_human(bot, chat_id, f"🔐 Connect Gmail securely:\n{url}")
                except Exception as e:
                    logger.exception("gmail connect error: %s", e)
                    await send_human(bot, chat_id, "❌ Failed to build OAuth link. Check server logs.")
                return

            # /gmail inbox
            if cmd == "inbox":
                try:
                    summary = gmail_summary(user_id)
                    await send_human(bot, chat_id, summary or "No recent emails or Gmail not connected.")
                except Exception as e:
                    logger.exception("gmail inbox error: %s", e)
                    await send_human(bot, chat_id, "❌ Error fetching inbox. Check logs.")
                return

            # /gmail disconnect
            if cmd == "disconnect":
                try:
                    disconnect_user(user_id)
                    await send_human(bot, chat_id, "✅ Gmail disconnected safely.")
                except Exception as e:
                    logger.exception("gmail disconnect error: %s", e)
                    await send_human(bot, chat_id, "❌ Error disconnecting. Check logs.")
                return

            # /gmail send <draft_id>
            if cmd.startswith("send"):
                sub = cmd.split(maxsplit=1)
                if len(sub) < 2:
                    await send_human(bot, chat_id, "Usage: /gmail send <draft_id>")
                    return
                draft_id = sub[1].strip()
                try:
                    ok = send_message_from_draft(user_id, draft_id)
                    await send_human(bot, chat_id, "✅ Mail sent." if ok else "❌ Failed to send draft. Check logs.")
                except Exception as e:
                    logger.exception("gmail send error: %s", e)
                    await send_human(bot, chat_id, "❌ Error sending draft. Check logs.")
                return

            # /gmail draft to | subject | instructions
            if cmd.startswith("draft"):
                payload = cmd[len("draft"):].strip()
                if not payload:
                    await send_human(bot, chat_id, "Usage: /gmail draft <to> | <subject> | <instructions>")
                    return
                try:
                    parts = [p.strip() for p in payload.split("|")]
                    to_addr = parts[0] if len(parts) >= 1 else None
                    subject = parts[1] if len(parts) >= 2 else "(no subject)"
                    instructions = parts[2] if len(parts) >= 3 else "Please draft a short professional email."
                    if not to_addr or "@" not in to_addr:
                        await send_human(bot, chat_id, "Please provide a valid recipient email (e.g. hr@company.com).")
                        return

                    ai_prompt = (
                        f"GENERATE_EMAIL_BODY_ONLY:\n"
                        f"Recipient: {to_addr}\nSubject: {subject}\nContext/Instruction: {instructions}\n\n"
                        "Output: Provide only the email body as plain text. Do not include emojis, signatures, or extra commentary."
                    )

                    # Ask LLM to generate the body (runs in thread)
                    email_body = await asyncio.to_thread(
                        generate_response,
                        user_message=ai_prompt,
                        persona_key=user_id,
                        user_ip=user_id
                    )

                    email_body = (email_body or "").strip()
                    # small sanitize: strip emoji leftovers from polish_reply
                    email_body = email_body.replace("😎", "").replace("☕", "").strip()
                    if not email_body:
                        await send_human(bot, chat_id, "❌ AI failed to generate the email body. Try rewording the instructions.")
                        return

                    created = create_draft(user_id, to_addr, subject, email_body)
                    if created and isinstance(created, dict) and created.get("id"):
                        draft_id = created.get("id")
                        reply_text = (
                            "✅ Draft created.\n\n"
                            f"To: {to_addr}\nSubject: {subject}\n\n"
                            f"---\n{email_body[:1000]}\n---\n\n"
                            f"Use `/gmail send {draft_id}` to send this draft, or `/gmail disconnect` to revoke access."
                        )
                        await send_human(bot, chat_id, reply_text)
                    else:
                        await send_human(bot, chat_id, "❌ Failed to create draft. Check server logs.")
                except Exception as e:
                    logger.exception("gmail draft error: %s", e)
                    await send_human(bot, chat_id, "❌ Error while creating draft. Check logs.")
                return

            # Help fallback
            await send_human(bot, chat_id,
                "Gmail commands:\n"
                "/gmail connect\n"
                "/gmail inbox\n"
                "/gmail draft to@example.com | Subject | Instructions\n"
                "/gmail send <draft_id>\n"
                "/gmail disconnect"
            )
            return

        # ---- 3) Non-command -> forward to LLM in background (unchanged behaviour) ----
        try:
            # Typing indicator (best effort)
            await bot.send_chat_action(chat_id, "typing")
        except:
            pass

        # Use thread to call blocking generate_response
        reply = await asyncio.to_thread(
            generate_response,
            user_message=user_text,
            persona_key=user_id,
            user_ip=user_id
        )
        await send_human(bot, chat_id, reply)

    except Exception as e:
        logger.exception("process_update failed: %s", e)
