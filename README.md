# 🚗 Carbolo — WhatsApp Test-Drive Booking Agent

An AI-powered WhatsApp agent that books car test drives end-to-end. Built for Indian car dealerships — answers questions from a grounded knowledge base, checks real Google Calendar availability, creates booking events, and sends T-24h and T-2h WhatsApp reminders.

---

## 🧪 Test the Live Bot

**WhatsApp Number:** +1 415 523 8886 (Twilio Sandbox)

```
Step 1: Save +1 415 523 8886 on your phone
Step 2: Send this WhatsApp message to join:
        join <your-keyword>
Step 3: After joining, message freely to test
```

**Try these messages:**
- `"hi"`
- `"brezza vxi me sunroof hai?"`
- `"test drive book karna hai this weekend"`
- `"Baleno Alpha ka price kitna hai?"`

---

## Architecture

```
Customer (WhatsApp)
       │
       ▼
Twilio WhatsApp Sandbox
       │  webhook POST /webhook
       ▼
┌─────────────────────────┐
│   FastAPI (main.py)     │  ← receives & sends WhatsApp messages
│   ThreadPoolExecutor    │    returns 200 immediately, processes
└────────────┬────────────┘    in background thread
             │
             ▼
┌─────────────────────────┐
│   Agent (agent.py)      │  ← Claude Haiku with tool calling
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
│  (service account)      │    idempotent — no duplicate events
└─────────────────────────┘
         │ on booking
         ▼
┌─────────────────────────┐
│  APScheduler            │  ← schedules T-24h and T-2h jobs
│  (SQLite persistence)   │    survives server restarts
└────────────┬────────────┘
             │ at trigger time
             ▼
Twilio WhatsApp  →  Customer gets reminder
```

---

## Features

| Feature | Status |
|---|---|
| WhatsApp integration (live Twilio sandbox number) | ✅ |
| KB-grounded answers (zero hallucination) | ✅ |
| Google Calendar availability check (real tool call) | ✅ |
| Google Calendar event creation (real tool call) | ✅ |
| T-24h & T-2h WhatsApp reminders | ✅ |
| Idempotent booking (no duplicate events) | ✅ |
| Hinglish conversation handling | ✅ |
| Reminder persistence across restarts (SQLite) | ✅ |
| Duplicate webhook message deduplication | ✅ |

---

## Tech Choices & Why

| Component | Choice | Why |
|---|---|---|
| **Framework** | FastAPI | Async, minimal, perfect for webhook handlers. ThreadPoolExecutor prevents Twilio timeouts |
| **LLM** | Claude Haiku (Anthropic) | Cheapest model with reliable tool calling; fastest response time |
| **KB / RAG** | Simple JSON + keyword search | KB is only 3–5 models; vector DB would be overkill and a hallucination risk |
| **Calendar** | Google Calendar API + Service Account | Service account avoids OAuth flow; FreeBusy API gives real-time availability |
| **Scheduler** | APScheduler + SQLite jobstore | Built-in persistence — reminder jobs survive server restarts without Redis |
| **WhatsApp** | Twilio WhatsApp Sandbox | Free sandbox number; anyone can join; no number verification needed |
| **Hosting** | Render.com | Free tier, automatic deploys from GitHub, public HTTPS URL for webhook |

---

## Local Setup

### Prerequisites
- Python 3.11+
- A Twilio account (free)
- A Google Cloud account (free)
- An Anthropic API key

### 1. Clone and install

```bash
git clone https://github.com/ManavJ8/carbolo-agent.git
cd carbolo-agent
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Set up environment variables

```bash
cp .env.example .env
# Edit .env with your actual keys
```

### 3. Google Calendar service account

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create project → Enable **Google Calendar API**
3. Go to **IAM & Admin → Service Accounts → Create Service Account**
4. Name it "carbolo-agent" → Create
5. Click the service account → **Keys → Add Key → JSON** → download
6. Save as `service_account.json` in the project root
7. Open [calendar.google.com](https://calendar.google.com) → Create a new calendar
8. Calendar **Settings → Share with specific people** → add the service account email with **"Make changes to events"** permission
9. Copy the **Calendar ID** → put in `.env` as `GOOGLE_CALENDAR_ID`

### 4. Twilio WhatsApp Sandbox setup

1. Go to [console.twilio.com](https://console.twilio.com)
2. Sign up free → go to **Messaging → Try it out → Send a WhatsApp message**
3. Copy **Account SID** → `TWILIO_ACCOUNT_SID` in `.env`
4. Copy **Auth Token** → `TWILIO_AUTH_TOKEN` in `.env`
5. Note the sandbox number → `TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886` in `.env`
6. Under **Sandbox Configuration** → set webhook URL to `https://your-app.onrender.com/webhook`
7. Method: **HTTP POST** → Save

### 5. Run locally

```bash
python main.py
```

### 6. Run in demo mode (reminders fire in 2 min and 5 min)

```bash
# Windows
set DEMO_MODE=true && python main.py

# Mac/Linux
DEMO_MODE=true python main.py
```

---

## Deployment (Render.com)

1. Push code to GitHub (`service_account.json` and `.env` must be in `.gitignore`)
2. Go to [render.com](https://render.com) → New → Web Service → Connect GitHub repo
3. Settings:
   - **Build command**: `pip install -r requirements.txt`
   - **Start command**: `python main.py`
   - **Plan**: Free
4. Add environment variables under **Environment** tab:

```
ANTHROPIC_API_KEY           = sk-ant-...
TWILIO_ACCOUNT_SID          = ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN           = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_WHATSAPP_NUMBER      = whatsapp:+14155238886
GOOGLE_CALENDAR_ID          = xxx@group.calendar.google.com
GOOGLE_SERVICE_ACCOUNT_JSON = { paste entire service_account.json contents }
DEMO_MODE                   = false
```

5. Click **Create Web Service** → Render deploys automatically
6. Copy Render URL → update Twilio sandbox webhook URL

---

## Project Structure

```
carbolo-agent/
├── main.py               # FastAPI app + Twilio webhook handler
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
Agent:    The Brezza VXi doesn't come with a sunroof — that's on the
          ZXi+ variant. Want me to share VXi features, or are you
          interested in the ZXi+?

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

*(A real event appears on Google Calendar; reminders arrive on WhatsApp at T-24h and T-2h)*

---

## Known Limitations

- Conversation history is in-memory — clears on server restart (Redis would fix this)
- Twilio sandbox limited to 50 messages/day on free trial
- Twilio sandbox requires users to send a join message before chatting
- Google Calendar service account credentials stored as env var in production
- APScheduler jobs may misfire if server is down at trigger time (grace period: 1 hour)
- Render free tier spins down after 15 min inactivity — first message may be slow

---

## Bonus Features (stretch goals)

- [ ] Lead qualification (budget, fuel preference, timeline) captured during chat
- [ ] Reschedule / cancel flow over WhatsApp
- [ ] Cross-session memory using Redis

---

*Built as part of Carbolo Backend Intern Assignment.*
