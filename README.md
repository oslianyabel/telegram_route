# Cheese Bot — Ruta del Queso

AI-powered chatbot for **Ruta del Queso**, a tourism platform in Uruguay that connects travelers with cheese establishments, gastronomic experiences, and thematic routes. The bot handles discovery, real-time availability, reservations, payments and customer support through WhatsApp and Telegram.

WhatsApp (temporal): **+598 91 656 911**
Telegram: @cheese_route_bot

---

## Use Cases

- Answer questions about cheese experiences, routes and establishments
- Check real-time availability for experiences and routes
- Create, modify and cancel individual reservations
- Guide users through payment instructions
- Send QR codes, itineraries and reminders
- Open support tickets and escalate to a human agent
- Manage CRM contacts and leads

---

## Key Features

- Conversational AI agent powered by **Google Gemini** via PydanticAI
- Multi-channel support: **WhatsApp Business API** and **Telegram**
- Persistent conversation history in **PostgreSQL**
- ERP integration for catalog, availability and reservations
- `/restart` command to reset chat history on demand
- Developer error notifications via Telegram
- Error monitoring with **Sentry**
- Audio transcription (Speech-to-Text) support
- Correlation ID middleware for request tracing

---

## Technologies

| Layer | Technology |
|---|---|
| Language | Python 3.13+ |
| API framework | FastAPI |
| AI agent | PydanticAI |
| AI model | Google Gemini |
| Messaging — WhatsApp | Meta WhatsApp Business API |
| Messaging — Telegram | python-telegram-bot |
| Database | PostgreSQL (asyncpg + SQLAlchemy + databases) |
| Audio | ffmpeg + Speech-to-Text |
| Error monitoring | Sentry |
| Package manager | uv |

---

## Architecture

```
cheese_bot/
├── chatbot/
│   ├── ai_agent/       # PydanticAI agent, prompts, tools and dependencies
│   │   └── tools/      # Catalog, availability, customer, payments, booking, support
│   ├── api/            # FastAPI routers: WhatsApp webhook, ERP webhook, Telegram, chat
│   │   └── utils/      # Message handling, queue, webhook parsing, security
│   ├── audio/          # Audio conversion and speech-to-text
│   ├── core/           # Config, logging and Sentry setup
│   ├── db/             # Database schema (users, messages) and services
│   ├── erp/            # ERP client helpers
│   └── messaging/      # WhatsApp and Telegram notification clients
├── context/            # API specs, documentation and ERP I/O examples
├── scripts/            # Manual test and debug scripts
└── docker/             # Container configuration
```

**Request flow:**
1. Message received via WhatsApp webhook or Telegram
2. Message queued and processed asynchronously
3. PydanticAI agent runs with injected dependencies (contact, ERP client, DB)
4. Agent calls ERP tools as needed and returns a structured response
5. Response sent back to the user through the corresponding channel

**Background workers:**

Two async workers run continuously alongside the API server:

| Worker | File | Scan interval | Purpose |
|---|---|---|---|
| Lead Follow-up | `chatbot/reminders/lead_followup.py` | 30 min | Sends a personalised re-engagement message to users who showed interest (lead detected) but did not complete a reservation |
| Deposit Reminder | `chatbot/reminders/deposit_reminder.py` | 15 min | Sends payment instructions to users whose confirmed ticket still has `amount_remaining > 0` (up to 3 reminders, spaced ≥ 4 h apart) |

Both workers detect the user's channel (WhatsApp or Telegram) from the stored conversation history and deliver the message through the appropriate channel.

### Reminders logic

**Reminders are enabled by default** for all users. Clients can opt out or opt back in at any time by asking the bot; the agent uses the `stop_lead_followups` and `start_lead_followups` tools to manage this preference. The opt-out applies to both reminder types.

#### Lead follow-up reminders

Triggered when `upsert_lead` was called in a conversation but no reservation was made:

1. **First reminder** — sent after the user has been inactive for **≥ 4 hours**.
2. **Subsequent reminders** — sent only if the user **responded** to the previous reminder, at least **20 hours** have elapsed since that reminder, and the user has again been inactive for **≥ 4 hours**.
3. **Hard stops** — no more reminders are sent if:
   - The user did not respond to the last reminder (`no_response_since_last_reminder`).
   - The user has been inactive for more than **24 hours** since their last message (`inactive_window_expired`).
   - The user has already received **3 reminders** (`followup_limit_reached`).
   - The user created a reservation (`reservation_already_created`).
   - The user opted out (`followup_opted_out`).
4. **WhatsApp constraint** — messages are only sent within the **24-hour free messaging window** imposed by Meta.
5. **Performance** — the worker only queries users who have had activity in the last 24 hours, avoiding full table scans.

#### Deposit payment reminders

Triggered when a ticket is confirmed (`APPROVED`) by the establishment but `amount_remaining > 0`:

1. A reminder is sent after the user has been inactive for **≥ 4 hours** since their last message.
2. Subsequent reminders require **≥ 4 hours** since the last reminder was sent.
3. Maximum **3 reminders** per ticket.
4. The ticket must have a **future date** (past-dated tickets are excluded).
5. Once `amount_remaining = 0` is detected, the ticket is **permanently excluded** (`reminder_count` set to 3).
6. Reminders are skipped if the user has **opted out**.

---

## Environment Variables

Create a `.env` file in the project root with the following variables:

```env
# General
ENV_STATE=dev          # dev | prod
DATABASE_URL=postgresql://user:password@host:5432/dbname

# ERP
ERP_HOST=https://your-erp-host.com
ERP_USER=your_erp_user
ERP_PASSWORD=your_erp_password
ERP_API_TOKEN=your_erp_api_token

# AI
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=your_google_api_key

# Meta WhatsApp Business API
WHATSAPP_ACCESS_TOKEN=your_token
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
WHATSAPP_VERIFY_TOKEN=your_verify_token
WHATSAPP_BOT_NUMBER=+59891656911
WABA_ID=your_waba_id

# Sentry
SENTRY_DSN=https://...@sentry.io/...

# Server
SERVER_HOST=https://your-server.com

# Auth
ADMIN_API_KEY=your_admin_api_key

# Telegram developer notifications
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_DEV_CHAT_ID=your_chat_id

# Other
MINUTES_BETWEEN_IMAGES=30
USE_FFMPEG=true
```

---

## APIs Consumed

| API | Purpose |
|---|---|
| ERP REST API | Catalog, availability, reservations, contacts, leads |
| Meta WhatsApp Business API | Send and receive WhatsApp messages |
| Google AI (Gemini) | Natural language understanding and response generation |

---

## Database

**PostgreSQL** with two main tables:

- `users` — stores contact info, permissions and last interaction timestamp
- `messages` — stores full conversation history per user, including tool calls

---

## How to Run

### Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) installed
- PostgreSQL instance running
- `.env` file configured

### Install dependencies

```bash
uv sync
```

### Run the API server (WhatsApp + ERP webhooks)

```bash
uv run uvicorn chatbot.api.main:app --host 0.0.0.0 --port 8000
```

### Run the Telegram bot

```bash
uv run python scripts/run_telegram_bot.py
```

### API Documentation (Swagger)

Once the server is running, open [http://localhost:8000/docs](http://localhost:8000/docs) for the interactive Swagger UI.

---

## Running Tests

```bash
uv run pytest
```

Run a specific test file with output:

```bash
uv run pytest -s path/to/test_file.py
```

---

## Areas for Improvement

- Add structured agent outputs (`answer`, `reasoning`, `needs_human`)
- Variable for selecting AI model at runtime via environment variable
- Implement WhatsApp message templates
- Add OCR support for image processing
- Add date/time utility tools for the agent
- Complete remaining reservation tools (modify, cancel, itinerary, surveys)

---

© Osliani Figueiras Saucedo
