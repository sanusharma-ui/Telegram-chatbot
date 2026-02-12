# webhook_entry.py (or your webhook main)
import os
import asyncio
import logging
from fastapi import FastAPI, Request, Response, Query
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from backend.groq_handler import generate_response
from interaction.printer import send_human

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook_entry")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN")

bot = Bot(token=BOT_TOKEN)

app = FastAPI()

@app.get("/")
async def health():
    return {"status": "ok"}

@app.get("/gmail/callback")
async def gmail_callback(state: str = Query(None), code: str = Query(None)):
    try:
        if not state or not code:
            return Response("Missing state or code", status_code=400)
        from backend.gmail_integration import handle_oauth_callback
        user_id = handle_oauth_callback(state, code)
        if not user_id:
            return Response("Authorization failed or state expired. You may close this window.", status_code=200)
        return Response("Gmail connected successfully. You may close this window and return to Telegram.", media_type="text/plain")
    except Exception as e:
        logger.exception("gmail callback failed: %s", e)
        return Response("Server error during OAuth callback. Check logs.", status_code=500)

@app.post("/webhook")
async def telegram_webhook(request: Request):
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

        if user_text.startswith("/gmail") or user_text.startswith("/help") or user_text.startswith("/start"):
            from backend.gmail_integration import (
                get_auth_url_for_user,
                gmail_summary,
                create_draft,
                send_message_from_draft,
                disconnect_user
            )
            from backend.gmail_search import search_messages

            tokens = user_text.split()
            subcmd = tokens[1] if len(tokens) > 1 else ""
            args = tokens[2:] if len(tokens) > 2 else []

            # /gmail connect
            if subcmd == "connect":
                try:
                    url = get_auth_url_for_user(user_id, need_send=True)
                    # send as a single button so Telegram doesn't break the URL
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔐 Connect Gmail", url=url)]
                    ])
                    await bot.send_message(chat_id, "Click below to securely connect Gmail:", reply_markup=kb, disable_web_page_preview=True)
                except Exception as e:
                    logger.exception("gmail connect error: %s", e)
                    await send_human(bot, chat_id, "❌ Failed to build OAuth link. Check server logs.")
                return

            # /gmail inbox
            if subcmd == "inbox":
                try:
                    summary = gmail_summary(user_id)
                    await send_human(bot, chat_id, summary or "No recent emails or Gmail not connected.")
                except Exception as e:
                    logger.exception("gmail inbox error: %s", e)
                    await send_human(bot, chat_id, "❌ Error fetching inbox. Check logs.")
                return

            # /gmail search <query>
            if subcmd == "search":
                try:
                    query = " ".join(args) if args else "in:inbox"
                    results = search_messages(user_id, query)
                    if not results:
                        await send_human(bot, chat_id, "No results found.")
                        return
                    lines = ["📬 Search results:"]
                    for m in results:
                        lines.append(f"- {m.get('id')}")
                    await send_human(bot, chat_id, "\n".join(lines))
                except Exception as e:
                    logger.exception("gmail search error: %s", e)
                    await send_human(bot, chat_id, "❌ Error during search.")
                return

            # /gmail send <draft_id>
            if subcmd == "send":
                if not args:
                    await send_human(bot, chat_id, "Usage: /gmail send <draft_id>")
                    return
                try:
                    ok = send_message_from_draft(user_id, args[0])
                    await send_human(bot, chat_id, "✅ Mail sent." if ok else "❌ Failed to send draft. Check logs.")
                except Exception as e:
                    logger.exception("gmail send error: %s", e)
                    await send_human(bot, chat_id, "❌ Error sending draft. Check logs.")
                return

            # /gmail draft <to> | <subject> | <instructions>
            if subcmd == "draft":
                payload = " ".join(args)
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

                    email_body = await asyncio.to_thread(
                        generate_response,
                        user_message=ai_prompt,
                        persona_key=user_id,
                        user_ip=user_id
                    )

                    email_body = (email_body or "").strip()
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

            # /gmail disconnect
            if subcmd == "disconnect":
                try:
                    disconnect_user(user_id)
                    await send_human(bot, chat_id, "✅ Gmail disconnected safely.")
                except Exception as e:
                    logger.exception("gmail disconnect error: %s", e)
                    await send_human(bot, chat_id, "❌ Error disconnecting. Check logs.")
                return      

            # fallback
            await send_human(bot, chat_id,
                "Gmail commands:\n"
                "/gmail connect\n"
                "/gmail inbox\n"
                "/gmail search <query>\n"
                "/gmail draft <to> | <subject> | <instructions>\n"
                "/gmail send <draft_id>\n"
                "/gmail disconnect"
            )
            return

        try:
            await bot.send_chat_action(chat_id, "typing")
        except:
            pass

        try:
            reply = await asyncio.to_thread(
                generate_response,
                user_message=user_text,
                persona_key=user_id,
                user_ip=user_id
            )
            await send_human(bot, chat_id, reply)
        except Exception as e:
            logger.exception("LLM reply failed: %s", e)

    except Exception as e:
        logger.exception("process_update failed: %s", e)