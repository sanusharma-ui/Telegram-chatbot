import os
import uuid
import tempfile
from typing import Any, Dict, Optional, Tuple

from aiogram import Bot

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")


def _safe_ext(name: Optional[str], fallback: str = ".jpg") -> str:
    if not name:
        return fallback
    ext = os.path.splitext(name)[1].lower()
    return ext if ext else fallback


def _is_image_document(file_name: Optional[str], mime_type: Optional[str]) -> bool:
    name = (file_name or "").lower()
    mime = (mime_type or "").lower()
    return mime.startswith("image/") or name.endswith(IMAGE_EXTENSIONS)


async def download_telegram_file(bot: Bot, file_id: str, original_name: Optional[str] = None) -> str:
    tg_file = await bot.get_file(file_id)
    suffix = _safe_ext(original_name or getattr(tg_file, "file_path", None))
    temp_path = os.path.join(tempfile.gettempdir(), f"tg_{uuid.uuid4().hex}{suffix}")
    await bot.download_file(tg_file.file_path, destination=temp_path)
    return temp_path


def extract_text_from_aiogram_message(message) -> str:
    return ((getattr(message, "text", None) or getattr(message, "caption", None) or "")).strip()


async def extract_image_from_aiogram_message(bot: Bot, message) -> Tuple[Optional[str], bool]:
    if getattr(message, "photo", None):
        biggest = message.photo[-1]
        path = await download_telegram_file(bot, biggest.file_id, "photo.jpg")
        return path, True

    document = getattr(message, "document", None)
    if document and _is_image_document(getattr(document, "file_name", None), getattr(document, "mime_type", None)):
        path = await download_telegram_file(bot, document.file_id, getattr(document, "file_name", None))
        return path, True

    return None, False


def extract_text_from_update_dict(message: Dict[str, Any]) -> str:
    return ((message.get("text") or message.get("caption") or "")).strip()


async def extract_image_from_update_dict(bot: Bot, message: Dict[str, Any]) -> Tuple[Optional[str], bool]:
    photos = message.get("photo") or []
    if photos:
        biggest = photos[-1]
        file_id = biggest.get("file_id")
        if file_id:
            path = await download_telegram_file(bot, file_id, "photo.jpg")
            return path, True

    document = message.get("document") or {}
    if document:
        file_name = document.get("file_name")
        mime_type = document.get("mime_type")
        if _is_image_document(file_name, mime_type):
            file_id = document.get("file_id")
            if file_id:
                path = await download_telegram_file(bot, file_id, file_name)
                return path, True

    return None, False