"""
tools.py — Real Google Calendar tool implementations.
These are the actual functions the LLM calls via tool use.
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/calendar"]
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
SLOT_DURATION_MINUTES = 30
IST = ZoneInfo("Asia/Kolkata")

# Business hours: 10am – 6pm IST
BUSINESS_START_HOUR = 10
BUSINESS_END_HOUR = 18


def _get_calendar_service():
    """
    Build and return Google Calendar API service.
    Supports two credential modes:
    - Local dev: reads service_account.json file
    - Production (Render): reads GOOGLE_SERVICE_ACCOUNT_JSON env var
    """
    sa_json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_json_str:
        # Production: credentials stored as env var (JSON string)
        sa_info = json.loads(sa_json_str)
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=SCOPES
        )
    else:
        # Local dev: credentials in a JSON file
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
    return build("calendar", "v3", credentials=creds)


def check_availability(preferred_date: str = None) -> dict:
    """
    Check Google Calendar free/busy and return next 3 open 30-min slots.
    Called by the LLM as a tool.

    Args:
        preferred_date: natural string like 'this weekend', '2024-12-14', 'tomorrow'

    Returns:
        dict with 'slots' list or 'error'
    """
    try:
        service = _get_calendar_service()

        # Parse preferred date or default to next 7 days
        now = datetime.now(IST)
        search_start, search_end = _parse_preferred_date(preferred_date, now)

        # Query free/busy
        body = {
            "timeMin": search_start.isoformat(),
            "timeMax": search_end.isoformat(),
            "items": [{"id": CALENDAR_ID}],
        }
        freebusy = service.freebusy().query(body=body).execute()
        busy_periods = freebusy["calendars"][CALENDAR_ID]["busy"]

        # Find open slots
        open_slots = _find_open_slots(search_start, search_end, busy_periods, count=3)

        if not open_slots:
            return {
                "slots": [],
                "message": "No open slots found in that period. Try a different date.",
            }

        formatted = []
        for i, slot in enumerate(open_slots, 1):
            formatted.append({
                "index": i,
                "datetime_iso": slot.isoformat(),
                "display": slot.strftime("%A, %d %b %Y at %I:%M %p IST"),
                "date": slot.strftime("%Y-%m-%d"),
                "time": slot.strftime("%H:%M"),
            })

        return {"slots": formatted, "count": len(formatted)}

    except FileNotFoundError:
        logger.error("service_account.json not found")
        return {"error": "Calendar not configured. Please contact support."}
    except HttpError as e:
        logger.error(f"Google Calendar API error: {e}")
        return {"error": f"Calendar API error: {str(e)}"}
    except Exception as e:
        logger.error(f"check_availability error: {e}", exc_info=True)
        return {"error": "Could not check availability right now. Please try again."}


def book_test_drive(
    customer_name: str,
    phone: str,
    car_model: str,
    slot_datetime: str,
) -> dict:
    """
    Create a Google Calendar event for the test drive.
    Idempotent: if event already exists for same phone+slot, returns existing event.

    Args:
        customer_name: Customer's full name
        phone: WhatsApp phone number (with country code)
        car_model: e.g. "Maruti Brezza ZXi+"
        slot_datetime: ISO format datetime string

    Returns:
        dict with booking confirmation or error
    """
    try:
        service = _get_calendar_service()

        # Parse the slot datetime
        slot_dt = _parse_iso_datetime(slot_datetime)
        slot_end = slot_dt + timedelta(minutes=SLOT_DURATION_MINUTES)

        # ── Idempotency check ──────────────────────────────────────────────
        # Search for existing event with same phone in that time window
        existing_events = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=(slot_dt - timedelta(minutes=5)).isoformat(),
            timeMax=(slot_end + timedelta(minutes=5)).isoformat(),
            q=phone,  # search by phone number in event description
            singleEvents=True,
        ).execute()

        if existing_events.get("items"):
            ev = existing_events["items"][0]
            logger.info(f"Duplicate booking prevented for {phone} at {slot_datetime}")
            return {
                "status": "already_booked",
                "event_id": ev["id"],
                "message": f"Test drive already booked for {slot_dt.strftime('%A, %d %b at %I:%M %p')}",
                "slot_datetime_iso": slot_dt.isoformat(),
                "customer_name": customer_name,
                "car_model": car_model,
            }

        # ── Create new event ───────────────────────────────────────────────
        event_body = {
            "summary": f"Test Drive — {car_model} ({customer_name})",
            "description": (
                f"Customer: {customer_name}\n"
                f"Phone: {phone}\n"
                f"Car: {car_model}\n"
                f"Booked via Carbolo WhatsApp Agent"
            ),
            "start": {
                "dateTime": slot_dt.isoformat(),
                "timeZone": "Asia/Kolkata",
            },
            "end": {
                "dateTime": slot_end.isoformat(),
                "timeZone": "Asia/Kolkata",
            },
            "reminders": {"useDefault": False, "overrides": []},
        }

        created_event = service.events().insert(
            calendarId=CALENDAR_ID, body=event_body
        ).execute()

        logger.info(f"Calendar event created: {created_event['id']} for {phone}")

        return {
            "status": "booked",
            "event_id": created_event["id"],
            "event_link": created_event.get("htmlLink", ""),
            "slot_datetime_iso": slot_dt.isoformat(),
            "slot_display": slot_dt.strftime("%A, %d %b %Y at %I:%M %p IST"),
            "customer_name": customer_name,
            "car_model": car_model,
            "phone": phone,
            "message": f"Test drive confirmed for {slot_dt.strftime('%A, %d %b at %I:%M %p')}!",
        }

    except FileNotFoundError:
        logger.error("service_account.json not found")
        return {"error": "Calendar not configured. Please contact support."}
    except HttpError as e:
        logger.error(f"Google Calendar API error: {e}")
        return {"error": f"Calendar booking failed: {str(e)}"}
    except Exception as e:
        logger.error(f"book_test_drive error: {e}", exc_info=True)
        return {"error": "Booking failed. Please try again or call us."}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_preferred_date(preferred_date: str, now: datetime):
    """
    Parse natural language date string to (start, end) datetime range.
    Handles English AND Hinglish date expressions.
    Defaults to next 7 days if None or unrecognised.
    """
    if not preferred_date:
        return now, now + timedelta(days=7)

    pref = preferred_date.lower().strip()

    # ── Hinglish date aliases ──────────────────────────────────────────────
    hinglish_date_map = {
        "kal":       "tomorrow",    # kal = tomorrow (or yesterday — context says tomorrow)
        "aaj":       "today",       # aaj = today
        "parso":     "day_after",   # parso = day after tomorrow
        "shanivar":  "saturday",    # Saturday in Hindi
        "shanivaar": "saturday",
        "ravivar":   "sunday",      # Sunday in Hindi
        "ravivaar":  "sunday",
        "somvar":    "monday",
        "mangalvar": "tuesday",
        "budhvar":   "wednesday",
        "guruvar":   "thursday",
        "shukravar": "friday",
    }
    for hindi, english in hinglish_date_map.items():
        pref = pref.replace(hindi, english)

    # ── Time-of-day hints (used to narrow slot search) ────────────────────
    prefer_afternoon = any(w in pref for w in ("evening", "sham", "afternoon", "shaam"))
    prefer_morning   = any(w in pref for w in ("morning", "subah", "subha"))

    def _window_for_day(d: datetime):
        """Return (start, end) for a given day, optionally biased by time of day."""
        if prefer_afternoon:
            start = d.replace(hour=14, minute=0, second=0, microsecond=0)
        elif prefer_morning:
            start = d.replace(hour=BUSINESS_START_HOUR, minute=0, second=0, microsecond=0)
        else:
            start = d.replace(hour=BUSINESS_START_HOUR, minute=0, second=0, microsecond=0)
        end = d.replace(hour=BUSINESS_END_HOUR, minute=0, second=0, microsecond=0)
        return start, end

    if "tomorrow" in pref:
        d = now + timedelta(days=1)
        return _window_for_day(d)

    if "day_after" in pref:
        d = now + timedelta(days=2)
        return _window_for_day(d)

    if "today" in pref:
        start = max(now, now.replace(hour=BUSINESS_START_HOUR, minute=0, second=0, microsecond=0))
        end = now.replace(hour=BUSINESS_END_HOUR, minute=0, second=0, microsecond=0)
        return start, end

    if "weekend" in pref or "saturday" in pref or "sunday" in pref:
        # Find next Saturday
        days_ahead = (5 - now.weekday()) % 7  # 5 = Saturday
        if days_ahead == 0:
            days_ahead = 7
        next_sat = now + timedelta(days=days_ahead)

        if "sunday" in pref:
            target = next_sat + timedelta(days=1)
            return _window_for_day(target)

        # "weekend" = Saturday + Sunday
        sat_start, _ = _window_for_day(next_sat)
        sun = next_sat + timedelta(days=1)
        _, sun_end = _window_for_day(sun)
        return sat_start, sun_end

    # Day-of-week names
    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    }
    for day_name, weekday_num in day_map.items():
        if day_name in pref:
            days_ahead = (weekday_num - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            d = now + timedelta(days=days_ahead)
            return _window_for_day(d)

    # Try to parse explicit date like "2024-12-14"
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            d = datetime.strptime(preferred_date.strip(), fmt).replace(tzinfo=IST)
            return _window_for_day(d)
        except ValueError:
            continue

    # Default: next 7 days
    return now, now + timedelta(days=7)


def _find_open_slots(
    search_start: datetime,
    search_end: datetime,
    busy_periods: list,
    count: int = 3,
) -> list:
    """Walk through business hours and find open 30-min slots."""
    open_slots = []
    cursor = search_start

    # Round up to next clean 30-min mark
    if cursor.minute not in (0, 30):
        cursor = cursor.replace(
            minute=30 if cursor.minute < 30 else 0,
            second=0, microsecond=0
        )
        if cursor.minute == 0:
            cursor += timedelta(hours=1)

    while cursor < search_end and len(open_slots) < count:
        # Skip non-business hours
        if cursor.hour < BUSINESS_START_HOUR or cursor.hour >= BUSINESS_END_HOUR:
            cursor = cursor.replace(hour=BUSINESS_START_HOUR, minute=0, second=0, microsecond=0)
            cursor += timedelta(days=1)
            continue

        # Skip weekends (optional — remove if showroom is open weekends)
        # if cursor.weekday() >= 6:  # Sunday
        #     cursor += timedelta(days=1)
        #     continue

        slot_end = cursor + timedelta(minutes=SLOT_DURATION_MINUTES)

        # Check against busy periods
        is_busy = False
        for busy in busy_periods:
            busy_start = datetime.fromisoformat(busy["start"].replace("Z", "+00:00")).astimezone(IST)
            busy_end = datetime.fromisoformat(busy["end"].replace("Z", "+00:00")).astimezone(IST)
            if cursor < busy_end and slot_end > busy_start:
                is_busy = True
                cursor = busy_end  # jump past the busy period
                break

        if not is_busy:
            open_slots.append(cursor)
            cursor += timedelta(minutes=SLOT_DURATION_MINUTES)

    return open_slots


def _parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO datetime string, ensuring IST timezone."""
    # Remove trailing Z and handle various formats
    dt_str = dt_str.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        else:
            dt = dt.astimezone(IST)
        return dt
    except ValueError:
        # Try without timezone
        dt = datetime.strptime(dt_str[:19], "%Y-%m-%dT%H:%M:%S")
        return dt.replace(tzinfo=IST)
