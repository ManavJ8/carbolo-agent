"""
scheduler.py — APScheduler with SQLite persistence.
Schedules T-24h and T-2h WhatsApp reminders for booked test drives.
Survives server restarts because jobs are stored in reminders.db.
"""

import os
import logging
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── Scheduler setup ────────────────────────────────────────────────────────────
jobstores = {
    "default": SQLAlchemyJobStore(url="sqlite:///reminders.db")
}
executors = {"default": ThreadPoolExecutor(5)}
job_defaults = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600}

scheduler = BackgroundScheduler(
    jobstores=jobstores,
    executors=executors,
    job_defaults=job_defaults,
    timezone=IST,
)


def start_scheduler():
    """Start the scheduler. Call once at app startup."""
    if not scheduler.running:
        scheduler.start()
        logger.info("APScheduler started with SQLite persistence")


def stop_scheduler():
    """Graceful shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")


# ── WhatsApp sender ────────────────────────────────────────────────────────────

def send_whatsapp_message(to_phone: str, message: str) -> bool:
    """
    Send a WhatsApp message via Meta Cloud API.
    Returns True on success, False on failure.
    """
    token = os.getenv("WHATSAPP_TOKEN")
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

    if not token or not phone_number_id:
        logger.error("WhatsApp credentials not set in environment")
        return False

    # Normalise phone: must start with country code, no + or spaces
    phone = to_phone.strip().lstrip("+").replace(" ", "").replace("-", "")

    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message},
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        logger.info(f"WhatsApp message sent to {phone}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"WhatsApp send failed to {phone}: {e}")
        return False


# ── Reminder job functions ─────────────────────────────────────────────────────

def send_24h_reminder(phone: str, customer_name: str, car_model: str, slot_iso: str):
    """Job function: send T-24h reminder."""
    slot_dt = datetime.fromisoformat(slot_iso).astimezone(IST)
    slot_display = slot_dt.strftime("%A, %d %b at %I:%M %p")

    message = (
        f"🚗 Reminder: Hi {customer_name}! Your test drive for *{car_model}* "
        f"is tomorrow — *{slot_display} IST*.\n\n"
        f"📍 Please arrive 5 minutes early. See you at the showroom!\n\n"
        f"Reply CANCEL if you need to reschedule."
    )
    success = send_whatsapp_message(phone, message)
    logger.info(f"T-24h reminder {'sent' if success else 'FAILED'} to {phone} for {slot_iso}")


def send_2h_reminder(phone: str, customer_name: str, car_model: str, slot_iso: str):
    """Job function: send T-2h reminder."""
    slot_dt = datetime.fromisoformat(slot_iso).astimezone(IST)
    slot_display = slot_dt.strftime("%I:%M %p")

    message = (
        f"⏰ Just 2 hours to go! Hi {customer_name}, your *{car_model}* test drive "
        f"is at *{slot_display} IST* today.\n\n"
        f"We're excited to see you! 🎉"
    )
    success = send_whatsapp_message(phone, message)
    logger.info(f"T-2h reminder {'sent' if success else 'FAILED'} to {phone} for {slot_iso}")


# ── Scheduler API ──────────────────────────────────────────────────────────────

def schedule_reminders(
    phone: str,
    customer_name: str,
    car_model: str,
    slot_datetime_iso: str,
) -> dict:
    """
    Schedule T-24h and T-2h WhatsApp reminders for a booking.
    Idempotent: replace_existing=True means re-booking same slot just updates.
    Persisted to SQLite so reminders survive server restarts.
    """
    try:
        slot_dt = datetime.fromisoformat(slot_datetime_iso).astimezone(IST)
        now = datetime.now(IST)

        # Compute trigger times
        t_24h = slot_dt - timedelta(hours=24)
        t_2h = slot_dt - timedelta(hours=2)

        # Demo mode: fire much sooner for testing
        demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true"
        if demo_mode:
            t_24h = now + timedelta(minutes=2)
            t_2h = now + timedelta(minutes=5)
            logger.info("DEMO_MODE: reminders scheduled 2min and 5min from now")

        scheduled = []

        # Build stable job IDs (idempotent)
        safe_phone = phone.lstrip("+").replace(" ", "")
        safe_slot = slot_dt.strftime("%Y%m%dT%H%M")
        job_24h_id = f"rem24_{safe_phone}_{safe_slot}"
        job_2h_id = f"rem2_{safe_phone}_{safe_slot}"

        # T-24h reminder
        if t_24h > now:
            scheduler.add_job(
                send_24h_reminder,
                trigger="date",
                run_date=t_24h,
                args=[phone, customer_name, car_model, slot_datetime_iso],
                id=job_24h_id,
                replace_existing=True,
            )
            scheduled.append(f"T-24h at {t_24h.strftime('%d %b %H:%M IST')}")
            logger.info(f"Scheduled T-24h reminder job {job_24h_id} at {t_24h}")
        else:
            logger.info(f"Skipping T-24h reminder for {phone} — already past")

        # T-2h reminder
        if t_2h > now:
            scheduler.add_job(
                send_2h_reminder,
                trigger="date",
                run_date=t_2h,
                args=[phone, customer_name, car_model, slot_datetime_iso],
                id=job_2h_id,
                replace_existing=True,
            )
            scheduled.append(f"T-2h at {t_2h.strftime('%d %b %H:%M IST')}")
            logger.info(f"Scheduled T-2h reminder job {job_2h_id} at {t_2h}")
        else:
            logger.info(f"Skipping T-2h reminder for {phone} — already past")

        return {
            "scheduled": scheduled,
            "count": len(scheduled),
        }

    except Exception as e:
        logger.error(f"schedule_reminders error: {e}", exc_info=True)
        return {"scheduled": [], "error": str(e)}


def cancel_reminders(phone: str, slot_datetime_iso: str):
    """Cancel reminders for a specific booking (for reschedule/cancel flow)."""
    try:
        slot_dt = datetime.fromisoformat(slot_datetime_iso).astimezone(IST)
        safe_phone = phone.lstrip("+").replace(" ", "")
        safe_slot = slot_dt.strftime("%Y%m%dT%H%M")

        for job_id in [f"rem24_{safe_phone}_{safe_slot}", f"rem2_{safe_phone}_{safe_slot}"]:
            try:
                scheduler.remove_job(job_id)
                logger.info(f"Cancelled reminder job {job_id}")
            except Exception:
                pass  # Job may not exist, that's fine
    except Exception as e:
        logger.error(f"cancel_reminders error: {e}")


def list_pending_reminders() -> list:
    """Return list of all pending reminder jobs (for debugging/admin)."""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time),
            "func": job.func.__name__ if job.func else "unknown",
        })
    return jobs
