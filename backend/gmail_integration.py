# backend/gmail_integration.py

import os
import re
import json
import time
import base64
import logging
import secrets
import hashlib
from typing import Optional, Dict, List

from dotenv import load_dotenv
from cryptography.fernet import Fernet, InvalidToken
from email.mime.text import MIMEText

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request

load_dotenv()
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# ENV / CONFIG
# ─────────────────────────────────────────────
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
OAUTH_REDIRECT = os.getenv("OAUTH_REDIRECT_URI") or os.getenv("GOOGLE_REDIRECT_URI")
TOKEN_KEY = os.getenv("TOKEN_ENC_KEY")
REDIS_URL = os.getenv("REDIS_URL")

SCOPES_READ = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

SCOPES_DRAFT = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]

STATE_TTL_SECONDS = 600  # 10 minutes

# ─────────────────────────────────────────────
# REDIS (optional)
# ─────────────────────────────────────────────
use_redis = False
r = None

try:
    import redis  # type: ignore

    if REDIS_URL:
        r = redis.from_url(REDIS_URL, decode_responses=False)
        r.ping()
        use_redis = True
        logger.info("Using Redis for Gmail OAuth/token storage")
except Exception:
    r = None
    use_redis = False
    logger.warning("Redis unavailable; falling back to filesystem storage")

# ─────────────────────────────────────────────
# ENCRYPTION
# ─────────────────────────────────────────────
fernet = None

if TOKEN_KEY:
    try:
        key_bytes = TOKEN_KEY.encode() if isinstance(TOKEN_KEY, str) else TOKEN_KEY
        fernet = Fernet(key_bytes)
    except Exception as e:
        logger.exception("Invalid TOKEN_ENC_KEY: %s", e)
        fernet = None
else:
    logger.warning("TOKEN_ENC_KEY not set")


def _encrypt(obj: Dict) -> bytes:
    if not fernet:
        raise RuntimeError("Encryption key missing or invalid")
    return fernet.encrypt(json.dumps(obj).encode("utf-8"))


def _decrypt(token_bytes: bytes) -> Dict:
    if not fernet:
        raise RuntimeError("Encryption key missing or invalid")
    try:
        raw = fernet.decrypt(token_bytes)
        return json.loads(raw.decode("utf-8"))
    except InvalidToken:
        logger.exception("Invalid encrypted token data")
        return {}


# ─────────────────────────────────────────────
# FILESYSTEM PATHS
# ─────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
TOKENS_DIR = os.path.join(BASE_DIR, "gmail_tokens")
STATES_DIR = os.path.join(BASE_DIR, "gmail_states")

os.makedirs(TOKENS_DIR, exist_ok=True)
os.makedirs(STATES_DIR, exist_ok=True)

# ─────────────────────────────────────────────
# TOKEN STORAGE
# ─────────────────────────────────────────────
def store_tokens_for_user(user_id: str, token_obj: Dict) -> None:
    enc = _encrypt(token_obj)
    key = f"gmail:tokens:{user_id}"

    if use_redis and r:
        r.set(key, enc)
        return

    path = os.path.join(TOKENS_DIR, f"{user_id}.token")
    with open(path, "wb") as f:
        f.write(enc)


def load_tokens_for_user(user_id: str) -> Optional[Dict]:
    key = f"gmail:tokens:{user_id}"

    try:
        if use_redis and r:
            enc = r.get(key)
            if not enc:
                return None
            return _decrypt(enc)

        path = os.path.join(TOKENS_DIR, f"{user_id}.token")
        if not os.path.exists(path):
            return None

        with open(path, "rb") as f:
            enc = f.read()
        return _decrypt(enc)

    except Exception as e:
        logger.exception("load_tokens_for_user failed: %s", e)
        return None


def disconnect_user(user_id: str) -> None:
    key = f"gmail:tokens:{user_id}"

    if use_redis and r:
        try:
            r.delete(key)
        except Exception:
            logger.exception("Failed deleting Redis token for user=%s", user_id)
        return

    path = os.path.join(TOKENS_DIR, f"{user_id}.token")
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            logger.exception("Failed deleting token file for user=%s", user_id)


# ─────────────────────────────────────────────
# OAUTH STATE STORAGE
# ─────────────────────────────────────────────
def _save_state_blob(state: str, payload: Dict) -> None:
    wrapped = {
        "created_at": int(time.time()),
        "payload": payload,
    }

    if use_redis and r:
        r.set(
            f"gmail:state:{state}",
            json.dumps(wrapped).encode("utf-8"),
            ex=STATE_TTL_SECONDS,
        )
        return

    path = os.path.join(STATES_DIR, state)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(wrapped, f)


def _load_state_blob(state: str) -> Optional[Dict]:
    now = int(time.time())

    if use_redis and r:
        raw = r.get(f"gmail:state:{state}")
        if not raw:
            return None

        try:
            wrapped = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            return wrapped.get("payload")
        finally:
            try:
                r.delete(f"gmail:state:{state}")
            except Exception:
                logger.exception("Failed deleting oauth state from Redis")

    path = os.path.join(STATES_DIR, state)
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            wrapped = json.load(f)
    except Exception:
        logger.exception("Failed reading oauth state file")
        return None
    finally:
        try:
            os.remove(path)
        except Exception:
            pass

    created_at = wrapped.get("created_at", 0)
    if now - created_at > STATE_TTL_SECONDS:
        logger.warning("OAuth state expired on filesystem: %s", state)
        return None

    return wrapped.get("payload")


# ─────────────────────────────────────────────
# PKCE HELPERS
# ─────────────────────────────────────────────
def _generate_pkce_pair():
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")
    return code_verifier, code_challenge


# ─────────────────────────────────────────────
# FLOW BUILDER
# ─────────────────────────────────────────────
def build_flow(
    state: Optional[str] = None,
    scopes: Optional[List[str]] = None,
) -> Flow:
    if not CLIENT_ID or not CLIENT_SECRET or not OAUTH_REDIRECT:
        raise RuntimeError(
            "Missing Google OAuth configuration. "
            f"CLIENT_ID set={bool(CLIENT_ID)}, "
            f"CLIENT_SECRET set={bool(CLIENT_SECRET)}, "
            f"OAUTH_REDIRECT set={bool(OAUTH_REDIRECT)} value={OAUTH_REDIRECT}"
        )

    client_config = {
        "web": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [OAUTH_REDIRECT],
        }
    }

    flow = Flow.from_client_config(
        client_config=client_config,
        scopes=scopes or SCOPES_READ,
        state=state,
        redirect_uri=OAUTH_REDIRECT,
    )
    return flow


# ─────────────────────────────────────────────
# PUBLIC: GET AUTH URL
# ─────────────────────────────────────────────
def get_auth_url_for_user(user_id: str, need_send: bool = False) -> str:
    scopes = list(SCOPES_READ)
    if need_send:
        scopes.extend(SCOPES_DRAFT)

    flow = build_flow(scopes=scopes)

    code_verifier, code_challenge = _generate_pkce_pair()
    flow.code_verifier = code_verifier

    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )

    _save_state_blob(state, {
        "user_id": user_id,
        "scopes": scopes,
        "code_verifier": code_verifier,
    })

    logger.info(
        "Generated Gmail OAuth URL user=%s state=%s redirect=%s pkce=True",
        user_id,
        state,
        OAUTH_REDIRECT,
    )
    return auth_url


# ─────────────────────────────────────────────
# PUBLIC: HANDLE OAUTH CALLBACK
# ─────────────────────────────────────────────
def handle_oauth_callback(
    state: str,
    code: str,
    full_callback_url: Optional[str] = None,
) -> Optional[str]:
    blob = _load_state_blob(state)
    if not blob:
        logger.warning("State missing or expired for state=%s", state)
        return None

    user_id = blob.get("user_id")
    scopes = blob.get("scopes", SCOPES_READ)
    code_verifier = blob.get("code_verifier")

    try:
        flow = build_flow(state=state, scopes=scopes)

        if code_verifier:
            flow.code_verifier = code_verifier

        if full_callback_url:
            flow.fetch_token(
                authorization_response=full_callback_url,
                code_verifier=code_verifier,
            )
        else:
            flow.fetch_token(
                code=code,
                code_verifier=code_verifier,
            )

        creds = flow.credentials
        token_obj = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else scopes,
        }

        store_tokens_for_user(user_id, token_obj)
        logger.info("Stored Gmail tokens for user=%s", user_id)
        return user_id

    except Exception as e:
        logger.exception("handle_oauth_callback failed: %s", e)
        return None


# ─────────────────────────────────────────────
# GMAIL SERVICE HELPER
# ─────────────────────────────────────────────
def _get_gmail_service_for_user(user_id: str):
    tok = load_tokens_for_user(user_id)
    if not tok:
        return None

    creds = Credentials(
        token=tok.get("token"),
        refresh_token=tok.get("refresh_token"),
        token_uri=tok.get("token_uri"),
        client_id=tok.get("client_id"),
        client_secret=tok.get("client_secret"),
        scopes=tok.get("scopes"),
    )

    try:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            store_tokens_for_user(user_id, {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes,
            })
    except Exception:
        logger.exception("Gmail token refresh failed")

    try:
        return build("gmail", "v1", credentials=creds)
    except Exception:
        logger.exception("Failed to build Gmail service")
        return None


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def classify_email(subject: str, sender: str) -> str:
    text = f"{subject} {sender}".lower()

    if any(x in text for x in ["invoice", "bill", "payment", "receipt"]):
        return "💳 Bills"
    if any(x in text for x in ["hr", "interview", "career", "recruit"]):
        return "📄 HR"
    if any(x in text for x in ["otp", "verification", "code"]):
        return "🔐 OTP"
    return "👤 Personal"


def gmail_summary(user_id: str, max_results: int = 5) -> Optional[str]:
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None

    try:
        res = svc.users().messages().list(
            userId="me",
            maxResults=max_results,
            q="in:inbox -category:promotions",
        ).execute()

        items = res.get("messages", []) or []
        if not items:
            return "No recent emails found."

        lines = []
        for item in items:
            msg = svc.users().messages().get(
                userId="me",
                id=item["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()

            headers = {
                h["name"]: h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }

            sender = headers.get("From", "?")
            subject = headers.get("Subject", "(no subject)")
            date = headers.get("Date", "")
            lines.append(f"{sender} | {subject} | {date}")

        return "\n".join(lines)

    except HttpError as e:
        logger.exception("gmail_summary failed: %s", e)
        return None


def gmail_smart_summary(user_id: str, max_results: int = 10) -> Optional[str]:
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None

    try:
        res = svc.users().messages().list(
            userId="me",
            maxResults=max_results,
            q="in:inbox",
        ).execute()

        categories: Dict[str, int] = {}

        for item in res.get("messages", []):
            msg = svc.users().messages().get(
                userId="me",
                id=item["id"],
                format="metadata",
                metadataHeaders=["From", "Subject"],
            ).execute()

            headers = {
                h["name"]: h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }

            category = classify_email(
                headers.get("Subject", ""),
                headers.get("From", "")
            )
            categories[category] = categories.get(category, 0) + 1

        if not categories:
            return "No emails found."

        return "\n".join(f"{k}: {v}" for k, v in categories.items())

    except HttpError as e:
        logger.exception("gmail_smart_summary failed: %s", e)
        return None


# ─────────────────────────────────────────────
# DRAFT / SEND HELPERS
# ─────────────────────────────────────────────
def create_draft(
    user_id: str,
    to: str,
    subject: str,
    body: str,
) -> Optional[Dict]:
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return None

    email_re = r"^[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9.\-]+$"
    if not re.match(email_re, to.strip()):
        logger.error("Invalid email address in create_draft: %s", to)
        return None

    try:
        message = MIMEText(body, "plain", "utf-8")
        message["to"] = to
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        draft_body = {"message": {"raw": raw}}

        return svc.users().drafts().create(
            userId="me",
            body=draft_body
        ).execute()

    except HttpError as e:
        logger.exception("create_draft failed: %s", e)
        return None


def send_message_from_draft(user_id: str, draft_id: str) -> bool:
    svc = _get_gmail_service_for_user(user_id)
    if not svc:
        return False

    try:
        svc.users().drafts().send(
            userId="me",
            body={"id": draft_id},
        ).execute()
        return True

    except HttpError as e:
        logger.exception("send_message_from_draft failed: %s", e)
        return False