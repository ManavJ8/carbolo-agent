# 🚗 Carbolo — WhatsApp Test-Drive Booking Agent

An AI-powered WhatsApp agent that books car test drives end-to-end. Built for Indian car dealerships — answers questions from a grounded knowledge base, checks real Google Calendar availability, creates booking events, and sends T-24h and T-2h WhatsApp reminders.

---

## Architecture

```
Customer (WhatsApp)
       │
       ▼
Meta WhatsApp Cloud API
       │  webhook POST /webhook
       ▼
┌─────────────────────────┐
│   FastAPI (main.py)     │  ← receives & sends WhatsApp messages
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│   Agent (agent.py)      │  ← Claude claude-haiku-4-5 with tool calling
│   + KB context inject   │    conversation history per phone number
└────────┬────────────────┘
         │  tool calls
    ┌────┴─────┐
    ▼          ▼
check_      book_test_
availability  drive
    │          │
    └────┬─────┘
         ▼
┌─────────────────────────┐
│  Google Calendar API    │  ← real FreeBusy query + event creation
│  (service account)      │
└─────────────────────────┘
         │ on booking
         ▼
┌─────────────────────────┐
│  APScheduler            │  ← schedules T-24h and T-2h jobs
│  (SQLite persistence)   │    survives server restarts
└────────────┬────────────┘
             │ at trigger time
             ▼
Meta WhatsApp Cloud API  →  Customer gets reminder
```

---

## Features

| Feature | Status |
|---|---|
| WhatsApp integration (live number) | ✅ |
| KB-grounded answers (no hallucination) | ✅ |
| Google Calendar availability check (tool call) | ✅ |
| Google Calendar event creation (tool call) | ✅ |
| T-24h & T-2h WhatsApp reminders | ✅ |
| Idempotent booking (no duplicate events) | ✅ |
| Hinglish conversation handling | ✅ |
| Reminder persistence across restarts (SQLite) | ✅ |

---

## Tech Choices & Why

| Component | Choice | Why |
|---|---|---|
| **Framework** | FastAPI | Async, minimal, perfect for webhook handlers |
| **LLM** | Claude Haiku (Anthropic) | Cheapest model with reliable tool calling; free tier available |
| **KB / RAG** | Simple JSON + keyword search | KB is only 3–5 models; vector DB would be overkill and a liability for grounding |
| **Calendar** | Google Calendar API + Service Account | Service account avoids OAuth flow complexity; FreeBusy API gives real availability |
| **Scheduler** | APScheduler + SQLite jobstore | Built-in persistence — reminder jobs survive server restarts without Redis |
| **WhatsApp** | Meta WhatsApp Cloud API | Free test number; official API; widely documented |
| **Hosting** | Render.com | Free tier, automatic deploys from GitHub, public HTTPS URL for webhook |

---

## Local Setup

### Prerequisites
- Python 3.11+
- A Meta Developer account (free)
- A Google Cloud account (free)
- An Anthropic API key (free tier)

On Windows, install Python from [python.org](https://www.python.org/downloads/) and make sure "Add python.exe to PATH" is enabled. If Windows keeps redirecting `python` to the Microsoft Store, disable the `python.exe` and `python3.exe` app execution aliases in Settings.

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/carbolo-agent.git
cd carbolo-agent
# Windows: py -3 -m venv venv
# macOS/Linux: python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Set up environment variables

```bash
cp .env.example .env
# Edit .env with your actual keys (see sections below)
```

### 3. Google Calendar service account

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (e.g. "Carbolo")
3. Enable **Google Calendar API**
4. Go to **IAM & Admin → Service Accounts → Create Service Account**
5. Name it "carbolo-agent", click Create
6. Click the service account → **Keys → Add Key → JSON** → download
7. Save the downloaded file as `service_account.json` in the project root
8. Open [calendar.google.com](https://calendar.google.com) → Create a new calendar
9. Go to calendar **Settings → Share with specific people** → add the service account email (looks like `carbolo-agent@your-project.iam.gserviceaccount.com`) with **"Make changes to events"** permission
10. Copy the **Calendar ID** (under Integrate calendar) → put in `.env` as `GOOGLE_CALENDAR_ID`

### 4. Meta WhatsApp setup

1. Go to [developers.facebook.com](https://developers.facebook.com) → Create App → Business
2. Add WhatsApp product
3. In **API Setup**: copy the **Phone Number ID** → `WHATSAPP_PHONE_NUMBER_ID` in `.env`
4. Generate a **Temporary Access Token** → `WHATSAPP_TOKEN` in `.env`
5. Add your personal number as a test recipient
6. Webhook setup: set URL to `https://your-app.onrender.com/webhook`, verify token = `carbolo_verify_token`

### 5. Run locally (with ngrok for webhook testing)

```bash
# Terminal 1 — run the app
# Windows: py -3 main.py
# macOS/Linux: python3 main.py

# Terminal 2 — expose localhost via ngrok
ngrok http 8000
# Copy the https URL and use as webhook URL in Meta dashboard
```

### 6. Run in demo mode (see reminders fire fast)

```bash
# Windows: set DEMO_MODE=true && py -3 main.py
# macOS/Linux: DEMO_MODE=true python3 main.py
# Reminders fire 2 min and 5 min after booking instead of 24h/2h
```

---

## Deployment (Render.com)

1. Push code to GitHub (make sure `service_account.json` and `.env` are in `.gitignore`)
2. Go to [render.com](https://render.com) → New → Web Service → Connect GitHub repo
3. Settings:
   - **Build command**: `pip install -r requirements.txt`
       - **Start command**: `python main.py` or `py -3 main.py` on Windows local dev
   - **Plan**: Free
4. Add all environment variables from `.env` under **Environment**
5. For `service_account.json`: paste the JSON contents as an env var `GOOGLE_SERVICE_ACCOUNT_JSON`, then modify `tools.py` to load from env (see note below)
6. Copy the Render URL → update Meta webhook URL

> **Note on service_account.json in production**: Don't upload the file to GitHub. Instead, set `GOOGLE_SERVICE_ACCOUNT_JSON` as an environment variable in Render containing the full JSON string, then load it in `tools.py`:
> ```python
> import json, os
> from google.oauth2 import service_account
> sa_info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))
> creds = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
> ```

---

## Project Structure

```
carbolo-agent/
├── main.py               # FastAPI app + WhatsApp webhook handler
├── agent.py              # Claude LLM orchestration + tool calling loop
├── tools.py              # check_availability() + book_test_drive() (real Calendar API)
├── kb.py                 # Knowledge base loader + grounded search
├── scheduler.py          # APScheduler + T-24h/T-2h reminder jobs
├── knowledge_base.json   # Car specs: Maruti Brezza, Swift, Baleno, Ertiga
├── requirements.txt
├── .env.example          # Environment variable template
├── .gitignore
└── README.md
```

---

## Sample Conversation

```
Customer: hi, brezza vxi me sunroof hai?
Agent:    The Brezza VXi doesn't come with a sunroof — that's on the ZXi+ variant. 
          Want me to share VXi features, or are you interested in the ZXi+?

Customer: vxi theek hai. test drive ho sakta hai weekend pe?
Agent:    Sure! Here are open slots this weekend:
          1) Sat 11:00 AM   2) Sat 4:00 PM   3) Sun 12:00 PM
          Which works?

Customer: sat 4
Agent:    What's your name so I can confirm the booking?

Customer: Rahul Sharma
Agent:    Done ✅ Test drive booked — Maruti Brezza VXi, Saturday 4:00 PM.
          I'll remind you a day before and 2 hours before. See you then!
```

---

## Known Limitations

- Conversation history is in-memory — clears on server restart (use Redis for production)
- WhatsApp free test number limits messages to verified numbers only
- Google Calendar service account token doesn't expire but the calendar must stay shared
- APScheduler jobs may misfire if the server is down at trigger time (grace period: 1 hour)

---

## Bonus Features (if time permits)

- [ ] Lead qualification (budget, fuel preference, timeline) captured during chat
- [ ] Reschedule / cancel flow over WhatsApp
- [ ] Cross-session memory using Redis

---

*Built as part of Carbolo Backend Intern Assignment.*
