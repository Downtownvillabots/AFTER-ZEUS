# ============================================================
# DOWNTOWN VILLA — ULTIMATE BACKUP PLUGIN  (fast + self-healing)
# ============================================================
#
# ONE ADMIN COMMAND:
#
#     /backup
#
# Media -> Media2 -> Media3 ordered, resumable, self-healing panel.
#
# ============================================================

import os
import time
import asyncio
import hashlib
import logging
import traceback
from datetime import datetime, timedelta
from collections import defaultdict

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.errors import (
    FloodWait,
    RPCError,
)

from database.ia_filterdb import (
    db,
    db2,
    db3,
)

try:
    from info import (
        ADMINS,
        COLLECTION_NAME,
        MULTIPLE_DB,
    )
except Exception:
    ADMINS = []
    COLLECTION_NAME = "Telegram_files"
    MULTIPLE_DB = True


# ============================================================
# LOGGER
# ============================================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── SEPARATE MongoDB SHARDS for backup state (keeps Media DBs clean) ──
try:
    from motor.motor_asyncio import AsyncIOMotorClient as _BackupMongoClient

    _BACKUP_DBNAME = os.getenv("BACKUP_MONGO_DB", "downtown_backup")
    _BACKUP_URIS = []

    for i in range(1, 11):
        uri = os.getenv(f"BACKUP_MONGO_URI_{i}", "").strip()
        if uri:
            _BACKUP_URIS.append(uri)

    single_uri = os.getenv("BACKUP_MONGO_URI", "").strip()
    if single_uri and single_uri not in _BACKUP_URIS:
        _BACKUP_URIS.insert(0, single_uri)

    _backup_clients = []
    _backup_dbs = []

    for uri in _BACKUP_URIS:
        try:
            client = _BackupMongoClient(uri, serverSelectionTimeoutMS=8000)
            db_handle = client[_BACKUP_DBNAME]
            _backup_clients.append(client)
            _backup_dbs.append(db_handle)
            logger.info(f"[BACKUP] Backup Mongo shard connected: {len(_backup_dbs)}")
        except Exception as e:
            logger.warning(f"[BACKUP] Shard failed: {e}")

    if _backup_dbs:
        _backup_db = _backup_dbs[0]
        logger.info(f"[BACKUP] {len(_backup_dbs)} backup shard(s) active")
    else:
        _backup_db = None
        logger.warning("[BACKUP] No backup Mongo configured — using Media DB (may fill up!)")
except Exception as _e:
    _backup_db = None
    _backup_dbs = []
    logger.exception(f"[BACKUP] Backup Mongo init failed: {_e}")


# ============================================================
# CONFIGURATION
# ============================================================

BACKUP_CHANNEL_ID_RAW = os.getenv("BACKUP_CHANNEL_ID", "").strip()

BACKUP_AUTO_START = os.getenv("BACKUP_AUTO_START", "true").lower() in {
    "true", "1", "yes", "on",
}

BACKUP_WATCH_INTERVAL = max(2, int(os.getenv("BACKUP_WATCH_INTERVAL", "5")))
BACKUP_UPLOAD_DELAY   = max(0.0, float(os.getenv("BACKUP_UPLOAD_DELAY", "0.5")))
BACKUP_RETRY_DELAY    = max(1, int(os.getenv("BACKUP_RETRY_DELAY", "5")))
BACKUP_MAX_RETRIES    = max(1, int(os.getenv("BACKUP_MAX_RETRIES", "8")))
BACKUP_RECONCILE_MESSAGES = max(100, int(os.getenv("BACKUP_RECONCILE_MESSAGES", "1000")))

BACKUP_STATE_COLLECTION = os.getenv(
    "BACKUP_STATE_COLLECTION",
    f"{COLLECTION_NAME}_backup_state",
)
BACKUP_RUN_COLLECTION = os.getenv(
    "BACKUP_RUN_COLLECTION",
    f"{COLLECTION_NAME}_backup_runs",
)
BACKUP_PANEL_COLLECTION = os.getenv(
    "BACKUP_PANEL_COLLECTION",
    f"{COLLECTION_NAME}_backup_panel",
)
BACKUP_TOKEN_PREFIX = os.getenv("BACKUP_TOKEN_PREFIX", "DTV-BACKUP")

# Larger batches → far fewer Mongo round-trips during scan
BACKUP_SCAN_BATCH = max(100, int(os.getenv("BACKUP_SCAN_BATCH", "1000")))
BACKUP_UPLOADED_PRELOAD_BATCH = max(
    1000, int(os.getenv("BACKUP_UPLOADED_PRELOAD_BATCH", "5000"))
)


# ============================================================
# ADMIN PARSING
# ============================================================

def _build_admin_set():
    result = set()
    values = ADMINS

    if isinstance(values, (str, int)):
        values = str(values).replace(",", " ").split()

    if values is None:
        values = []

    try:
        for value in values:
            try:
                result.add(int(value))
            except Exception:
                pass
    except Exception:
        pass

    env_admins = os.getenv("ADMINS", "").replace(",", " ").split()
    for value in env_admins:
        try:
            result.add(int(value))
        except Exception:
            pass

    return result


ADMIN_IDS = _build_admin_set()


def is_admin(user_id):
    try:
        return int(user_id) in ADMIN_IDS
    except Exception:
        return False


# ============================================================
# CHANNEL
# ============================================================

def get_backup_channel_id():
    if not BACKUP_CHANNEL_ID_RAW:
        return None
    try:
        return int(BACKUP_CHANNEL_ID_RAW)
    except Exception:
        return None


def backup_configured():
    return get_backup_channel_id() is not None


# ============================================================
# SOURCE DATABASE ORDER
# ============================================================

SOURCE_DATABASES = [
    ("Media", db, 1),
    ("Media2", db2, 2),
    ("Media3", db3, 3),
]


def enabled_source_databases():
    if MULTIPLE_DB:
        return SOURCE_DATABASES
    return SOURCE_DATABASES[:1]


def source_collection(database):
    if database is None:
        return None
    return database[COLLECTION_NAME]


# ============================================================
# COLLECTION ACCESSORS
# ============================================================

def state_collection(shard_index: int = 0):
    if _backup_dbs:
        if 0 <= shard_index < len(_backup_dbs):
            return _backup_dbs[shard_index][BACKUP_STATE_COLLECTION]
        return _backup_dbs[0][BACKUP_STATE_COLLECTION]
    if db is None:
        return None
    return db[BACKUP_STATE_COLLECTION]


def run_collection(shard_index: int = 0):
    if _backup_dbs:
        return _backup_dbs[0][BACKUP_RUN_COLLECTION]
    if db is None:
        return None
    return db[BACKUP_RUN_COLLECTION]


def panel_collection():
    if _backup_dbs:
        return _backup_dbs[0][BACKUP_PANEL_COLLECTION]
    if db is not None:
        return db[BACKUP_PANEL_COLLECTION]
    return None


def get_shard_for_file(source_db: str, file_id: str) -> int:
    if not _backup_dbs:
        return 0
    key = f"{source_db}:{file_id}".encode("utf-8")
    h = int.from_bytes(key[:4], "big")
    return h % len(_backup_dbs)


def all_state_collections():
    if _backup_dbs:
        return [d[BACKUP_STATE_COLLECTION] for d in _backup_dbs]
    if db is not None:
        return [db[BACKUP_STATE_COLLECTION]]
    return []


# ============================================================
# RUNTIME STATE
# ============================================================

STATE = {
    "running": False,
    "paused": False,
    "stop_requested": False,
    "mode": "IDLE",
    "started_at": None,
    "finished_at": None,
    "current_db": None,
    "current_db_number": 0,
    "current_file": None,
    "current_file_id": None,
    "current_file_size": 0,
    "current_source_index": 0,
    "current_source_total": 0,
    "current_uploaded": 0,
    "current_failed": 0,
    "current_skipped": 0,
    "total_uploaded": 0,
    "total_failed": 0,
    "total_skipped": 0,
    "speed": 0.0,
    "eta": None,
    "last_error": None,
    "last_activity": None,
    "last_success": None,
    "last_message_id": None,
    "last_cycle": 0,
    "last_scan": None,
    "run_id": None,
    "message": "",
    "worker_pid": os.getpid(),
    "flood_wait_until": None,
    "last_flood_wait": None,
    "preload_progress": None,
}


WORKER_TASK = None
WATCHER_TASK = None

STATE_LOCK = asyncio.Lock()
PANEL_LOCK = asyncio.Lock()

ACTIVE_PANELS = {}


# ============================================================
# HELPERS
# ============================================================

def now():
    return datetime.utcnow()


def now_text():
    return now().strftime("%d %b %Y • %H:%M:%S UTC")


def fmt_int(value):
    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def fmt_float(value, places=2):
    try:
        return f"{float(value):.{places}f}"
    except Exception:
        return f"{0:.{places}f}"


def fmt_bytes(value):
    try:
        value = float(value)
    except Exception:
        return "0 B"
    if value <= 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB")
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.2f} {units[index]}"


def fmt_duration(seconds):
    if seconds is None:
        return "0s"
    try:
        seconds = max(0, int(seconds))
    except Exception:
        return "0s"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def html_escape(value):
    text = str(value if value is not None else "")
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def short(value, length=90):
    text = str(value if value is not None else "-")
    if len(text) <= length:
        return text
    return text[: length - 3] + "..."


# ============================================================
# COLORFUL PROGRESS BAR  🟢🟡🟠🔴
# ============================================================

def progress_bar(current, total, length=20):
    """
    Square-block progress bar.
    """
    try:
        current = float(current)
        total = float(total)
    except Exception:
        current = 0
        total = 0

    if total <= 0:
        percent = 0.0
    else:
        percent = (current / total) * 100

    percent = max(0.0, min(100.0, percent))
    filled = int(length * percent / 100)

    if percent >= 90:
        fill = "🟩"
    elif percent >= 60:
        fill = "🟨"
    elif percent >= 30:
        fill = "🟧"
    else:
        fill = "🟥"

    empty = "⬜"
    bar = fill * filled + empty * (length - filled)
    return f"<code>{bar}</code> <b>{percent:.1f}%</b>"


def _bar_text(pct, length=12):
    """
    Emoji progress bar that changes color as it grows:
    red → orange → yellow → green
    """
    try:
        pct = float(pct)
    except Exception:
        pct = 0.0
    pct = max(0.0, min(100.0, pct))
    filled = int(length * pct / 100)

    if pct >= 90:
        fill = "🟩"
    elif pct >= 60:
        fill = "🟨"
    elif pct >= 30:
        fill = "🟧"
    else:
        fill = "🟥"

    empty = "⬜"
    return fill * filled + empty * (length - filled)


def _eta_seconds(pending, speed):
    """Return ETA in seconds, or None."""
    try:
        if speed and speed > 0 and pending and pending > 0:
            return int(pending / speed)
    except Exception:
        pass
    return None


def _wall_time(seconds):
    """HH:MM:SS UTC from now + seconds."""
    if not seconds:
        return "—"
    try:
        return (now() + timedelta(seconds=int(seconds))).strftime("%H:%M:%S UTC")
    except Exception:
        return "—"


def status_icon(status):

def status_icon(status):
    status = str(status or "").upper()

    if status in {"ONLINE", "RUNNING", "UPLOADED", "COMPLETED", "CONNECTED", "ACTIVE"}:
        return "🟢"
    if status in {"FAILED", "ERROR", "OFFLINE", "STOPPED", "CANCELLED"}:
        return "🔴"
    if status in {"PAUSED", "WAITING", "PENDING", "UPLOADING", "RECONCILING"}:
        return "🟡"
    if status in {"FLOOD_WAIT"}:
        return "🌊"
    return "⚪"


def source_file_id(document):
    value = document.get("_id")
    if value is None:
        value = document.get("file_id")
    if value is None:
        return None
    return str(value)


def source_file_name(document):
    value = document.get("file_name")
    if value:
        return str(value)
    return "Unknown File"


def source_file_size(document):
    try:
        return int(document.get("file_size", 0) or 0)
    except Exception:
        return 0


def source_caption(document):
    value = document.get("caption")
    if value:
        return str(value)
    return source_file_name(document)


def backup_key(source_db, file_id):
    return f"{source_db}:{file_id}"


def backup_token(source_db, file_id):
    raw = (
        f"{BACKUP_TOKEN_PREFIX}|{source_db}|{file_id}"
    ).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return f"{BACKUP_TOKEN_PREFIX}-{source_db}-{digest[:32]}"


# ============================================================
# PANEL MESSAGE RECOVERY
# ============================================================

def _is_invalid_message_error(exc) -> bool:
    """
    True only when the stored panel message can no longer be edited.
    FloodWait / transient RPC are NOT treated as invalid.
    """
    try:
        text = str(exc).upper()
    except Exception:
        return False

    markers = (
        "MESSAGE_ID_INVALID",
        "MESSAGEIDINVALID",
        "MESSAGE_NOT_FOUND",
        "MESSAGE TO EDIT NOT FOUND",
        "MESSAGE TO BE EDITED NOT FOUND",
        "MESSAGE_DELETE_FORBIDDEN",
        "MESSAGE_AUTHOR_REQUIRED",
        "CHAT_WRITE_FORBIDDEN",
        "PEER_ID_INVALID",
        "CHANNEL_INVALID",
    )
    return any(m in text for m in markers)


async def save_panel_ref(chat_id, message_id, status="active"):
    if chat_id is None or message_id is None:
        return
    coll = panel_collection()
    if coll is None:
        return
    try:
        await coll.update_one(
            {"_id": "current_panel"},
            {
                "$set": {
                    "chat_id": int(chat_id),
                    "message_id": int(message_id),
                    "updated_at": now(),
                    "status": str(status),
                },
                "$setOnInsert": {"created_at": now()},
            },
            upsert=True,
        )
    except Exception:
        logger.exception("[BACKUP] Failed to persist panel reference")


async def load_panel_ref():
    coll = panel_collection()
    if coll is None:
        return None
    try:
        return await coll.find_one({"_id": "current_panel"})
    except Exception:
        logger.exception("[BACKUP] Failed to load panel reference")
        return None


async def mark_panel_ref_stale():
    coll = panel_collection()
    if coll is None:
        return
    try:
        await coll.update_one(
            {"_id": "current_panel"},
            {"$set": {"status": "stale", "updated_at": now()}},
        )
    except Exception:
        pass


async def recreate_panel(client, chat_id, page="live"):
    """
    Create a fresh panel after the previous one became invalid.
    Protected by PANEL_LOCK so at most one recreation happens.
    """
    if client is None or chat_id is None:
        logger.warning("[BACKUP] Cannot recreate panel — missing client or chat_id")
        return None

    async with PANEL_LOCK:
        logger.info("[BACKUP] Backup panel message invalid; recreating panel")

        try:
            if page == "history":
                text = await build_history_page()
            elif page == "failures":
                text = await build_failure_page()
            else:
                text = await build_status_page()

            sent = await client.send_message(
                chat_id=int(chat_id),
                text=text,
                reply_markup=backup_keyboard(),
                disable_web_page_preview=True,
            )

            new_id = getattr(sent, "id", None)
            if not new_id:
                logger.warning("[BACKUP] Panel recreation returned no message ID")
                return None

            STATE["last_message_id"] = int(new_id)
            await save_panel_ref(int(chat_id), int(new_id), status="active")

            ACTIVE_PANELS[int(new_id)] = {
                "page": page,
                "task": None,
                "closed": False,
                "created": time.time(),
            }

            logger.info(
                "[BACKUP] Backup panel recreated successfully: "
                "chat_id=%s, message_id=%s",
                chat_id, new_id,
            )
            return sent

        except FloodWait as exc:
            wait = max(1, int(getattr(exc, "value", 30)))
            logger.warning("[BACKUP] FloodWait %ss during panel recreation", wait)
            await asyncio.sleep(wait + 2)
            return None

        except Exception:
            logger.exception("[BACKUP] Panel recreation failed")
            return None


async def restore_panel_ref(client):
    ref = await load_panel_ref()
    if not ref:
        return None

    chat_id = ref.get("chat_id")
    message_id = ref.get("message_id")
    if chat_id is None or message_id is None:
        return None

    try:
        STATE["last_message_id"] = int(message_id)
    except Exception:
        pass

    try:
        message = await client.get_messages(
            chat_id=int(chat_id),
            message_ids=int(message_id),
        )

        if not message:
            await mark_panel_ref_stale()
            return None

        open_panel(message, "live")
        logger.info(
            "[BACKUP] Restored panel: chat_id=%s message_id=%s",
            chat_id, message_id,
        )
        return message

    except FloodWait as exc:
        wait = max(1, int(getattr(exc, "value", 30)))
        await asyncio.sleep(wait + 2)
        return None

    except Exception as exc:
        logger.info(
            "[BACKUP] Persisted panel ref unusable (%s); "
            "will recreate on next update",
            exc,
        )
        await mark_panel_ref_stale()
        return None


async def _edit_panel_message(message, text, keyboard=None):
    """
    Edit panel with built-in FloodWait retry.
    Returns (success, invalid).
    """
    for _ in range(3):
        try:
            await message.edit_text(
                text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            return True, False

        except FloodWait as exc:
            wait = max(1, int(getattr(exc, "value", 30)))
            logger.warning("[BACKUP] FloodWait %ss during panel edit", wait)
            await asyncio.sleep(wait + 2)
            continue

        except RPCError as exc:
            exc_str = str(exc).upper()
            if "MESSAGE_NOT_MODIFIED" in exc_str:
                return True, False
            if _is_invalid_message_error(exc):
                logger.warning(
                    "[BACKUP] Backup panel message invalid; recreating panel"
                )
                return False, True
            logger.warning("Backup panel edit failed: %s", exc)
            return False, False

        except Exception as exc:
            if _is_invalid_message_error(exc):
                logger.warning(
                    "[BACKUP] Backup panel message invalid; recreating panel"
                )
                return False, True
            logger.warning("Backup panel edit error: %s", exc)
            return False, False

    return False, False


# ============================================================
# INDEXES
# ============================================================

async def ensure_indexes():
    if not all_state_collections():
        return False

    try:
        for collection in all_state_collections():
            try:
                await collection.create_index(
                    [("source_db", 1), ("file_id", 1)],
                    unique=True,
                    name="source_file_unique",
                )
                await collection.create_index(
                    [("source_db", 1), ("status", 1)],
                    name="source_status",
                )
                await collection.create_index(
                    [("status", 1), ("updated_at", 1)],
                    name="status_updated",
                )
                await collection.create_index(
                    [("backup_token", 1)],
                    unique=True,
                    sparse=True,
                    name="backup_token_unique",
                )
            except Exception:
                logger.exception("Index creation failed on a shard")

        runs = run_collection()
        if runs is not None:
            await runs.create_index(
                [("started_at", -1)],
                name="runs_started",
            )

        return True
    except Exception:
        logger.exception("Backup index creation failed")
        return False


# ============================================================
# STATE STORAGE
# ============================================================

async def get_state(source_db, file_id):
    fid = str(file_id)
    idx = get_shard_for_file(source_db, fid)
    coll = state_collection(idx)
    if coll is None:
        return None
    try:
        rec = await coll.find_one(
            {"source_db": source_db, "file_id": fid}
        )
        if rec:
            return rec
        for c in all_state_collections():
            rec = await c.find_one(
                {"source_db": source_db, "file_id": fid}
            )
            if rec:
                return rec
        return None
    except Exception:
        logger.exception("Backup state read failed")
        return None


async def get_status(source_db, file_id):
    record = await get_state(source_db, file_id)
    if not record:
        return "PENDING"
    return str(record.get("status", "PENDING")).upper()


async def set_state(
    source_db,
    document,
    status,
    *,
    message_id=None,
    error=None,
    attempts=None,
):
    file_id = source_file_id(document)
    if not file_id:
        raise ValueError("Document has no file_id")

    fid = str(file_id)
    idx = get_shard_for_file(source_db, fid)
    coll = state_collection(idx)
    if coll is None:
        raise RuntimeError("Backup state collection unavailable")

    token = backup_token(source_db, fid)
    timestamp = now()

    update = {
        "$set": {
            "source_db": source_db,
            "file_id": fid,
            "file_name": source_file_name(document),
            "file_size": source_file_size(document),
            "backup_token": token,
            "status": str(status).upper(),
            "updated_at": timestamp,
            "shard_index": idx,
        },
        "$setOnInsert": {"created_at": timestamp},
    }

    if message_id is not None:
        update["$set"]["message_id"] = int(message_id)
    if error is not None:
        update["$set"]["last_error"] = str(error)[:4000]
    if attempts is not None:
        update["$set"]["attempts"] = int(attempts)

    await coll.update_one(
        {"source_db": source_db, "file_id": fid},
        update,
        upsert=True,
    )


async def mark_uploading(source_db, document):
    file_id = source_file_id(document)
    existing = await get_state(source_db, file_id)
    attempts = int(existing.get("attempts", 0)) if existing else 0
    await set_state(
        source_db,
        document,
        "UPLOADING",
        attempts=attempts + 1,
    )


async def mark_uploaded(source_db, document, message_id):
    await set_state(
        source_db,
        document,
        "UPLOADED",
        message_id=message_id,
    )


async def mark_failed(source_db, document, error):
    existing = await get_state(source_db, source_file_id(document))
    attempts = int(existing.get("attempts", 0)) if existing else 1
    await set_state(
        source_db,
        document,
        "FAILED",
        error=error,
        attempts=attempts,
    )


async def reset_failed():
    total = 0
    for coll in all_state_collections():
        try:
            r = await coll.update_many(
                {"status": "FAILED"},
                {"$set": {"status": "PENDING", "updated_at": now()}},
            )
            total += r.modified_count
        except Exception:
            pass
    return total

# ============================================================
# RUN HISTORY
# ============================================================

async def create_run():
    collection = run_collection()
    if collection is None:
        return None
    result = await collection.insert_one(
        {
            "started_at": now(),
            "status": "RUNNING",
            "uploaded": 0,
            "failed": 0,
            "skipped": 0,
            "pid": os.getpid(),
        }
    )
    return result.inserted_id


async def finish_run(status):
    collection = run_collection()
    run_id = STATE.get("run_id")
    if collection is None or run_id is None:
        return

    await collection.update_one(
        {"_id": run_id},
        {
            "$set": {
                "status": status,
                "finished_at": now(),
                "uploaded": STATE["total_uploaded"],
                "failed": STATE["total_failed"],
                "skipped": STATE["total_skipped"],
                "last_error": STATE["last_error"],
            }
        },
    )


async def get_history(limit=12):
    collection = run_collection()
    if collection is None:
        return []
    cursor = collection.find({}).sort("started_at", -1).limit(int(limit))
    return await cursor.to_list(length=int(limit))


# ============================================================
# COUNTS
# ============================================================

async def count_state(source_db=None, status=None):
    query = {}
    if source_db:
        query["source_db"] = source_db
    if status:
        query["status"] = str(status).upper()

    total = 0
    for coll in all_state_collections():
        try:
            total += await coll.count_documents(query)
        except Exception:
            pass
    return total


async def source_count(source_db, database):
    collection = source_collection(database)
    if collection is None:
        return 0
    try:
        return await collection.count_documents({})
    except Exception:
        return 0


async def database_snapshot():
    snapshot = {}

    for source_db, database, number in enabled_source_databases():
        total     = await source_count(source_db, database)
        uploaded  = await count_state(source_db, "UPLOADED")
        uploading = await count_state(source_db, "UPLOADING")
        failed    = await count_state(source_db, "FAILED")
        pending   = max(0, total - uploaded)

        snapshot[source_db] = {
            "number": number,
            "total": total,
            "uploaded": uploaded,
            "pending": pending,
            "uploading": uploading,
            "failed": failed,
        }

    return snapshot


async def total_pending():
    snapshot = await database_snapshot()
    return sum(item["pending"] for item in snapshot.values())


# ============================================================
# FAST PERSISTENT RESUME SCANNER  🚀
# ============================================================
#
# The old version queried Mongo once per 100-doc batch → 1500+
# round-trips before the first uploadable file.
#
# This version pre-loads ALL uploaded file_ids for the source_db
# in a single cursor pass, then walks the source in one go and
# yields only the ones that are NOT uploaded yet.
#
# Memory: ~40 bytes per uploaded file_id.
#   156,000 IDs  ≈  6 MB
#   1,000,000    ≈ 40 MB
#
# The scanner is called once per source_db per run_backup pass
# (Media / Media2 / Media3). The pre-loaded set is rebuilt each
# call, so newly-uploaded files are always skipped correctly.
#
# ============================================================

async def _load_uploaded_set(source_db):
    """
    Return a set() of every file_id already marked UPLOADED
    for this source_db.
    """
    uploaded = set()

    # Search across ALL state shards (file → shard is deterministic
    # but the mapping isn't exposed here without walking each file).
    for coll in all_state_collections():
        if coll is None:
            continue
        try:
            cursor = coll.find(
                {"source_db": source_db, "status": "UPLOADED"},
                {"file_id": 1, "_id": 0},
            ).batch_size(BACKUP_UPLOADED_PRELOAD_BATCH)

            async for record in cursor:
                value = record.get("file_id")
                if value is not None:
                    uploaded.add(str(value))

        except Exception:
            logger.exception(
                "[BACKUP] Failed to pre-load uploaded ids from a shard"
            )

    logger.info(
        "[BACKUP] Pre-loaded %s uploaded file_ids for %s",
        fmt_int(len(uploaded)),
        source_db,
    )

    return uploaded


async def pending_documents(database, source_db):
    """
    Fast resume scanner.

    Loads the full UPLOADED id set ONCE, then streams the source
    collection and yields only documents that are not yet uploaded.

    Uses $natural order so the sequence is stable across runs.
    """
    source = source_collection(database)
    if source is None:
        return

    STATE["preload_progress"] = f"Loading UPLOADED ids for {source_db}…"
    uploaded = await _load_uploaded_set(source_db)
    STATE["preload_progress"] = None

    total = await source_count(source_db, database)
    scanned = 0
    yielded = 0

    cursor = source.find({}).sort("$natural", 1).batch_size(
        BACKUP_SCAN_BATCH
    )

    async for document in cursor:
        scanned += 1

        if scanned % 5000 == 0:
            STATE["preload_progress"] = (
                f"Scanning {source_db}: "
                f"{fmt_int(scanned)}/{fmt_int(total)} "
                f"(skipped {fmt_int(scanned - yielded)})"
            )

        file_id = source_file_id(document)

        if not file_id:
            yield document
            yielded += 1
            continue

        if file_id not in uploaded:
            yield document
            yielded += 1

    STATE["preload_progress"] = None

    logger.info(
        "[BACKUP] Scan complete for %s: scanned=%s yielded=%s",
        source_db,
        fmt_int(scanned),
        fmt_int(yielded),
    )


# ============================================================
# CRASH RECONCILIATION
# ============================================================

async def uploading_records(limit=2000):
    out = []
    colls = all_state_collections()
    if not colls:
        return []
    per = max(1, int(limit) // len(colls))
    for coll in colls:
        try:
            cursor = coll.find(
                {"status": "UPLOADING"}
            ).sort("updated_at", 1).limit(per)
            recs = await cursor.to_list(length=per)
            out.extend(recs)
        except Exception:
            pass
    return out[:int(limit)]


async def find_source_document(source_db, file_id):
    for name, database, _ in enabled_source_databases():
        if name != source_db:
            continue
        collection = source_collection(database)
        if collection is None:
            return None
        try:
            result = await collection.find_one({"_id": file_id})
            if result is not None:
                return result
            return await collection.find_one({"_id": str(file_id)})
        except Exception:
            return None
    return None


def message_has_token(message, token):
    if message is None:
        return False
    caption = getattr(message, "caption", None)
    text = getattr(message, "text", None)
    combined = f"{caption or ''}\n{text or ''}"
    return token in combined


async def reconcile_one(app, record):
    token = record.get("backup_token")
    if not token:
        return False

    channel = get_backup_channel_id()
    if channel is None:
        return False

    try:
        async for message in app.get_chat_history(
            channel,
            limit=BACKUP_RECONCILE_MESSAGES,
        ):
            if message_has_token(message, token):
                source_db = record.get("source_db")
                file_id   = record.get("file_id")

                document = await find_source_document(source_db, file_id)
                if document is None:
                    return False

                await mark_uploaded(source_db, document, message.id)
                STATE["last_message_id"] = message.id
                STATE["last_success"] = now_text()

                logger.info(
                    "[BACKUP][RECONCILE] %s/%s -> message %s",
                    source_db, file_id, message.id,
                )
                return True

    except FloodWait as exc:
        await asyncio.sleep(int(getattr(exc, "value", 30)) + 2)
    except Exception:
        logger.exception("Reconciliation failed")

    return False


async def reconcile_interrupted():
    if not backup_configured():
        return 0

    records = await uploading_records()
    if not records:
        return 0

    STATE["mode"] = "RECONCILING"
    STATE["message"] = f"Checking {len(records):,} interrupted uploads"

    recovered = 0
    app = STATE.get("_client")
    if app is None:
        return 0

    for record in records:
        if STATE["stop_requested"]:
            break
        if await reconcile_one(app, record):
            recovered += 1

    return recovered


# ============================================================
# BACKUP CAPTION
# ============================================================

def make_caption(source_db, document):
    token = backup_token(source_db, source_file_id(document))
    original = source_caption(document)

    return (
        f"{original}\n\n"
        f"🗄️ <b>DOWNTOWN VILLA BACKUP</b>\n"
        f"📚 <b>DATABASE:</b> {source_db}\n"
        f"🔐 <code>{token}</code>"
    )

# ============================================================
# TELEGRAM UPLOAD  (enhanced error handling + FloodWait)
# ============================================================

async def upload_one(app, source_db, document):
    file_id   = source_file_id(document)
    file_name = source_file_name(document)

    if not file_id:
        await mark_failed(source_db, document, "Missing file_id")
        return False

    STATE["current_file"]      = file_name
    STATE["current_file_id"]   = file_id
    STATE["current_file_size"] = source_file_size(document)
    STATE["message"]           = f"Uploading {file_name}"

    await mark_uploading(source_db, document)

    caption = make_caption(source_db, document)
    attempts = 0

    while attempts < BACKUP_MAX_RETRIES:
        attempts += 1

        if STATE["stop_requested"]:
            return False

        # Honour any active FloodWait
        while STATE["paused"]:
            await asyncio.sleep(1)
            if STATE["stop_requested"]:
                return False

        try:
            STATE["mode"] = "UPLOADING"

            sent = await app.send_cached_media(
                chat_id=get_backup_channel_id(),
                file_id=file_id,
                caption=caption,
            )

            message_id = getattr(sent, "id", None)
            if not message_id:
                raise RuntimeError("Telegram returned no message ID")

            await mark_uploaded(source_db, document, message_id)

            STATE["last_message_id"] = message_id
            STATE["last_success"]    = now_text()
            STATE["last_activity"]   = now_text()
            STATE["message"]         = f"Uploaded: {file_name}"

            logger.info(
                "[BACKUP][SUCCESS] %s | %s | message=%s",
                source_db, file_name, message_id,
            )
            return True

        except FloodWait as exc:
            wait = max(1, int(getattr(exc, "value", 30)))

            STATE["mode"] = "FLOOD_WAIT"
            STATE["message"] = f"🌊 Telegram FloodWait: {wait}s"
            STATE["flood_wait_until"] = time.time() + wait
            STATE["last_flood_wait"] = now_text()

            logger.warning("[BACKUP] FloodWait %ss", wait)

            # Sleep in small chunks so pause/stop work
            deadline = time.time() + wait + 2
            while time.time() < deadline:
                if STATE["stop_requested"]:
                    return False
                await asyncio.sleep(min(5, max(1, deadline - time.time())))

            STATE["flood_wait_until"] = None
            # Don't count FloodWait against retry attempts
            attempts -= 1
            continue

        except RPCError as exc:
            error = str(exc)
            STATE["last_error"] = error
            logger.error("[BACKUP][RPC] %s", error)

            if attempts >= BACKUP_MAX_RETRIES:
                await mark_failed(source_db, document, error)
                return False

            delay = BACKUP_RETRY_DELAY * min(attempts, 6)
            STATE["message"] = f"RPC retry in {delay}s (attempt {attempts})"
            await asyncio.sleep(delay)

        except asyncio.CancelledError:
            raise

        except (TimeoutError, asyncio.TimeoutError) as exc:
            error = f"Timeout: {exc}"
            STATE["last_error"] = error
            logger.error("[BACKUP][TIMEOUT] %s", error)

            if attempts >= BACKUP_MAX_RETRIES:
                await mark_failed(source_db, document, error)
                return False

            await asyncio.sleep(BACKUP_RETRY_DELAY * min(attempts, 6))

        except (ConnectionError, OSError) as exc:
            error = f"Connection: {exc}"
            STATE["last_error"] = error
            logger.error("[BACKUP][NET] %s", error)

            if attempts >= BACKUP_MAX_RETRIES:
                await mark_failed(source_db, document, error)
                return False

            await asyncio.sleep(BACKUP_RETRY_DELAY * min(attempts, 6))

        except Exception as exc:
            error = str(exc)
            STATE["last_error"] = error
            logger.error("[BACKUP][ERROR] %s", error)

            if attempts >= BACKUP_MAX_RETRIES:
                await mark_failed(source_db, document, error)
                return False

            await asyncio.sleep(BACKUP_RETRY_DELAY * min(attempts, 6))

    return False


# ============================================================
# SINGLE DATABASE PASS
# ============================================================

async def backup_database(app, source_db, database, number):
    source = source_collection(database)
    if source is None:
        STATE["last_error"] = f"{source_db} unavailable"
        return False

    total            = await source.count_documents({})
    already_uploaded = await count_state(source_db, "UPLOADED")

    STATE["current_db"]          = source_db
    STATE["current_db_number"]   = number
    STATE["current_source_total"] = total
    STATE["current_source_index"] = 0
    STATE["current_uploaded"]    = 0
    STATE["current_failed"]      = 0
    STATE["current_skipped"]     = already_uploaded
    STATE["message"]             = f"{source_db}: {fmt_int(total)} source files"

    logger.info(
        "[BACKUP] START %s total=%s already_uploaded=%s",
        source_db, total, already_uploaded,
    )

    if total == 0:
        return True

    start_time = time.monotonic()

    async for document in pending_documents(database, source_db):
        if STATE["stop_requested"]:
            STATE["mode"] = "STOPPING"
            return False

        while STATE["paused"]:
            STATE["mode"] = "PAUSED"
            STATE["message"] = "⏸️ Backup paused"
            await asyncio.sleep(1)
            if STATE["stop_requested"]:
                return False

        STATE["mode"] = "UPLOADING"
        STATE["current_source_index"] += 1

        file_id = source_file_id(document)
        status  = await get_status(source_db, file_id)

        if status == "UPLOADED":
            STATE["current_skipped"] += 1
            STATE["total_skipped"]   += 1
            continue

        success = await upload_one(app, source_db, document)

        if success:
            STATE["current_uploaded"] += 1
            STATE["total_uploaded"]   += 1
        else:
            STATE["current_failed"] += 1
            STATE["total_failed"]   += 1

        processed = (
            STATE["current_uploaded"]
            + STATE["current_failed"]
            + STATE["current_skipped"]
        )

        elapsed = max(0.001, time.monotonic() - start_time)
        STATE["speed"] = processed / elapsed

        remaining = max(0, total - already_uploaded - processed)
        if STATE["speed"] > 0:
            STATE["eta"] = remaining / STATE["speed"]

        STATE["last_activity"] = now_text()

        if BACKUP_UPLOAD_DELAY > 0:
            await asyncio.sleep(BACKUP_UPLOAD_DELAY)

    return True


# ============================================================
# FULL ORDERED BACKUP
# ============================================================

async def run_backup(app, retry_failed=False):
    global WORKER_TASK

    async with STATE_LOCK:
        if STATE["running"]:
            return False

        STATE["running"]         = True
        STATE["paused"]          = False
        STATE["stop_requested"]  = False
        STATE["mode"]            = "STARTING"
        STATE["started_at"]      = now()
        STATE["finished_at"]     = None
        STATE["last_error"]      = None
        STATE["total_uploaded"]  = 0
        STATE["total_failed"]    = 0
        STATE["total_skipped"]   = 0
        STATE["speed"]           = 0
        STATE["eta"]             = None
        STATE["run_id"]          = None
        STATE["_client"]         = app
        STATE["preload_progress"] = None

    if not backup_configured():
        STATE["mode"] = "ERROR"
        STATE["last_error"] = "BACKUP_CHANNEL_ID is missing"
        STATE["running"] = False
        return False

    try:
        await ensure_indexes()

        if retry_failed:
            await reset_failed()

        await reconcile_interrupted()

        STATE["mode"] = "RUNNING"
        STATE["run_id"] = await create_run()

        logger.info("================================================")
        logger.info("[BACKUP] RESUMABLE BACKUP STARTED")
        logger.info("[BACKUP] ORDER: Media -> Media2 -> Media3")
        logger.info("================================================")

        success = True

        for source_db, database, number in enabled_source_databases():
            if STATE["stop_requested"]:
                success = False
                break

            success = await backup_database(app, source_db, database, number)
            if not success:
                break

        if STATE["stop_requested"]:
            STATE["mode"] = "STOPPED"
            await finish_run("STOPPED")
            return False

        if success:
            STATE["mode"] = "COMPLETED"
            STATE["message"] = "Media → Media2 → Media3 completed"
            await finish_run("COMPLETED")
            return True

        STATE["mode"] = "FAILED"
        await finish_run("FAILED")
        return False

    except asyncio.CancelledError:
        STATE["mode"] = "CANCELLED"
        try:
            await finish_run("CANCELLED")
        except Exception:
            pass
        raise

    except Exception as exc:
        STATE["mode"] = "ERROR"
        STATE["last_error"] = str(exc)
        logger.error(
            "[BACKUP] Worker crashed:\n%s",
            traceback.format_exc(),
        )
        try:
            await finish_run("ERROR")
        except Exception:
            pass
        return False

    finally:
        STATE["finished_at"]    = now()
        STATE["running"]        = False
        STATE["paused"]         = False
        STATE["stop_requested"] = False
        STATE["preload_progress"] = None
        STATE.pop("_client", None)


# ============================================================
# NEW FILE WATCHER
# ============================================================

async def watcher_loop(app):
    while True:
        try:
            STATE["last_scan"] = now_text()

            if not backup_configured():
                STATE["mode"] = "NOT_CONFIGURED"
                await asyncio.sleep(BACKUP_WATCH_INTERVAL)
                continue

            if not STATE["running"]:
                pending = await total_pending()

                if pending > 0:
                    await start_backup(app)
                else:
                    STATE["mode"] = "WATCHING"
                    STATE["message"] = "👀 Watching for newly indexed files"

            await asyncio.sleep(BACKUP_WATCH_INTERVAL)

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            STATE["last_error"] = str(exc)
            logger.error("[BACKUP] Watcher error: %s", exc)
            await asyncio.sleep(BACKUP_WATCH_INTERVAL)


def ensure_watcher(app):
    global WATCHER_TASK
    if WATCHER_TASK is None or WATCHER_TASK.done():
        WATCHER_TASK = asyncio.create_task(watcher_loop(app))


# ============================================================
# CONTROL
# ============================================================

async def start_backup(app):
    global WORKER_TASK
    if WORKER_TASK is None or WORKER_TASK.done():
        WORKER_TASK = asyncio.create_task(
            run_backup(app, retry_failed=False)
        )
        return True
    return False


async def retry_failed_files(app):
    global WORKER_TASK
    if WORKER_TASK is None or WORKER_TASK.done():
        WORKER_TASK = asyncio.create_task(
            run_backup(app, retry_failed=True)
        )
        return True
    return False


def pause_backup():
    if STATE["running"]:
        STATE["paused"] = True
        STATE["message"] = "⏸️ Pause requested"
        return True
    return False


def resume_backup():
    if STATE["paused"]:
        STATE["paused"] = False
        STATE["mode"] = "RUNNING"
        STATE["message"] = "▶️ Backup resumed"
        return True
    return False


def stop_backup():
    if STATE["running"]:
        STATE["stop_requested"] = True
        STATE["mode"] = "STOPPING"
        STATE["message"] = "⏹️ Stopping safely after current operation"
        return True
    return False


# ============================================================
# LIVE SNAPSHOT
# ============================================================

async def live_snapshot():
    databases = await database_snapshot()

    total_source = sum(item["total"]     for item in databases.values())
    uploaded     = sum(item["uploaded"]  for item in databases.values())
    pending      = sum(item["pending"]   for item in databases.values())
    failed       = sum(item["failed"]    for item in databases.values())
    uploading    = sum(item["uploading"] for item in databases.values())

    return {
        "databases": databases,
        "total_source": total_source,
        "uploaded": uploaded,
        "pending": pending,
        "failed": failed,
        "uploading": uploading,
        "state": dict(STATE),
    }


# ============================================================
# STATUS PAGE  — colorful, rich, emoji-decorated
# ============================================================

async def build_status_page():
    snapshot = await live_snapshot()
    state    = snapshot["state"]

    total    = snapshot["total_source"]
    uploaded = snapshot["uploaded"]
    pending  = snapshot["pending"]
    failed   = snapshot["failed"]

    overall_pct = (uploaded / total * 100) if total else 0

    runtime = 0
    if state.get("started_at"):
        runtime = (now() - state["started_at"]).total_seconds()

    mode  = state.get("mode") or "IDLE"
    speed = float(state.get("speed") or 0)

    # ── Stable planning speed (fallback to session avg) ──
    planning_speed = speed
    if planning_speed <= 0:
        try:
            total_done = int(state.get("total_uploaded") or 0) + \
                         int(state.get("total_skipped") or 0)
            if runtime > 0 and total_done > 0:
                planning_speed = total_done / runtime
        except Exception:
            planning_speed = 0.0

    # ── Cumulative per-DB ETA ────────────────────────────
    db_order = ["Media", "Media2", "Media3"]
    db_data  = snapshot["databases"]

    cumulative = 0.0
    db_plan    = {}

    for name in db_order:
        item = db_data.get(name)
        if not item:
            continue

        eta_db = _eta_seconds(item["pending"], planning_speed) or 0
        cumulative += eta_db

        db_plan[name] = {
            "eta":        eta_db,
            "cumulative": int(cumulative),
        }

    total_eta_sec = int(cumulative) if cumulative > 0 else None
    total_eta_txt = fmt_duration(total_eta_sec) if total_eta_sec else "—"
    full_done_txt = _wall_time(total_eta_sec)

    # ── FloodWait line ───────────────────────────────────
    flood_line = ""
    flood_until = state.get("flood_wait_until")
    if flood_until and flood_until > time.time():
        remaining = int(flood_until - time.time())
        flood_line = f"🌊 <b>FloodWait</b> · {fmt_duration(remaining)}"

    preload = state.get("preload_progress")
    preload_line = f"\n🔎 <i>{html_escape(preload)}</i>" if preload else ""

    # ── Mode banner ──────────────────────────────────────
    mode_banner = {
        "UPLOADING":    "🟡 ᴜᴘʟᴏᴀᴅɪɴɢ · ʟɪᴠᴇ",
        "RUNNING":      "🟢 ʀᴜɴɴɪɴɢ",
        "WATCHING":     "👀 ᴡᴀᴛᴄʜɪɴɢ",
        "PAUSED":       "⏸ ᴘᴀᴜsᴇᴅ",
        "STOPPING":     "⏹ sᴛᴏᴘᴘɪɴɢ",
        "STOPPED":      "🔴 sᴛᴏᴘᴘᴇᴅ",
        "COMPLETED":    "✅ ᴄᴏᴍᴘʟᴇᴛᴇᴅ",
        "FLOOD_WAIT":   "🌊 ꜰʟᴏᴏᴅᴡᴀɪᴛ",
        "RECONCILING":  "♻️ ʀᴇᴄᴏɴᴄɪʟɪɴɢ",
        "IDLE":         "💤 ɪᴅʟᴇ",
    }.get(mode, f"{status_icon(mode)} {html_escape(mode)}")

    # ── Header ───────────────────────────────────────────
    text = (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "   💜  <b>D O W N T O W N</b>\n"
        "      <b>V I L L A</b>\n"
        "   ⚡ <b>BACKUP CORE</b> ⚡\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"

        f"{mode_banner}\n"
        f"{preload_line}"
        f"\n"
    )

    # ── Global ───────────────────────────────────────────
    text += (
        "╭─〔 🌐 <b>GLOBAL</b> 〕────────╮\n"
        f"│ 📚 <code>{fmt_int(total)}</code> ꜰɪʟᴇs\n"
        f"│ 💾 <code>{fmt_int(uploaded)}</code> ᴅᴏɴᴇ\n"
        f"│ ⏳ <code>{fmt_int(pending)}</code> ʟᴇꜰᴛ\n"
        f"│ ❌ <code>{fmt_int(failed)}</code> ꜰᴀɪʟᴇᴅ\n"
        f"│\n"
        f"│ {_bar_text(overall_pct, 12)} <b>{overall_pct:.1f}%</b>\n"
        f"╰───────────────────────╯\n\n"
    )

    # ── Per-DB sections ─────────────────────────────────
    db_colors = {"Media": "💜", "Media2": "💙", "Media3": "💚"}
    db_labels = {"Media": "MEDIA", "Media2": "MEDIA 2", "Media3": "MEDIA 3"}

    for name in db_order:
        item = db_data.get(name)
        if not item:
            continue

        db_total = item["total"]
        db_up    = item["uploaded"]
        db_pend  = item["pending"]
        db_fail  = item["failed"]

        if db_total == 0:
            text += (
                f"╭─〔 {db_colors[name]} <b>{db_labels[name]}</b> 〕─────────╮\n"
                f"│ <i>ᴇᴍᴘᴛʏ</i>\n"
                f"╰───────────────────────╯\n\n"
            )
            continue

        db_pct  = db_up / db_total * 100
        plan    = db_plan.get(name, {})
        eta_s   = plan.get("eta", 0)
        eta_str = fmt_duration(eta_s) if eta_s else "—"
        done_wall = _wall_time(plan.get("cumulative"))

        text += (
            f"╭─〔 {db_colors[name]} <b>{db_labels[name]}</b> 〕─────────╮\n"
            f"│ 📦 <code>{fmt_int(db_total)}</code> ᴛᴏᴛᴀʟ\n"
            f"│ ✅ <code>{fmt_int(db_up)}</code> ᴅᴏɴᴇ\n"
            f"│ ⏳ <code>{fmt_int(db_pend)}</code> ʟᴇꜰᴛ\n"
        )

        if db_fail:
            text += f"│ ❌ <code>{fmt_int(db_fail)}</code> ꜰᴀɪʟᴇᴅ\n"

        text += (
            f"│\n"
            f"│ {_bar_text(db_pct, 12)} <b>{db_pct:.1f}%</b>\n"
            f"│\n"
            f"│ ⏱ ᴇsᴛɪᴍᴀᴛᴇ\n"
            f"│     ✦ <b>{eta_str}</b>\n"
            f"│ 🏁 <code>{done_wall}</code>\n"
            f"╰───────────────────────╯\n\n"
        )

    # ── Final projection ─────────────────────────────────
    text += (
        "╭─〔 🏆 <b>FINAL</b> 〕──────────╮\n"
        "│\n"
    )

    for name in db_order:
        item = db_data.get(name)
        if not item or item["total"] == 0:
            continue
        icon = "⏳" if item["pending"] > 0 else "✅"
        text += f"│ {db_colors[name]} {db_labels[name]:<9} {icon}\n"

    text += (
        f"│\n"
        f"│ 🚀 ꜰᴜʟʟ ʙᴀᴄᴋᴜᴘ\n"
        f"│    <b>{full_done_txt}</b>\n"
        f"│\n"
        f"│ ⏱ <b>{total_eta_txt}</b>\n"
        f"╰───────────────────────╯\n\n"
    )

    # ── Current file ─────────────────────────────────────
    cur_file = short(state.get("current_file"), 40)
    cur_size = fmt_bytes(state.get("current_file_size"))
    cur_id   = state.get("last_message_id") or "—"

    text += (
        "╭─〔 🎬 <b>NOW PROCESSING</b> 〕─╮\n"
        "│\n"
        f"│ 🎞️ <i>{html_escape(cur_file)}</i>\n"
        "│\n"
        f"│ 💾 <b>{cur_size}</b>\n"
        f"│ 🆔 <code>{cur_id}</code>\n"
        "│\n"
        f"│ {mode_banner}\n"
        "╰───────────────────────╯\n\n"
    )

    # ── Core status ──────────────────────────────────────
    chan_ok   = "🟢" if get_backup_channel_id() else "🔴"
    worker_ok = "🟢" if STATE.get("running") else "🟡"
    db_ok     = "🟢" if all_state_collections() else "🔴"
    fail_ok   = "🟢" if failed == 0 else "🔴"

    text += (
        "╭─〔 🛰️ <b>CORE</b> 〕───────────╮\n"
        f"│ {db_ok} ᴅᴀᴛᴀʙᴀsᴇ   "
        f"{'ONLINE' if all_state_collections() else 'OFFLINE'}\n"
        f"│ {worker_ok} ᴡᴏʀᴋᴇʀ     "
        f"{'ACTIVE' if STATE.get('running') else 'IDLE'}\n"
        f"│ {chan_ok} ᴛᴇʟᴇɢʀᴀᴍ   "
        f"{'CONNECTED' if get_backup_channel_id() else 'NOT SET'}\n"
        f"│ {fail_ok} ꜰᴀɪʟᴇᴅ     {fmt_int(failed)}\n"
        "╰───────────────────────╯"
    )

    if flood_line:
        text += f"\n\n{flood_line}"

    if state.get("last_error"):
        text += (
            f"\n\n⚠️ <b>LAST ERROR</b>\n"
            f"<code>{short(state.get('last_error'), 220)}</code>"
        )

    return text[:4000]
# ============================================================
# BUTTONS
# ============================================================

def backup_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 LIVE",         callback_data="dtv_backup_live"),
            InlineKeyboardButton("▶️ START",        callback_data="dtv_backup_start"),
        ],
        [
            InlineKeyboardButton("⏸️ PAUSE",        callback_data="dtv_backup_pause"),
            InlineKeyboardButton("▶️ RESUME",       callback_data="dtv_backup_resume"),
        ],
        [
            InlineKeyboardButton("⏹️ STOP",         callback_data="dtv_backup_stop"),
            InlineKeyboardButton("🔁 RETRY FAILED", callback_data="dtv_backup_retry"),
        ],
        [
            InlineKeyboardButton("❌ FAILURES",     callback_data="dtv_backup_failures"),
            InlineKeyboardButton("🧾 HISTORY",      callback_data="dtv_backup_history"),
        ],
        [
            InlineKeyboardButton("♻️ RECONCILE",    callback_data="dtv_backup_reconcile"),
            InlineKeyboardButton("❌ CLOSE",        callback_data="dtv_backup_close"),
        ],
    ])


# ============================================================
# SAFE EDIT  (kept for compatibility — silently ignores stale panels)
# ============================================================

async def safe_edit(message, text, keyboard=None):
    try:
        await message.edit_text(
            text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return True

    except RPCError as exc:
        if "MESSAGE_NOT_MODIFIED" in str(exc).upper():
            return True
        logger.debug("Backup panel edit failed: %s", exc)

    except Exception as exc:
        logger.debug("Backup panel edit error: %s", exc)

    return False


# ============================================================
# LIVE PANEL UPDATER  (self-healing)
# ============================================================

async def live_panel_loop(client, message, page="live"):
    last_text = None
    current_message = message

    while True:
        try:
            if page == "live":
                text = await build_status_page()
            elif page == "history":
                text = await build_history_page()
            elif page == "failures":
                text = await build_failure_page()
            else:
                text = await build_status_page()

            if text != last_text:
                success, invalid = await _edit_panel_message(
                    current_message,
                    text,
                    backup_keyboard(),
                )

                if invalid:
                    chat_id = None
                    stale_id = None

                    try:
                        chat_id = current_message.chat.id
                    except Exception:
                        pass

                    try:
                        stale_id = current_message.id
                    except Exception:
                        pass

                    if chat_id is None:
                        ref = await load_panel_ref()
                        if ref:
                            chat_id = ref.get("chat_id")

                    await mark_panel_ref_stale()

                    new_message = await recreate_panel(client, chat_id, page)

                    if new_message is None:
                        logger.warning(
                            "[BACKUP] Panel recreation failed; "
                            "stopping updater for this panel"
                        )
                        break

                    if stale_id is not None:
                        ACTIVE_PANELS.pop(stale_id, None)

                    record = ACTIVE_PANELS.get(new_message.id)
                    if record is None:
                        ACTIVE_PANELS[new_message.id] = {
                            "page": page,
                            "task": asyncio.current_task(),
                            "closed": False,
                            "created": time.time(),
                        }
                    else:
                        record["task"] = asyncio.current_task()
                        record["closed"] = False
                        record["page"] = page

                    current_message = new_message
                    last_text = text

                    await asyncio.sleep(2)
                    continue

                if success:
                    last_text = text

            await asyncio.sleep(2)

            record = ACTIVE_PANELS.get(current_message.id)
            if not record:
                break
            if record.get("closed"):
                break
            if record.get("page") != page:
                break

        except asyncio.CancelledError:
            break

        except Exception:
            logger.exception("Backup live panel error")
            await asyncio.sleep(3)


def stop_panel(message_id):
    record = ACTIVE_PANELS.get(message_id)
    if record:
        record["closed"] = True
        task = record.get("task")
        if task:
            try:
                task.cancel()
            except Exception:
                pass


def open_panel(message, page="live"):
    stop_panel(message.id)

    task = asyncio.create_task(
        live_panel_loop(
            STATE.get("_client"),
            message,
            page,
        )
    )

    ACTIVE_PANELS[message.id] = {
        "page": page,
        "task": task,
        "closed": False,
        "created": time.time(),
    }

    try:
        STATE["last_message_id"] = int(message.id)
    except Exception:
        pass

    try:
        chat_id    = int(message.chat.id)
        message_id = int(message.id)

        async def _persist():
            await save_panel_ref(chat_id, message_id, status="active")

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_persist())
        except RuntimeError:
            pass
    except Exception:
        pass

    return task


# ============================================================
# /backup — THE ONLY COMMAND
# ============================================================

@Client.on_message(filters.command("backup"))
async def backup_command(client, message):
    user = message.from_user
    if user is None or not is_admin(user.id):
        return

    STATE["_client"] = client

    await ensure_indexes()
    ensure_watcher(client)

    text = await build_status_page()

    sent = await message.reply_text(
        text,
        reply_markup=backup_keyboard(),
        disable_web_page_preview=True,
    )

    open_panel(sent, "live")

    try:
        await save_panel_ref(
            int(sent.chat.id),
            int(sent.id),
            status="active",
        )
    except Exception:
        pass


# ============================================================
# CALLBACKS
# ============================================================

@Client.on_callback_query(filters.regex(r"^dtv_backup_"))
async def backup_callback(client, query):
    user = query.from_user
    if user is None or not is_admin(user.id):
        await query.answer("❌ Access denied", show_alert=True)
        return

    STATE["_client"] = client
    data = str(query.data)

    try:
        if data == "dtv_backup_live":
            await query.answer("🔄 Live monitoring")

            text = await build_status_page()
            success, invalid = await _edit_panel_message(
                query.message, text, backup_keyboard()
            )

            if invalid:
                cid = None
                try:
                    cid = query.message.chat.id
                except Exception:
                    pass
                if cid is None:
                    ref = await load_panel_ref()
                    if ref:
                        cid = ref.get("chat_id")

                await mark_panel_ref_stale()
                new_msg = await recreate_panel(client, cid, "live")
                if new_msg is not None:
                    open_panel(new_msg, "live")
                return

            open_panel(query.message, "live")
            return

        if data == "dtv_backup_start":
            started = await start_backup(client)
            ensure_watcher(client)
            await query.answer(
                "▶️ Backup started" if started
                else "🟡 Backup already running"
            )
            return

        if data == "dtv_backup_pause":
            changed = pause_backup()
            await query.answer(
                "⏸️ Backup paused" if changed
                else "🟡 Nothing is running"
            )
            return

        if data == "dtv_backup_resume":
            changed = resume_backup()
            await query.answer(
                "▶️ Backup resumed" if changed
                else "🟡 Backup is not paused"
            )
            return

        if data == "dtv_backup_stop":
            changed = stop_backup()
            await query.answer(
                "⏹️ Stop requested" if changed
                else "🟡 Nothing is running"
            )
            return

        if data == "dtv_backup_retry":
            started = await retry_failed_files(client)
            await query.answer(
                "🔁 Failed files retry started" if started
                else "🟡 Backup already running"
            )
            return

        if data == "dtv_backup_reconcile":
            await query.answer("♻️ Reconciliation started")
            asyncio.create_task(reconcile_interrupted())
            return

        if data == "dtv_backup_history":
            text = await build_history_page()
            success, invalid = await _edit_panel_message(
                query.message, text, backup_keyboard()
            )

            if invalid:
                cid = None
                try:
                    cid = query.message.chat.id
                except Exception:
                    pass
                if cid is None:
                    ref = await load_panel_ref()
                    if ref:
                        cid = ref.get("chat_id")

                await mark_panel_ref_stale()
                new_msg = await recreate_panel(client, cid, "history")
                if new_msg is not None:
                    open_panel(new_msg, "history")
                await query.answer("🧾 History (new panel)")
                return

            open_panel(query.message, "history")
            await query.answer("🧾 History")
            return

        if data == "dtv_backup_failures":
            text = await build_failure_page()
            success, invalid = await _edit_panel_message(
                query.message, text, backup_keyboard()
            )

            if invalid:
                cid = None
                try:
                    cid = query.message.chat.id
                except Exception:
                    pass
                if cid is None:
                    ref = await load_panel_ref()
                    if ref:
                        cid = ref.get("chat_id")

                await mark_panel_ref_stale()
                new_msg = await recreate_panel(client, cid, "failures")
                if new_msg is not None:
                    open_panel(new_msg, "failures")
                await query.answer("❌ Failed files (new panel)")
                return

            open_panel(query.message, "failures")
            await query.answer("❌ Failed files")
            return

        if data == "dtv_backup_close":
            stop_panel(query.message.id)
            ACTIVE_PANELS.pop(query.message.id, None)
            await query.message.delete()
            return

    except Exception as exc:
        logger.exception("Backup callback error")
        try:
            await query.answer(f"❌ {short(exc, 150)}", show_alert=True)
        except Exception:
            pass

# ============================================================
# AUTO START / STARTUP HOOK
# ============================================================
#
# Call this ONCE after the Pyrogram client is fully started:
#
#     from plugins.backup import initialize_backup
#     await initialize_backup(app)
#
# Without this hook, /backup still works, but the panel loop and
# the watcher only start on first /backup. With it, the panel is
# restored from MongoDB and the watcher resumes on every restart.
#
# ============================================================

async def initialize_backup(client):
    STATE["_client"] = client

    await ensure_indexes()

    # Resume the persisted panel reference (survives restarts).
    try:
        await restore_panel_ref(client)
    except Exception:
        logger.exception("[BACKUP] Panel restore failed")

    ensure_watcher(client)

    if not BACKUP_AUTO_START:
        return

    if not STATE["running"]:
        await start_backup(client)


# ============================================================
# FULL BACKUP RESET — /reset_backup  +  RESET button
# ============================================================

_RESETTABLE_STATE_KEYS = (
    "started_at",
    "finished_at",
    "current_db",
    "current_db_number",
    "current_file",
    "current_file_id",
    "current_file_size",
    "current_source_index",
    "current_source_total",
    "current_uploaded",
    "current_failed",
    "current_skipped",
    "total_uploaded",
    "total_failed",
    "total_skipped",
    "speed",
    "eta",
    "last_error",
    "last_activity",
    "last_success",
    "last_message_id",
    "last_cycle",
    "last_scan",
    "run_id",
    "message",
    "flood_wait_until",
    "last_flood_wait",
    "preload_progress",
)


async def reset_all_backup_state():
    result = {
        "state_total": 0,
        "runs_total": 0,
        "per_shard": [],
        "errors": [],
    }

    state_colls = all_state_collections()
    if not state_colls:
        result["errors"].append("No backup shards available")
        return result

    for idx, coll in enumerate(state_colls):
        shard_info = {"shard": idx + 1, "state": 0, "runs": 0, "error": None}

        try:
            r = await coll.delete_many({})
            shard_info["state"] = r.deleted_count
            result["state_total"] += r.deleted_count
        except Exception as e:
            shard_info["error"] = f"state: {e}"
            result["errors"].append(f"Shard {idx + 1} state: {e}")

        if idx == 0:
            try:
                rc = run_collection()
                if rc is not None:
                    r = await rc.delete_many({})
                    shard_info["runs"] = r.deleted_count
                    result["runs_total"] += r.deleted_count
            except Exception as e:
                shard_info["error"] = (shard_info["error"] or "") + f" runs: {e}"
                result["errors"].append(f"Shard {idx + 1} runs: {e}")

        result["per_shard"].append(shard_info)

    for key in _RESETTABLE_STATE_KEYS:
        if key in STATE:
            STATE[key] = None if key in (
                "started_at", "finished_at", "last_error",
                "last_activity", "last_success",
                "last_message_id", "run_id",
                "current_db", "current_file", "current_file_id",
                "flood_wait_until", "last_flood_wait",
                "preload_progress",
            ) else (
                0 if isinstance(STATE.get(key), int) else
                (0.0 if isinstance(STATE.get(key), float) else "")
            )

    STATE["mode"] = "IDLE"
    STATE["message"] = "✅ Reset complete — ready for fresh start"
    STATE["speed"] = 0.0
    STATE["eta"] = None
    STATE["current_file_size"] = 0
    STATE["run_id"] = None

    return result


def _reset_confirm_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🗑️ YES, DELETE EVERYTHING",
                callback_data="dtv_backup_reset_confirm",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ CANCEL",
                callback_data="dtv_backup_reset_cancel",
            ),
        ],
    ])


def _build_reset_confirm_text():
    colls = all_state_collections()
    shard_count = len(colls)

    return (
        "⚠️ <b>FULL BACKUP RESET</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "This will <b>PERMANENTLY DELETE</b>:\n\n"
        f"  🗄️ Backup state — <b>ALL {shard_count} shard(s)</b>\n"
        "  📋 Run history\n"
        "  🔢 All progress counters\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ <b>What happens next:</b>\n"
        "  • Every file will be treated as NEW\n"
        "  • The next backup run will re-upload from scratch\n"
        "  • Files already in your backup channel will be <b>duplicated</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>This CANNOT be undone.</b>\n\n"
        "Type or click to proceed 👇"
    )


@Client.on_message(filters.command("reset_backup"))
async def reset_backup_command(client, message):
    user = message.from_user
    if user is None or not is_admin(user.id):
        return

    STATE["_client"] = client

    text = _build_reset_confirm_text()

    await message.reply_text(
        text,
        reply_markup=_reset_confirm_keyboard(),
        disable_web_page_preview=True,
    )


def backup_reset_button():
    return [
        InlineKeyboardButton(
            "🔄 FULL RESET",
            callback_data="dtv_backup_reset_prompt",
        ),
    ]


@Client.on_callback_query(filters.regex(r"^dtv_backup_reset_prompt$"))
async def cb_reset_prompt(client, query):
    user = query.from_user
    if user is None or not is_admin(user.id):
        await query.answer("❌ Access denied", show_alert=True)
        return

    STATE["_client"] = client

    text = _build_reset_confirm_text()

    await _edit_panel_message(
        query.message,
        text,
        _reset_confirm_keyboard(),
    )

    await query.answer("⚠️ Confirm to reset")


@Client.on_callback_query(filters.regex(r"^dtv_backup_reset_confirm$"))
async def cb_reset_confirm(client, query):
    user = query.from_user
    if user is None or not is_admin(user.id):
        await query.answer("❌ Access denied", show_alert=True)
        return

    STATE["_client"] = client

    await query.answer("🗑️ Deleting...", show_alert=False)

    await _edit_panel_message(
        query.message,
        (
            "🗑️ <b>RESETTING BACKUP STATE...</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⏳ This may take a few seconds...\n"
        ),
    )

    try:
        result = await reset_all_backup_state()

        lines = [
            "✅ <b>BACKUP RESET COMPLETE</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"🗄️ State docs deleted: <b>{fmt_int(result['state_total'])}</b>",
            f"📋 Run history deleted: <b>{fmt_int(result['runs_total'])}</b>",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "📊 <b>Per shard:</b>",
            "",
        ]

        for s in result["per_shard"]:
            icon = "🟢" if not s["error"] else "🔴"
            lines.append(
                f"{icon} Shard #{s['shard']}: "
                f"<b>{fmt_int(s['state'])}</b> state, "
                f"<b>{fmt_int(s['runs'])}</b> runs"
            )
            if s["error"]:
                lines.append(
                    f"     ⚠️ <code>{html_escape(s['error'])[:120]}</code>"
                )

        if result["errors"]:
            lines += [
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "⚠️ <b>Some errors occurred</b> — check logs.",
            ]

        lines += [
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🎉 <b>Next backup will start fresh.</b>",
            "   Every file is now treated as NEW.",
            "",
            f"🕒 <code>{now_text()}</code>",
        ]

        final_text = "\n".join(lines)[:4000]

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 REFRESH PANEL",
                    callback_data="dtv_backup_live",
                ),
                InlineKeyboardButton(
                    "▶️ START NOW",
                    callback_data="dtv_backup_start",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ CLOSE",
                    callback_data="dtv_backup_close",
                ),
            ],
        ])

        await _edit_panel_message(query.message, final_text, kb)

        logger.info(
            "[BACKUP][RESET] State deleted=%s runs deleted=%s",
            result["state_total"],
            result["runs_total"],
        )

    except Exception as exc:
        logger.exception("Backup reset failed")
        await _edit_panel_message(
            query.message,
            (
                "❌ <b>RESET FAILED</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<code>{html_escape(str(exc))[:400]}</code>\n\n"
                "Check logs for details."
            ),
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "◀️ BACK",
                    callback_data="dtv_backup_live",
                ),
            ]]),
        )


@Client.on_callback_query(filters.regex(r"^dtv_backup_reset_cancel$"))
async def cb_reset_cancel(client, query):
    user = query.from_user
    if user is None or not is_admin(user.id):
        await query.answer("❌ Access denied", show_alert=True)
        return

    await query.answer("❌ Cancelled")

    STATE["_client"] = client

    text = await build_status_page()

    await _edit_panel_message(
        query.message,
        text,
        backup_keyboard(),
    )

    open_panel(query.message, "live")


logger.info("[BACKUP] Reset module loaded — /reset_backup")



# ============================================================
# END OF FILE
# ============================================================
