import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from twilio.rest import Client as TwilioClient

load_dotenv()

from agent import run_agent
from scheduler import start_scheduler, stop_scheduler, list_pending_reminders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Seen message IDs to prevent duplicates
_seen_message_ids: set[str] = set()
_seen_message_ids_order: list[str] = []
MAX_SEEN_IDS = 500


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Carbolo Agent...")
    start_scheduler()
    yield
    logger.info("Shutting down Carbolo Agent...")
    stop_scheduler()


app = FastAPI(
    title="Carbolo — WhatsApp Test-Drive Booking Agent",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/webhook")
async def receive_message(
    Body: str = Form(None),
    From: str = Form(None),
    MessageSid: str = Form(None),
):
    """Handle incoming Twilio WhatsApp messages."""
    if not Body or not From:
        return PlainTextResponse("ok")

    # Deduplicate
    if MessageSid and MessageSid in _seen_message_ids:
        logger.info(f"Duplicate message {MessageSid} ignored")
        return PlainTextResponse("ok")
    if MessageSid:
        _seen_message_ids.add(MessageSid)
        _seen_message_ids_order.append(MessageSid)
        if len(_seen_message_ids_order) > MAX_SEEN_IDS:
            oldest = _seen_message_ids_order.pop(0)
            _seen_message_ids.discard(oldest)

    # Extract phone number — Twilio sends "whatsapp:+919876543210"
    phone = From.replace("whatsapp:", "").strip()
    text = Body.strip()

    logger.info(f"Message from {phone}: {text[:80]}")

    # Run agent
    reply = run_agent(phone=phone, user_message=text)

    # Send reply via Twilio
    _send_twilio_reply(to=From, message=reply)

    return PlainTextResponse("ok")


def _send_twilio_reply(to: str, message: str):
    """Send WhatsApp message via Twilio."""
    try:
        msg = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to,
            body=message,
        )
        logger.info(f"Reply sent: {msg.sid}")
    except Exception as e:
        logger.error(f"Twilio send failed: {e}")


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
    return {"pending_reminders": list_pending_reminders()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=False,
    )