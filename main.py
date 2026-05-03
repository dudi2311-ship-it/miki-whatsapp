"""מיקי - WhatsApp AI Agent webhook server."""

import time
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse

from config import settings
from agent import get_response
from database import (
    init_db,
    get_state,
    set_state,
    replace_mirrored_events,
    list_mirrored_events_today,
)
import calendar_service
import gmail_service

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("miki")

_seen_messages: dict[str, float] = {}
DEDUP_WINDOW = 60


def _cleanup_seen():
    now = time.time()
    expired = [k for k, v in _seen_messages.items() if now - v > DEDUP_WINDOW]
    for k in expired:
        del _seen_messages[k]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("מיקי מוכן ומקשיב...")
    yield


app = FastAPI(title="miki", lifespan=lifespan)


@app.get("/")
async def root():
    return {"status": "ok", "agent": "miki"}


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "miki"}


@app.get("/debug/trigger-error")
async def debug_trigger_error(token: str = ""):
    """Manually trigger a fake error to verify owner-alert plumbing.

    Hit GET /debug/trigger-error?token=<CRON_TOKEN> — owner should get a
    🚨 WhatsApp alert. Remove this endpoint once monitoring is confirmed.
    """
    if not settings.CRON_TOKEN or token != settings.CRON_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")
    try:
        raise RuntimeError("בדיקת ניטור ידנית — מתפוצץ בכוונה")
    except Exception as e:
        debug_text = await notify_owner_error(
            "debug/trigger-error", e, sender_name="בדיקה ידנית"
        )
    return {"ok": True, "alert_text": debug_text}


@app.post("/webhook/test")
async def webhook_test(request: Request):
    """Dry-run version of the webhook — runs the agent but never sends via Green API.

    Use this for development/debugging instead of /webhook/green-api so we
    don't accidentally trigger WhatsApp spam protection by sending messages
    to non-existent test numbers.

    Returns a detailed error trace on failure so we can debug remotely.
    """
    import traceback
    try:
        data = await request.json()
        text = (
            data.get("messageData", {})
            .get("textMessageData", {})
            .get("textMessage", "")
        )
        phone = data.get("senderData", {}).get("chatId", "").replace("@c.us", "")
        if not text.strip() or not phone:
            return {"error": "missing text or phone"}
        reply = get_response(phone, text, data.get("senderData", {}).get("senderName", ""))
        return {"phone": phone, "user_message": text, "miki_reply": reply}
    except Exception as e:
        return JSONResponse(
            {
                "error": type(e).__name__,
                "message": str(e),
                "traceback": traceback.format_exc(),
            },
            status_code=500,
        )


@app.post("/webhook/green-api")
async def webhook(request: Request):
    """Handle incoming messages from Green API."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    webhook_type = data.get("typeWebhook")
    if webhook_type != "incomingMessageReceived":
        return {"ok": True, "skipped": webhook_type}

    message_data = data.get("messageData", {})
    message_type = message_data.get("typeMessage")
    if message_type != "textMessage":
        return {"ok": True, "skipped": message_type}

    sender_data = data.get("senderData", {})
    chat_id = sender_data.get("chatId", "")
    sender_name = sender_data.get("senderName", "")
    text = message_data.get("textMessageData", {}).get("textMessage", "")
    message_id = data.get("idMessage", "")

    if "@g.us" in chat_id:
        return {"ok": True, "skipped": "group_message"}

    if not text.strip():
        return {"ok": True, "skipped": "empty"}

    _cleanup_seen()
    if message_id in _seen_messages:
        return {"ok": True, "skipped": "duplicate"}
    _seen_messages[message_id] = time.time()

    phone = chat_id.replace("@c.us", "")
    logger.info(f"Message from {sender_name} ({phone}): {text[:80]}")
    is_owner = chat_id == settings.MIKI_OWNER_CHAT_ID

    try:
        reply = get_response(phone, text, sender_name)
    except Exception as e:
        logger.exception(f"Agent error: {e}")
        debug_text = await notify_owner_error(
            "webhook/agent", e, sender_name, phone, text
        )
        reply = debug_text if is_owner else "סליחה, משהו השתבש. נסה שוב בעוד רגע."

    try:
        await send_whatsapp_message(chat_id, reply)
        logger.info(f"Reply sent to {phone}: {reply[:80]}")
    except Exception as e:
        logger.exception(f"Failed to send reply: {e}")
        if not is_owner:
            await notify_owner_error(
                "webhook/send", e, sender_name, phone, text
            )

    return {"ok": True}


async def send_whatsapp_message(chat_id: str, message: str):
    url = (
        f"{settings.GREEN_API_URL}"
        f"/waInstance{settings.GREEN_API_INSTANCE}"
        f"/sendMessage/{settings.GREEN_API_TOKEN}"
    )
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json={"chatId": chat_id, "message": message},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()


async def notify_owner_error(
    where: str,
    exc: BaseException,
    sender_name: str = "",
    phone: str = "",
    user_text: str = "",
) -> str:
    """Push a debug message to the owner's WhatsApp on errors. Never raises."""
    lines = [
        "🚨 שגיאה במיקי",
        f"מקום: {where}",
        f"סוג: {type(exc).__name__}",
        f"הודעה: {str(exc)[:200]}",
    ]
    if sender_name or phone:
        who = sender_name or "?"
        if phone:
            who += f" ({phone})"
        snippet = user_text.strip().replace("\n", " ")[:80]
        if snippet:
            who += f' — "{snippet}"'
        lines.append(f"ממי: {who}")
    debug_text = "\n".join(lines)

    if not settings.MIKI_OWNER_CHAT_ID:
        return debug_text
    try:
        await send_whatsapp_message(settings.MIKI_OWNER_CHAT_ID, debug_text)
    except Exception:
        logger.exception("notify_owner_error failed to deliver alert")
    return debug_text


def _format_morning_brief() -> str:
    now = datetime.now(ISRAEL_TZ)
    today_ymd = now.strftime("%Y-%m-%d")
    weekday_he = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"][now.weekday()]
    lines = [f"בוקר טוב ☀️ יום {weekday_he}, {now.strftime('%d/%m')}"]

    combined: list[tuple[str, str, str]] = []  # (time, title, source)

    try:
        events = calendar_service.list_events(days_ahead=1, include_work=True)
        for e in events:
            start = e.get("start", "")
            if start.startswith(today_ymd):
                time_str = start[11:16] if len(start) >= 16 else ""
                source = "💼" if e.get("is_work") else "📅"
                combined.append((time_str, e.get("title", ""), source))
    except Exception as e:
        logger.exception("morning brief google calendar failed")
        lines.append(f"\n⚠️ לא הצלחתי למשוך Google Calendar: {e}")

    try:
        for e in list_mirrored_events_today(today_ymd):
            start = e.get("start_iso", "")
            time_str = start[11:16] if len(start) >= 16 else ""
            combined.append((time_str, e.get("title", ""), "📱"))
    except Exception as e:
        logger.exception("morning brief mirrored events failed")

    combined.sort(key=lambda t: t[0] or "99:99")
    if combined:
        lines.append("\n📅 היום ביומן:")
        for time_str, title, source in combined:
            lines.append(f"  • {source} {time_str} {title}")
    else:
        lines.append("\n📅 אין אירועים היום.")

    try:
        unread = gmail_service.search_messages(query="is:unread", max_results=5)
        if unread:
            lines.append(f"\n📧 {len(unread)} מיילים שלא נקראו (אחרונים):")
            for m in unread[:5]:
                sender = m.get("from", "")[:40]
                subj = m.get("subject", "")[:50]
                lines.append(f"  • {sender} — {subj}")
        else:
            lines.append("\n📧 תיבת הדואר ריקה מהודעות חדשות.")
    except Exception as e:
        logger.exception("morning brief gmail failed")
        lines.append(f"\n⚠️ לא הצלחתי למשוך מיילים: {e}")

    return "\n".join(lines)


@app.post("/sync/iphone-events")
async def sync_iphone_events(request: Request, x_cron_token: str = Header(default="")):
    """Receive a fresh batch of iPhone calendar events (the iOS Shortcut bridge).

    Body: {"events": [{"id": "...", "title": "...", "start_iso": "...",
                       "end_iso": "...", "location": "...", "notes": "...",
                       "calendar_name": "..."}]}

    Replaces the entire mirrored_events table with the new set.
    """
    if not settings.CRON_TOKEN or x_cron_token != settings.CRON_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    events = data.get("events") or []
    if not isinstance(events, list):
        return JSONResponse({"error": "events must be a list"}, status_code=400)

    try:
        count = replace_mirrored_events(events)
    except Exception as e:
        logger.exception("sync_iphone_events failed")
        return JSONResponse({"error": str(e)}, status_code=500)

    return {"ok": True, "stored": count}


@app.post("/cron/check-mail")
async def check_mail(x_cron_token: str = Header(default="")):
    """Check for new important mail since last run; alert owner via WhatsApp.

    Designed to be hit every 10-15 minutes by an external scheduler.
    Uses Gmail's category:primary to exclude promotions/social/updates.
    State is stored in Supabase under the key 'last_mail_check_unix'.
    """
    if not settings.CRON_TOKEN or x_cron_token != settings.CRON_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")
    if not settings.MIKI_OWNER_CHAT_ID:
        raise HTTPException(status_code=500, detail="MIKI_OWNER_CHAT_ID not set")

    now_unix = int(time.time())
    last_str = get_state("last_mail_check_unix")
    if not last_str:
        set_state("last_mail_check_unix", str(now_unix))
        return {"ok": True, "skipped": "first run, baseline saved"}

    try:
        last_unix = int(last_str)
    except ValueError:
        last_unix = now_unix - 900

    try:
        msgs = gmail_service.search_messages(
            query=f"is:unread category:primary after:{last_unix}",
            max_results=10,
        )
    except Exception as e:
        logger.exception("check-mail search failed")
        await notify_owner_error("cron/check-mail (gmail search)", e)
        return JSONResponse({"error": str(e)}, status_code=500)

    set_state("last_mail_check_unix", str(now_unix))

    if not msgs:
        return {"ok": True, "new_mail": 0}

    lines = [f"📬 {len(msgs)} מייל{'ים' if len(msgs) != 1 else ''} חדש{'ים' if len(msgs) != 1 else ''}:"]
    for m in msgs[:5]:
        sender = m.get("from", "")[:40]
        subj = m.get("subject", "")[:60]
        lines.append(f"  • {sender} — {subj}")
    text = "\n".join(lines)

    try:
        await send_whatsapp_message(settings.MIKI_OWNER_CHAT_ID, text)
    except Exception as e:
        logger.exception("check-mail send failed")
        await notify_owner_error("cron/check-mail (send)", e)
        return JSONResponse({"error": str(e)}, status_code=500)

    return {"ok": True, "new_mail": len(msgs), "preview": text[:300]}


@app.post("/cron/morning-brief")
async def morning_brief(x_cron_token: str = Header(default="")):
    """Daily morning brief — pushed to the owner via WhatsApp.

    Protected by a shared secret in the X-Cron-Token header. Configure an
    external scheduler (cron-job.org, Render Cron, etc.) to POST here once a day.
    """
    if not settings.CRON_TOKEN or x_cron_token != settings.CRON_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")
    if not settings.MIKI_OWNER_CHAT_ID:
        raise HTTPException(status_code=500, detail="MIKI_OWNER_CHAT_ID not set")

    brief = _format_morning_brief()
    try:
        await send_whatsapp_message(settings.MIKI_OWNER_CHAT_ID, brief)
    except Exception as e:
        logger.exception("morning brief send failed")
        await notify_owner_error("cron/morning-brief (send)", e)
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True, "sent_to": settings.MIKI_OWNER_CHAT_ID, "preview": brief[:300]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
