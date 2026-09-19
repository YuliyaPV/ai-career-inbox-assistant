# AI Career Inbox Assistant

A reusable Telegram Business bot template for an AI-powered inbox assistant.

The example workflow is recruiting: the bot analyzes incoming recruiter messages, keeps conversation and job context, prepares replies, asks the user for decisions when needed, and can optionally send a small allow-list of safe profile-based auto-replies.

The project is intentionally designed so that another person can reuse the code by replacing the candidate profile and response policy with their own data.

> This repository contains the reusable version of the bot described in the Habr article.

## What it does

- Receives Telegram Business messages.
- Detects recruiting conversations and keeps a Career Inbox.
- Maintains inbound/outbound conversation history in SQLite.
- Keeps job context isolated between vacancies.
- Extracts role, company, work format, location, and technologies when available.
- Uses a configurable candidate profile and reply policy as constraints.
- Treats missing profile information as `UNKNOWN` instead of inventing experience.
- Supports `DRAFT`, `ASK_USER`, `NO_REPLY`, and `ESCALATE` decisions.
- Handles multiple decisions in one recruiter message, for example salary + interview availability.
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
              ├── data/candidate_profile.json
              └── data/reply_policy.json
```

## Customize for your own use

This repository is designed to be adapted to a different person, profession, or workflow.

The application logic stays the same; the main customization happens in the candidate profile and response policy.

### 1. Create your candidate profile

Copy:

```text
data/candidate_profile.example.json
```

to:

```text
data/candidate_profile.json
```

Then replace the example values with your own information:

- name and professional title;
- location and timezone;
- preferred work format;
- target roles;
- experience;
- domains;
- skills;
- languages.

The candidate profile is the source of truth for personal facts.

If a skill or experience is not confirmed in the profile, the assistant should treat it as `UNKNOWN` rather than inventing an answer.

### 2. Configure your response policy

Copy:

```text
data/reply_policy.example.json
```

to:

```text
data/reply_policy.json
```

Then adjust the rules to match your own preferences.

For example, you can define how the assistant should handle:

- salary questions;
- interview availability;
- relocation;
- hybrid or office-based roles;
- unknown skills or experience;
- sensitive information;
- automatic replies.

The policy is intentionally separate from the candidate profile.

The profile describes **facts**.

The policy describes **what the assistant is allowed to do with those facts**.

### 3. Configure environment variables

Copy:

```text
.env.example
```

to:

```text
.env
```

and set your own credentials.

See the [Configuration](#configuration) section below.

### Important

Do **not** commit the following files or secrets to a public repository:

- `data/candidate_profile.json`
- `data/reply_policy.json`
- `.env`
- Telegram bot tokens
- OpenAI API keys
- `bot.db`
- conversation history
- private recruiter messages
- other personal data

The `.example.json` files are templates intended for the public repository.

Your real profile and credentials should remain private.

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

Safe Auto-Replies are **OFF by default**.

When enabled, only deterministic, profile-backed questions are eligible for automatic replies.

Salary, interview availability, unknown skills, relocation decisions, vacancy interest, and ambiguous messages stay in Copilot mode.

## Running locally

This project intentionally uses Python standard-library HTTP/SQLite code rather than a large Telegram framework.

There are currently no third-party Python dependencies in `requirements.txt`.

Run:

```bash
python3 bot.py
```

Before starting the bot, configure your environment variables and create your personal profile/policy files.

## Telegram Business

The bot is designed to work with Telegram Business messages.

Connect the bot to your Telegram Business account and grant the required message/reply permissions.

Use:

```text
/start
/status
/ping
```

to verify the connection.

Only **one running polling instance** may use the same Telegram bot token.

Running two copies at the same time causes Telegram `409 Conflict` errors.

## Deploy-F

The project can be uploaded as a Python application to Deploy-F.

Set the same environment variables shown in the [Configuration](#configuration) section and start:

```bash
python3 bot.py
```

For a persistent deployment, keep the same `bot.db` between restarts and deployments if you want to preserve conversation history and pending decisions.

## Database

The application stores state in:

```text
bot.db
```

using SQLite.

The database contains application state such as:

- conversations;
- inbound/outbound messages;
- job context;
- pending decisions;
- editing state.

Keeping the same database between deployments preserves conversation history and pending decisions.

Starting with a new database creates a clean Inbox.

Because the database can contain private conversation data, it should **never be committed to a public repository**.

## Safety model

The assistant is designed around a simple principle:

> Automate routine work, but keep the human in control of decisions that matter.

The candidate profile is the source of truth for experience and skills.

If a recruiter asks about a skill that is not confirmed in the profile, the bot does not manufacture an answer; it asks the user instead.

Salary acceptance, interview availability, and other candidate-specific decisions require explicit user input.

The Copilot does not silently make those decisions on the user's behalf.

### Safe Auto-Replies

Safe Auto-Replies are deliberately narrow and opt-in.

They are intended for simple questions that can be answered directly from confirmed profile facts.

For example:

```text
Are you based in Serbia and what languages do you speak?
```

can be answered automatically if both facts are present in the profile.

But:

```text
Are you based in Serbia and do you have Tableau experience?
```

should stay in Copilot mode if Tableau experience is not confirmed.

If a message contains both a safe profile fact and an unknown or non-allow-listed question, the automatic reply is blocked.

## Human-in-the-loop workflow

The assistant uses several possible actions:

```text
AUTO_REPLY
DRAFT
ASK_USER
NO_REPLY
ESCALATE
```

The exact action depends on the message, the candidate profile, the response policy, and the conversation context.

Examples:

- a routine profile question → possible auto-reply;
- a normal recruiting message → draft;
- salary expectations → ask the user;
- interview availability → ask the user;
- unknown experience → ask the user;
- sensitive information → escalate;
- explicit decision not to reply → no reply.

This separation is intentional: the LLM can analyze the conversation, while the policy determines which actions are allowed.

## Job context

The assistant keeps job context separately for different vacancies.

For example:

```text
Company A
Data Analyst
SQL + Power BI
Remote
```

and later:

```text
Company B
Analytics Engineer
dbt + Greenplum
Remote
```

are treated as different job contexts.

This prevents information from one vacancy from accidentally appearing in the analysis of another vacancy.

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

Before publishing your own fork or repository, verify that it contains no:

- real Telegram bot token;
- OpenAI/API key;
- private Telegram IDs;
- real recruiter messages;
- conversation history;
- personal CV data you do not want public;
- `bot.db`;
- `.env`.

For public repositories, consider enabling GitHub security features such as secret scanning, push protection, Dependabot alerts, and code scanning where appropriate.

## Reuse beyond recruiting

Although this example focuses on recruiter messages, the same architecture can be used for other types of personal or professional inboxes.

The general pattern is:

```text
Incoming message
       ↓
Context + profile
       ↓
Intent / relevance analysis
       ↓
Policy
       ↓
┌───────────────┬──────────────┐
│ Safe to reply │ Needs human  │
│ automatically │ decision     │
└───────────────┴──────────────┘
       ↓
Draft / Auto-reply / Ask user
```

Possible applications include customer inquiries, freelance requests, networking messages, professional support inboxes, or other workflows where the assistant can handle routine communication while keeping important decisions with a human.

## License

MIT — see `LICENSE`.
