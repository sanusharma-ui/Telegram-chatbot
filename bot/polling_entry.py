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
    disconnect_user,
    _get_gmail_service_for_user  # Added for metadata fetch
)
from backend.gmail_inbox_ops import (
    read_full_email, mark_read, mark_unread, star_messages,
    unstar_messages, archive_messages, delete_messages,
    _get_message  # Internal but using for metadata
)
from backend.gmail_labels import list_labels, create_label, delete_label
from backend.gmail_search import search_messages
from backend.gmail_drafts import update_draft, delete_draft, get_draft
from backend.gmail_send_safe import send_safely, send_draft_by_id
from backend.gmail_threads import fetch_thread, summarize_thread_ai
from backend.gmail_attachments import list_attachments
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

    # ── GMAIL commands (extended with new modules) ─────────────────────────────
    if base_cmd in ("/gmail", "/help"):
        if len(tokens) < 2:
            await message.reply(
                "📧 *Gmail Commands (Updated)*\n\n"
                "/gmail connect\n"
                "/gmail disconnect\n"
                "/gmail inbox [smart]\n"
                "/gmail search <query> → list messages with IDs\n"
                "/gmail read <message_id> → full email\n"
                "/gmail thread <thread_id> → AI summary\n"
                "/gmail mark read|unread|star|archive <id1> <id2>...\n"
                "/gmail delete <message_id> → **IRREVERSIBLE**\n"
                "/gmail labels list\n"
                "/gmail labels create <name>\n"
                "/gmail labels delete <label_id>\n"
                "/gmail draft ... (existing)\n"
                "/gmail send <draft_id>\n",
                parse_mode="Markdown"
            )
            return

        cmd = tokens[1].lower()

        # quick connection guard for most operations (don't block connect/disconnect/help)
        if cmd not in ("connect", "disconnect", "help"):
            # a lightweight connectivity check: gmail_summary returns None if service unavailable
            try:
                conn_check = gmail_summary(user_id, max_results=1)
                if conn_check is None:
                    await message.reply("❌ Gmail not connected. Use `/gmail connect` first.", parse_mode="Markdown")
                    return
            except Exception:
                await message.reply("❌ Gmail not connected. Use `/gmail connect` first.", parse_mode="Markdown")
                return

        # CONNECT
        if cmd == "connect":
            try:
                url = get_auth_url_for_user(user_id, need_send=True)
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔐 Connect Gmail", url=url)]])
                await bot.send_message(chat_id, "Click below to securely connect Gmail:", reply_markup=kb, disable_web_page_preview=True)
            except Exception as e:
                logger.exception("gmail connect error: %s", e)
                await message.reply("❌ Failed to build OAuth link.")
            return

        # DISCONNECT
        if cmd == "disconnect":
            try:
                disconnect_user(user_id)
                await message.reply("✅ Gmail disconnected.")
            except Exception as e:
                logger.exception("gmail disconnect error: %s", e)
                await message.reply("❌ Error disconnecting.")
            return

        # INBOX (smart or normal)
        if cmd == "inbox":
            sub = tokens[2].lower() if len(tokens) > 2 else ""
            try:
                if sub == "smart":
                    summary = gmail_smart_summary(user_id)
                else:
                    summary = gmail_summary(user_id, max_results=10)
                await message.reply(summary or "No emails or not connected.")
            except Exception as e:
                logger.exception("gmail inbox error: %s", e)
                await message.reply("❌ Error fetching inbox.")
            return

        # SEARCH
        if cmd == "search":
            query = " ".join(tokens[2:]) or "in:inbox"
            try:
                results = search_messages(user_id, query, max_results=15)
                if not results:
                    await message.reply("No results or not connected.")
                    return
                lines = ["📬 Search results (copy ID for actions):"]
                svc = _get_gmail_service_for_user(user_id)  # Get service for metadata
                for msg in results:
                    msg_id = msg.get("id")
                    thread_id = msg.get("threadId", "")
                    meta = _get_message(svc, msg_id, format="metadata")  # Use internal _get_message
                    if meta and meta.get("payload"):
                        headers = {h["name"]: h["value"] for h in meta["payload"].get("headers", [])}
                        lines.append(f"ID: `{msg_id}` | Thread: `{thread_id}`\n{headers.get('From','?')} | {headers.get('Subject','(no subject)')}\n")
                    else:
                        lines.append(f"ID: `{msg_id}` | Thread: `{thread_id}`")
                await message.reply("\n".join(lines), parse_mode="Markdown")
            except Exception as e:
                logger.exception("gmail search error: %s", e)
                await message.reply("❌ Error during search.")
            return

        # READ full email
        if cmd == "read":
            if len(tokens) < 3:
                await message.reply("Usage: /gmail read <message_id>")
                return
            msg_id = tokens[2]
            try:
                email = read_full_email(user_id, msg_id)
                if not email:
                    await message.reply("Failed to read or not connected.")
                    return
                text = (
                    f"📧 *From:* {email.get('from')}\n"
                    f"*Subject:* {email.get('subject')}\n"
                    f"*Date:* {email.get('date')}\n\n"
                    f"{(email.get('body') or '')[:3000]}"
                )
                await message.reply(text, parse_mode="Markdown")
            except Exception as e:
                logger.exception("gmail read error: %s", e)
                await message.reply("❌ Error reading message.")
            return

        # THREAD summary (AI)
        if cmd == "thread":
            if len(tokens) < 3:
                await message.reply("Usage: /gmail thread <thread_id>")
                return
            thread_id = tokens[2]
            try:
                summary = summarize_thread_ai(user_id, thread_id)
                if not summary:
                    await message.reply("Failed to summarize or not connected.")
                    return
                await message.reply(f"📝 Thread Summary:\n\n{summary}")
            except Exception as e:
                logger.exception("gmail thread error: %s", e)
                await message.reply("❌ Error summarizing thread.")
            return

        # MARK operations (read/unread/star/archive)
        if cmd == "mark":
            if len(tokens) < 4:
                await message.reply("Usage: /gmail mark read|unread|star|archive <id1> <id2>...")
                return
            action = tokens[2].lower()
            msg_ids = tokens[3:]
            try:
                success = False
                if action == "read":
                    success = mark_read(user_id, msg_ids)
                elif action == "unread":
                    success = mark_unread(user_id, msg_ids)
                elif action == "star":
                    success = star_messages(user_id, msg_ids)
                elif action == "archive":
                    success = archive_messages(user_id, msg_ids)
                await message.reply("✅ Done" if success else "❌ Failed")
            except Exception as e:
                logger.exception("gmail mark error: %s", e)
                await message.reply("❌ Error performing mark operation.")
            return

        # DELETE (permanent)
        if cmd == "delete":
            if len(tokens) < 3:
                await message.reply("Usage: /gmail delete <message_id> → **PERMANENT DELETE**")
                return
            msg_ids = tokens[2:]
            try:
                success = delete_messages(user_id, msg_ids)
                await message.reply("🗑️ Deleted permanently" if success else "❌ Failed")
            except Exception as e:
                logger.exception("gmail delete error: %s", e)
                await message.reply("❌ Error deleting messages.")
            return

        # LABELS CRUD
        if cmd == "labels":
            sub = tokens[2].lower() if len(tokens) > 2 else ""
            try:
                if sub == "list":
                    labels = list_labels(user_id)
                    if not labels:
                        await message.reply("No labels or not connected.")
                        return
                    lines = ["🏷️ Your labels:"]
                    for l in labels:
                        lines.append(f"{l['name']} — ID: `{l['id']}`")
                    await message.reply("\n".join(lines), parse_mode="Markdown")
                    return
                if sub == "create" and len(tokens) > 3:
                    name = " ".join(tokens[3:])
                    label = create_label(user_id, name)
                    await message.reply(f"✅ Created: {label['name']}" if label else "❌ Failed")
                    return
                if sub == "delete" and len(tokens) > 3:
                    label_id = tokens[3]
                    success = delete_label(user_id, label_id)
                    await message.reply("✅ Deleted" if success else "❌ Failed")
                    return
            except Exception as e:
                logger.exception("gmail labels error: %s", e)
                await message.reply("❌ Error with labels command.")
                return

        # DRAFT (use existing logic you already have for generation & create_draft)
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

        # SEND (send draft by id)
        if cmd == "send":
            draft_id = tokens[2] if len(tokens) > 2 else ""
            if draft_id:
                try:
                    success = send_draft_by_id(user_id, draft_id)
                    await message.reply("✅ Sent!" if success else "❌ Failed")
                except Exception as e:
                    logger.exception("gmail send error: %s", e)
                    await message.reply("❌ Error sending draft.")
            return

        # fallback
        await message.reply("Unknown subcommand. Use /gmail for help.")
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