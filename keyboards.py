def reply_keyboard(pending_id, conversation_id, action=None):
    # In v6.3, ASK_USER never exposes a direct Send button until the
    # required decision has been explicitly resolved.
    if action == "ASK_USER":
        return ask_user_keyboard(pending_id, conversation_id)
    return {
        "inline_keyboard": [
            [
                {"text": "✉️ Send", "callback_data": "send:" + str(pending_id)},
                {"text": "✏️ Edit", "callback_data": "edit:" + str(pending_id)},
            ],
            [{"text": "🚫 Don't reply", "callback_data": "noreply:" + str(pending_id)}],
            [{"text": "🔕 Ignore this chat", "callback_data": "ignore:" + str(conversation_id)}],
        ]
    }


def ask_user_keyboard(pending_id, conversation_id):
    return {
        "inline_keyboard": [
            [{"text": "✏️ Write manually", "callback_data": "edit:" + str(pending_id)}],
            [{"text": "🚫 Don't reply", "callback_data": "noreply:" + str(pending_id)}],
            [{"text": "🔕 Ignore this chat", "callback_data": "ignore:" + str(conversation_id)}],
        ]
    }


def decision_keyboard(pending_id, conversation_id, options):
    rows = []
    for label, action in options:
        rows.append([{
            "text": label,
            "callback_data": "decision:{}:{}".format(action, pending_id),
        }])
    rows.append([
        {"text": "✏️ Write manually", "callback_data": "edit:" + str(pending_id)}
    ])
    rows.append([
        {"text": "🚫 Don't reply", "callback_data": "noreply:" + str(pending_id)},
        {"text": "🔕 Ignore chat", "callback_data": "ignore:" + str(conversation_id)},
    ])
    return {"inline_keyboard": rows}


def chats_keyboard(conversations, active_filter="all", counts=None):
    counts = counts or {}
    labels = [
        ("all", "📥 All"),
        ("waiting_user", "🟡 Waiting for me"),
        ("waiting_recruiter", "🔵 Waiting recruiter"),
        ("interview", "🟣 Interviews"),
        ("closed", "⚪ Closed"),
    ]
    rows = []
    filter_row = []
    for key, label in labels:
        count = counts.get(key)
        if count is not None:
            label += " ({})".format(count)
        if key == active_filter:
            label = "✅ " + label
        filter_row.append({"text": label, "callback_data": "filter:" + key})
        if len(filter_row) == 2:
            rows.append(filter_row)
            filter_row = []
    if filter_row:
        rows.append(filter_row)

    for conversation in conversations:
        status = conversation["chat_status"]
        rows.append([{
            "text": "📂 Open · {}".format(conversation.get("contact_name") or "Chat {}".format(conversation["chat_id"])),
            "callback_data": "open:{}".format(conversation["id"]),
        }])
        label = "👀 Watch" if status == "IGNORED" else "🔕 Ignore"
        action = "watch" if status == "IGNORED" else "ignore"
        rows.append([{
            "text": "{} · chat {}".format(label, conversation["chat_id"]),
            "callback_data": "{}:{}".format(action, conversation["id"]),
        }])
    return {"inline_keyboard": rows}

def conversation_card_keyboard(conversation, pending=None):
    rows = []
    if pending and pending.get("draft"):
        if pending.get("action") == "ASK_USER":
            rows.append([{
                "text": "⚠️ Your decision is required", "callback_data": "noop:" + str(pending["id"]),
            }])
            rows.append([{
                "text": "✏️ Write manually", "callback_data": "edit:" + str(pending["id"]),
            }])
            rows.append([{
                "text": "🚫 Don't reply", "callback_data": "noreply:" + str(pending["id"]),
            }])
        else:
            rows.append([
                {"text": "✉️ Send draft", "callback_data": "send:" + str(pending["id"])},
                {"text": "✏️ Edit", "callback_data": "edit:" + str(pending["id"])}
            ])
            rows.append([{
                "text": "🚫 Don't reply", "callback_data": "noreply:" + str(pending["id"]),
            }])
    rows.append([{"text": "🔙 Back to chats", "callback_data": "back_chats"}])
    if conversation.get("chat_status") == "RECRUITING":
        rows.append([{
            "text": "🔕 Ignore chat", "callback_data": "ignore:" + str(conversation["id"]),
        }])
    else:
        rows.append([{
            "text": "👀 Watch chat", "callback_data": "watch:" + str(conversation["id"]),
        }])
    return {"inline_keyboard": rows}


def multi_decision_keyboard(pending_id, conversation_id, domains, selected=None):
    selected = selected or {}
    rows = []
    if "salary" in domains:
        rows.append([{"text": "💰 Salary: choose response", "callback_data": "noop:" + str(pending_id)}])
        rows.extend([
            [{"text": ("✅ " if selected.get("salary") == "salary_interested" else "") + "Interested", "callback_data": "multi:salary_interested:" + str(pending_id)}],
            [{"text": ("✅ " if selected.get("salary") == "salary_details" else "") + "Ask for more details", "callback_data": "multi:salary_details:" + str(pending_id)}],
            [{"text": ("✅ " if selected.get("salary") == "salary_later" else "") + "Discuss later", "callback_data": "multi:salary_later:" + str(pending_id)}],
        ])
    if "interview" in domains:
        rows.append([{"text": "📅 Interview: choose response", "callback_data": "noop:" + str(pending_id)}])
        rows.extend([
            [{"text": ("✅ " if selected.get("interview") == "interview_available" else "") + "I’m available", "callback_data": "multi:interview_available:" + str(pending_id)}],
            [{"text": ("✅ " if selected.get("interview") == "interview_specific_time" else "") + "Ask for specific time", "callback_data": "multi:interview_specific_time:" + str(pending_id)}],
            [{"text": ("✅ " if selected.get("interview") == "interview_unavailable" else "") + "Not available", "callback_data": "multi:interview_unavailable:" + str(pending_id)}],
        ])
    rows.append([{"text": "✏️ Write manually", "callback_data": "edit:" + str(pending_id)}])
    rows.append([
        {"text": "🚫 Don't reply", "callback_data": "noreply:" + str(pending_id)},
        {"text": "🔕 Ignore chat", "callback_data": "ignore:" + str(conversation_id)},
    ])
    return {"inline_keyboard": rows}


def auto_reply_keyboard(enabled=False):
    return {
        "inline_keyboard": [
            [
                {"text": ("🟢 Turn off" if enabled else "🟢 Turn on"), "callback_data": "auto:off" if enabled else "auto:on"},
                {"text": "ℹ️ Status", "callback_data": "auto:status"},
            ],
        ]
    }
