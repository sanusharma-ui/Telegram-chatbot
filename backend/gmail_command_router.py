# backend/gmail_command_router.py
"""
Centralized Gmail command router.

Usage:
- polling (aiogram): await handle_gmail_command(user_id, chat_id, text, send_func=message.reply)
- webhook (FastAPI): await handle_gmail_command(user_id, chat_id, text, send_func=_send_func)
  where _send_func is an async function taking a single string argument.

send_func must be an awaitable callable: async def send_func(text: str) -> Any
"""

import asyncio
import logging
from typing import Callable, Awaitable, List, Optional

from backend.gmail_integration import (
    get_auth_url_for_user,
    gmail_summary,
    gmail_smart_summary,
    create_draft,
    send_message_from_draft,
    disconnect_user,
    _get_gmail_service_for_user,
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
from backend.gmail_send_safe import send_safely, send_draft_by_id
from backend.gmail_attachments import list_attachments, download_attachment, attach_file_to_draft
from backend.groq_handler import generate_response

logger = logging.getLogger(__name__)

# Commands that require Gmail to be connected (connect/disconnect excluded)
NEEDS_GMAIL = {
    "inbox", "search", "read", "thread",
    "mark", "delete", "labels", "draft", "send"
}

# Typing alias for the send function the router expects
SendFunc = Callable[[str], Awaitable[None]]


def _format_lines(lines: List[str]) -> str:
    """Helper to join lines safely for send_func."""
    return "\n".join(lines)


def _safe_get_headers(meta: dict) -> dict:
    if not meta:
        return {}
    hdrs = {}
    for h in meta.get("payload", {}).get("headers", []):
        name = h.get("name")
        value = h.get("value")
        if name and value:
            hdrs[name] = value
    return hdrs


async def handle_gmail_command(
    user_id: str,
    chat_id: int,
    text: str,
    send_func: SendFunc,
) -> None:
    """
    Main router function.

    Parameters
    - user_id: str representation of Telegram user id (used as storage key)
    - chat_id: Telegram chat id (only for logging; replies go via send_func)
    - text: full message text as user sent (e.g. "/gmail search in:inbox")
    - send_func: async callable that accepts message text and sends it (await send_func(msg))
    """
    try:
        if not text:
            await send_func("Empty command.")
            return

        tokens = text.strip().split()
        if not tokens:
            await send_func("Empty command.")
            return

        # normalize command token (handles /gmail@BotName)
        first = tokens[0].split("@", 1)[0].lower()
        if first not in ("/gmail", "/help", "/start"):
            # Not a gmail command — caller should avoid calling this, but handle gracefully
            await send_func("Router: Unknown top-level command. Use /help for Gmail commands.")
            return

        # handle /start and /help quickly
        if first == "/start":
            await send_func("👋 Bot ready.\nUse /gmail to manage Gmail or /help for more commands.")
            return

        if first == "/help" or (first == "/gmail" and len(tokens) == 1):
            help_text = (
                "📧 Gmail Commands:\n\n"
                "/gmail connect\n"
                "/gmail disconnect\n"
                "/gmail inbox [smart]\n"
                "/gmail search <query>\n"
                "/gmail read <message_id>\n"
                "/gmail thread <thread_id>\n"
                "/gmail mark read|unread|star|archive <id1> <id2>...\n"
                "/gmail delete <id1> <id2> ...   (permanent)\n"
                "/gmail labels list\n"
                "/gmail labels create <name>\n"
                "/gmail labels delete <label_id>\n"
                "/gmail draft <to> | <subject> | <instructions>\n"
                "/gmail send <draft_id>\n"
            )
            await send_func(help_text)
            return

        # now handle /gmail subcommands
        subcmd = tokens[1].lower() if len(tokens) > 1 else ""
        args = tokens[2:] if len(tokens) > 2 else []

        # quick connectivity guard for operations needing gmail
        if subcmd in NEEDS_GMAIL and subcmd != "connect":
            svc = _get_gmail_service_for_user(user_id)
            if not svc:
                await send_func("❌ Gmail not connected. Use `/gmail connect` first.")
                return

        # ---------- connect ----------
        if subcmd == "connect":
            try:
                url = get_auth_url_for_user(user_id, need_send=True)
                # send plain url — caller chooses how to render (button vs plain)
                await send_func(f"🔐 Connect Gmail:\n{url}")
            except Exception as e:
                logger.exception("connect failed: %s", e)
                await send_func("❌ Failed to build OAuth link. Check server logs.")
            return

        # ---------- disconnect ----------
        if subcmd == "disconnect":
            try:
                disconnect_user(user_id)
                await send_func("✅ Gmail disconnected.")
            except Exception as e:
                logger.exception("disconnect failed: %s", e)
                await send_func("❌ Failed to disconnect. Check server logs.")
            return

        # ---------- inbox ----------
        if subcmd == "inbox":
            sub = args[0].lower() if args else ""
            try:
                if sub == "smart":
                    summary = gmail_smart_summary(user_id)
                else:
                    summary = gmail_summary(user_id, max_results=10)
                await send_func(summary or "No recent emails found.")
            except Exception as e:
                logger.exception("inbox failed: %s", e)
                await send_func("❌ Error fetching inbox. Check logs.")
            return

        # ---------- search ----------
        if subcmd == "search":
            query = " ".join(args) if args else "in:inbox"
            try:
                results = search_messages(user_id, query, max_results=20)
                if not results:
                    await send_func("No results found.")
                    return

                svc = _get_gmail_service_for_user(user_id)
                lines = ["📬 Search results (copy ID for actions):"]
                for r in results:
                    mid = r.get("id")
                    tid = r.get("threadId", "")
                    try:
                        meta = _get_message(svc, mid, format="metadata")
                        headers = _safe_get_headers(meta)
                        sender = headers.get("From", "?")
                        subject = headers.get("Subject", "(no subject)")
                        lines.append(f"ID: `{mid}` | Thread: `{tid}`\n{sender} | {subject}")
                    except Exception:
                        lines.append(f"ID: `{mid}` | Thread: `{tid}`")
                await send_func(_format_lines(lines))
            except Exception as e:
                logger.exception("search failed: %s", e)
                await send_func("❌ Error during search. Check logs.")
            return

        # ---------- read ----------
        if subcmd == "read":
            if not args:
                await send_func("Usage: /gmail read <message_id>")
                return
            msg_id = args[0]
            try:
                email = read_full_email(user_id, msg_id)
                if not email:
                    await send_func("❌ Failed to read message (maybe invalid id or not connected).")
                    return
                body = (email.get("body") or "")[:3500]
                text = (
                    f"📧 From: {email.get('from')}\n"
                    f"Subject: {email.get('subject')}\n"
                    f"Date: {email.get('date')}\n\n"
                    f"{body}"
                )
                await send_func(text)
            except Exception as e:
                logger.exception("read failed: %s", e)
                await send_func("❌ Error reading message. Check logs.")
            return

        # ---------- thread ----------
        if subcmd == "thread":
            if not args:
                await send_func("Usage: /gmail thread <thread_id>")
                return
            thread_id = args[0]
            try:
                summary = summarize_thread_ai(user_id, thread_id)
                await send_func(summary or "❌ Failed to summarize thread.")
            except Exception as e:
                logger.exception("thread summarize failed: %s", e)
                await send_func("❌ Error summarizing thread. Check logs.")
            return

        # ---------- mark ----------
        if subcmd == "mark":
            if len(args) < 2:
                await send_func("Usage: /gmail mark read|unread|star|archive <id1> <id2> ...")
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
                await send_func("✅ Done" if ok else "❌ Failed")
            except Exception as e:
                logger.exception("mark failed: %s", e)
                await send_func("❌ Error performing mark operation. Check logs.")
            return

        # ---------- delete ----------
        if subcmd == "delete":
            if not args:
                await send_func("Usage: /gmail delete <id1> <id2> ... (permanent delete!)")
                return
            ids = args
            try:
                ok = delete_messages(user_id, ids)
                await send_func("🗑️ Deleted permanently" if ok else "❌ Failed")
            except Exception as e:
                logger.exception("delete failed: %s", e)
                await send_func("❌ Error deleting messages. Check logs.")
            return

        # ---------- labels ----------
        if subcmd == "labels":
            sub = args[0].lower() if args else ""
            try:
                if sub == "list":
                    labels = list_labels(user_id)
                    if not labels:
                        await send_func("No labels found.")
                        return
                    lines = [f"{l.get('name','(no name)')} — `{l.get('id')}`" for l in labels]
                    await send_func(_format_lines(["🏷️ Your labels:"] + lines))
                    return

                if sub == "create":
                    if len(args) < 2:
                        await send_func("Usage: /gmail labels create <name>")
                        return
                    name = " ".join(args[1:])
                    label = create_label(user_id, name)
                    await send_func(f"✅ Created: {label.get('name')}" if label else "❌ Failed to create label")
                    return

                if sub == "delete":
                    if len(args) < 2:
                        await send_func("Usage: /gmail labels delete <label_id>")
                        return
                    label_id = args[1]
                    ok = delete_label(user_id, label_id)
                    await send_func("✅ Deleted" if ok else "❌ Failed to delete label")
                    return
            except Exception as e:
                logger.exception("labels failed: %s", e)
                await send_func("❌ Error with labels command. Check logs.")
            return

        # ---------- draft ----------
        if subcmd == "draft":
            # keep everything after the first "draft" token (so pipes survive)
            payload = text.partition("draft")[2].strip()
            if not payload:
                await send_func("Usage: /gmail draft <to> | <subject> | <instructions>")
                return
            try:
                parts = [p.strip() for p in payload.split("|")]
                to_addr = parts[0] if len(parts) >= 1 else None
                subject = parts[1] if len(parts) >= 2 else "(no subject)"
                instructions = parts[2] if len(parts) >= 3 else "Short professional email please."

                if not to_addr or "@" not in to_addr:
                    await send_func("❌ Please provide a valid recipient email (e.g. name@example.com).")
                    return

                ai_prompt = (
                    f"GENERATE_EMAIL_BODY_ONLY:\n"
                    f"Recipient: {to_addr}\nSubject: {subject}\nContext/Instruction: {instructions}\n\n"
                    "Output: Provide only the email body as plain text. No emojis or signature."
                )
                # Offload to thread since generate_response may be blocking I/O or CPU-bound
                email_body = await asyncio.to_thread(
                    generate_response,
                    user_message=ai_prompt,
                    persona_key=user_id,
                    user_ip=user_id,
                )
                email_body = (email_body or "").strip()
                if not email_body:
                    await send_func("❌ AI failed to generate the email body. Try rewording.")
                    return

                created = create_draft(user_id, to_addr, subject, email_body)
                if created and isinstance(created, dict) and created.get("id"):
                    await send_func(f"✅ Draft created. Draft ID: `{created.get('id')}`")
                else:
                    await send_func("❌ Failed to create draft. Check logs.")
            except Exception as e:
                logger.exception("draft failed: %s", e)
                await send_func("❌ Error creating draft. Check logs.")
            return

        # ---------- send ----------
        if subcmd == "send":
            if not args:
                await send_func("Usage: /gmail send <draft_id>")
                return
            draft_id = args[0]
            try:
                ok = send_message_from_draft(user_id, draft_id)
                await send_func("✅ Sent!" if ok else "❌ Failed to send draft. Check logs.")
            except Exception as e:
                logger.exception("send failed: %s", e)
                await send_func("❌ Error sending draft. Check logs.")
            return

        # unknown subcommand
        await send_func("❓ Unknown Gmail subcommand. Use /help to view commands.")
    except Exception as e:
        logger.exception("handle_gmail_command top-level error: %s", e)
        try:
            await send_func("❌ Internal error handling Gmail command. Check server logs.")
        except Exception:
            logger.exception("failed to send error message to user")
