"""מיקי - AI conversation logic using Gemini."""

import logging

import google.generativeai as genai

from config import settings
from database import get_history, save_message

logger = logging.getLogger("miki.agent")

genai.configure(api_key=settings.GEMINI_API_KEY)

_model = genai.GenerativeModel(
    model_name=settings.GEMINI_MODEL,
    system_instruction=settings.SYSTEM_PROMPT,
)


def _to_gemini_contents(history: list[dict], new_user_message: str) -> list[dict]:
    """Convert OpenAI-style history to Gemini contents format.

    Gemini uses 'user' and 'model' roles (not 'assistant').
    """
    contents = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    contents.append({"role": "user", "parts": [{"text": new_user_message}]})
    return contents


def _extract_text(response) -> str:
    """Extract text from a Gemini response, handling multi-part output.

    Gemini 2.5 'thinking' mode returns multiple parts — we concatenate
    all text parts and skip non-text parts.
    """
    try:
        candidate = response.candidates[0]
        parts = candidate.content.parts
        chunks = [p.text for p in parts if hasattr(p, "text") and p.text]
        return "".join(chunks).strip()
    except (AttributeError, IndexError) as e:
        logger.error(f"Failed to extract text from Gemini response: {e}")
        return ""


def get_response(phone: str, message: str, sender_name: str = "") -> str:
    """Process a message and return an AI response."""
    history = get_history(phone, limit=settings.MAX_HISTORY)
    contents = _to_gemini_contents(history, message)

    response = _model.generate_content(
        contents=contents,
        generation_config={
            "max_output_tokens": 2000,
            "temperature": 0.7,
        },
    )

    reply = _extract_text(response)
    if not reply:
        reply = "סליחה, לא הצלחתי לענות הפעם. נסה שוב."

    save_message(phone, "user", message)
    save_message(phone, "assistant", reply)

    return reply
