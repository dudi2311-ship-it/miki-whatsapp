"""מיקי - AI conversation logic using Gemini.

Capabilities:
- Web search via Google Search grounding
- Google Calendar read/write via function calling
- Conversation memory via Supabase
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from google import genai
from google.genai import types

from config import settings
from database import get_history, save_message
import calendar_service
import gmail_service

logger = logging.getLogger("miki.agent")

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

_client = genai.Client(api_key=settings.GEMINI_API_KEY)


def list_my_events(days_ahead: int = 7, include_work: bool = True) -> dict:
    """List upcoming events from the user's Google Calendar.

    Use this when the user asks about their schedule, what's coming up,
    or specific upcoming events. Work meetings are included by default.

    Args:
        days_ahead: How many days into the future to look. 1 = today, 7 = week.
        include_work: Defaults to True (show everything). Set False only if the
            user explicitly asks to hide work.

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


def search_gmail(query: str = "", max_results: int = 10) -> dict:
    """Search the user's Gmail and return short summaries of matching messages.

    Use this whenever the user asks about their email — recent mail, unread,
    messages from a specific sender, mail about a topic. Don't read full
    bodies here — call read_gmail_message afterwards if the user wants details.

    Args:
        query: Gmail search syntax. Examples:
            '' (empty) — most recent mail
            'is:unread' — unread only
            'from:plus.google.com newer_than:7d' — sender + recency
            'subject:חשבונית' — subject contains text (Hebrew works)
        max_results: How many to return (default 10, keep small).

    Returns:
        A dict with 'messages' (list of summaries with id, from, subject,
        date, snippet, unread) and 'count'.
    """
    try:
        msgs = gmail_service.search_messages(query=query, max_results=max_results)
        return {"messages": msgs, "count": len(msgs)}
    except Exception as e:
        logger.exception("search_gmail failed")
        return {"error": str(e)}


def read_gmail_message(message_id: str) -> dict:
    """Read the full body of a Gmail message by ID (from search_gmail).

    Args:
        message_id: The message ID returned by search_gmail.

    Returns:
        Full message with from, to, subject, date, body (truncated to 8k chars).
    """
    try:
        return gmail_service.get_message(message_id)
    except Exception as e:
        logger.exception("read_gmail_message failed")
        return {"error": str(e)}


def mark_gmail_read(message_id: str) -> dict:
    """Mark a Gmail message as read (removes the UNREAD label).

    Use when the user asks to mark a specific message as read, or after they
    confirm they've handled an unread item.

    Args:
        message_id: The message ID returned by search_gmail.
    """
    try:
        return gmail_service.mark_as_read(message_id)
    except Exception as e:
        logger.exception("mark_gmail_read failed")
        return {"error": str(e)}


def label_gmail(message_id: str, label_name: str) -> dict:
    """Add a label to a Gmail message. Creates the label if it doesn't exist.

    Use when the user asks to tag/categorize a message (e.g. "תייג כ'חשבונות'",
    'put this in the Receipts label'). Hebrew label names are fine.

    Args:
        message_id: The message ID returned by search_gmail.
        label_name: The label name to add (created automatically if missing).
    """
    try:
        return gmail_service.add_label(message_id, label_name)
    except Exception as e:
        logger.exception("label_gmail failed")
        return {"error": str(e)}


def send_gmail(to: str, subject: str, body: str) -> dict:
    """Send a plain-text email from the user's Gmail account.

    Use this only after explicitly confirming with the user — never send mail
    on a vague request. Confirm recipient, subject, and body content before
    calling this. Hebrew is fine in any field.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Plain-text body. Use \\n for line breaks.

    Returns:
        Confirmation with the sent message id.
    """
    try:
        return gmail_service.send_email(to=to, subject=subject, body=body)
    except Exception as e:
        logger.exception("send_gmail failed")
        return {"error": str(e)}


def _green_api_url(method: str) -> str:
    return (
        f"{settings.GREEN_API_URL}"
        f"/waInstance{settings.GREEN_API_INSTANCE}"
        f"/{method}/{settings.GREEN_API_TOKEN}"
    )


def _normalize_chat_id(value: str) -> str:
    """קבל chatId מלא או מספר טלפון, החזר chatId תקין ל-Green API.

    דוגמאות: '972501234567' → '972501234567@c.us', '12345@g.us' → '12345@g.us'.
    """
    v = (value or "").strip()
    if "@" in v:
        return v
    digits = "".join(c for c in v if c.isdigit())
    return f"{digits}@c.us" if digits else v


def list_recent_whatsapps(minutes: int = 1440) -> dict:
    """הודעות ווטסאפ נכנסות אחרונות (Green API LastIncomingMessages).

    כולל גם הודעות מקבוצות. is_group=True מסמן הודעה מקבוצה (chatId שמסתיים ב-@g.us);
    במקרה כזה chatName הוא שם הקבוצה ו-senderName הוא מי שכתב בתוכה.
    Green API מחזיר עד 24 שעות אחורה.

    Args:
        minutes: כמה דקות אחורה למשוך (ברירת מחדל 1440 = 24 שעות).

    Returns:
        dict עם 'messages' (רשימה של {chatId, chatName, senderName, textMessage, timestamp, is_group}) ו-'count'.
    """
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(_green_api_url("lastIncomingMessages"), params={"minutes": minutes})
            response.raise_for_status()
            raw = response.json()
        messages = []
        for m in raw if isinstance(raw, list) else []:
            if m.get("typeMessage") != "textMessage":
                continue
            chat_id = m.get("chatId", "")
            messages.append({
                "chatId": chat_id,
                "chatName": m.get("chatName", ""),
                "senderName": m.get("senderName", ""),
                "textMessage": m.get("textMessage", ""),
                "timestamp": m.get("timestamp", 0),
                "is_group": chat_id.endswith("@g.us"),
            })
        return {"messages": messages, "count": len(messages)}
    except Exception as e:
        logger.exception("list_recent_whatsapps failed")
        return {"error": str(e)}


def read_whatsapp_chat(chat_id: str, count: int = 20) -> dict:
    """קרא הודעות אחרונות מצ'אט ווטסאפ ספציפי (Green API getChatHistory).

    שימוש: "מה היה עם ליאור היום", "סכם את הקבוצה X". עובד גם על קבוצות.
    אם אין chat_id ביד — קודם find_whatsapp_chats לפי שם, ואז קרא לכאן.

    Args:
        chat_id: chatId מלא ('972501234567@c.us' או '...@g.us') או רק מספר טלפון.
        count: כמה הודעות אחרונות לשלוף (ברירת מחדל 20, מקסימום 100).

    Returns:
        dict עם 'messages' (טקסט בלבד, מהחדש לישן) ו-'chat_id', 'is_group'.
    """
    try:
        normalized = _normalize_chat_id(chat_id)
        with httpx.Client(timeout=30) as client:
            response = client.post(
                _green_api_url("getChatHistory"),
                json={"chatId": normalized, "count": min(count, 100)},
            )
            response.raise_for_status()
            raw = response.json()
        messages = []
        for m in raw if isinstance(raw, list) else []:
            if m.get("typeMessage") != "textMessage":
                continue
            messages.append({
                "type": m.get("type", ""),
                "senderName": m.get("senderName", ""),
                "textMessage": m.get("textMessage", ""),
                "timestamp": m.get("timestamp", 0),
            })
        return {
            "chat_id": normalized,
            "is_group": normalized.endswith("@g.us"),
            "messages": messages,
            "count": len(messages),
        }
    except Exception as e:
        logger.exception("read_whatsapp_chat failed")
        return {"error": str(e)}


def find_whatsapp_chats(query: str) -> dict:
    """חפש איש קשר או צ'אט בווטסאפ לפי שם חלקי (Green API getContacts).

    שימוש: דודי אומר "שלח לליאור..." ואין chatId — קרא לזה עם query="ליאור",
    קבל את ה-chatId, ואז send_whatsapp_to. אם יש כמה תוצאות — תאשר עם דודי לפני שליחה.

    Args:
        query: מחרוזת חיפוש (חלקית, רישיות לא מבדילה). דוגמאות: 'ליאור', 'אמא', 'משפחה'.

    Returns:
        dict עם 'matches' (רשימת {id, name, type}) ו-'count'.
    """
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(_green_api_url("getContacts"))
            response.raise_for_status()
            raw = response.json()
        q = (query or "").strip().lower()
        matches = []
        for c in raw if isinstance(raw, list) else []:
            name = (c.get("name") or "").lower()
            cid = (c.get("id") or "").lower()
            if not q or q in name or q in cid:
                matches.append({
                    "id": c.get("id", ""),
                    "name": c.get("name", ""),
                    "type": c.get("type", ""),
                })
        return {"matches": matches[:20], "count": len(matches)}
    except Exception as e:
        logger.exception("find_whatsapp_chats failed")
        return {"error": str(e)}


def send_whatsapp_to(chat_id: str, message: str) -> dict:
    """שלח הודעת ווטסאפ למישהו אחר (לא חזרה לדודי).

    **חובה אישור מפורש מדודי לפני קריאה** — נמען + תוכן ההודעה.
    אל תשתמש בזה כדי לענות לדודי עצמו (זה קורה אוטומטית בלולאת ה-webhook).
    אם יש ספק לגבי chatId — find_whatsapp_chats קודם.

    Args:
        chat_id: chatId מלא ('...@c.us' / '...@g.us') או מספר טלפון.
        message: תוכן ההודעה.

    Returns:
        dict עם 'sent': True ו-'idMessage', או 'error'.
    """
    try:
        normalized = _normalize_chat_id(chat_id)
        with httpx.Client(timeout=30) as client:
            response = client.post(
                _green_api_url("sendMessage"),
                json={"chatId": normalized, "message": message},
            )
            response.raise_for_status()
            data = response.json()
        return {"sent": True, "chat_id": normalized, "idMessage": data.get("idMessage", "")}
    except Exception as e:
        logger.exception("send_whatsapp_to failed")
        return {"error": str(e)}


def set_reminder(text: str, fire_at_iso: str, recurrence: str = "") -> dict:
    """תזמן תזכורת ב-WhatsApp — חד פעמית או חוזרת.

    שימוש: כשדודי אומר "תזכיר לי X בשעה Y" / "בעוד שעה תזכיר לי..." / "מחר ב-9 תזכיר לי...".
    חובה להמיר זמן יחסי לזמן מוחלט בעזרת השעה הנוכחית מה-system prompt לפני הקריאה.

    Args:
        text: תוכן התזכורת בלשון ציווי קצרה (למשל "להתקשר לאמא").
        fire_at_iso: זמן הירי הראשון בפורמט ISO 8601 עם אזור זמן ישראל,
            למשל '2026-05-04T18:00:00+03:00'.
        recurrence: ריק לתזכורת חד פעמית. לחזרה: 'daily' / 'weekly:Sun,Tue,Thu' /
            'monthly:15'. ימי השבוע באנגלית קצר (Sun, Mon, Tue, Wed, Thu, Fri, Sat).
            דוגמאות: "כל יום בשעה 7" → fire_at_iso=המחר ב-07:00, recurrence='daily'.
            "כל ראשון ב-9" → fire_at_iso=הראשון הקרוב ב-9, recurrence='weekly:Sun'.
            "ב-1 לחודש" → fire_at_iso=ה-1 הקרוב, recurrence='monthly:1'.

    Returns:
        dict עם id, fire_at, recurrence, או 'error'.
    """
    try:
        from database import create_reminder as _create
        result = _create(
            settings.MIKI_OWNER_CHAT_ID,
            text,
            fire_at_iso,
            recurrence=recurrence or None,
        )
        return {
            "created": True,
            "id": str(result.get("id", "")),
            "fire_at": fire_at_iso,
            "text": text,
            "recurrence": recurrence or None,
        }
    except Exception as e:
        logger.exception("set_reminder failed")
        return {"error": str(e)}


def list_reminders() -> dict:
    """החזר את התזכורות הקרובות שעוד לא ירו.

    שימוש: כשדודי שואל "אילו תזכורות יש לי", "תזכיר לי מה תזמנתי".

    Returns:
        dict עם 'reminders' (רשימת תזכורות פעילות עם id, text, fire_at).
    """
    try:
        from database import list_pending_reminders
        rows = list_pending_reminders(settings.MIKI_OWNER_CHAT_ID, limit=20)
        return {"reminders": rows, "count": len(rows)}
    except Exception as e:
        logger.exception("list_reminders failed")
        return {"error": str(e)}


def cancel_reminder_by_id(reminder_id: str) -> dict:
    """בטל תזכורת לפי ה-id שלה (מ-list_reminders).

    שימוש: כשדודי אומר "בטל את התזכורת על X" — קודם list_reminders,
    מוצא את ה-id המתאים, ואז קורא לפונקציה הזאת.
    """
    try:
        from database import cancel_reminder as _cancel
        _cancel(reminder_id)
        return {"cancelled": True, "id": reminder_id}
    except Exception as e:
        logger.exception("cancel_reminder_by_id failed")
        return {"error": str(e)}


def remember_fact(category: str, content: str) -> dict:
    """שמור עובדה ארוכת טווח על דודי שמיקי תזכור בכל שיחה.

    שימוש: כשדודי משתף משהו אישי שכדאי לזכור לטווח ארוך — העדפות, פרטי משפחה,
    בריאות, מקום עבודה, תחומי עניין, אלרגיות, שמות חברים. אחרי השמירה אמור לדודי
    מה נשמר.

    אל תשמור: דברים זמניים (משימות פתוחות, רגשות חולפים), פרטים שכבר שמורים,
    מידע שדודי לא ביקש מפורשות שתזכור.

    Args:
        category: קטגוריה קצרה באנגלית. דוגמאות: 'preferences', 'family', 'health',
            'work', 'interests', 'projects'. תאחיד עם קטגוריות קיימות (ראה list_facts).
        content: העובדה עצמה במשפט קצר בעברית. דוגמאות: "אוהב לקום ב-7",
            "אשתו ליאור היא שרת בריאות הציבור", "אלרגי לחומוס".

    Returns:
        dict עם id ופרטי העובדה שנשמרה.
    """
    try:
        from database import add_fact as _add
        result = _add(settings.MIKI_OWNER_CHAT_ID, category, content)
        return {
            "saved": True,
            "id": str(result.get("id", "")),
            "category": category,
            "content": content,
        }
    except Exception as e:
        logger.exception("remember_fact failed")
        return {"error": str(e)}


def list_my_facts(category: str = "") -> dict:
    """החזר את העובדות הארוכות-טווח שמיקי שומרת על דודי.

    שימוש: כשדודי שואל "מה אתה זוכר עליי", "אילו העדפות שמרת", או כשצריך
    למצוא id לפני forget_fact. ה-system prompt כבר מקבל אוטומטית את העובדות,
    אז אין צורך לקרוא לזה כדי להשתמש בהן בתשובה רגילה.

    Args:
        category: סנן לקטגוריה אחת ('preferences' / 'family' / וכו'). ריק = הכל.

    Returns:
        dict עם 'facts' (id, category, content, created_at) ו-'count'.
    """
    try:
        from database import list_facts as _list
        rows = _list(settings.MIKI_OWNER_CHAT_ID, category=category or None)
        return {"facts": rows, "count": len(rows)}
    except Exception as e:
        logger.exception("list_my_facts failed")
        return {"error": str(e)}


def forget_fact(fact_id: str) -> dict:
    """מחק עובדה לפי ה-id שלה (מ-list_my_facts).

    שימוש: כשדודי אומר "תשכח שX", "תוריד את העובדה ש...", "כבר לא רלוונטי" —
    קודם list_my_facts כדי למצוא את ה-id, ואז קריאה לכאן.
    """
    try:
        from database import remove_fact as _remove
        _remove(fact_id)
        return {"forgotten": True, "id": fact_id}
    except Exception as e:
        logger.exception("forget_fact failed")
        return {"error": str(e)}


def web_search(query: str) -> dict:
    """Search the live web for current information using Google Search.

    Use this whenever the user asks about current events, recent news, prices,
    sports results, opening hours, weather forecasts, or any fact that may have
    changed since training. Don't use it for personal data — that's what the
    calendar tools are for.

    Args:
        query: The search query, in the user's language (Hebrew is fine).

    Returns:
        A dict with 'answer' (a synthesized answer) and 'sources' (URLs used).
    """
    try:
        response = _client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.3,
                max_output_tokens=1500,
            ),
        )
        answer = _extract_text(response) or getattr(response, "text", "") or ""
        sources: list[str] = []
        try:
            grounding = response.candidates[0].grounding_metadata
            chunks = getattr(grounding, "grounding_chunks", None) or []
            for c in chunks:
                web = getattr(c, "web", None)
                if web and getattr(web, "uri", None):
                    sources.append(web.uri)
        except (AttributeError, IndexError):
            pass
        return {"answer": answer.strip(), "sources": sources[:5]}
    except Exception as e:
        logger.exception("web_search failed")
        return {"error": str(e)}


_TOOLS = [
    list_my_events,
    create_calendar_event,
    update_calendar_event,
    delete_calendar_event,
    search_gmail,
    read_gmail_message,
    mark_gmail_read,
    label_gmail,
    send_gmail,
    list_recent_whatsapps,
    read_whatsapp_chat,
    find_whatsapp_chats,
    send_whatsapp_to,
    set_reminder,
    list_reminders,
    cancel_reminder_by_id,
    remember_fact,
    list_my_facts,
    forget_fact,
    web_search,
]


def _format_facts_block() -> str:
    """Group remembered facts by category and format for the system prompt."""
    if not settings.MIKI_OWNER_CHAT_ID:
        return ""
    try:
        from database import list_facts
        facts = list_facts(settings.MIKI_OWNER_CHAT_ID)
    except Exception:
        logger.exception("_format_facts_block failed to load facts")
        return ""
    if not facts:
        return ""
    by_category: dict[str, list[str]] = {}
    for f in facts:
        cat = f.get("category", "other") or "other"
        by_category.setdefault(cat, []).append(f.get("content", ""))
    lines = ["## עובדות שאני זוכר על דודי"]
    for cat in sorted(by_category):
        lines.append(f"**{cat}:**")
        for content in by_category[cat]:
            if content:
                lines.append(f"- {content}")
    return "\n".join(lines)


def _build_system_prompt() -> str:
    """System prompt with current Israel time + remembered facts injected."""
    now = datetime.now(ISRAEL_TZ)
    weekday_he = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"][now.weekday()]
    facts_block = _format_facts_block()
    facts_section = f"\n\n{facts_block}" if facts_block else ""
    return f"""{settings.SYSTEM_PROMPT}{facts_section}

יש לך גישה לכלים אמיתיים. **חובה** להשתמש בהם — לא לדמיין או להמציא תשובות:
- list_my_events — לקריאת אירועים מהיומן
- create_calendar_event — להוספת אירוע חדש
- update_calendar_event — לשינוי אירוע קיים
- delete_calendar_event — למחיקת אירוע
- search_gmail — חיפוש מיילים (היסטוריה, "is:unread", "from:X", "subject:Y")
- read_gmail_message — קריאת גוף מייל מלא (לפי message_id מ-search_gmail)
- mark_gmail_read — סימון מייל כנקרא (מסיר את התווית UNREAD)
- label_gmail — הוספת תווית למייל (יוצרת את התווית אם לא קיימת)
- send_gmail — שליחת מייל. **רק אחרי אישור מפורש של דודי לכתובת/נושא/תוכן**.
- list_recent_whatsapps — הודעות ווטסאפ נכנסות אחרונות (עד 24 שעות), כולל קבוצות
- read_whatsapp_chat — קריאת היסטוריית צ'אט/קבוצה ספציפיים לפי chat_id
- find_whatsapp_chats — חיפוש איש קשר/קבוצה לפי שם (לקבל chat_id)
- send_whatsapp_to — שליחת ווטסאפ לאדם/קבוצה. **רק אחרי אישור מפורש של דודי לנמען ולתוכן**.
- set_reminder — תזמון תזכורת. **המר זמן יחסי לזמן מוחלט** (ISO 8601 עם +03:00). תומך בחזרה: 'daily' / 'weekly:Sun,Tue' / 'monthly:15'.
- list_reminders — הצגת תזכורות קרובות שטרם ירו
- cancel_reminder_by_id — ביטול תזכורת לפי id
- remember_fact — שמירת עובדה ארוכת טווח (העדפות, משפחה, בריאות, וכו')
- list_my_facts — הצגת עובדות שמורות (בעיקר כדי למצוא id לפני forget_fact)
- forget_fact — מחיקת עובדה לפי id
- web_search — חיפוש חי באינטרנט (חדשות, מחירים, שעות פתיחה, מזג אוויר, כל דבר שיכול להשתנות)

כשדודי שואל על היומן/המיילים או מבקש להוסיף/לשנות/למחוק — **קרא לפונקציה המתאימה ישירות** ואז ענה לו עם התוצאה.
כשדודי שואל על משהו שדורש מידע עדכני (מה קרה, מה המחיר, מתי פתוח) — **קרא ל-web_search** במקום לנחש.
לעולם אל תגיד "אני אוסיף" / "אני אבדוק" / "אזכיר לך" / "תזכורת מוגדרת" / "אני אזכור" בלי לקרוא לפונקציה.

⚠️ **תזכורות — חוק ברזל:**
כל ביטוי שדומה ל"תזכיר לי", "תיתן לי תזכורת", "תזמן", "אל תיתן לי לשכוח", "תעיר אותי", "תזכיר ב-...", "בעוד X דקות/שעות תזכיר" — **חייב** קריאה ישירה ל-`set_reminder`.
שלבי החובה לפני כל אישור לדודי:
1. חשב את `fire_at_iso` המוחלט מהשעה הנוכחית למטה (לא לבקש מדודי לחשב).
2. אם זו תזכורת חוזרת ("כל יום", "כל ראשון", "ב-1 לחודש") — קבע את ה-recurrence המתאים. אחרת — recurrence ריק.
3. קרא ל-`set_reminder(text, fire_at_iso, recurrence)`.
4. רק אחרי שקיבלת `created: true` — אישר לדודי "תזכורת נשמרה ל-HH:MM" + ציין אם חוזרת.
5. אם קיבלת `error` — אמור לדודי שלא הצלחת לשמור.
**אסור** לכתוב "אזכיר לך" או "סבבה אזכיר" בלי לקרוא קודם ל-`set_reminder`. גם אם הזמן רחוק. גם אם כבר אמרת בעבר באותה שיחה.

⚠️ **עובדות ארוכות-טווח — חוק ברזל:**
כל ביטוי שדומה ל"תזכור ש...", "תזכור עליי ש...", "אני אוהב X", "אני שונא Y", "אני מעדיף Z", "אשתי/הבן/הבת/אבא/אמא שלי...", "אני אלרגי ל...", "אני לוקח תרופה...", "אני עובד ב...", "התחביב שלי..." — **חייב** קריאה ישירה ל-`remember_fact`. אסור לאשר בלי לקרוא קודם.
שלבי החובה לפני כל אישור לדודי:
1. בחר category באנגלית: 'preferences' / 'family' / 'health' / 'work' / 'interests' / 'projects'. תאחיד עם קטגוריות מהעובדות שלמעלה.
2. נסח content קצר בעברית — משפט אחד שמתאר את העובדה.
3. קרא ל-`remember_fact(category, content)`. אם דודי שיתף 2-3 עובדות בהודעה אחת — קרא 2-3 פעמים.
4. רק אחרי `saved: true` — אישר לדודי בקצרה ("שמרתי שאתה אוהב לקום ב-7").
5. אם `error` — אמור שלא הצלחת לשמור.

**אסור** לכתוב "רשמתי לפניי" / "אני אזכור" / "אשתדל לזכור" / "סבבה אזכור" / "אני זוכר" בלי לקרוא קודם ל-`remember_fact`. גם אם דודי כבר אמר את אותו הדבר. גם אם זה נראה זמני — אם הוא ביקש לזכור, תזכור.

**אל תשמור** רק במקרים האלה: משימות פתוחות (זה לתזכורות), פרטים קצרי-מועד (לאן הולכים הערב), עובדה שכבר זהה לאחת מהעובדות למעלה.
אם עובדה השתנתה — forget_fact הישנה ואז remember_fact חדשה.
אל תשאל "האם לזכור?" — תחליט לבד לפי הקריטריונים.

היומן כולל הכל — אישי ועבודה. אין סינון אוטומטי. אירועי עבודה מסומנים ב-`is_work: true` כדי שתוכל להזכיר את ההקשר אם רלוונטי.

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
