"""
main.py — FastAPI application with WhatsApp Cloud API webhook.
Entry point for the Carbolo WhatsApp Test-Drive Booking Agent.
"""

import os
import logging
import hmac
import hashlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse
import httpx
from dotenv import load_dotenv

# ── Load .env FIRST before reading any os.getenv() ───────────────────────────
load_dotenv()

from agent import run_agent
from scheduler import start_scheduler, stop_scheduler, list_pending_reminders

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config — read AFTER load_dotenv() ────────────────────────────────────────
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "carbolo_verify_token")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")  # optional signature verification


# ── App lifespan ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start/stop scheduler with the app."""
    logger.info("Starting Carbolo Agent...")
    start_scheduler()
    yield
    logger.info("Shutting down Carbolo Agent...")
    stop_scheduler()


app = FastAPI(
    title="Carbolo — WhatsApp Test-Drive Booking Agent",
    description="AI car sales agent for Indian dealerships",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Seen message IDs (prevents processing duplicate webhook deliveries) ───────
# Meta occasionally delivers the same webhook event twice within seconds.
# We track the last 500 message IDs and silently drop duplicates.
_seen_message_ids: set[str] = set()
_seen_message_ids_order: list[str] = []   # to cap memory
MAX_SEEN_IDS = 500


# ── Webhook verification (GET) ────────────────────────────────────────────────
@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Meta webhook verification handshake."""
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return PlainTextResponse(content=hub_challenge)
    logger.warning(f"Webhook verification failed. Token: {hub_verify_token}")
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Incoming messages (POST) ──────────────────────────────────────────────────
@app.post("/webhook")
async def receive_message(request: Request):
    """
    Handle incoming WhatsApp messages.
    Meta sends a JSON payload; we extract the message and phone, then run the agent.
    """
    # Signature verification disabled for now
    # Enable by setting WHATSAPP_APP_SECRET in environment variables
    data = await request.json()
    logger.debug(f"Webhook payload: {data}")

    try:
        # Navigate Meta's nested payload structure
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            # Could be a status update (delivered, read) — ignore
            return {"status": "ok"}

        message = messages[0]
        msg_type = message.get("type")

        # Only handle text messages for now
        if msg_type != "text":
            logger.info(f"Ignoring non-text message type: {msg_type}")
            await _send_whatsapp_reply(
                message["from"],
                "Sorry, I can only handle text messages right now. Please type your question! 😊"
            )
            return {"status": "ok"}

        phone = message["from"]  # e.g. "919876543210"
        text = message["text"]["body"].strip()
        msg_id = message.get("id", "")

        # ── Deduplicate: Meta can deliver the same webhook twice ──────────
        if msg_id and msg_id in _seen_message_ids:
            logger.info(f"Duplicate webhook message {msg_id} — ignored")
            return {"status": "ok"}
        if msg_id:
            _seen_message_ids.add(msg_id)
            _seen_message_ids_order.append(msg_id)
            if len(_seen_message_ids_order) > MAX_SEEN_IDS:
                oldest = _seen_message_ids_order.pop(0)
                _seen_message_ids.discard(oldest)

        logger.info(f"Message from {phone}: {text[:80]}")

        # Run the agent (this may call tools, do multi-step reasoning)
        reply = run_agent(phone=phone, user_message=text)

        # Send reply back
        await _send_whatsapp_reply(phone, reply)

        return {"status": "ok"}

    except (KeyError, IndexError) as e:
        logger.error(f"Failed to parse webhook payload: {e}")
        return {"status": "ok"}  # Always return 200 to Meta
    except Exception as e:
        logger.error(f"Webhook handler error: {e}", exc_info=True)
        return {"status": "ok"}  # Always return 200 to avoid Meta retries


async def _send_whatsapp_reply(to_phone: str, message: str):
    """Send a WhatsApp text message via Meta Cloud API."""
    url = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": message},
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()
            logger.info(f"Reply sent to {to_phone}")
        except httpx.HTTPStatusError as e:
            logger.error(f"WhatsApp API error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.error(f"Failed to send WhatsApp reply: {e}")


# ── Health & admin endpoints ──────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "service": "Carbolo WhatsApp Agent",
        "status": "running",
        "version": "1.0.0",
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/admin/reminders")
async def admin_reminders():
    """List all pending reminder jobs (for debugging)."""
    return {"pending_reminders": list_pending_reminders()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=os.getenv("ENV", "production") == "development",
    )
