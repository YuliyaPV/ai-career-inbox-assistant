
import json
import os
import re
import time
import traceback

from analyzer import analyze_message, apply_policy, is_recruiting_result
from auto_reply import build_safe_auto_reply
from keyboards import auto_reply_keyboard, chats_keyboard, conversation_card_keyboard, decision_keyboard, multi_decision_keyboard, reply_keyboard
from storage import (
    add_message,
    clear_edit_state,
    get_conversation,
    get_edit_state,
    get_history,
    get_job_context,
    get_or_create_conversation,
    get_pending,
    get_latest_pending,
    get_setting,
    claim_pending,
    mark_pending_sent,
    mark_pending_handled,
    init_db,
    list_conversations,
    save_pending,
    set_chat_status,
    set_process_status,
    set_edit_state,
    update_job_context,
    set_setting,
    update_contact,
)
from telegram_api import get_updates, send_message, get_business_connection


def fmt_result(result):
    job = result.get("job") or {}
    technologies = ", ".join(job.get("technologies") or []) or "—"
    unknown = ", ".join(result.get("unknown_information") or []) or "—"

    return (
        "Intent: " + str(result.get("intent")) + "\n"
        "Profile match: " + str(result.get("profile_match")) + "\n"
        "Action: " + str(result.get("action")) + "\n"
        "Role: " + str(job.get("role") or "—") + "\n"
        "Company: " + str(job.get("company") or "—") + "\n"
        "Work format: " + str(job.get("work_format") or "—") + "\n"
        "Location: " + str(job.get("location") or "—") + "\n"
        "Technologies: " + technologies + "\n"
        "Unknown information: " + unknown + "\n"
        "Reason: " + str(result.get("reason") or "—")
    )


def decision_options(result):
    # v5.6: when the analyzer extracted concrete interview choices from the
    # recruiter message, expose those exact choices as buttons.
    dynamic = result.get("decision_options") or []
    if dynamic:
        return [(item["label"], "choice_" + str(i)) for i, item in enumerate(dynamic)]

    intent = result.get("intent")
    if intent == "interview_request":
        return [
            ("📅 I’m available", "interview_available"),
            ("❌ Not available", "interview_unavailable"),
            ("🕐 Ask for specific time", "interview_specific_time"),
        ]
    if intent == "salary_question":
        return [
            ("💬 Interested", "salary_interested"),
            ("❓ Ask for more details", "salary_details"),
            ("⏳ Discuss later", "salary_later"),
        ]
    return []


def enrich_decision_options(result, source_text):
    if result.get("intent") != "interview_request":
        return result

    # Extract simple human-readable slots such as:
    # "Tuesday at 11:00 CET or Wednesday at 15:00 CET".
    # This is intentionally conservative: if we cannot identify at least two
    # complete choices, keep the standard ASK_USER buttons.
    pattern = re.compile(
        r"\b(?:on\s+)?(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(?:at\s+)?(\d{1,2}:\d{2})\s*(CET|CEST|UTC)?",
        re.IGNORECASE,
    )
    matches = pattern.findall(source_text)
    choices = []
    for day, clock, zone in matches:
        label = f"📅 {day.title()} {clock}"
        if zone:
            label += f" {zone.upper()}"
        choices.append({
            "label": label,
            "day": day.title(),
            "time": clock,
            "timezone": zone.upper() if zone else None,
        })

    if len(choices) >= 2:
        # Keep only unique labels and cap the keyboard to four concrete slots.
        unique = []
        seen = set()
        for choice in choices:
            if choice["label"] not in seen:
                unique.append(choice)
                seen.add(choice["label"])
        result["decision_options"] = unique[:4]

    return result


def merge_job_context(result, context):
    """Merge same-vacancy facts, but isolate a newly introduced vacancy."""
    context = context or {}
    incoming = result.get("job") or {}
    relation = result.get("job_context_relation", "unknown")

    if relation == "new_job":
        # Never carry facts (especially technologies) from the previous vacancy.
        merged = {
            "role": incoming.get("role"),
            "company": incoming.get("company"),
            "work_format": incoming.get("work_format"),
            "location": incoming.get("location"),
            "technologies": list(dict.fromkeys(incoming.get("technologies") or [])),
            "domain": incoming.get("domain"),
        }
    else:
        merged = dict(context)
        for key in ("role", "company", "work_format", "location", "domain"):
            value = incoming.get(key)
            if value:
                merged[key] = value
        incoming_tech = incoming.get("technologies") or []
        old_tech = merged.get("technologies") or []
        merged["technologies"] = list(dict.fromkeys([*old_tech, *incoming_tech]))

    result["job"] = {
        "role": merged.get("role"),
        "company": merged.get("company"),
        "work_format": merged.get("work_format"),
        "location": merged.get("location"),
        "technologies": merged.get("technologies") or [],
        "domain": merged.get("domain"),
    }
    return result



def format_conversation_card(conversation, pending=None):
    job = get_job_context(conversation["id"]) or {}
    technologies = ", ".join(job.get("technologies") or []) or "—"
    contact = conversation.get("contact_name") or "Unknown contact"
    username = conversation.get("contact_username") or ""
    if username:
        contact += " · @" + username.lstrip("@")

    status = conversation.get("chat_status") or "NEW"
    process_status = conversation.get("process_status") or "NEW"
    last_role = conversation.get("last_role") or "—"
    last_text = conversation.get("last_text") or "—"
    if len(last_text) > 300:
        last_text = last_text[:297] + "..."

    if pending and pending.get("draft") and pending.get("state") in (None, "PENDING", "SENDING"):
        draft = pending["draft"]
        draft_state = "🟢 Draft ready"
    else:
        draft = ""
        draft_state = "⚪ No draft"

    lines = [
        "💼 " + str(job.get("role") or "Recruiting conversation"),
        "👤 " + contact,
        "",
        "Status: " + status + " · " + process_status + " · " + draft_state,
        "Company: " + str(job.get("company") or "—"),
        "Work format: " + str(job.get("work_format") or "—"),
        "Location: " + str(job.get("location") or "—"),
        "Technologies: " + technologies,
        "",
        "💬 Last message (" + last_role + "):",
        last_text,
    ]
    if draft:
        lines.extend(["", "✍️ Latest draft:", draft])
    return "\n".join(lines)


def chat_filter_label(filter_name):
    labels = {
        "all": "All",
        "waiting_user": "Waiting for me",
        "waiting_recruiter": "Waiting for recruiter",
        "interview": "Interview scheduled",
        "closed": "Closed",
    }
    return labels.get(filter_name, "All")


def filter_conversations(conversations, filter_name):
    if filter_name == "waiting_user":
        return [c for c in conversations if c.get("chat_status") == "RECRUITING" and c.get("process_status") == "WAITING_USER"]
    if filter_name == "waiting_recruiter":
        return [c for c in conversations if c.get("chat_status") == "RECRUITING" and c.get("process_status") == "WAITING_RECRUITER"]
    if filter_name == "interview":
        return [c for c in conversations if c.get("chat_status") == "RECRUITING" and c.get("process_status") == "INTERVIEW_SCHEDULED"]
    if filter_name == "closed":
        return [c for c in conversations if c.get("process_status") == "CLOSED" or c.get("chat_status") == "IGNORED"]
    return conversations


def show_chats(admin_chat_id, filter_name="all"):
    conversations = list_conversations(100)
    visible = filter_conversations(conversations, filter_name)
    if not visible:
        counts = {
            "all": len(conversations),
            "waiting_user": len(filter_conversations(conversations, "waiting_user")),
            "waiting_recruiter": len(filter_conversations(conversations, "waiting_recruiter")),
            "interview": len(filter_conversations(conversations, "interview")),
            "closed": len(filter_conversations(conversations, "closed")),
        }
        send_message(
            admin_chat_id,
            "📥 Career Inbox\n\nNo conversations in: " + chat_filter_label(filter_name),
            reply_markup=chats_keyboard([], filter_name, counts),
        )
        return

    counts = {
        "all": len(conversations),
        "waiting_user": len(filter_conversations(conversations, "waiting_user")),
        "waiting_recruiter": len(filter_conversations(conversations, "waiting_recruiter")),
        "interview": len(filter_conversations(conversations, "interview")),
        "closed": len(filter_conversations(conversations, "closed")),
    }
    lines = ["📥 Career Inbox", "", "Filter: " + chat_filter_label(filter_name), ""]
    for row in visible[:20]:
        job = json.loads(row.get("job_context_json") or "{}") if row.get("job_context_json") else {}
        role = job.get("role") or "Recruiting conversation"
        contact = row.get("contact_name") or "Chat {}".format(row["chat_id"])
        process_status = row.get("process_status") or "NEW"
        if process_status == "WAITING_USER":
            state = "🟡 Waiting for me"
        elif process_status == "WAITING_RECRUITER":
            state = "🔵 Waiting for recruiter"
        elif process_status == "INTERVIEW_SCHEDULED":
            state = "🟣 Interview scheduled"
        elif process_status == "CLOSED":
            state = "⚪ Closed"
        elif row.get("latest_pending_id") and row.get("latest_draft"):
            state = "🟢 Draft ready"
        else:
            state = "⚪ New"
        lines.append("💼 {}\n{}\nStatus: {} · {}\nLast: {} — {}".format(
            role, contact, row.get("chat_status") or "NEW", state,
            row.get("last_role") or "—",
            (row.get("last_text") or "—")[:160],
        ))
        lines.append("")
    send_message(
        admin_chat_id,
        "\n".join(lines).rstrip(),
        reply_markup=chats_keyboard(visible[:20], filter_name, counts),
    )

def show_today(admin_chat_id):
    conversations = list_conversations(50)
    if not conversations:
        send_message(admin_chat_id, "📭 Nothing in Career Inbox today.")
        return

    waiting_user = [c for c in conversations if c.get("process_status") == "WAITING_USER" and c.get("chat_status") == "RECRUITING"]
    waiting_recruiter = [c for c in conversations if c.get("process_status") == "WAITING_RECRUITER" and c.get("chat_status") == "RECRUITING"]
    interviews = [c for c in conversations if c.get("process_status") == "INTERVIEW_SCHEDULED" and c.get("chat_status") == "RECRUITING"]

    lines = ["📅 Today", ""]
    lines.append("🟡 Waiting for me: {}".format(len(waiting_user)))
    for c in waiting_user[:10]:
        job = json.loads(c.get("job_context_json") or "{}") if c.get("job_context_json") else {}
        role = job.get("role") or "Recruiting conversation"
        contact = c.get("contact_name") or "Chat {}".format(c["chat_id"])
        lines.append("• {} — {}".format(role, contact))

    lines.extend(["", "🔵 Waiting for recruiter: {}".format(len(waiting_recruiter))])
    for c in waiting_recruiter[:10]:
        job = json.loads(c.get("job_context_json") or "{}") if c.get("job_context_json") else {}
        role = job.get("role") or "Recruiting conversation"
        contact = c.get("contact_name") or "Chat {}".format(c["chat_id"])
        lines.append("• {} — {}".format(role, contact))

    lines.extend(["", "🟣 Interview scheduled: {}".format(len(interviews))])
    for c in interviews[:10]:
        job = json.loads(c.get("job_context_json") or "{}") if c.get("job_context_json") else {}
        role = job.get("role") or "Recruiting conversation"
        contact = c.get("contact_name") or "Chat {}".format(c["chat_id"])
        lines.append("• {} — {}".format(role, contact))

    send_message(admin_chat_id, "\n".join(lines), reply_markup=chats_keyboard(conversations[:20]))


def decision_domains(result, source_text=""):
    """Return only decisions that require explicit user input."""
    intent = result.get("intent")
    text = (source_text or "").lower()
    unknown = " ".join(result.get("unknown_information") or []).lower()
    domains = []

    salary_markers = (
        "salary", "compensation", "salary expectations", "pay range",
        "rate", "gross", "net", "€", "eur", "зарплат", "компенсац",
    )
    interview_markers = (
        "interview", "availability", "available", "what days", "what time",
        "schedule", "time slot", "собеседован", "когда удобно",
    )

    if intent == "salary_question" or any(m in unknown for m in salary_markers) or any(m in text for m in salary_markers):
        domains.append("salary")
    if intent == "interview_request" or any(m in unknown for m in interview_markers) or any(m in text for m in interview_markers):
        domains.append("interview")
    return domains


def decision_summary(result, source_text=""):
    labels = {"salary": "💰 Salary", "interview": "📅 Interview"}
    return ", ".join(labels[d] for d in decision_domains(result, source_text) if d in labels)


def is_multi_decision(result, source_text=""):
    return result.get("action") == "ASK_USER" and len(decision_domains(result, source_text)) > 1


def selected_decisions(result):
    value = result.get("selected_decisions") or {}
    return value if isinstance(value, dict) else {}


def multi_decision_summary(result):
    selected = selected_decisions(result)
    labels = {
        "salary_interested": "Interested",
        "salary_details": "Ask for more details",
        "salary_later": "Discuss later",
        "interview_available": "I’m available",
        "interview_specific_time": "Ask for specific time",
        "interview_unavailable": "Not available",
    }
    return ", ".join(labels.get(v, v) for v in selected.values()) or "none"


def compose_multi_draft(result):
    selected = selected_decisions(result)
    parts = []
    salary = selected.get("salary")
    interview = selected.get("interview")
    salary_templates = {
        "salary_interested": "Thanks for sharing the salary range. The opportunity sounds interesting.",
        "salary_details": "Thanks for sharing the salary range. Could you please clarify whether the range is gross or net and share the full employment details?",
        "salary_later": "Thanks for sharing the details. I’d prefer to discuss compensation later in the process, once I learn more about the role and responsibilities.",
    }
    interview_templates = {
        "interview_available": "I’m available for an introductory interview next week. Could you please share the available time slots?",
        "interview_specific_time": "Could you please share the proposed interview time and timezone?",
        "interview_unavailable": "I’m not available for an interview next week. Could you please let me know if there are alternative dates?",
    }
    if salary in salary_templates:
        parts.append(salary_templates[salary])
    if interview in interview_templates:
        parts.append(interview_templates[interview])
    if not parts:
        return None
    return " ".join(parts)


def notify_admin(admin_chat_id, text, result, pending_id, conversation_id):
    decisions = decision_summary(result, text)
    decision_line = ("\n⚠️ Your decision required: " + decisions + "\n" if decisions else "")
    body = (
        "📩 New recruiting message\n\n"
        "Message:\n" + text + "\n\n"
        + fmt_result(result)
        + decision_line
        + "\nSuggested reply:\n"
        + str(result.get("reply") or ("Use the buttons below to choose how to respond." if result.get("action") == "ASK_USER" else "—"))
    )

    return send_message(
        int(admin_chat_id),
        body,
        reply_markup=(
            multi_decision_keyboard(
                pending_id, conversation_id, decision_domains(result, text), selected_decisions(result)
            )
            if is_multi_decision(result, text)
            else (
                decision_keyboard(pending_id, conversation_id, decision_options(result))
                if result.get("action") == "ASK_USER" and decision_options(result)
                else reply_keyboard(pending_id, conversation_id, result.get("action"))
            )
        ),
    )


def configured_admin_chat_id():
    return os.environ.get("ADMIN_CHAT_ID", "").strip()


def is_authorized_admin(chat_id):
    configured = configured_admin_chat_id()
    return bool(configured) and str(configured) == str(chat_id)


def auto_reply_is_enabled():
    return get_setting("auto_reply_enabled", "false").lower() == "true"


def try_auto_reply(conversation, text, result, message_id, admin_chat_id):
    """Send only a deterministic, allow-listed profile answer.

    Returns True when an automatic reply was sent. Returns False when the
    message must remain in normal Copilot flow. If sending fails, the caller
    can fall back to a pending draft.
    """
    if not auto_reply_is_enabled():
        return False

    draft = build_safe_auto_reply(text, result)
    if not draft:
        return False

    try:
        sent = send_message(
            conversation["chat_id"],
            draft,
            business_connection_id=conversation["business_connection_id"],
        )
        sent_message_id = sent.get("message_id") if isinstance(sent, dict) else None
        add_message(conversation["id"], "outbound", draft, sent_message_id)
        set_process_status(conversation["id"], "WAITING_RECRUITER")
        send_message(
            admin_chat_id,
            "🤖 Auto-reply sent and saved as outbound history.\n\n"
            + draft,
        )
        print("Safe auto-reply sent: conversation_id={}".format(conversation["id"]), flush=True)
        return True
    except Exception:
        print("Safe auto-reply failed; falling back to Copilot draft", flush=True)
        raise


def handle_business_message(message):
    text = (message.get("text") or message.get("caption") or "").strip()
    if not text:
        print("Business message skipped: empty text", flush=True)
        return

    admin_chat_id = get_setting("admin_chat_id")
    connection_id = message.get("business_connection_id")
    # A business_message itself carries the connection id, even when the
    # separate business_connection update was missed before polling started.
    # Persist it here so diagnostics and outbound replies can recover.
    if connection_id:
        set_setting("business_connection_id", connection_id)
        if not get_setting("business_owner_id"):
            try:
                connection_info = get_business_connection(connection_id)
                user = connection_info.get("user") or {}
                if user.get("id"):
                    set_setting("business_owner_id", user["id"])
                if connection_info.get("is_enabled") is not None:
                    set_setting("business_connection_enabled", str(bool(connection_info.get("is_enabled"))).lower())
                print("Business connection resolved via API: id={} owner={} enabled={}".format(
                    connection_id, user.get("id"), connection_info.get("is_enabled")
                ), flush=True)
            except Exception as exc:
                print("Business connection lookup failed: {}".format(repr(exc)), flush=True)
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")

    print(
        "Processing business message: chat_id={} message_id={} connection_id={}".format(
            chat_id, message_id, connection_id
        ),
        flush=True,
    )

    if not admin_chat_id or not connection_id or not chat_id:
        print("Business message skipped: missing admin/connection/chat", flush=True)
        return

    if not is_authorized_admin(admin_chat_id):
        print("Business message skipped: ADMIN_CHAT_ID is not configured or does not match", flush=True)
        return

    conversation = get_or_create_conversation(chat_id, connection_id)
    chat_name = " ".join(
        part for part in [chat.get("first_name"), chat.get("last_name")] if part
    ).strip() or chat.get("title") or ""
    update_contact(conversation["id"], chat_name, chat.get("username") or "")
    conversation = get_conversation(conversation["id"]) or conversation
    status = conversation["chat_status"]

    sender = message.get("from") or {}
    sender_id = sender.get("id")
    owner_id = get_setting("business_owner_id")

    if owner_id and sender_id and str(sender_id) == str(owner_id):
        print("Detected outbound business message", flush=True)
        if status == "RECRUITING":
            add_message(
                conversation["id"], "outbound", text, message_id
            )
        return

    if status == "IGNORED":
        print("Ignored chat; no AI analysis", flush=True)
        return

    if status == "RECRUITING":
        history = get_history(conversation["id"], 20)
        job_context = get_job_context(conversation["id"])
        add_message(conversation["id"], "inbound", text, message_id)

        result = analyze_message(text, history, job_context)
        result = merge_job_context(result, job_context)
        result = apply_policy(result, text, history)
        result = enrich_decision_options(result, text)
        update_job_context(
            conversation["id"],
            result.get("job") or {},
            replace=(result.get("job_context_relation") == "new_job"),
        )
        if result.get("action") == "NO_REPLY":
            print("Recruiting chat: NO_REPLY", flush=True)
            return

        if result.get("action") == "DRAFT" and auto_reply_is_enabled():
            try:
                if try_auto_reply(conversation, text, result, message_id, admin_chat_id):
                    return
            except Exception:
                # Fall back to normal Copilot flow if the automatic send fails.
                pass

        pending_id = save_pending(
            conversation["id"], message_id, result
        )
        set_process_status(conversation["id"], "INTERVIEW_SCHEDULED" if result.get("intent") == "interview_confirmation" else "WAITING_USER")
        notify_admin(
            admin_chat_id,
            text,
            result,
            pending_id,
            conversation["id"],
        )
        return

    print("New chat: classifying first inbound message", flush=True)
    result = analyze_message(text, [], {})
    result = merge_job_context(result, {})
    result = apply_policy(result, text, [])
    result = enrich_decision_options(result, text)

    if is_recruiting_result(result):
        set_chat_status(conversation["id"], "RECRUITING")
        add_message(conversation["id"], "inbound", text, message_id)
        update_job_context(
            conversation["id"],
            result.get("job") or {},
            replace=(result.get("job_context_relation") == "new_job"),
        )

        if result.get("action") == "NO_REPLY":
            return

        if result.get("action") == "DRAFT" and auto_reply_is_enabled():
            try:
                if try_auto_reply(conversation, text, result, message_id, admin_chat_id):
                    return
            except Exception:
                # Fall back to normal Copilot flow if the automatic send fails.
                pass

        pending_id = save_pending(
            conversation["id"], message_id, result
        )
        set_process_status(conversation["id"], "WAITING_USER")
        notify_admin(
            admin_chat_id,
            text,
            result,
            pending_id,
            conversation["id"],
        )
    else:
        set_chat_status(conversation["id"], "IGNORED")
        print("New chat classified as non-recruiting; ignored", flush=True)


def handle_callback(query):
    data = query.get("data") or ""
    message = query.get("message") or {}
    chat = message.get("chat") or {}
    admin_chat_id = chat.get("id")

    if not admin_chat_id:
        return

    # Callback buttons are part of the private admin interface too.
    # Do not process a forged callback from another Telegram chat.
    if not is_authorized_admin(admin_chat_id):
        return

    if data.startswith("auto:"):
        mode = data.split(":", 1)[1]
        if mode == "on":
            set_setting("auto_reply_enabled", "true")
            send_message(
                admin_chat_id,
                "🤖 Safe auto-replies: ON\n\n"
                "Only allow-listed profile facts can be answered automatically. "
                "Salary, interview availability, unknown skills, job interest, and other non-whitelisted topics remain in Copilot mode.",
                auto_reply_keyboard(True),
            )
        elif mode == "off":
            set_setting("auto_reply_enabled", "false")
            send_message(
                admin_chat_id,
                "🧑‍💻 Safe auto-replies: OFF\n\nAll recruiting messages stay in Copilot mode.",
                auto_reply_keyboard(False),
            )
        else:
            send_message(
                admin_chat_id,
                "🤖 Safe auto-replies are " + ("ON" if auto_reply_is_enabled() else "OFF") + ".\n\nChoose an action below.",
                auto_reply_keyboard(auto_reply_is_enabled()),
            )
        return

    if data.startswith("multi:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        decision, pending_id_text = parts[1], parts[2]
        pending = get_pending(int(pending_id_text))
        if not pending:
            send_message(admin_chat_id, "This decision is no longer available.")
            return
        conversation = get_conversation(pending["conversation_id"])
        if not conversation:
            send_message(admin_chat_id, "Conversation not found.")
            return
        result = json.loads(pending.get("result_json") or "{}")
        selected = selected_decisions(result)
        if decision.startswith("salary_"):
            selected["salary"] = decision
        elif decision.startswith("interview_"):
            selected["interview"] = decision
        result["selected_decisions"] = selected
        domains = decision_domains(result, " ".join(result.get("unknown_information") or []))
        draft = compose_multi_draft(result)
        all_selected = all(domain in selected for domain in domains)
        if all_selected:
            result["reply"] = draft
            result["action"] = "DRAFT"
            connection = __import__("storage").conn()
            connection.execute(
                "UPDATE pending SET result_json=?, draft=?, action=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), draft or "", "DRAFT", pending["id"]),
            )
            connection.commit()
            connection.close()
            send_message(
                admin_chat_id,
                "✅ Decisions selected: " + multi_decision_summary(result) + "\n\nSuggested reply:\n" + str(draft or "—"),
                reply_markup=reply_keyboard(pending["id"], conversation["id"], "DRAFT"),
            )
        else:
            # Keep the same pending item and update its stored result so the next
            # button click can complete the combined decision.
            connection = __import__("storage").conn()
            connection.execute(
                "UPDATE pending SET result_json=?, draft=?, action=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), draft or pending.get("draft") or "", "ASK_USER", pending["id"]),
            )
            connection.commit()
            connection.close()
            send_message(
                admin_chat_id,
                "Selected: " + multi_decision_summary(result) + "\n\nPlease choose the remaining decision.",
                reply_markup=multi_decision_keyboard(pending["id"], conversation["id"], domains, selected),
            )
        return

    if data.startswith("noop:"):
        return

    if data.startswith("decision:"):
        parts = data.split(":", 2)
        if len(parts) != 3:
            return
        decision, pending_id_text = parts[1], parts[2]
        pending = get_pending(int(pending_id_text))
        if not pending:
            send_message(admin_chat_id, "This draft is no longer available.")
            return
        conversation = get_conversation(pending["conversation_id"])
        if not conversation:
            send_message(admin_chat_id, "Conversation not found.")
            return

        templates = {
            "interview_available": "Thanks! I’d be happy to discuss the role. I’m available for an interview next week. Could you please share the available time slots?",
            "interview_unavailable": "Thanks for reaching out! I’m not available for an interview next week. Could you please let me know if there are alternative dates?",
            "interview_specific_time": "Thanks! I’d be happy to discuss the role. Could you please share the proposed interview time and timezone?",
            "salary_interested": "Thanks for sharing the salary range. The opportunity sounds interesting. I’d be happy to learn more about the role and next steps.",
            "salary_details": "Thanks for sharing the salary range. Could you please clarify whether the range is gross or net and share the full employment details?",
            "salary_later": "Thanks for sharing the details. I’d prefer to discuss compensation later in the process, once I learn more about the role and responsibilities.",
        }
        draft = templates.get(decision)
        if decision.startswith("choice_"):
            try:
                choice_index = int(decision.split("_", 1)[1])
                options = json.loads(pending.get("result_json") or "{}").get("decision_options") or []
                choice = options[choice_index]
            except (ValueError, IndexError, TypeError, json.JSONDecodeError):
                send_message(admin_chat_id, "This interview option is no longer available.")
                return
            label = choice.get("label", "").replace("📅 ", "", 1).strip()
            draft = (
                "Thanks! " + label + " works for me. "
                "I’m looking forward to discussing the role."
            )
        if not draft:
            send_message(admin_chat_id, "Unknown decision.")
            return

        # Claim before sending so rapid double-clicks cannot send twice.
        if not claim_pending(pending["id"]):
            send_message(admin_chat_id, "ℹ️ This decision has already been handled.")
            return
        connection_id = conversation["business_connection_id"]
        try:
            sent = send_message(
                conversation["chat_id"],
                draft,
                business_connection_id=connection_id,
            )
            sent_message_id = sent.get("message_id") if isinstance(sent, dict) else None
            add_message(conversation["id"], "outbound", draft, sent_message_id)
            mark_pending_sent(pending["id"])
            set_process_status(conversation["id"], "WAITING_RECRUITER")
            send_message(admin_chat_id, "✅ Decision selected and reply sent. Saved as outbound history.")
        except Exception:
            # Allow a retry if Telegram failed after the claim.
            connection = __import__("storage").conn()
            connection.execute("UPDATE pending SET state='PENDING', handled_at=NULL WHERE id=?", (pending["id"],))
            connection.commit()
            connection.close()
            raise
        return

    if data.startswith("filter:"):
        filter_name = data.split(":", 1)[1]
        if filter_name not in {"all", "waiting_user", "waiting_recruiter", "interview", "closed"}:
            filter_name = "all"
        show_chats(admin_chat_id, filter_name)
        return

    if data == "back_chats":
        show_chats(admin_chat_id)
        return

    if data.startswith("open:"):
        conversation_id = int(data.split(":", 1)[1])
        conversation = get_conversation(conversation_id)
        if not conversation:
            send_message(admin_chat_id, "Conversation not found.")
            return
        pending = get_latest_pending(conversation_id)
        if pending and pending.get("state") not in (None, "PENDING", "SENDING"):
            pending = None
        send_message(
            admin_chat_id,
            format_conversation_card(conversation, pending),
            reply_markup=conversation_card_keyboard(conversation, pending),
        )
        return

    if data.startswith("ignore:"):
        conversation_id = int(data.split(":", 1)[1])
        set_chat_status(conversation_id, "IGNORED")
        set_process_status(conversation_id, "CLOSED")
        send_message(
            admin_chat_id,
            "🔕 Chat ignored. New messages from this chat will not be analyzed.",
        )
        return

    if data.startswith("watch:"):
        conversation_id = int(data.split(":", 1)[1])
        set_chat_status(conversation_id, "RECRUITING")
        set_process_status(conversation_id, "WAITING_RECRUITER")
        send_message(
            admin_chat_id,
            "👀 Chat is now watched as a recruiting conversation.",
        )
        return

    if ":" not in data:
        return

    action, pending_id_text = data.split(":", 1)
    if action not in {"send", "edit", "noreply"}:
        return

    pending = get_pending(int(pending_id_text))
    if not pending:
        send_message(admin_chat_id, "This draft is no longer available.")
        return

    if action == "noreply":
        mark_pending_handled(pending["id"])
        clear_edit_state(admin_chat_id)
        send_message(admin_chat_id, "🚫 No reply sent.")
        return

    if action == "edit":
        set_edit_state(admin_chat_id, pending["id"])
        send_message(
            admin_chat_id,
            "✏️ Send the edited reply as your next message to this bot.",
        )
        return

    conversation = get_conversation(pending["conversation_id"])
    if not conversation:
        send_message(admin_chat_id, "Conversation not found.")
        return

    draft = pending["draft"]
    if not draft:
        send_message(admin_chat_id, "There is no reply to send.")
        return

    if not claim_pending(pending["id"]):
        send_message(admin_chat_id, "ℹ️ This draft has already been handled or sent.")
        return
    try:
        sent = send_message(
            conversation["chat_id"],
            draft,
            business_connection_id=conversation["business_connection_id"],
        )
        sent_message_id = sent.get("message_id") if isinstance(sent, dict) else None

        add_message(
            conversation["id"],
            "outbound",
            draft,
            sent_message_id,
        )
        mark_pending_sent(pending["id"])
        set_process_status(conversation["id"], "WAITING_RECRUITER")
        clear_edit_state(admin_chat_id)
        send_message(
            admin_chat_id,
            "✅ Reply sent and saved as outbound history.",
        )
    except Exception:
        connection = __import__("storage").conn()
        connection.execute("UPDATE pending SET state='PENDING', handled_at=NULL WHERE id=?", (pending["id"],))
        connection.commit()
        connection.close()
        raise


def show_status(admin_chat_id):
    owner = get_setting("business_owner_id")
    connection_id = get_setting("business_connection_id")
    conversations = list_conversations(100)
    recruiting = [c for c in conversations if c.get("chat_status") == "RECRUITING"]
    pending = [c for c in conversations if c.get("latest_pending_id") and c.get("latest_pending_action") in ("DRAFT", "ASK_USER")]
    connection_enabled = get_setting("business_connection_enabled")
    last_update_kind = get_setting("last_update_kind")
    lines = [
        "🩺 Career Inbox status",
        "",
        "Telegram polling: active if this message was received",
        "Last update: " + (last_update_kind or "not recorded"),
        "Business connection: " + ("configured" if connection_id else "not seen yet"),
        "Connection enabled: " + (connection_enabled if connection_enabled else "unknown"),
        "Safe auto-replies: " + ("ON" if auto_reply_is_enabled() else "OFF"),
        "Owner ID: " + ("saved" if owner else "not saved"),
        "Recruiting chats: " + str(len(recruiting)),
        "Pending actions: " + str(len(pending)),
        "",
        "Use /ping to test the bot connection."
    ]
    send_message(admin_chat_id, "\n".join(lines))


def handle_admin_message(message):
    chat = message.get("chat") or {}
    admin_chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if not admin_chat_id or not text:
        return

    if not is_authorized_admin(admin_chat_id):
        return

    edit_state = get_edit_state(admin_chat_id)
    if edit_state and not text.startswith("/"):
        pending = get_pending(edit_state["pending_id"])
        if not pending:
            clear_edit_state(admin_chat_id)
            return

        conversation = get_conversation(pending["conversation_id"])
        if not conversation:
            clear_edit_state(admin_chat_id)
            return

        if not claim_pending(pending["id"]):
            clear_edit_state(admin_chat_id)
            send_message(admin_chat_id, "ℹ️ This draft has already been handled or sent.")
            return

        try:
            sent = send_message(
                conversation["chat_id"],
                text,
                business_connection_id=conversation["business_connection_id"],
            )
            sent_message_id = (
                sent.get("message_id") if isinstance(sent, dict) else None
            )
            add_message(
                conversation["id"],
                "outbound",
                text,
                sent_message_id,
            )
            mark_pending_sent(pending["id"])
            set_process_status(conversation["id"], "WAITING_RECRUITER")
            clear_edit_state(admin_chat_id)
        except Exception:
            connection = __import__("storage").conn()
            connection.execute(
                "UPDATE pending SET state='PENDING', handled_at=NULL WHERE id=?",
                (pending["id"],),
            )
            connection.commit()
            connection.close()
            raise
        send_message(
            admin_chat_id,
            "✅ Edited reply sent and saved as outbound history.",
        )
        return

    if text == "/start":
        set_setting("admin_chat_id", admin_chat_id)
        send_message(
            admin_chat_id,
            "🤖 AI Career Inbox Assistant v6.4.3\n\n"
            "Copilot mode is enabled.\n"
            "/ping — Telegram connection test\n"
            "/analyze <text> — analyzer test\n"
            "/chats — Career Inbox\n"
            "/today — what needs attention today\n"
            "/status — diagnostics\n"
            "/auto — safe auto-replies",
        )
        return

    if text == "/ping":
        send_message(admin_chat_id, "🏓 Pong! Telegram connection works.")
        return

    if text.startswith("/analyze "):
        sample = text[len("/analyze "):].strip()
        try:
            result = apply_policy(analyze_message(sample, []))
            send_message(
                admin_chat_id,
                fmt_result(result)
                + "\n\nSuggested reply:\n"
                + str(result.get("reply") or "—"),
            )
        except Exception as exc:
            send_message(
                admin_chat_id,
                "❌ Analyze error: " + str(exc),
            )
        return

    if text == "/chats":
        show_chats(admin_chat_id)
        return

    if text == "/today":
        show_today(admin_chat_id)
        return

    if text == "/auto" or text.startswith("/auto "):
        mode = text[len("/auto"):].strip().lower()
        if mode == "on":
            set_setting("auto_reply_enabled", "true")
            send_message(
                admin_chat_id,
                "🤖 Safe auto-replies: ON\n\n"
                "Only allow-listed profile facts can be answered automatically. "
                "Salary, interview availability, unknown skills, job interest, and other non-whitelisted topics remain in Copilot mode.",
                auto_reply_keyboard(True),
            )
        elif mode == "off":
            set_setting("auto_reply_enabled", "false")
            send_message(
                admin_chat_id,
                "🧑‍💻 Safe auto-replies: OFF\n\nAll recruiting messages stay in Copilot mode.",
                auto_reply_keyboard(False),
            )
        elif mode in ("status", ""):
            send_message(
                admin_chat_id,
                "🤖 Safe auto-replies are " + ("ON" if auto_reply_is_enabled() else "OFF") + ".\n\nChoose an action below.",
                auto_reply_keyboard(auto_reply_is_enabled()),
            )
        else:
            send_message(
                admin_chat_id,
                "🤖 Unknown /auto option. Choose an action below.",
                auto_reply_keyboard(auto_reply_is_enabled()),
            )
        return

    if text == "/status":
        show_status(admin_chat_id)
        return

    send_message(
        admin_chat_id,
        "Use /start, /ping, /analyze <text>, /chats, /today, or /status.",
    )


def main():
    init_db()
    print(
        "AI Career Inbox Assistant v6.4.3 started.",
        flush=True,
    )
    print(
        "OpenAI model: "
        + os.environ.get("OPENAI_MODEL", "gpt-5.6-luna"),
        flush=True,
    )

    offset = None

    while True:
        try:
            updates = get_updates(offset=offset, timeout=25)

            if updates:
                print(
                    "Telegram: received {} update(s)".format(len(updates)),
                    flush=True,
                )

            for update in updates:
                offset = update["update_id"] + 1

                kinds = [
                    key
                    for key in (
                        "message",
                        "callback_query",
                        "business_connection",
                        "business_message",
                        "edited_business_message",
                    )
                    if key in update
                ]

                print(
                    "Telegram UPDATE {}: {}".format(
                        update.get("update_id"), kinds
                    ),
                    flush=True,
                )
                if kinds:
                    set_setting("last_update_kind", ", ".join(kinds))

                if "business_connection" in update:
                    connection = update["business_connection"]
                    user = connection.get("user") or {}
                    print(
                        "Business connection received: id={} enabled={}".format(
                            connection.get("id"),
                            connection.get("is_enabled"),
                        ),
                        flush=True,
                    )
                    if user.get("id"):
                        set_setting("business_owner_id", user["id"])
                    if connection.get("is_enabled") and connection.get("id"):
                        set_setting(
                            "business_connection_id",
                            connection.get("id"),
                        )

                if "callback_query" in update:
                    handle_callback(update["callback_query"])

                if "business_message" in update:
                    handle_business_message(update["business_message"])

                if "message" in update:
                    message = update["message"]
                    print(
                        "Regular message received: chat_id={} text={!r}".format(
                            (message.get("chat") or {}).get("id"),
                            (message.get("text") or "")[:80],
                        ),
                        flush=True,
                    )
                    handle_admin_message(message)

        except Exception as exc:
            print("Loop error: " + repr(exc), flush=True)
            traceback.print_exc()
            time.sleep(3)


if __name__ == "__main__":
    main()
