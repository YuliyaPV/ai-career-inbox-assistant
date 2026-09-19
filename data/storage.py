
import json
import sqlite3
import time

DB_PATH = "bot.db"


def conn():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = conn()
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            business_connection_id TEXT NOT NULL,
            chat_status TEXT NOT NULL DEFAULT 'NEW',
            process_status TEXT NOT NULL DEFAULT 'NEW',
            contact_name TEXT NOT NULL DEFAULT '',
            contact_username TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            job_context_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(chat_id, business_connection_id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            telegram_message_id INTEGER,
            created_at REAL NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id)
        );

        CREATE TABLE IF NOT EXISTS pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            source_message_id INTEGER,
            draft TEXT,
            action TEXT,
            reason TEXT,
            result_json TEXT,
            state TEXT NOT NULL DEFAULT 'PENDING',
            created_at REAL NOT NULL,
            handled_at REAL
        );

        CREATE TABLE IF NOT EXISTS edit_state (
            admin_chat_id INTEGER PRIMARY KEY,
            pending_id INTEGER NOT NULL
        );
        """
    )

    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(conversations)").fetchall()
    }
    if "chat_status" not in columns:
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN chat_status TEXT NOT NULL DEFAULT 'NEW'"
        )
    if "process_status" not in columns:
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN process_status TEXT NOT NULL DEFAULT 'NEW'"
        )
    if "created_at" not in columns:
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN created_at REAL NOT NULL DEFAULT 0"
        )
    if "updated_at" not in columns:
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN updated_at REAL NOT NULL DEFAULT 0"
        )
    if "job_context_json" not in columns:
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN job_context_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "contact_name" not in columns:
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN contact_name TEXT NOT NULL DEFAULT ''"
        )
    if "contact_username" not in columns:
        connection.execute(
            "ALTER TABLE conversations ADD COLUMN contact_username TEXT NOT NULL DEFAULT ''"
        )

    pending_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(pending)").fetchall()
    }
    if "result_json" not in pending_columns:
        connection.execute("ALTER TABLE pending ADD COLUMN result_json TEXT")
    if "state" not in pending_columns:
        connection.execute("ALTER TABLE pending ADD COLUMN state TEXT NOT NULL DEFAULT 'PENDING'")
    if "handled_at" not in pending_columns:
        connection.execute("ALTER TABLE pending ADD COLUMN handled_at REAL")

    connection.execute(
        "UPDATE messages SET role='inbound' WHERE role='recruiter'"
    )
    connection.execute(
        "UPDATE messages SET role='outbound' WHERE role='assistant'"
    )

    now = time.time()
    connection.execute(
        "UPDATE conversations SET created_at=? WHERE created_at=0", (now,)
    )
    connection.execute(
        "UPDATE conversations SET updated_at=? WHERE updated_at=0", (now,)
    )
    connection.commit()
    connection.close()


def set_setting(key, value):
    connection = conn()
    connection.execute(
        """
        INSERT INTO settings(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, str(value)),
    )
    connection.commit()
    connection.close()


def get_setting(key, default=None):
    connection = conn()
    row = connection.execute(
        "SELECT value FROM settings WHERE key=?", (key,)
    ).fetchone()
    connection.close()
    return row["value"] if row else default


def get_or_create_conversation(chat_id, connection_id):
    connection = conn()
    row = connection.execute(
        """
        SELECT * FROM conversations
        WHERE chat_id=? AND business_connection_id=?
        """,
        (chat_id, connection_id),
    ).fetchone()

    if row is None:
        now = time.time()
        cursor = connection.execute(
            """
            INSERT INTO conversations
            (chat_id, business_connection_id, chat_status, created_at, updated_at)
            VALUES(?, ?, 'NEW', ?, ?)
            """,
            (chat_id, connection_id, now, now),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM conversations WHERE id=?", (cursor.lastrowid,)
        ).fetchone()

    result = dict(row)
    connection.close()
    return result


def get_conversation(conversation_id):
    connection = conn()
    row = connection.execute(
        """
        SELECT c.*,
               (SELECT m.role FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_role,
               (SELECT m.text FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_text,
               (SELECT m.created_at FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_message_at
        FROM conversations c
        WHERE c.id=?
        """,
        (conversation_id,),
    ).fetchone()
    connection.close()
    return dict(row) if row else None


def set_process_status(conversation_id, status):
    connection = conn()
    connection.execute(
        "UPDATE conversations SET process_status=?, updated_at=? WHERE id=?",
        (status, time.time(), conversation_id),
    )
    connection.commit()
    connection.close()


def set_chat_status(conversation_id, status):
    connection = conn()
    connection.execute(
        "UPDATE conversations SET chat_status=?, updated_at=? WHERE id=?",
        (status, time.time(), conversation_id),
    )
    connection.commit()
    connection.close()


def add_message(conversation_id, role, text, telegram_message_id=None):
    connection = conn()

    if telegram_message_id is not None:
        row = connection.execute(
            """
            SELECT id FROM messages
            WHERE conversation_id=? AND telegram_message_id=?
            """,
            (conversation_id, telegram_message_id),
        ).fetchone()
        if row:
            connection.close()
            return row["id"]

    cursor = connection.execute(
        """
        INSERT INTO messages
        (conversation_id, role, text, telegram_message_id, created_at)
        VALUES(?, ?, ?, ?, ?)
        """,
        (conversation_id, role, text, telegram_message_id, time.time()),
    )
    connection.execute(
        "UPDATE conversations SET updated_at=? WHERE id=?",
        (time.time(), conversation_id),
    )
    connection.commit()
    message_id = cursor.lastrowid
    connection.close()
    return message_id


def get_history(conversation_id, limit=20):
    connection = conn()
    rows = connection.execute(
        """
        SELECT role, text FROM messages
        WHERE conversation_id=?
        ORDER BY id DESC LIMIT ?
        """,
        (conversation_id, limit),
    ).fetchall()
    connection.close()
    return [dict(row) for row in reversed(rows)]



def get_job_context(conversation_id):
    connection = conn()
    row = connection.execute(
        "SELECT job_context_json FROM conversations WHERE id=?", (conversation_id,)
    ).fetchone()
    connection.close()
    if not row:
        return {}
    try:
        value = json.loads(row["job_context_json"] or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def update_job_context(conversation_id, job, replace=False):
    if not isinstance(job, dict):
        return
    current = {} if replace else get_job_context(conversation_id)
    merged = dict(current)
    for key in ("role", "company", "work_format", "location", "domain"):
        value = job.get(key)
        if value:
            merged[key] = value
    technologies = job.get("technologies")
    if technologies:
        old = merged.get("technologies") or []
        merged["technologies"] = list(dict.fromkeys([*old, *technologies]))
    connection = conn()
    connection.execute(
        "UPDATE conversations SET job_context_json=?, updated_at=? WHERE id=?",
        (json.dumps(merged, ensure_ascii=False), time.time(), conversation_id),
    )
    connection.commit()
    connection.close()

def save_pending(conversation_id, source_message_id, result):
    connection = conn()
    cursor = connection.execute(
        """
        INSERT INTO pending
        (conversation_id, source_message_id, draft, action, reason, result_json, state, created_at)
        VALUES(?, ?, ?, ?, ?, ?, 'PENDING', ?)
        """,
        (
            conversation_id,
            source_message_id,
            result.get("reply") or "",
            result.get("action"),
            result.get("reason"),
            json.dumps(result, ensure_ascii=False),
            time.time(),
        ),
    )
    connection.commit()
    pending_id = cursor.lastrowid
    connection.close()
    return pending_id


def claim_pending(pending_id):
    """Atomically claim a pending action so a double-click cannot send twice."""
    connection = conn()
    now = time.time()
    cursor = connection.execute(
        "UPDATE pending SET state='SENDING', handled_at=? WHERE id=? AND state='PENDING'",
        (now, pending_id),
    )
    connection.commit()
    claimed = cursor.rowcount == 1
    connection.close()
    return claimed


def mark_pending_sent(pending_id):
    connection = conn()
    connection.execute(
        "UPDATE pending SET state='SENT', handled_at=? WHERE id=?",
        (time.time(), pending_id),
    )
    connection.commit()
    connection.close()


def mark_pending_handled(pending_id):
    connection = conn()
    connection.execute(
        "UPDATE pending SET state='HANDLED', handled_at=? WHERE id=?",
        (time.time(), pending_id),
    )
    connection.commit()
    connection.close()


def get_pending(pending_id):
    connection = conn()
    row = connection.execute(
        "SELECT * FROM pending WHERE id=?", (pending_id,)
    ).fetchone()
    connection.close()
    return dict(row) if row else None


def set_edit_state(admin_chat_id, pending_id):
    connection = conn()
    connection.execute(
        """
        INSERT INTO edit_state(admin_chat_id, pending_id) VALUES(?, ?)
        ON CONFLICT(admin_chat_id) DO UPDATE SET pending_id=excluded.pending_id
        """,
        (admin_chat_id, pending_id),
    )
    connection.commit()
    connection.close()


def get_edit_state(admin_chat_id):
    connection = conn()
    row = connection.execute(
        "SELECT * FROM edit_state WHERE admin_chat_id=?", (admin_chat_id,)
    ).fetchone()
    connection.close()
    return dict(row) if row else None


def clear_edit_state(admin_chat_id):
    connection = conn()
    connection.execute(
        "DELETE FROM edit_state WHERE admin_chat_id=?", (admin_chat_id,)
    )
    connection.commit()
    connection.close()


def update_contact(conversation_id, name=None, username=None):
    connection = conn()
    current = connection.execute(
        "SELECT contact_name, contact_username FROM conversations WHERE id=?",
        (conversation_id,),
    ).fetchone()
    if not current:
        connection.close()
        return
    contact_name = name if name else current["contact_name"]
    contact_username = username if username else current["contact_username"]
    connection.execute(
        "UPDATE conversations SET contact_name=?, contact_username=?, updated_at=? WHERE id=?",
        (contact_name or "", contact_username or "", time.time(), conversation_id),
    )
    connection.commit()
    connection.close()


def get_latest_pending(conversation_id):
    connection = conn()
    row = connection.execute(
        "SELECT * FROM pending WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    connection.close()
    return dict(row) if row else None


def list_conversations(limit=20):
    connection = conn()
    rows = connection.execute(
        """
        SELECT c.*,
               (SELECT m.role FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_role,
               (SELECT m.text FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_text,
               (SELECT m.created_at FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_message_at,
               (SELECT p.id FROM pending p WHERE p.conversation_id=c.id ORDER BY p.id DESC LIMIT 1) AS latest_pending_id,
               (SELECT p.action FROM pending p WHERE p.conversation_id=c.id ORDER BY p.id DESC LIMIT 1) AS latest_pending_action,
               (SELECT p.draft FROM pending p WHERE p.conversation_id=c.id ORDER BY p.id DESC LIMIT 1) AS latest_draft
        FROM conversations c
        ORDER BY updated_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    connection.close()
    return [dict(row) for row in rows]
