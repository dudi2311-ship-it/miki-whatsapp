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
from database import init_db
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

    try:
        reply = get_response(phone, text, sender_name)
    except Exception as e:
        logger.exception(f"Agent error: {e}")
        reply = "סליחה, משהו השתבש. נסה שוב בעוד רגע."

    try:
        await send_whatsapp_message(chat_id, reply)
        logger.info(f"Reply sent to {phone}: {reply[:80]}")
    except Exception as e:
        logger.exception(f"Failed to send reply: {e}")

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


def _format_morning_brief() -> str:
    now = datetime.now(ISRAEL_TZ)
    weekday_he = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"][now.weekday()]
    lines = [f"בוקר טוב ☀️ יום {weekday_he}, {now.strftime('%d/%m')}"]

    try:
        events = calendar_service.list_events(days_ahead=1, include_work=False)
        today_events = [
            e for e in events
            if e.get("start", "").startswith(now.strftime("%Y-%m-%d"))
        ]
        if today_events:
            lines.append("\n📅 היום ביומן (פרטי):")
            for e in today_events:
                start = e.get("start", "")
                time_str = start[11:16] if len(start) >= 16 else ""
                lines.append(f"  • {time_str} {e.get('title', '')}")
        else:
            lines.append("\n📅 אין אירועים פרטיים ביומן היום.")
    except Exception as e:
        logger.exception("morning brief calendar failed")
        lines.append(f"\n⚠️ לא הצלחתי למשוך יומן: {e}")

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
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True, "sent_to": settings.MIKI_OWNER_CHAT_ID, "preview": brief[:300]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
