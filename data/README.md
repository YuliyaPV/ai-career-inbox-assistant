# AI Career Inbox Assistant

A reusable Telegram Business bot template for an AI-powered inbox assistant. The example workflow is recruiting: the bot analyzes incoming recruiter messages, keeps conversation and job context, prepares replies, asks the user for decisions when needed, and can optionally send a small allow-list of safe profile-based auto-replies.

The project is intentionally designed so that another person can reuse the code by replacing the candidate profile and response policy with their own data.

## What it does

- Receives Telegram Business messages.
- Detects recruiting conversations and keeps a Career Inbox.
- Maintains inbound/outbound conversation history in SQLite.
- Keeps job context isolated between vacancies.
- Extracts role, company, work format, location, and technologies when available.
- Uses a configurable candidate profile and reply policy as constraints.
- Treats missing profile information as `UNKNOWN` instead of inventing experience.
- Supports `DRAFT`, `ASK_USER`, `NO_REPLY`, and `ESCALATE` decisions.
- Handles multiple decisions in one recruiter message, e.g. salary + interview availability.
- Offers concrete interview-time buttons when the recruiter provides specific slots.
- Supports `Send`, `Edit`, `Don't reply`, and `Ignore` from the Career Inbox.
- Saves outbound replies to conversation history without analyzing them again.
- Persists pending decisions and protects against double-send.
- Restores unfinished decisions after a restart.
- Supports opt-in Safe Auto-Replies for a small allow-list of profile facts.

## Architecture

```text
Telegram Business
       │
       ▼
   bot.py
       │
       ├── telegram_api.py  — Telegram Bot API HTTP calls
       ├── analyzer.py      — LLM analysis + policy application
       ├── auto_reply.py    — deterministic safe auto-reply rules
       ├── keyboards.py     — inline keyboard UI
       └── storage.py       — SQLite conversations, messages, pending state
              │
              ├── data/candidate_profile.example.json
              └── data/reply_policy.example.json
```

## Reuse the project for your own profile

The repository contains example configuration files. For your own deployment, create:

```text
data/candidate_profile.json
data/reply_policy.json
```

using the example files as templates. The application falls back to the example files if personal configuration files are not present, which keeps the public repository runnable as a demo. Replace the examples with your own profile and policy before connecting the bot to real messages. Keep these files free of secrets; they contain profile/policy data, not credentials.

The reusable part is the application logic: Telegram handling, SQLite persistence, job-context isolation, decision workflows, double-send protection, Career Inbox, and safe auto-reply rules.

## Configuration

Copy `.env.example` to `.env` and set:

```text
BOT_TOKEN=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-luna
```

Never commit `.env`, Telegram bot tokens, API keys, database files, or private conversation history.

## Main commands

- `/start` — start/help
- `/ping` — Telegram connection test
- `/analyze <text>` — analyzer test
- `/chats` — Career Inbox
- `/today` — conversations requiring attention
- `/status` — Telegram Business diagnostics
- `/auto` — Safe Auto-Replies menu

Safe Auto-Replies are **OFF by default**. When enabled, only deterministic, profile-backed questions are eligible. Salary, interview availability, unknown skills, relocation decisions, vacancy interest, and ambiguous messages stay in Copilot mode.

## Running locally

This project intentionally uses Python standard-library HTTP/SQLite code rather than a large Telegram framework. There are currently no third-party Python dependencies in `requirements.txt`.

Run:

```bash
python3 bot.py
```

## Deploy-F

The project can be uploaded as a Python application to Deploy-F. Set the same environment variables shown above and start `python3 bot.py`.

Connect the bot to Telegram Business and grant the required message/reply permissions. Use `/start`, `/status`, and `/ping` to verify the connection.

Only **one running polling instance** may use the same Telegram bot token. Running two copies at the same time causes Telegram `409 Conflict` errors.

## Database

The application stores state in `bot.db` using SQLite. Keeping the same database between deployments preserves conversation history and pending decisions. Starting with a new database creates a clean Inbox.

## Safety model

The candidate profile is the source of truth for experience and skills. If a recruiter asks about a skill that is not confirmed in the profile, the bot does not manufacture an answer; it asks the user instead.

Salary acceptance, interview availability, and other candidate-specific decisions require explicit user input. The Copilot does not silently make those decisions on the user's behalf.

Safe Auto-Replies are deliberately narrow and opt-in. If a message contains both a safe profile fact and an unknown/non-allow-listed question, the automatic reply is blocked and the message stays in Copilot mode.

## Project files

- `bot.py` — application entry point and Telegram event handling
- `analyzer.py` — AI analyzer and policy logic
- `auto_reply.py` — deterministic safe auto-reply rules
- `storage.py` — SQLite persistence and pending-action state
- `telegram_api.py` — minimal Telegram API client
- `keyboards.py` — inline keyboards and decision UI
- `config.py` — environment/config loading
- `data/candidate_profile.example.json` — example candidate facts
- `data/reply_policy.example.json` — example response rules
- `.env.example` — required environment variables

## Public repository checklist

Before publishing your own fork/repository, verify that it contains no:

- real Telegram bot token;
- OpenAI/API key;
- private Telegram IDs;
- real recruiter messages;
- conversation history;
- personal CV data you do not want public.

GitHub recommends using a README and a license for public projects, and enabling security features such as secret scanning, push protection, Dependabot alerts, and code scanning where appropriate.

## License

MIT — see `LICENSE`.
