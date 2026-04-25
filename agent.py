"""מיקי - AI conversation logic using Gemini with Google Search grounding."""

import logging

from google import genai
from google.genai import types

from config import settings
from database import get_history, save_message

logger = logging.getLogger("miki.agent")

_client = genai.Client(api_key=settings.GEMINI_API_KEY)

_search_tool = types.Tool(google_search=types.GoogleSearch())

_generation_config = types.GenerateContentConfig(
    system_instruction=settings.SYSTEM_PROMPT,
    tools=[_search_tool],
    max_output_tokens=2000,
    temperature=0.7,
)


def _to_gemini_contents(history: list[dict], new_user_message: str) -> list[types.Content]:
    """Convert OpenAI-style history to Gemini Content objects.

    Gemini uses 'user' and 'model' roles (not 'assistant').
    """
    contents: list[types.Content] = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])])
        )
    contents.append(
        types.Content(role="user", parts=[types.Part.from_text(text=new_user_message)])
    )
    return contents


def _extract_text(response) -> str:
    """Extract text from a Gemini response, handling multi-part output.

    Gemini 2.5 'thinking' mode returns multiple parts — concatenate all
    text parts and skip non-text parts (e.g., grounding metadata).
    """
    try:
        candidate = response.candidates[0]
        parts = candidate.content.parts or []
        chunks = [p.text for p in parts if getattr(p, "text", None)]
        return "".join(chunks).strip()
    except (AttributeError, IndexError) as e:
        logger.error(f"Failed to extract text from Gemini response: {e}")
        return ""


def get_response(phone: str, message: str, sender_name: str = "") -> str:
    """Process a message and return an AI response (with web search if helpful)."""
    history = get_history(phone, limit=settings.MAX_HISTORY)
    contents = _to_gemini_contents(history, message)

    response = _client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=contents,
        config=_generation_config,
    )

    reply = _extract_text(response)
    if not reply:
        reply = "סליחה, לא הצלחתי לענות הפעם. נסה שוב."

    save_message(phone, "user", message)
    save_message(phone, "assistant", reply)

    return reply
