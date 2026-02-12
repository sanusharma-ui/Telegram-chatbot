# backend/gmail_attachments.py
import base64
import io
import logging
from typing import List, Optional, Dict
from googleapiclient.errors import HttpError
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

def list_attachments(user_id: str, message_id: str) -> Optional[List[Dict]]:
    """
    Returns a list of attachments found in the message with fields:
    { filename, mimeType, size, attachmentId, partId }
    """
    from backend.gmail_integration import _get_gmail_service_for_user
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None

    try:
        msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        parts = msg.get("payload", {}).get("parts", []) or []
        attachments = []
        def walk(parts_list):
            for p in parts_list:
                filename = p.get("filename")
                body = p.get("body", {})
                if filename and body.get("attachmentId"):
                    attachments.append({
                        "filename": filename,
                        "mimeType": p.get("mimeType"),
                        "size": body.get("size", 0),
                        "attachmentId": body.get("attachmentId"),
                        "partId": p.get("partId")
                    })
                if p.get("parts"):
                    walk(p.get("parts"))
        walk(parts)
        return attachments
    except HttpError as e:
        logger.exception("list_attachments failed: %s", e)
        return None

def download_attachment(user_id: str, message_id: str, attachment_id: str) -> Optional[bytes]:
    """
    Downloads the attachment content bytes for given attachmentId.
    """
    from backend.gmail_integration import _get_gmail_service_for_user
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None
    try:
        att = svc.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()
        data = att.get("data")
        if not data:
            return None
        return base64.urlsafe_b64decode(data)
    except HttpError as e:
        logger.exception("download_attachment failed: %s", e)
        return None

def attach_file_to_draft(user_id: str, draft_id: str, filename: str, file_bytes: bytes, mime_type: str = "application/octet-stream") -> Optional[Dict]:
    """
    Attach a file to an existing draft by updating the draft's raw message.
    NOTE: This replaces the draft content — we fetch existing draft, build a multipart message merging old body + attachments.
    """
    from backend.gmail_integration import _get_gmail_service_for_user
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None
    try:
        # Fetch existing draft (raw)
        draft = svc.users().drafts().get(userId="me", id=draft_id, format="full").execute()
        msg = draft.get("message", {})
        # Decode existing raw if present
        raw = msg.get("raw")
        existing_body_text = ""
        if raw:
            decoded = base64.urlsafe_b64decode(raw.encode())
            # Try naive parse to extract text/plain (best-effort)
            try:
                from email import message_from_bytes
                parsed = message_from_bytes(decoded)
                if parsed.is_multipart():
                    for part in parsed.walk():
                        if part.get_content_type() == "text/plain" and not part.get_filename():
                            existing_body_text = part.get_payload(decode=True).decode(errors="ignore")
                            break
                else:
                    existing_body_text = parsed.get_payload(decode=True).decode(errors="ignore")
            except Exception:
                existing_body_text = ""
        # Build multipart message
        outer = MIMEMultipart()
        outer["To"] = msg.get("payload", {}).get("headers", [{}])[0].get("value", "") if msg.get("payload") else ""
        outer["Subject"] = next((h["value"] for h in msg.get("payload", {}).get("headers", []) if h["name"].lower()=="subject"), "")
        # attach text
        outer.attach(MIMEText(existing_body_text or "", "plain", "utf-8"))
        # attach file
        part = MIMEBase(*mime_type.split("/", 1)) if "/" in mime_type else MIMEBase("application", "octet-stream")
        part.set_payload(file_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        outer.attach(part)
        # Encode raw
        raw_new = base64.urlsafe_b64encode(outer.as_bytes()).decode()
        body = {"id": draft_id, "message": {"raw": raw_new}}
        updated = svc.users().drafts().update(userId="me", id=draft_id, body=body).execute()
        return updated
    except HttpError as e:
        logger.exception("attach_file_to_draft failed: %s", e)
        return None
    except Exception as e:
        logger.exception("attach_file_to_draft general error: %s", e)
        return None
