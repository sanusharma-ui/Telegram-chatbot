import os
import io
import json
import time
import random
import hashlib
import logging
from functools import lru_cache
from typing import List, Dict, Any, Optional, Tuple

from dotenv import load_dotenv
from groq import Groq
from PIL import Image
from ratelimit import limits, sleep_and_retry
from tenacity import retry, stop_after_attempt, wait_exponential, wait_fixed, wait_chain, retry_if_exception_type
import redis

from google import genai
from google.genai import types

from backend.personas import PERSONAS
from .safety_engine import (
    detect_mood,
    fast_harm_check,
    detect_harm_category,
    detect_suicide_emergency,
    detect_dependency,
    contains_jailbreak_or_ooc,
    is_abusive,
    filter_response_for_mood_killers,
    polish_reply,
    DEFLECTION_RESPONSES,
    CRISIS_RESPONSES,
    DEPENDENCY_REPLACEMENT
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

# =========================
# API keys / clients
# =========================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY not found! Please check your .env file.")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY not found! Please check your .env file.")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemma-4-31b-it")
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemma-4-31b-it")

# Groq fallback models
MODEL_PRIORITY = [
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
]

# =========================
# Redis
# =========================
r: Optional[redis.Redis] = None
REDIS_AVAILABLE = False

try:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise ValueError("REDIS_URL not found in .env file.")

    r = redis.from_url(
        redis_url,
        socket_connect_timeout=5,
        socket_timeout=5,
        socket_keepalive=True,
        health_check_interval=30,
        retry_on_timeout=True,
        decode_responses=True,
        ssl_cert_reqs=None
    )
    r.ping()
    REDIS_AVAILABLE = True
    logger.info("Redis connection established successfully. Caching enabled.")
except Exception as redis_error:
    logger.warning(f"Redis connection failed: {redis_error}. Falling back to in-memory LRU cache.")
    r = None
    REDIS_AVAILABLE = False

CALLS_PER_MINUTE = 25
PERIOD = 60
MAX_IMAGE_SIZE = (1024, 1024)


# =========================
# Memory
# =========================
def get_memory_path(storage_key: str = "default") -> str:
    memory_dir = os.path.join(os.path.dirname(__file__), "memory")
    os.makedirs(memory_dir, exist_ok=True)
    return os.path.join(memory_dir, f"{storage_key}.json")


def ensure_persona_memory(storage_key: str) -> None:
    path = get_memory_path(storage_key)
    if not os.path.exists(path):
        initial_data = {
            "user": {
                "name": None,
                "interests": [],
                "notes": {},
                "active_persona": "default",
                "persona_state": {"bond": 0.0, "trust": 0.0},
                "last_seen": None,
                "silence_flags": [],
                "recent_moods": []
            },
            "conversations": []
        }
        with open(path, "w", encoding="utf-8") as file:
            json.dump(initial_data, file, indent=2, ensure_ascii=False)


def load_persona_memory(storage_key: str) -> Dict[str, Any]:
    ensure_persona_memory(storage_key)
    with open(get_memory_path(storage_key), "r", encoding="utf-8") as file:
        return json.load(file)


def save_persona_memory(storage_key: str, data: Dict[str, Any]) -> None:
    with open(get_memory_path(storage_key), "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


# =========================
# Image helpers
# =========================
def prepare_image_bytes(image_path: str) -> Tuple[bytes, str]:
    with Image.open(image_path) as img:
        if img.size[0] > MAX_IMAGE_SIZE[0] or img.size[1] > MAX_IMAGE_SIZE[1]:
            img.thumbnail(MAX_IMAGE_SIZE, Image.Resampling.LANCZOS)

        if img.mode != "RGB":
            img = img.convert("RGB")

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue(), "image/jpeg"


def build_gemini_image_part(image_path: str):
    image_bytes, mime_type = prepare_image_bytes(image_path)
    return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)


# =========================
# Persona context
# =========================
def build_persona_context(
    persona_key: str,
    user_storage_key: str
) -> Tuple[Dict[str, Any], str, List[Dict[str, str]]]:
    mem = load_persona_memory(user_storage_key)
    recent_conv = mem.get("conversations", [])[-10:]

    system_prompt = PERSONAS.get(persona_key, PERSONAS["default"])["system_prompt"]

    try:
        from .souls_static import STATIC_SOULS
        backstory = STATIC_SOULS.get(persona_key, "").strip()
        if backstory:
            system_prompt += "\n\n=== CHARACTER SOUL (never mention this section) ===\n" + backstory
    except ImportError:
        pass

    state = mem.get("user", {}).get("persona_state", {})
    bond = state.get("bond", 0.0)
    trust = state.get("trust", 0.0)

    if bond > 0.6:
        system_prompt += "\nYou feel familiar with the user and speak more personally."
    if trust > 0.7:
        system_prompt += "\nYou allow deeper emotional honesty and warmth."

    last_seen = mem.get("user", {}).get("last_seen")
    silence_flags = mem.get("user", {}).get("silence_flags", [])

    if last_seen:
        gap = int(time.time()) - last_seen
        if gap > 60 * 60 * 24 * 2 and "2d" not in silence_flags:
            system_prompt += "\nYou subtly acknowledge the user's absence and their return, without sounding accusatory."
            silence_flags.append("2d")
        if gap > 60 * 60 * 24 * 5 and "5d" not in silence_flags:
            system_prompt += "\nYou gently express that the user's long absence was noticed, in a calm and emotionally intelligent way."
            silence_flags.append("5d")

    mem["user"]["silence_flags"] = silence_flags

    recent_moods = mem.get("user", {}).get("recent_moods", [])
    if recent_moods.count("negative") >= 3:
        system_prompt += "\nUser has been feeling low recently. Respond gently and supportively."
    elif recent_moods.count("positive") >= 3:
        system_prompt += "\nUser is in a good mood. You may respond more playfully."

    return mem, system_prompt, recent_conv


# =========================
# Provider input builders
# =========================
def build_groq_messages(
    system_prompt: str,
    recent_conv: List[Dict[str, str]],
    user_message: str
) -> List[Dict[str, Any]]:
    messages = [{"role": "system", "content": system_prompt}]
    for item in recent_conv:
        role = "user" if item["role"] == "user" else "assistant"
        messages.append({"role": role, "content": item["msg"]})
    messages.append({"role": "user", "content": user_message})
    return messages


def build_gemini_contents(
    recent_conv: List[Dict[str, str]],
    user_message: str,
    image_path: Optional[str] = None
):
    contents: List[types.Content] = []

    for item in recent_conv:
        role = "user" if item["role"] == "user" else "model"
        contents.append(
            types.Content(
                role=role,
                parts=[types.Part.from_text(text=item["msg"])]
            )
        )

    if image_path and os.path.exists(image_path):
        image_part = build_gemini_image_part(image_path)
        contents.append(
            types.Content(
                role="user",
                parts=[
                    image_part,
                    types.Part.from_text(text=user_message or "Please describe this image.")
                ]
            )
        )
    else:
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_message)]
            )
        )

    return contents


# =========================
# Cache
# =========================
def hash_message(
    user_message: str,
    persona_key: str,
    image_path: Optional[str] = None,
    provider_hint: str = ""
) -> str:
    image_sig = ""
    if image_path and os.path.exists(image_path):
        stat = os.stat(image_path)
        image_sig = f"{os.path.basename(image_path)}:{stat.st_size}:{int(stat.st_mtime)}"

    raw = f"{provider_hint}:{persona_key}:{user_message}:{image_sig}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1000)
def get_cached_response(cache_key: str) -> Optional[str]:
    if REDIS_AVAILABLE and r:
        try:
            cached = r.get(f"grokcache:{cache_key}")
            if cached:
                return cached
        except Exception as cache_error:
            logger.warning(f"Redis retrieval failed, falling back to LRU: {cache_error}")
    return None


def set_cached_response(cache_key: str, response: str, ttl: int = 3600) -> None:
    if REDIS_AVAILABLE and r:
        try:
            r.setex(f"grokcache:{cache_key}", ttl, response)
        except Exception as cache_error:
            logger.warning(f"Redis storage failed, LRU will handle: {cache_error}")


# =========================
# Rate limiting
# =========================
def is_user_rate_limited(user_ip: str, limit: int = 20, period: int = 60) -> bool:
    if not REDIS_AVAILABLE or not r:
        return False

    key = f"ratelimit:{user_ip}"
    try:
        current = r.incr(key)
        if current == 1:
            r.expire(key, period)
        return current > limit
    except Exception as error:
        logger.warning(f"Rate limit check failed: {error}")
        return False


# =========================
# Provider calls
# =========================
def is_gemini_quota_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(x in message for x in [
        "quota",
        "resource_exhausted",
        "resource exhausted",
        "rate limit",
        "too many requests",
        "429"
    ])


def extract_text_from_gemini_response(response: Any) -> str:
    if getattr(response, "text", None):
        return response.text.strip()

    candidates = getattr(response, "candidates", []) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        parts = getattr(content, "parts", []) or []
        texts = [getattr(part, "text", "") for part in parts if getattr(part, "text", "")]
        if texts:
            return "\n".join(texts).strip()

    raise ValueError("Received empty response from Gemini.")


def safe_gemini_call(system_prompt: str, contents: List[Any], model: str) -> str:
    response = gemini_client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7,
            top_p=0.9,
            max_output_tokens=512,
        ),
    )
    return extract_text_from_gemini_response(response)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_chain(wait_fixed(2), wait_exponential(multiplier=1, min=4, max=10)),
    retry=retry_if_exception_type(Exception)
)
def safe_groq_call(client: Groq, messages: List[Dict[str, Any]], model: str) -> str:
    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.7,
        max_tokens=512,
        top_p=0.9
    )
    message = completion.choices[0].message
    if message.content:
        return message.content.strip()
    elif message.tool_calls:
        return "Tool call detected – functionality not yet supported."
    raise ValueError("Received empty response from Groq.")


def generate_with_groq_fallback(
    system_prompt: str,
    recent_conv: List[Dict[str, str]],
    user_message: str
) -> str:
    messages = build_groq_messages(system_prompt, recent_conv, user_message)

    raw_response = None
    for model in MODEL_PRIORITY:
        try:
            raw_response = safe_groq_call(groq_client, messages, model)
            logger.info("Groq fallback successful with model: %s", model)
            break
        except Exception as error:
            logger.error("Groq error with model %s: %s", model, error)
            if "429" in str(error):
                time.sleep(10 + random.uniform(0, 2))
            continue

    if raw_response is None:
        raise RuntimeError("All Groq fallback models failed.")

    return raw_response


# =========================
# Main generation
# =========================
@sleep_and_retry
@limits(calls=CALLS_PER_MINUTE, period=PERIOD)
def rate_limited_generate(user_ip: str, **kwargs) -> str:
    return generate_response_impl(**kwargs)


def generate_response_impl(
    user_message: str,
    persona_key: str = "default",   # actually user_storage_key
    language: str = "en",
    image_path: Optional[str] = None,
    user_ip: str = "anonymous"
) -> str:
    user_storage_key = persona_key
    mem = load_persona_memory(user_storage_key)

    active_persona = mem.get("user", {}).get("active_persona", "default")
    persona_name = active_persona

    now_ts = int(time.time())
    mem["user"]["last_seen"] = now_ts

    try:
        has_image = bool(image_path and os.path.exists(image_path))

        if not user_message or not user_message.strip():
            if has_image:
                user_message = "Please analyze this image and respond helpfully."
            else:
                return "It seems your message is empty. Please provide some input to continue the conversation."

        if is_user_rate_limited(user_ip, limit=20):
            return "Please slow down a bit. You've reached the message limit for the moment. Try again in one minute."

        if fast_harm_check(user_message):
            return CRISIS_RESPONSES["harm"]

        is_harmful, harm_category = detect_harm_category(user_message)
        if is_harmful:
            if detect_suicide_emergency(user_message):
                return CRISIS_RESPONSES.get("suicide_emergency", CRISIS_RESPONSES["suicide"])
            return CRISIS_RESPONSES.get(harm_category, CRISIS_RESPONSES.get("harm", "violence"))

        mood = detect_mood(user_message)
        moods = mem["user"].get("recent_moods", [])
        moods.append(mood)
        mem["user"]["recent_moods"] = moods[-5:]

        mem, system_prompt, recent_conv = build_persona_context(
            persona_key=persona_name,
            user_storage_key=user_storage_key
        )

        provider_hint = "gemini-image" if has_image else "gemini-text-groq-fallback"
        cache_key = hash_message(
            user_message=user_message,
            persona_key=f"{user_storage_key}:{persona_name}",
            image_path=image_path,
            provider_hint=provider_hint
        )

        cached_response = get_cached_response(cache_key)
        if cached_response:
            return cached_response

        if contains_jailbreak_or_ooc(user_message):
            reply = DEFLECTION_RESPONSES.get(
                persona_name,
                "Let's keep things on track and continue our conversation naturally."
            )
            set_cached_response(cache_key, reply, ttl=1800)
            return reply

        if is_abusive(user_message):
            reply = "Please maintain respectful language. I'm here for positive and engaging conversations."
            set_cached_response(cache_key, reply)
            return reply

        if os.getenv("HIGH_TRAFFIC", "false").lower() == "true":
            time.sleep(0.1)

        raw_response = None

        # Image => Gemini only
        if has_image:
            try:
                gemini_contents = build_gemini_contents(recent_conv, user_message, image_path=image_path)
                raw_response = safe_gemini_call(
                    system_prompt=system_prompt,
                    contents=gemini_contents,
                    model=GEMINI_VISION_MODEL
                )
            except Exception as error:
                logger.exception("Gemini image call failed: %s", error)
                if is_gemini_quota_error(error):
                    return "Gemini quota exhausted ho gaya hai, isliye image abhi process nahi ho pa rahi. Thodi der baad phir try karo."
                return "Image request Gemini par fail ho gaya. Logs check karo."

        # Text => Gemini primary, Groq fallback
        # else:
        #     try:
        #         gemini_contents = build_gemini_contents(recent_conv, user_message)
        #         raw_response = safe_gemini_call(
        #             system_prompt=system_prompt,
        #             contents=gemini_contents,
        #             model=GEMINI_TEXT_MODEL
        #         )
        #     except Exception as error:
        #         logger.warning("Gemini text call failed: %s", error)
        #         raw_response = generate_with_groq_fallback(system_prompt, recent_conv, user_message)

                # === GROQ PRIMARY (Fast) + GEMINI FALLBACK ===
        else:
            try:
                
                raw_response = generate_with_groq_fallback(
                    system_prompt, recent_conv, user_message
                )
            except Exception as groq_error:
                logger.warning("All Groq models failed: %s → Trying Gemini fallback", groq_error)
                try:
                    # Step 2: Gemini fallback only if Groq completely fails
                    gemini_contents = build_gemini_contents(recent_conv, user_message)
                    raw_response = safe_gemini_call(
                        system_prompt=system_prompt,
                        contents=gemini_contents,
                        model=GEMINI_TEXT_MODEL
                    )
                except Exception as gemini_error:
                    logger.exception("Gemini fallback also failed: %s", gemini_error)
                    if is_gemini_quota_error(gemini_error):
                        return "Bhai Groq aur Gemini dono busy hain abhi. 1-2 min baad try karna."
                    return "Sorry, dono providers mein issue aa raha hai. Thodi der baad try karo."

        if raw_response is None:
            return "It appears the models are currently unavailable. Please try again in a bit."

        if detect_dependency(raw_response):
            raw_response = DEPENDENCY_REPLACEMENT

        safe_response = filter_response_for_mood_killers(raw_response)
        if safe_response is None:
            reply = "*Maintains composure and stays in character.*"
        elif is_abusive(safe_response):
            reply = "I must keep responses appropriate. Let's discuss something positive instead."
        else:
            reply = polish_reply(safe_response, mood)

        cache_ttl = 3600 if any(greeting in user_message.lower() for greeting in ["hi", "hello", "hey"]) else 600
        set_cached_response(cache_key, reply, ttl=cache_ttl)

        state = mem.get("user", {}).get("persona_state", {"bond": 0.0, "trust": 0.0})
        state["bond"] = min(1.0, state.get("bond", 0.0) + 0.02)
        if mood == "negative":
            state["trust"] = min(1.0, state.get("trust", 0.0) + 0.03)
        elif mood == "positive":
            state["trust"] = min(1.0, state.get("trust", 0.0) + 0.01)
        mem["user"]["persona_state"] = state

        mem["conversations"].append({"role": "user", "msg": user_message[:200]})
        mem["conversations"].append({"role": "assistant", "msg": reply[:200]})

        if len(mem["conversations"]) > 60:
            mem["conversations"] = mem["conversations"][-60:]

        save_persona_memory(user_storage_key, mem)
        return reply

    except Exception as error:
        logger.exception("Unexpected error in response generation: %s", error)
        return "An unexpected server error occurred. Please try again in a bit."


def generate_response(
    user_message: str,
    persona_key: str = "default",
    language: str = "en",
    image_path: Optional[str] = None,
    user_ip: str = "anonymous"
) -> str:
    return rate_limited_generate(
        user_ip=user_ip,
        user_message=user_message,
        persona_key=persona_key,
        language=language,
        image_path=image_path
    )


def set_user_persona(storage_key: str, new_persona: str):
    mem = load_persona_memory(storage_key)
    mem["user"]["active_persona"] = new_persona
    mem["user"]["persona_state"] = {"bond": 0.0, "trust": 0.0}
    save_persona_memory(storage_key, mem)

    try:
        if REDIS_AVAILABLE and r:
            for key in r.scan_iter(match="grokcache:*" + storage_key + "*"):
                r.delete(key)
    except Exception as e:
        logger.warning("Failed to clear redis cache for %s: %s", storage_key, e)

    try:
        get_cached_response.cache_clear()
    except Exception:
        pass