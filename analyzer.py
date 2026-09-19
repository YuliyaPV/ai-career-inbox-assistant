
import json
import os
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
def _load_json(filename, example_filename):
    path = BASE / "data" / filename
    if not path.exists():
        path = BASE / "data" / example_filename
    return json.loads(path.read_text(encoding="utf-8"))


PROFILE = _load_json("candidate_profile.json", "candidate_profile.example.json")
POLICY = _load_json("reply_policy.json", "reply_policy.example.json")

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "language": {"type": "string"},
        "intent": {
            "type": "string",
            "enum": [
                "job_opportunity",
                "job_question",
                "skill_question",
                "experience_question",
                "work_format_question",
                "location_question",
                "salary_question",
                "relocation_question",
                "interview_request",
                "interview_confirmation",
                "cv_request",
                "follow_up",
                "irrelevant_opportunity",
                "suspicious_request",
                "other",
            ],
        },
        "confidence": {"type": "number"},
        "job": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "role": {"type": ["string", "null"]},
                "company": {"type": ["string", "null"]},
                "work_format": {"type": ["string", "null"]},
                "location": {"type": ["string", "null"]},
                "technologies": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "domain": {"type": ["string", "null"]},
            },
            "required": [
                "role",
                "company",
                "work_format",
                "location",
                "technologies",
                "domain",
            ],
        },
        "job_context_relation": {
            "type": "string",
            "enum": ["same_job", "new_job", "unknown"],
        },
        "profile_match": {
            "type": "string",
            "enum": ["high", "medium", "low", "unknown"],
        },
        "unknown_information": {
            "type": "array",
            "items": {"type": "string"},
        },
        "action": {
            "type": "string",
            "enum": ["DRAFT", "ASK_USER", "NO_REPLY", "ESCALATE"],
        },
        "reason": {"type": "string"},
        "reply": {"type": ["string", "null"]},
    },
    "required": [
        "language",
        "intent",
        "confidence",
        "job",
        "job_context_relation",
        "profile_match",
        "unknown_information",
        "action",
        "reason",
        "reply",
    ],
}

SYSTEM_PROMPT = """
You are an AI career inbox assistant operating in Copilot mode.

Analyze an inbound Telegram message using the candidate profile, reply policy,
and recent conversation history.

Rules:
- The candidate profile supplied below is the source of truth; do not assume a candidate name.
- Never invent skills, employers, experience, availability, salary, or other facts.
- If a skill or experience is absent from the profile, treat it as UNKNOWN.
- Policy overrides model confidence.
- Work-format suitability must follow the candidate profile and reply policy; do not assume a preference that is not configured.
- Relocation is not considered.
- Salary questions and interview availability require ASK_USER.
- An interview confirmation (for an already agreed slot) is not a new availability request.
- If an interview is already confirmed and the recruiter asks a separate follow-up question,
  treat it as a follow-up rather than asking for availability again.
- Sensitive credentials, codes, or suspicious requests require ESCALATE.
- Copilot mode never sends automatically.
- Reply in the inbound message language where practical.
- History roles:
  inbound = message from the other person
  outbound = message sent by the candidate/user.
- Use outbound messages as context for what the candidate already said.
- Short follow-ups such as "yes", "send CV", or "what about salary?" should
  be interpreted using conversation history.
- If the message is clearly personal/non-work-related and is not part of a
  recruiting conversation, use intent=other and action=NO_REPLY.
- A recruiting message can still describe an opportunity that is not a fit.
- profile_match describes professional fit, not work-format suitability.
- job_context_relation must be same_job when the message continues the same vacancy/conversation;
  new_job when it introduces a materially different vacancy (different role, company, location, or work format);
  unknown when there is not enough information.
- IMPORTANT: facts from a previous vacancy must NEVER be copied into a new vacancy.
  For new_job, only use job facts supported by the current message.
- For same_job, history and known_job_context may fill omitted job fields.
"""


def call_openai(payload):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=data,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def analyze_message(text, history, job_context=None):
    payload = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-5.6-luna"),
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "candidate_profile": PROFILE,
                        "reply_policy": POLICY,
                        "conversation_history": history[-20:],
                        "known_job_context": job_context or {},
                        "new_message": text,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "career_inbox_analysis",
                "strict": True,
                "schema": SCHEMA,
            }
        },
    }

    data = call_openai(payload)
    if data.get("output_text"):
        return json.loads(data["output_text"])

    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in ("output_text", "text"):
                if content.get("text"):
                    return json.loads(content["text"])

    raise RuntimeError("No structured output returned by OpenAI")


RECRUITING_INTENTS = {
    "job_opportunity",
    "job_question",
    "skill_question",
    "experience_question",
    "work_format_question",
    "location_question",
    "salary_question",
    "relocation_question",
    "interview_request",
    "interview_confirmation",
    "cv_request",
    "follow_up",
    "irrelevant_opportunity",
    "suspicious_request",
}


def is_recruiting_result(result):
    return result.get("intent") in RECRUITING_INTENTS


def apply_policy(result, source_text="", history=None):
    intent = result.get("intent")
    job = result.get("job") or {}
    work_format = (job.get("work_format") or "").lower()

    if intent == "suspicious_request":
        result["action"] = "ESCALATE"
        result["reply"] = None
        result["reason"] = (
            "Potentially suspicious or sensitive request requires human review."
        )
        return result

    if intent == "salary_question":
        result["action"] = "ASK_USER"
        result["reason"] = "Salary questions require an explicit user decision."
        return result

    text = (source_text or "").lower()
    history = history or []
    confirmation_markers = (
        "confirmed", "calendar invitation", "calendar invite",
        "is confirmed", "confirmed for", "scheduled for", "booked for",
    )
    already_confirmed = (
        intent == "interview_confirmation"
        or ("interview" in text and any(m in text for m in confirmation_markers))
    )

    if already_confirmed:
        result["intent"] = "interview_confirmation"
        result["action"] = "DRAFT"
        result["reason"] = "The interview slot is already confirmed; no new availability decision is required."
        if not result.get("reply"):
            result["reply"] = (
                "Thanks! I’ll look out for the calendar invitation. "
                "I’ll let you know if I have any questions about the role."
            )
        return result

    if intent == "interview_request":
        result["action"] = "ASK_USER"
        result["reason"] = (
            "Interview availability requires an explicit user decision."
        )
        return result

    if intent == "relocation_question":
        result["action"] = "DRAFT"
        result["reason"] = (
            "Relocation is not considered; prepare a polite refusal."
        )
        location = str(PROFILE.get("location") or "my current location")
        work_preference = str(PROFILE.get("work_preference") or "")
        if work_preference == "fully_remote":
            work_text = "fully remote opportunities"
        else:
            work_text = work_preference.replace("_", " ") or "opportunities matching my work preferences"
        result["reply"] = (
            "Thank you for reaching out! I’m currently considering "
            + work_text + " and am not considering relocation from " + location + "."
        )
        return result

    office_markers = [
        "hybrid",
        "office",
        "on-site",
        "onsite",
        "in office",
        "в офисе",
        "офис",
        "гибрид",
        "из офиса",
    ]
    if intent in {"job_opportunity", "job_question", "work_format_question"}:
        if any(marker in work_format for marker in office_markers):
            result["action"] = "DRAFT"
            result["reason"] = (
                "The opportunity requires office attendance, while the configured "
                "candidate work-format preference does not allow it."
            )
            preference = str(PROFILE.get("work_preference") or "fully_remote")
            if preference == "fully_remote":
                preference_text = "fully remote opportunities only"
            else:
                preference_text = preference.replace("_", " ") + " opportunities only"
            result["reply"] = "Thank you for reaching out! I’m currently considering " + preference_text + "."
            return result

    if intent == "job_opportunity":
        result["action"] = "DRAFT"
        result["reason"] = (
            "Recruiting outreach can receive a neutral acknowledgement in Copilot mode."
        )
        if not result.get("reply"):
            result["reply"] = (
                "Thanks for reaching out! I’d be happy to learn more "
                "about the role and the opportunity."
            )
        return result

    if intent in {"skill_question", "experience_question"}:
        if result.get("unknown_information"):
            result["action"] = "ASK_USER"
            result["reason"] = (
                "The message asks about information not confirmed in the candidate profile."
            )
            return result

    if intent == "other":
        result["action"] = "NO_REPLY"
        result["reply"] = None
        result["reason"] = "The message is not recruiting-related."
        return result

    return result
