# webhook_entry.py
import os
import asyncio
import logging
from typing import Dict, Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook_entry")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN missing")

WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN")

bot = Bot(token=BOT_TOKEN)
app = FastAPI()

# ====================== IMPORTS ======================
from interaction.printer import send_human
from backend.personas import PERSONAS
from backend.groq_handler import generate_response, set_user_persona
from backend.gmail_agent import should_handle_gmail_message, run_conversational_gmail_agent

from backend.gmail_integration import (
    get_auth_url_for_user,
    gmail_summary,
    gmail_smart_summary,
    create_draft,
    send_message_from_draft,
    disconnect_user,
    _get_gmail_service_for_user,
    handle_oauth_callback,
)
from backend.gmail_search import search_messages
from backend.gmail_inbox_ops import (
    read_full_email,
    mark_read,
    mark_unread,
    star_messages,
    archive_messages,
    delete_messages,
    _get_message,
)
from backend.gmail_labels import list_labels, create_label, delete_label
from backend.gmail_threads import summarize_thread_ai
from backend.gmail_drafts import update_draft, delete_draft, get_draft
from backend.gmail_send_safe import send_safely, send_draft_by_id
from backend.gmail_attachments import list_attachments, download_attachment, attach_file_to_draft

GMAIL_REQUIRED = {
    "inbox", "search", "read", "thread", "mark",
    "delete", "labels", "draft", "send", "disconnect"
}


@app.get("/")
async def health():
    return {"status": "ok"}


@app.get("/gmail/callback", response_class=HTMLResponse)
async def gmail_callback(request: Request):
    state = request.query_params.get("state")
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    logger.info(
        "OAuth callback hit: state=%s code_present=%s error=%s url=%s",
        state, bool(code), error, str(request.url)
    )

    try:
        if error:
            return HTMLResponse(
                f"""
                <html><body style="font-family:Arial,sans-serif;padding:24px;">
                <h2>Google authorization failed</h2>
                <p>Error: <b>{error}</b></p>
                <p>You may close this window.</p>
                </body></html>
                """,
                status_code=400,
            )

        if not state or not code:
            return HTMLResponse(
                """
                <html><body style="font-family:Arial,sans-serif;padding:24px;">
                <h2>Missing OAuth parameters</h2>
                <p>Please try again from Telegram.</p>
                </body></html>
                """,
                status_code=400,
            )

        user_id = handle_oauth_callback(
            state=state,
            code=code,
            full_callback_url=str(request.url),
        )

        if not user_id:
            return HTMLResponse(
                """
                <html><body style="font-family:Arial,sans-serif;padding:24px;">
                <h2>Authorization failed or state expired</h2>
                <p>Please run <b>/gmail connect</b> again in Telegram.</p>
                </body></html>
                """,
                status_code=400,
            )

        return HTMLResponse(
            """
            <html><body style="font-family:Arial,sans-serif;padding:24px;">
            <h2>✅ Gmail connected successfully!</h2>
            <p>You may close this window and return to Telegram.</p>
            </body></html>
            """,
            status_code=200,
        )

    except Exception as e:
        logger.exception("gmail callback failed: %s", e)
        return HTMLResponse(
            """
            <html><body style="font-family:Arial,sans-serif;padding:24px;">
            <h2>Server error during OAuth callback</h2>
            <p>Please check server logs.</p>
            </body></html>
            """,
            status_code=500,
        )


async def safe_process_update(update: Dict[str, Any]):
    try:
        await process_update(update)
    except Exception as e:
        logger.exception("Background process_update failed: %s", e)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    if WEBHOOK_SECRET_TOKEN:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET_TOKEN:
            logger.warning("Invalid webhook secret token")
            return {"ok": True}

    try:
        update = await request.json()
    except Exception as e:
        logger.error("Invalid JSON in webhook: %s", e)
        return {"ok": True}

    asyncio.create_task(safe_process_update(update))
    return {"ok": True}


async def process_update(update: Dict[str, Any]):
    try:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat = message.get("chat", {})
        from_user = message.get("from", {})

        chat_id = chat.get("id")
        user_id = str(from_user.get("id")) if from_user.get("id") is not None else None
        user_text = (message.get("text", "") or "").strip()

        if not chat_id or not user_id:
            logger.debug("Skipping update with missing chat/user: %s", update)
            return

        async def _send(text: str):
            await send_human(bot, chat_id, text)

        # ---------------- /persona ----------------
        if user_text.startswith("/persona"):
            parts = user_text.split(maxsplit=1)
            if len(parts) == 2:
                persona = parts[1].strip()
                if persona in PERSONAS:
                    set_user_persona(user_id, persona)
                    await _send(f"✅ Persona switched to *{PERSONAS[persona]['name']}*")
                    return
            await _send("Usage: /persona <name>\nAvailable: " + ", ".join(PERSONAS.keys()))
            return

        tokens = user_text.split()
        if not tokens:
            return

        first_token = tokens[0].split("@", 1)[0].lower()

        # ---------------- /start, /help, /gmail ----------------
        if first_token in ("/gmail", "/help", "/start"):
            if first_token == "/start":
                await _send("👋 Bot ready.\nUse /gmail to manage Gmail or /help for more commands.")
                return

            if first_token == "/help" or (first_token == "/gmail" and len(tokens) == 1):
                help_text = (
                    "📧 *Gmail Commands:*\n\n"
                    "/gmail connect\n"
                    "/gmail disconnect\n"
                    "/gmail inbox [smart]\n"
                    "/gmail search <query>\n"
                    "/gmail read <message_id>\n"
                    "/gmail thread <thread_id>\n"
                    "/gmail mark read|unread|star|archive <id1> <id2>...\n"
                    "/gmail delete <id>\n"
                    "/gmail labels list|create <name>|delete <label_id>\n"
                    "/gmail draft <to> | <subject> | <instructions>\n"
                    "/gmail send <draft_id>\n"
                )
                await _send(help_text)
                return

            subcmd = tokens[1].lower() if len(tokens) > 1 else ""
            args = tokens[2:] if len(tokens) > 2 else []

            if subcmd in GMAIL_REQUIRED and subcmd != "connect":
                if not _get_gmail_service_for_user(user_id):
                    await _send("❌ Gmail not connected. Use `/gmail connect` first.")
                    return

            # ---------- /gmail connect ----------
            if subcmd == "connect":
                try:
                    url = get_auth_url_for_user(user_id, need_send=True)
                    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔐 Connect Gmail", url=url)]])
                    await bot.send_message(
                        chat_id, "Click below to securely connect Gmail:",
                        reply_markup=kb, disable_web_page_preview=True
                    )
                except Exception as e:
                    logger.exception("gmail connect error: %s", e)
                    await _send("❌ Failed to build OAuth link. Check server logs.")
                return

            # ---------- /gmail disconnect ----------
            if subcmd == "disconnect":
                try:
                    disconnect_user(user_id)
                    await _send("✅ Gmail disconnected safely.")
                except Exception as e:
                    logger.exception("gmail disconnect error: %s", e)
                    await _send("❌ Error disconnecting. Check logs.")
                return

            # ---------- /gmail inbox [smart] ----------
            if subcmd == "inbox":
                sub = args[0].lower() if args else ""
                try:
                    if sub == "smart":
                        summary = gmail_smart_summary(user_id)
                    else:
                        summary = gmail_summary(user_id, max_results=10)
                    await _send(summary or "No recent emails found.")
                except Exception as e:
                    logger.exception("gmail inbox error: %s", e)
                    await _send("❌ Error fetching inbox. Check logs.")
                return

            # ---------- /gmail search <query> ----------
            if subcmd == "search":
                query = " ".join(args) if args else "in:inbox"
                try:
                    results = search_messages(user_id, query, max_results=20)
                    if not results:
                        await _send("No results found.")
                        return
                    svc = _get_gmail_service_for_user(user_id)
                    lines = ["📬 Search results (most recent first):"]
                    for m in results:
                        msg_id = m.get("id")
                        thread_id = m.get("threadId", "")
                        try:
                            meta = _get_message(svc, msg_id, format="metadata")
                            headers = (
                                {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
                                if meta and meta.get("payload") else {}
                            )
                            sender = headers.get("From", "?")
                            subject = headers.get("Subject", "(no subject)")
                            lines.append(f"- `{msg_id}` — {sender} | {subject} (thread: `{thread_id}`)")
                        except Exception:
                            lines.append(f"- `{msg_id}` (thread: `{thread_id}`)")
                    await _send("\n".join(lines))
                except Exception as e:
                    logger.exception("gmail search error: %s", e)
                    await _send("❌ Error during search. Check logs.")
                return

            # ---------- /gmail read <message_id> ----------
            if subcmd == "read":
                if not args:
                    await _send("Usage: /gmail read <message_id>")
                    return
                msg_id = args[0]
                try:
                    email = read_full_email(user_id, msg_id)
                    if not email:
                        await _send("❌ Failed to read the message or not connected.")
                        return
                    text = (
                        f"📧 *From:* {email.get('from')}\n"
                        f"*Subject:* {email.get('subject')}\n"
                        f"*Date:* {email.get('date')}\n\n"
                        f"{(email.get('body') or '')[:3500]}"
                    )
                    await _send(text)
                except Exception as e:
                    logger.exception("gmail read error: %s", e)
                    await _send("❌ Error reading message. Check logs.")
                return

            # ---------- /gmail thread <thread_id> ----------
            if subcmd == "thread":
                if not args:
                    await _send("Usage: /gmail thread <thread_id>")
                    return
                thread_id = args[0]
                try:
                    summary = summarize_thread_ai(user_id, thread_id)
                    await _send(summary or "❌ Failed to summarize thread.")
                except Exception as e:
                    logger.exception("gmail thread error: %s", e)
                    await _send("❌ Error summarizing thread. Check logs.")
                return

            # ---------- /gmail mark ----------
            if subcmd == "mark":
                if len(args) < 2:
                    await _send("Usage: /gmail mark read|unread|star|archive <id1> <id2> ...")
                    return
                action = args[0].lower()
                ids = args[1:]
                try:
                    ok = False
                    if action == "read":
                        ok = mark_read(user_id, ids)
                    elif action == "unread":
                        ok = mark_unread(user_id, ids)
                    elif action == "star":
                        ok = star_messages(user_id, ids)
                    elif action == "archive":
                        ok = archive_messages(user_id, ids)
                    else:
                        await _send("Unknown mark action. Use read, unread, star, or archive.")
                        return
                    await _send("✅ Done" if ok else "❌ Failed")
                except Exception as e:
                    logger.exception("gmail mark error: %s", e)
                    await _send("❌ Error performing mark operation. Check logs.")
                return

            # ---------- /gmail delete ----------
            if subcmd == "delete":
                if not args:
                    await _send("Usage: /gmail delete <message_id> ...")
                    return
                ids = args
                try:
                    ok = delete_messages(user_id, ids)
                    await _send("🗑️ Deleted permanently" if ok else "❌ Failed")
                except Exception as e:
                    logger.exception("gmail delete error: %s", e)
                    await _send("❌ Error deleting messages. Check logs.")
                return

            # ---------- /gmail labels ----------
            if subcmd == "labels":
                sub = args[0].lower() if args else ""
                try:
                    if sub == "list":
                        labels = list_labels(user_id)
                        if not labels:
                            await _send("No labels found.")
                            return
                        lines = ["🏷️ Your labels:"]
                        for l in labels:
                            lines.append(f"{l.get('name', '(no name)')} — `{l.get('id')}`")
                        await _send("\n".join(lines))
                        return

                    if sub == "create":
                        if len(args) < 2:
                            await _send("Usage: /gmail labels create <name>")
                            return
                        name = " ".join(args[1:])
                        label = create_label(user_id, name)
                        await _send(f"✅ Created: {label.get('name')}" if label else "❌ Failed to create label")
                        return

                    if sub == "delete":
                        if len(args) < 2:
                            await _send("Usage: /gmail labels delete <label_id>")
                            return
                        label_id = args[1]
                        ok = delete_label(user_id, label_id)
                        await _send("✅ Deleted" if ok else "❌ Failed to delete label")
                        return

                    await _send("Usage: /gmail labels list|create <name>|delete <label_id>")
                except Exception as e:
                    logger.exception("gmail labels error: %s", e)
                    await _send("❌ Error with labels command. Check logs.")
                return

            # ---------- /gmail draft ----------
            if subcmd == "draft":
                payload = user_text.partition("draft")[2].strip()
                if not payload:
                    await _send("Usage: /gmail draft <to> | <subject> | <instructions>")
                    return
                try:
                    parts = [p.strip() for p in payload.split("|")]
                    to_addr = parts[0] if len(parts) >= 1 else None
                    subject = parts[1] if len(parts) >= 2 else "(no subject)"
                    instructions = parts[2] if len(parts) >= 3 else "Short professional email please."

                    if not to_addr or "@" not in to_addr:
                        await _send("Please provide a valid recipient email (e.g. hr@company.com).")
                        return

                    ai_prompt = (
                        f"GENERATE_EMAIL_BODY_ONLY:\n"
                        f"Recipient: {to_addr}\n"
                        f"Subject: {subject}\n"
                        f"Context/Instruction: {instructions}\n\n"
                        "Output: Provide only the email body as plain text. Do not include emojis or signatures."
                    )

                    email_body = await asyncio.to_thread(
                        generate_response,
                        user_message=ai_prompt,
                        persona_key=user_id,
                        user_ip=user_id,
                    )

                    email_body = (email_body or "").strip()
                    if not email_body:
                        await _send("❌ AI failed to generate the email body. Try rewording the instructions.")
                        return

                    created = create_draft(user_id, to_addr, subject, email_body)
                    if created and isinstance(created, dict) and created.get("id"):
                        draft_id = created.get("id")
                        reply_text = (
                            "✅ Draft created.\n\n"
                            f"To: {to_addr}\n"
                            f"Subject: {subject}\n\n"
                            f"---\n{email_body[:1000]}\n---\n\n"
                            f"Use `/gmail send {draft_id}` to send this draft."
                        )
                        await _send(reply_text)
                    else:
                        await _send("❌ Failed to create draft. Check server logs.")
                except Exception as e:
                    logger.exception("gmail draft error: %s", e)
                    await _send("❌ Error while creating draft. Check logs.")
                return

            # ---------- /gmail send ----------
            if subcmd == "send":
                if not args:
                    await _send("Usage: /gmail send <draft_id>")
                    return
                draft_id = args[0]
                try:
                    ok = send_message_from_draft(user_id, draft_id)
                    await _send("✅ Mail sent." if ok else "❌ Failed to send draft. Check logs.")
                except Exception as e:
                    logger.exception("gmail send error: %s", e)
                    await _send("❌ Error sending draft. Check logs.")
                return

            await _send("Unknown /gmail subcommand. Use /help to see available commands.")
            return

        # ---------------- Natural-language Gmail agent ----------------
        if should_handle_gmail_message(user_id, user_text):
            try:
                result = await asyncio.to_thread(
                    run_conversational_gmail_agent,
                    user_id,
                    user_text,
                )

                ui_actions = result.get("ui_actions", []) or []
                for action in ui_actions:
                    if action.get("ui_action") == "gmail_connect" and action.get("connect_url"):
                        kb = InlineKeyboardMarkup(
                            inline_keyboard=[
                                [InlineKeyboardButton(text="🔐 Connect Gmail", url=action["connect_url"])]
                            ]
                        )
                        await bot.send_message(
                            chat_id,
                            "Click below to securely connect Gmail:",
                            reply_markup=kb,
                            disable_web_page_preview=True,
                        )

                await send_human(bot, chat_id, result.get("reply", "Done."))
                return

            except Exception as e:
                logger.exception("Gmail natural agent failed: %s", e)
                await send_human(bot, chat_id, "Sorry — something went wrong in the Gmail agent flow. Check logs.")
                return

        # ---------------- Fallback: normal conversational reply ----------------
        try:
            reply = await asyncio.to_thread(
                generate_response,
                user_message=user_text,
                persona_key=user_id,
                user_ip=user_id,
            )
            await send_human(bot, chat_id, reply)
        except Exception as e:
            logger.exception("LLM reply failed: %s", e)
            await send_human(bot, chat_id, "Sorry — something went wrong generating a reply. Check logs.")

    except Exception as e:
        logger.exception("process_update failed: %s", e)