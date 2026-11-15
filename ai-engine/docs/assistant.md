# Career assistant (chatbot)

A chat panel ("Ask AI", bottom right on every signed-in page) that answers
questions about job searching, CVs, interviews and careers in Tunisia. It is
powered by Anthropic Claude and can look up real jobs from the site's catalog.

## Turning it on

The assistant is off by default. Set in `.env` (or the server environment):

```env
ASSISTANT_ENABLED=true
ANTHROPIC_API_KEY=sk-ant-...        # from console.anthropic.com
# Optional
ASSISTANT_MODEL=claude-opus-5-5
ASSISTANT_EFFORT=low                # low | medium | high
ASSISTANT_MAX_MESSAGE_CHARS=2000
ASSISTANT_HISTORY_MESSAGES=20       # past messages replayed per request
ASSISTANT_MAX_TOOL_ROUNDS=4
USER_QUOTA_ASSISTANT_ATTEMPTS=60    # messages per user per window
USER_QUOTA_ASSISTANT_WINDOW_SECONDS=3600
```

Restart the API. `GET /api/v1/assistant/status` reports `enabled: true` once
both `ASSISTANT_ENABLED` and a key are set.

## Personalisation and privacy

What the user types is always sent to Anthropic. Personal context is added
only when **both** the server allows remote AI analysis
(`ALLOW_REMOTE_LLM_ANALYSIS=true`) **and** the user turned on AI features in
Settings. With both on, each request includes a short summary built by
`app/services/assistant/context.py`:

- first name only
- career goal (field and target role from Settings) and the fields detected
  from the CV
- fit with the target role: ESCO skills the CV has and is missing
- suggested roles, CV skills, ATS score and top fixes
- experience, education and languages from the CV builder
- tracked applications with their status, and best-matching jobs

Email, phone, address and links are never sent. Without consent the assistant
still answers general questions and the panel says "General advice only".

Job descriptions returned by tools are marked as untrusted text; the system
prompt tells the model to ignore any instructions inside them.

## Tools the model can call

| Tool | What it does |
|---|---|
| `search_jobs` | Searches the live job catalog (query, city, workplace). With consent, results include the user's match score. |
| `get_job` | Full details of one job by id. |
| `role_skills` | Essential ESCO skills for a role, and which ones the user's CV already shows. |

The model only recommends jobs that come from these tools, so it does not
invent openings.

## API

| Method | Path | |
|---|---|---|
| GET | `/assistant/status` | enabled, personalised, reason |
| GET | `/assistant/messages` | last 60 messages |
| POST | `/assistant/messages` | `{ "message": "..." }` → user message, reply, tools used |
| DELETE | `/assistant/messages` | clear the conversation |

History is stored in `chat_messages` (latest 200 per user, deleted with the
account). Responses are `Cache-Control: no-store`.

## Code

- `app/services/assistant/settings.py`: configuration
- `app/services/assistant/service.py`: system prompt and tool loop (Anthropic SDK)
- `app/services/assistant/tools.py`: tool schemas and execution
- `app/services/assistant/context.py`: personal context
- `app/api/routes/assistant.py`: endpoints
- `frontend/src/app/shared/components/assistant-chat/`: the chat panel
- Tests: `tests/test_assistant.py` (uses a fake client, no API calls)
