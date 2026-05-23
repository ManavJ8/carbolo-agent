"""
agent.py — LLM orchestration using Claude with tool calling.
The agent answers from the KB, calls check_availability and book_test_drive tools,
and manages per-phone conversation history.
"""

import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from kb import search_kb, get_kb_summary
from tools import check_availability, book_test_drive
from scheduler import schedule_reminders

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── In-memory conversation store (per phone number) ───────────────────────────
# For production you'd use Redis; this works fine for the demo.
_conversations: dict[str, list] = {}

# Tracks the last confirmed booking per phone — used for idempotency guard
# so that a second "confirm" from the same customer does not create a second event.
_last_booking: dict[str, dict] = {}

MAX_HISTORY = 20  # messages to keep per phone


def get_history(phone: str) -> list:
    return _conversations.get(phone, [])


def save_turn(phone: str, role: str, content):
    if phone not in _conversations:
        _conversations[phone] = []
    _conversations[phone].append({"role": role, "content": content})
    # Keep last N messages to avoid token bloat
    _conversations[phone] = _conversations[phone][-MAX_HISTORY:]


def set_last_booking(phone: str, booking: dict):
    """Remember the most recent confirmed booking for this phone (idempotency)."""
    _last_booking[phone] = booking


def get_last_booking(phone: str) -> dict | None:
    return _last_booking.get(phone)


def clear_history(phone: str):
    _conversations.pop(phone, None)
    _last_booking.pop(phone, None)


# ── Tool definitions for Claude ───────────────────────────────────────────────
TOOLS = [
    {
        "name": "check_availability",
        "description": (
            "Check real-time availability on Google Calendar and return the next "
            "3 open 30-minute test-drive slots. Call this when the customer asks "
            "about available times, slots, or wants to book a test drive."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "preferred_date": {
                    "type": "string",
                    "description": (
                        "Customer's preferred date or period, e.g. 'this weekend', "
                        "'tomorrow', 'Saturday', '2024-12-14'. Pass null for next available."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "book_test_drive",
        "description": (
            "Book a test drive on Google Calendar and schedule WhatsApp reminders. "
            "Call this only after the customer has confirmed a specific slot. "
            "Collect customer_name and car_model before calling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Customer's full name",
                },
                "phone": {
                    "type": "string",
                    "description": "Customer's WhatsApp number with country code, e.g. +919876543210",
                },
                "car_model": {
                    "type": "string",
                    "description": "Full car model and variant, e.g. 'Maruti Brezza ZXi+'",
                },
                "slot_datetime": {
                    "type": "string",
                    "description": "ISO 8601 datetime of the chosen slot, e.g. 2024-12-14T11:00:00+05:30",
                },
            },
            "required": ["customer_name", "phone", "car_model", "slot_datetime"],
        },
    },
]


# ── System prompt ─────────────────────────────────────────────────────────────
def _build_system_prompt(phone: str, user_message: str) -> str:
    kb_context = search_kb(user_message)
    kb_summary = get_kb_summary()
    now_str = datetime.now(IST).strftime("%A, %d %B %Y, %I:%M %p IST")

    return f"""You are Carbolo, a friendly car sales assistant for a Maruti Suzuki dealership in India. You talk to customers over WhatsApp.

Current date/time: {now_str}
Customer's WhatsApp number: {phone}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 RULE 1 — ANTI-HALLUCINATION (most important rule)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You MUST answer car questions ONLY using the KNOWLEDGE BASE DATA section below.

- If a feature IS in the KB → state it exactly as written.
- If a feature is NOT in the KB for that variant → say:
  "I don't have that detail right now — let me check with our team and get back to you! 🙏"
- NEVER guess, infer, or invent specs, prices, mileage, features, or colors.
- NEVER say a feature exists unless the KB explicitly lists it.
- NEVER say a variant has a sunroof, camera, or any feature unless `sunroof: true` or
  that feature appears in the features list for that exact variant.

WRONG example (hallucination): "The VXi might have cruise control as an option."
RIGHT example (grounded): "The VXi doesn't include cruise control — that's on the ZXi. Want details on the ZXi?"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 RULE 2 — HINGLISH HANDLING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Customers often write in Hinglish (Hindi + English mixed). Handle it naturally:
- "Brezza ka mileage kitna hai?" → answer the mileage for Brezza from the KB
- "kal evening slot hai kya?" → treat as "is there a slot tomorrow evening?" → call check_availability with preferred_date="tomorrow"
- "sunroof hai isme?" → answer whether that variant has a sunroof from KB
- "VXi theek hai, booking karo" → proceed to collect name and book
- "aaj ke liye slot chahiye" → call check_availability with preferred_date="today"
- "confirm kar do" → proceed to call book_test_drive if you have all details
Respond in the same language mix the customer uses. Keep it conversational and warm.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 RULE 3 — BOOKING FLOW (step by step)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Follow this exact sequence — never skip a step:
1. Customer wants a test drive → call check_availability tool (NEVER list fake slots)
2. Show the real slots returned by the tool, numbered
3. Customer picks a slot → ask for their name if you don't have it
4. You have name + slot → call book_test_drive tool (NEVER confirm without calling this)
5. Tool returns success → send confirmation message with date/time

NEVER confirm a booking with "Done ✅" unless book_test_drive tool returned status=booked or status=already_booked.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 RULE 4 — IDEMPOTENT BOOKING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
If the customer says "confirm" or "book" a second time for the same slot:
- The book_test_drive tool will return status=already_booked
- Respond: "You're already booked for [slot]! No duplicate created. See you then 😊"
- NEVER call book_test_drive twice for the same slot in the same conversation

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 SHOWROOM INFO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Test drives: 30 minutes
- Hours: 10 AM – 6 PM, 7 days a week
- For questions outside the KB, say you'll check with the team

{kb_summary}

{kb_context}
"""


# ── Tool executor ─────────────────────────────────────────────────────────────
def _execute_tool(tool_name: str, tool_input: dict, phone: str) -> str:
    """Execute the actual tool and return result as JSON string."""
    logger.info(f"Executing tool: {tool_name} with input: {tool_input}")

    if tool_name == "check_availability":
        result = check_availability(
            preferred_date=tool_input.get("preferred_date")
        )

    elif tool_name == "book_test_drive":
        # ── Application-level idempotency guard ───────────────────────────
        # If the agent somehow calls book_test_drive twice in one conversation
        # for the same slot (e.g. customer double-taps "confirm"), we detect it
        # here BEFORE hitting the Calendar API, and return already_booked immediately.
        slot_requested = tool_input.get("slot_datetime", "")
        last = get_last_booking(phone)
        if last and last.get("slot_datetime_iso") and slot_requested:
            # Compare just the date+hour+minute to avoid tz-format mismatches
            try:
                from tools import _parse_iso_datetime
                requested_dt = _parse_iso_datetime(slot_requested)
                booked_dt = _parse_iso_datetime(last["slot_datetime_iso"])
                if abs((requested_dt - booked_dt).total_seconds()) < 120:  # within 2 min = same slot
                    logger.info(f"App-level idempotency: duplicate book_test_drive blocked for {phone}")
                    return json.dumps({
                        "status": "already_booked",
                        "event_id": last.get("event_id", ""),
                        "slot_datetime_iso": last["slot_datetime_iso"],
                        "message": f"Already booked for {last.get('slot_display', slot_requested)}",
                        "customer_name": tool_input.get("customer_name", ""),
                        "car_model": tool_input.get("car_model", ""),
                    })
            except Exception:
                pass  # if parsing fails, let it fall through to the Calendar API check

        # Force the phone into the tool input so Claude can't override it
        tool_input["phone"] = phone
        result = book_test_drive(
            customer_name=tool_input["customer_name"],
            phone=phone,
            car_model=tool_input["car_model"],
            slot_datetime=tool_input["slot_datetime"],
        )

        # On success, save booking state AND schedule reminders
        if result.get("status") == "booked":
            set_last_booking(phone, result)
            reminder_result = schedule_reminders(
                phone=phone,
                customer_name=tool_input["customer_name"],
                car_model=tool_input["car_model"],
                slot_datetime_iso=result["slot_datetime_iso"],
            )
            result["reminders_scheduled"] = reminder_result.get("scheduled", [])

        # On already_booked (Calendar-level idempotency), still save state
        elif result.get("status") == "already_booked":
            set_last_booking(phone, result)

    else:
        result = {"error": f"Unknown tool: {tool_name}"}

    logger.info(f"Tool result: {result}")
    return json.dumps(result, default=str)


# ── Main agent function ────────────────────────────────────────────────────────
def run_agent(phone: str, user_message: str) -> str:
    """
    Process a WhatsApp message and return the agent's reply.
    Maintains conversation history per phone number.

    Args:
        phone: Customer's WhatsApp number (with country code)
        user_message: The incoming message text

    Returns:
        Agent's reply string
    """
    try:
        # Build message history
        history = get_history(phone)
        system_prompt = _build_system_prompt(phone, user_message)

        # Add user message to current turn
        messages = history + [{"role": "user", "content": user_message}]

        # ── Agentic loop (handles multi-step tool use) ─────────────────────
        max_iterations = 5
        for iteration in range(max_iterations):
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                system=system_prompt,
                tools=TOOLS,
                messages=messages,
            )

            logger.info(f"Claude response stop_reason: {response.stop_reason} (iteration {iteration})")

            # Collect the assistant message content
            assistant_content = response.content

            if response.stop_reason == "end_turn":
                # Final text response — extract and return
                text_blocks = [b.text for b in response.content if hasattr(b, "text")]
                final_reply = " ".join(text_blocks).strip()

                # Save to history
                save_turn(phone, "user", user_message)
                save_turn(phone, "assistant", final_reply)

                return final_reply

            elif response.stop_reason == "tool_use":
                # Add assistant's tool-use message to conversation
                messages.append({"role": "assistant", "content": assistant_content})

                # Execute each tool call
                tool_results = []
                for block in assistant_content:
                    if block.type == "tool_use":
                        tool_result = _execute_tool(block.name, block.input, phone)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": tool_result,
                        })

                # Add tool results to conversation
                messages.append({"role": "user", "content": tool_results})

            else:
                # Unexpected stop reason
                logger.warning(f"Unexpected stop_reason: {response.stop_reason}")
                break

        # If we exhausted iterations without end_turn
        fallback = "I'm having trouble processing that right now. Please try again in a moment! 🙏"
        save_turn(phone, "user", user_message)
        save_turn(phone, "assistant", fallback)
        return fallback

    except anthropic.APIConnectionError:
        logger.error("Anthropic API connection error")
        return "Sorry, I'm having connectivity issues right now. Please try again shortly!"
    except anthropic.RateLimitError:
        logger.error("Anthropic rate limit hit")
        return "I'm a bit busy right now! Please try again in a minute. 🙏"
    except Exception as e:
        logger.error(f"run_agent error for {phone}: {e}", exc_info=True)
        return "Something went wrong on my end. Please try again or call us directly!"
