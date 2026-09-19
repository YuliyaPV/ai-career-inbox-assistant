
import json
import os
import urllib.parse
import urllib.request

TOKEN = os.environ["BOT_TOKEN"]
BASE = "https://api.telegram.org/bot" + TOKEN


def call(method, params=None):
    params = params or {}
    data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(BASE + "/" + method, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError("Telegram API error: " + str(result))
    return result["result"]


def get_business_connection(business_connection_id):
    return call("getBusinessConnection", {"business_connection_id": business_connection_id})


def get_updates(offset=None, timeout=25):
    allowed = [
        "message",
        "callback_query",
        "business_connection",
        "business_message",
        "edited_business_message",
    ]
    params = {
        "timeout": timeout,
        "allowed_updates": json.dumps(allowed),
    }
    if offset is not None:
        params["offset"] = offset
    return call("getUpdates", params)


def send_message(chat_id, text, reply_markup=None, business_connection_id=None):
    params = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup is not None:
        params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    if business_connection_id:
        params["business_connection_id"] = business_connection_id
    return call("sendMessage", params)
