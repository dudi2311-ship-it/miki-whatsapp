"""מיקי - AI conversation logic using Gemini.

Capabilities:
- Web search via Google Search grounding
- Google Calendar read/write via function calling
- Conversation memory via Supabase
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types

from config import settings
from database import get_history, save_message
import calendar_service

logger = logging.getLogger("miki.agent")

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

_client = genai.Client(api_key=settings.GEMINI_API_KEY)


def list_my_events(days_ahead: int = 7, include_work: bool = False) -> dict:
    """List upcoming events from the user's Google Calendar.

    Use this when the user asks about their schedule, what's coming up,
    or specific upcoming events. Work meetings are filtered out by default
    (per user preference); set include_work=True only if the user explicitly
    asks to include work meetings.

    Args:
        days_ahead: How many days into the future to look. 1 = today, 7 = week.
        include_work: Set True only if user explicitly asks for work meetings.

    Returns:
        A dict with 'events' (list of events with id, title, start, end, location).
    """
    try:
        events = calendar_service.list_events(
            days_ahead=days_ahead, include_work=include_work
        )
        return {"events": events, "count": len(events)}
    except Exception as e:
        logger.exception("list_my_events failed")
        return {"error": str(e)}


def create_calendar_event(
    title: str,
    start_iso: str,
    duration_minutes: int = 60,
    description: str = "",
    location: str = "",
) -> dict:
    """Create a new event in the user's Google Calendar.

    Args:
        title: Event title (in Hebrew or English, as the user requested).
        start_iso: Start time as ISO 8601 with Israel timezone, e.g.
            '2026-04-30T10:00:00+03:00'. If the user gives relative time
            ('tomorrow at 10'), convert to absolute first using current time.
        duration_minutes: Length in minutes (default 60).
        description: Optional notes / agenda.
        location: Optional location.

    Returns:
        The created event details.
    """
    try:
        event = calendar_service.create_event(
            title=title,
            start_iso=start_iso,
            duration_minutes=duration_minutes,
            description=description,
            location=location,
        )
        return {"created": event}
    except Exception as e:
        logger.exception("create_calendar_event failed")
        return {"error": str(e)}


def update_calendar_event(
    event_id: str,
    title: str = "",
    start_iso: str = "",
    end_iso: str = "",
    description: str = "",
    location: str = "",
) -> dict:
    """Update an existing event. Use the event_id from list_my_events.

    Pass only the fields you want to change; leave others as empty string.

    Args:
        event_id: The event's ID (from list_my_events).
        title: New title (empty to keep current).
        start_iso: New start (ISO 8601 with timezone, empty to keep current).
        end_iso: New end (ISO 8601 with timezone, empty to keep current).
        description: New description (empty to keep current).
        location: New location (empty to keep current).
    """
    try:
        event = calendar_service.update_event(
            event_id=event_id,
            title=title or None,
            start_iso=start_iso or None,
            end_iso=end_iso or None,
            description=description or None,
            location=location or None,
        )
        return {"updated": event}
    except Exception as e:
        logger.exception("update_calendar_event failed")
        return {"error": str(e)}


def delete_calendar_event(event_id: str) -> dict:
    """Delete a calendar event by its ID.

    To find an event ID, first call list_my_events. When the user asks to
    delete by name, list events matching the name and delete each by ID.

    Args:
        event_id: The event's ID (from list_my_events).
    """
    try:
        return calendar_service.delete_event(event_id)
    except Exception as e:
        logger.exception("delete_calendar_event failed")
        return {"error": str(e)}


_TOOLS = [
    list_my_events,
    create_calendar_event,
    update_calendar_event,
    delete_calendar_event,
]


def _build_system_prompt() -> str:
    """System prompt with current Israel time injected."""
    now = datetime.now(ISRAEL_TZ)
    weekday_he = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"][now.weekday()]
    return f"""{settings.SYSTEM_PROMPT}

יש לך גישה לכלים אמיתיים ליומן Google Calendar של דודי. **חובה** להשתמש בהם — לא לדמיין או להמציא תשובות:
- list_my_events — לקריאת אירועים
- create_calendar_event — להוספת אירוע חדש
- update_calendar_event — לשינוי אירוע קיים
- delete_calendar_event — למחיקת אירוע

כשדודי שואל על היומן או מבקש להוסיף/לשנות/למחוק — **קרא לפונקציה המתאימה ישירות** ואז ענה לו עם התוצאה.
לעולם אל תגיד "אני אוסיף" / "אני אבדוק" בלי לקרוא לפונקציה. אם אתה צריך מידע מהיומן — קרא לפונקציה.

פגישות עבודה (משרד הבריאות, Teams, ועוד) מסוננות אוטומטית. אם דודי שואל על "פגישות" סתם — include_work=False (ברירת מחדל). רק אם הוא אומר במפורש "כולל עבודה" — include_work=True.

מידע נוכחי:
- היום: יום {weekday_he}, {now.strftime('%d/%m/%Y')}
- השעה: {now.strftime('%H:%M')} (שעון ישראל)
- כשיוצרים אירוע: השתמש בפורמט ISO 8601 עם איזור זמן ישראל (+03:00)

למחיקה: אם יש כמה אירועים שמתאימים לתיאור של דודי — תאשר איזה למחוק. אם הוא ביקש למחוק מספר אירועים מפורשות — תקרא ל-delete_calendar_event לכל אחד מהם.

יוזמות: רק אם יש משהו מיוחד (התנגשות, פער חריג). לא להציע סתם.
"""


def _to_gemini_contents(history: list[dict], new_user_message: str) -> list[types.Content]:
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
    try:
        candidate = response.candidates[0]
        parts = candidate.content.parts or []
        chunks = [p.text for p in parts if getattr(p, "text", None)]
        return "".join(chunks).strip()
    except (AttributeError, IndexError) as e:
        logger.error(f"Failed to extract text from Gemini response: {e}")
        return ""


def get_response(phone: str, message: str, sender_name: str = "") -> str:
    history = get_history(phone, limit=settings.MAX_HISTORY)
    contents = _to_gemini_contents(history, message)

    config = types.GenerateContentConfig(
        system_instruction=_build_system_prompt(),
        tools=_TOOLS,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            disable=False,
            maximum_remote_calls=8,
        ),
        max_output_tokens=2000,
        temperature=0.7,
    )

    response = _client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=contents,
        config=config,
    )

    afc_calls = getattr(response, "automatic_function_calling_history", None) or []
    if afc_calls:
        logger.info(f"Function calls executed: {len(afc_calls)}")

    reply = _extract_text(response) or getattr(response, "text", "") or ""
    reply = reply.strip()
    if not reply:
        reply = "סליחה, לא הצלחתי לענות הפעם. נסה שוב."

    save_message(phone, "user", message)
    save_message(phone, "assistant", reply)

    return reply
