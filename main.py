"""מיקי - WhatsApp AI Agent webhook server."""

import time
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import settings
from agent import get_response
from database import init_db

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
    """
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
