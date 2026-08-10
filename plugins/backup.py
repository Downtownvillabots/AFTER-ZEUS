# ============================================================
# DowntownVilla - ULTIMATE BACKUP SYSTEM
# ============================================================
#
# Features:
#
#   • Backup Media
#   • Backup Media2
#   • Backup Media3
#   • Uses BACKUP_CHANNEL_ID from Render Environment
#   • Resume after restart
#   • Separate backup tracking collection
#   • Does NOT modify Media / Media2 / Media3 documents
#   • Automatically skips already backed-up files
#   • Continuously watches for newly indexed files
#   • FloodWait handling
#   • Telegram RPC error handling
#   • Failed files can be retried
#   • Live /backup_status
#   • /backup_stats
#   • /backup_stop
#   • /backup command
#   • Uses REAL Pyrogram Client for Telegram
#   • NEVER uses MongoDB client for Telegram
#
# IMPORTANT:
#
# No changes are required in database/ia_filterdb.py
#
# ============================================================


import os
import asyncio
import logging
import html
from datetime import datetime
from typing import Optional, List, Tuple

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
    Media,
    Media2,
    Media3,
    db,
    db2,
    db3,
)

from info import (
    COLLECTION_NAME,
)


# ============================================================
# LOGGER
# ============================================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ============================================================
# ADMINS
# ============================================================

try:
    from info import ADMINS
except Exception:
    ADMINS = []


# ============================================================
# ENVIRONMENT CONFIGURATION
# ============================================================

BACKUP_CHANNEL_ID_RAW = os.getenv(
    "BACKUP_CHANNEL_ID",
    ""
).strip()


BACKUP_AUTO_START = os.getenv(
    "BACKUP_AUTO_START",
    "true"
).lower() in (
    "true",
    "1",
    "yes",
    "on",
)


BACKUP_CHECK_INTERVAL = int(
    os.getenv(
        "BACKUP_CHECK_INTERVAL",
        "30",
    )
)


BACKUP_BATCH_SIZE = int(
    os.getenv(
        "BACKUP_BATCH_SIZE",
        "20",
    )
)


BACKUP_UPLOAD_DELAY = float(
    os.getenv(
        "BACKUP_UPLOAD_DELAY",
        "1",
    )
)


BACKUP_RETRY_DELAY = int(
    os.getenv(
        "BACKUP_RETRY_DELAY",
        "30",
    )
)


# ============================================================
# BACKUP CHANNEL
# ============================================================

def get_backup_channel_id():
    """
    Convert BACKUP_CHANNEL_ID from Render environment
    variable into integer.
    """

    if not BACKUP_CHANNEL_ID_RAW:
        return None

    try:
        return int(BACKUP_CHANNEL_ID_RAW)

    except Exception:
        logger.error(
            "[BACKUP] Invalid BACKUP_CHANNEL_ID: %s",
            BACKUP_CHANNEL_ID_RAW,
        )

        return None


# ============================================================
# BACKUP TRACKING DATABASE
#
# Completely separate from Media / Media2 / Media3.
#
# Nothing inside the original media documents is changed.
# ============================================================

backup_collection = db[
    "downtownvilla_backup_status"
]


# ============================================================
# GLOBAL STATE
# ============================================================

backup_task = None

backup_running = False

backup_started_at = None

backup_finished_at = None

backup_current_file = None

backup_current_db = None

backup_last_error = None

backup_last_activity = None

backup_total_uploaded = 0

backup_total_skipped = 0

backup_total_failed = 0

backup_total_processed = 0

backup_lock = asyncio.Lock()

pyrogram_app = None


# ============================================================
# DATABASE DEFINITIONS
# ============================================================

DATABASES = [
    (
        "Media",
        db,
        Media,
    ),
    (
        "Media2",
        db2,
        Media2,
    ),
    (
        "Media3",
        db3,
        Media3,
    ),
]


# ============================================================
# TIME HELPERS
# ============================================================

def utc_now():
    return datetime.utcnow()


def utc_text():
    return utc_now().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


# ============================================================
# FORMAT HELPERS
# ============================================================

def format_bytes(size):
    if not size:
        return "0 B"

    try:
        size = float(size)
    except Exception:
        return "0 B"

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB",
        "PB",
    ]

    for unit in units:

        if size < 1024:
            return f"{size:.2f} {unit}"

        size /= 1024

    return f"{size:.2f} EB"


def format_duration(seconds):

    if not seconds:
        return "0s"

    try:
        seconds = int(seconds)
    except Exception:
        return "0s"

    days, seconds = divmod(
        seconds,
        86400,
    )

    hours, seconds = divmod(
        seconds,
        3600,
    )

    minutes, seconds = divmod(
        seconds,
        60,
    )

    result = []

    if days:
        result.append(
            f"{days}d"
        )

    if hours:
        result.append(
            f"{hours}h"
        )

    if minutes:
        result.append(
            f"{minutes}m"
        )

    if seconds or not result:
        result.append(
            f"{seconds}s"
        )

    return " ".join(result)


# ============================================================
# RUNTIME
# ============================================================

def get_runtime():

    if not backup_started_at:
        return "Not started"

    seconds = (
        utc_now()
        - backup_started_at
    ).total_seconds()

    return format_duration(
        seconds
    )


# ============================================================
# FILE ID
# ============================================================

def get_file_id(file):
    """
    Supports both:

        file.file_id

    and:

        MongoDB _id
    """

    file_id = getattr(
        file,
        "file_id",
        None,
    )

    if file_id:
        return str(file_id)

    file_id = getattr(
        file,
        "_id",
        None,
    )

    if file_id:
        return str(file_id)

    return None


# ============================================================
# FILE NAME
# ============================================================

def get_file_name(file):

    name = getattr(
        file,
        "file_name",
        None,
    )

    if not name:
        name = "Unknown File"

    return str(name)


# ============================================================
# CAPTION
# ============================================================

def get_file_caption(file):

    caption = getattr(
        file,
        "caption",
        None,
    )

    if caption:
        return str(caption)

    file_name = get_file_name(
        file
    )

    return (
        f"<code>"
        f"{html.escape(file_name)}"
        f"</code>"
    )


# ============================================================
# BACKUP STATUS
# ============================================================

async def get_backup_record(file_id):

    if not file_id:
        return None

    try:

        return await backup_collection.find_one(
            {
                "_id": str(file_id),
            }
        )

    except Exception:

        logger.exception(
            "[BACKUP] Error reading backup status "
            "for %s",
            file_id,
        )

        return None


# ============================================================
# CHECK COMPLETED
# ============================================================

async def is_backed_up(file_id):

    record = await get_backup_record(
        file_id
    )

    if not record:
        return False

    return (
        record.get("status")
        == "completed"
    )


# ============================================================
# MARK STARTED
# ============================================================

async def mark_started(
    file_id,
    source_db,
):

    try:

        await backup_collection.update_one(
            {
                "_id": str(file_id),
            },
            {
                "$set": {
                    "status": "uploading",
                    "source_db": source_db,
                    "backup_channel": get_backup_channel_id(),
                    "started_at": utc_now(),
                },
                "$unset": {
                    "error": "",
                },
            },
            upsert=True,
        )

    except Exception:

        logger.exception(
            "[BACKUP] Failed marking file "
            "as uploading: %s",
            file_id,
        )


# ============================================================
# MARK COMPLETED
# ============================================================

async def mark_completed(
    file_id,
    source_db,
    message_id=None,
):

    try:

        await backup_collection.update_one(
            {
                "_id": str(file_id),
            },
            {
                "$set": {
                    "status": "completed",
                    "source_db": source_db,
                    "backup_channel": get_backup_channel_id(),
                    "backup_message_id": message_id,
                    "completed_at": utc_now(),
                },
                "$unset": {
                    "error": "",
                },
            },
            upsert=True,
        )

    except Exception:

        logger.exception(
            "[BACKUP] Failed marking file "
            "as completed: %s",
            file_id,
        )


# ============================================================
# MARK FAILED
# ============================================================

async def mark_failed(
    file_id,
    source_db,
    error,
):

    try:

        await backup_collection.update_one(
            {
                "_id": str(file_id),
            },
            {
                "$set": {
                    "status": "failed",
                    "source_db": source_db,
                    "error": str(error)[
                        :3000
                    ],
                    "last_failed_at": utc_now(),
                }
            },
            upsert=True,
        )

    except Exception:

        logger.exception(
            "[BACKUP] Failed marking file "
            "as failed: %s",
            file_id,
        )


# ============================================================
# RESET UPLOADING RECORDS
#
# If Render crashes while a file is uploading,
# that file may remain marked "uploading".
#
# On next startup it becomes eligible again.
# ============================================================

async def reset_stale_uploads():

    try:

        result = await backup_collection.update_many(
            {
                "status": "uploading",
            },
            {
                "$set": {
                    "status": "failed",
                    "error": (
                        "Previous backup process "
                        "stopped during upload."
                    ),
                    "last_failed_at": utc_now(),
                }
            },
        )

        if result.modified_count:

            logger.info(
                "[BACKUP] Reset %s interrupted "
                "upload records.",
                result.modified_count,
            )

    except Exception:

        logger.exception(
            "[BACKUP] Could not reset stale uploads."
        )


# ============================================================
# GET COLLECTION
# ============================================================

def get_collection(database):

    return database[
        COLLECTION_NAME
    ]


# ============================================================
# COUNT DATABASE
# ============================================================

async def get_database_count(
    database,
    source_db,
):

    try:

        collection = get_collection(
            database
        )

        count = (
            await collection.count_documents({})
        )

        logger.info(
            "[BACKUP] %s collection contains "
            "%s documents.",
            source_db,
            count,
        )

        return count

    except Exception as e:

        logger.error(
            "[BACKUP] Failed counting %s: %s",
            source_db,
            e,
        )

        return 0


# ============================================================
# SCAN ONE DATABASE
# ============================================================

async def scan_model(
    database,
    source_db,
):

    global backup_total_skipped

    files_to_process = []

    try:

        collection = get_collection(
            database
        )

        total = await get_database_count(
            database,
            source_db,
        )

        if total == 0:

            logger.warning(
                "[BACKUP] %s is EMPTY.",
                source_db,
            )

            return []

        # ----------------------------------------------------
        # IMPORTANT
        #
        # We scan from oldest to newest.
        #
        # This allows the backup to continue from where it
        # previously stopped instead of always taking the
        # newest files.
        # ----------------------------------------------------

        cursor = (
            collection
            .find({})
            .sort(
                [
                    (
                        "_id",
                        1,
                    )
                ]
            )
            .limit(
                BACKUP_BATCH_SIZE
            )
        )

        documents = await cursor.to_list(
            length=BACKUP_BATCH_SIZE
        )

        logger.info(
            "[BACKUP] %s: read %s documents.",
            source_db,
            len(documents),
        )

        for document in documents:

            file_id = document.get(
                "_id"
            )

            if not file_id:
                logger.warning(
                    "[BACKUP] %s document "
                    "without _id.",
                    source_db,
                )
                continue

            file_id = str(
                file_id
            )

            # ------------------------------------------------
            # Check separate backup tracking collection
            # ------------------------------------------------

            status = await backup_collection.find_one(
                {
                    "_id": file_id,
                },
                {
                    "status": 1,
                },
            )

            if status and status.get(
                "status"
            ) == "completed":

                backup_total_skipped += 1

                continue

            # ------------------------------------------------
            # Raw Mongo document is converted into a normal
            # dictionary.
            #
            # This avoids relying on umongo's Document layer.
            # ------------------------------------------------

            files_to_process.append(
                (
                    source_db,
                    document,
                )
            )

        return files_to_process

    except Exception as e:

        logger.exception(
            "[BACKUP] ERROR scanning %s: %s",
            source_db,
            e,
        )

        return []


# ============================================================
# GET NEXT BACKUP BATCH
# ============================================================

async def get_backup_batch():

    for (
        source_db,
        database,
        model,
    ) in DATABASES:

        files = await scan_model(
            database,
            source_db,
        )

        if files:

            return files

    return []


# ============================================================
# BUILD TELEGRAM SEND
# ============================================================

async def send_cached_file(
    app,
    file_id,
    caption,
):

    channel_id = get_backup_channel_id()

    if not channel_id:
        raise RuntimeError(
            "BACKUP_CHANNEL_ID is not configured."
        )

    # --------------------------------------------------------
    # VERY IMPORTANT
    #
    # "app" MUST be the actual Pyrogram Client.
    #
    # It must NEVER be:
    #
    #     db
    #     db2
    #     db3
    #     client from Motor
    #
    # --------------------------------------------------------

    return await app.send_cached_media(
        chat_id=channel_id,
        file_id=file_id,
        caption=caption,
    )


# ============================================================
# BACKUP ONE FILE
# ============================================================

async def backup_one_file(
    app,
    source_db,
    document,
):

    global backup_current_file
    global backup_current_db

    global backup_total_uploaded
    global backup_total_failed
    global backup_total_processed

    global backup_last_error
    global backup_last_activity

    file_id = document.get(
        "_id"
    )

    if not file_id:

        backup_total_failed += 1

        backup_last_error = (
            f"{source_db}: document "
            "has no _id."
        )

        return False

    file_id = str(
        file_id
    )

    file_name = str(
        document.get(
            "file_name",
            "Unknown File",
        )
    )

    backup_current_file = file_name

    backup_current_db = source_db

    backup_last_activity = utc_text()

    # --------------------------------------------------------
    # DOUBLE CHECK
    # --------------------------------------------------------

    if await is_backed_up(
        file_id
    ):

        backup_total_skipped += 1

        return True

    # --------------------------------------------------------
    # Mark upload started
    # --------------------------------------------------------

    await mark_started(
        file_id,
        source_db,
    )

    # --------------------------------------------------------
    # Caption
    # --------------------------------------------------------

    caption = document.get(
        "caption"
    )

    if caption:

        caption = str(
            caption
        )

    else:

        caption = (
            f"<code>"
            f"{html.escape(file_name)}"
            f"</code>"
        )

    # --------------------------------------------------------
    # Upload
    # --------------------------------------------------------

    try:

        logger.info(
            "[BACKUP] Uploading [%s] %s",
            source_db,
            file_name,
        )

        while True:

            try:

                sent = await send_cached_file(
                    app=app,
                    file_id=file_id,
                    caption=caption,
                )

                break

            except FloodWait as e:

                wait_time = int(
                    getattr(
                        e,
                        "value",
                        30,
                    )
                )

                logger.warning(
                    "[BACKUP] FloodWait: "
                    "waiting %s seconds.",
                    wait_time,
                )

                await asyncio.sleep(
                    wait_time + 2
                )

            except RPCError as e:

                backup_total_failed += 1

                backup_total_processed += 1

                backup_last_error = str(
                    e
                )

                await mark_failed(
                    file_id,
                    source_db,
                    e,
                )

                logger.exception(
                    "[BACKUP] Telegram RPC error "
                    "[%s] %s: %s",
                    source_db,
                    file_name,
                    e,
                )

                return False

            except Exception as e:

                backup_total_failed += 1

                backup_total_processed += 1

                backup_last_error = str(
                    e
                )

                await mark_failed(
                    file_id,
                    source_db,
                    e,
                )

                logger.exception(
                    "[BACKUP] Upload failed "
                    "[%s] %s: %s",
                    source_db,
                    file_name,
                    e,
                )

                return False

        # ----------------------------------------------------
        # Mark completed
        # ----------------------------------------------------

        await mark_completed(
            file_id=file_id,
            source_db=source_db,
            message_id=getattr(
                sent,
                "id",
                None,
            ),
        )

        backup_total_uploaded += 1

        backup_total_processed += 1

        backup_last_activity = utc_text()

        backup_last_error = None

        logger.info(
            "[BACKUP] SUCCESS [%s] %s",
            source_db,
            file_name,
        )

        await asyncio.sleep(
            BACKUP_UPLOAD_DELAY
        )

        return True

    except Exception as e:

        backup_total_failed += 1

        backup_total_processed += 1

        backup_last_error = str(
            e
        )

        await mark_failed(
            file_id,
            source_db,
            e,
        )

        logger.exception(
            "[BACKUP] Unexpected error "
            "[%s] %s: %s",
            source_db,
            file_name,
            e,
        )

        return False


# ============================================================
# BACKUP WORKER
# ============================================================

async def backup_worker(
    app
):

    global backup_running

    global backup_started_at
    global backup_finished_at

    global backup_current_file
    global backup_current_db

    global backup_last_error
    global backup_last_activity

    if not get_backup_channel_id():

        logger.error(
            "[BACKUP] BACKUP_CHANNEL_ID "
            "is not configured."
        )

        return

    async with backup_lock:

        if backup_running:

            logger.info(
                "[BACKUP] Worker already running."
            )

            return

        backup_running = True

        backup_started_at = utc_now()

        backup_finished_at = None

        backup_current_file = None

        backup_current_db = None

        backup_last_error = None

        backup_last_activity = utc_text()

        logger.info(
            "=================================================="
        )

        logger.info(
            "[BACKUP] BACKUP WORKER STARTED"
        )

        logger.info(
            "[BACKUP] Watching Media / Media2 / Media3"
        )

        logger.info(
            "[BACKUP] Channel: %s",
            get_backup_channel_id(),
        )

        logger.info(
            "=================================================="
        )

        try:

            # ------------------------------------------------
            # Reset interrupted records
            # ------------------------------------------------

            await reset_stale_uploads()

            # ------------------------------------------------
            # Continuous loop
            # ------------------------------------------------

            while backup_running:

                batch = await get_backup_batch()

                # ------------------------------------------------
                # No files currently waiting.
                #
                # DO NOT STOP.
                #
                # Keep watching for newly indexed files.
                # ------------------------------------------------

                if not batch:

                    backup_current_file = None

                    backup_current_db = None

                    backup_last_activity = utc_text()

                    await asyncio.sleep(
                        BACKUP_CHECK_INTERVAL
                    )

                    continue

                # ------------------------------------------------
                # Process batch
                # ------------------------------------------------

                for (
                    source_db,
                    document,
                ) in batch:

                    if not backup_running:
                        break

                    await backup_one_file(
                        app=app,
                        source_db=source_db,
                        document=document,
                    )

                    await asyncio.sleep(
                        0.2
                    )

        except asyncio.CancelledError:

            logger.info(
                "[BACKUP] Worker cancellation received."
            )

            raise

        except Exception as e:

            backup_last_error = str(
                e
            )

            logger.exception(
                "[BACKUP] Worker crashed: %s",
                e,
            )

        finally:

            backup_running = False

            backup_finished_at = utc_now()

            backup_current_file = None

            backup_current_db = None

            logger.info(
                "[BACKUP] Worker stopped."
            )


# ============================================================
# START BACKUP
# ============================================================

async def start_backup_worker(
    app
):

    global backup_task
    global pyrogram_app

    pyrogram_app = app

    if not get_backup_channel_id():

        logger.error(
            "[BACKUP] Cannot start because "
            "BACKUP_CHANNEL_ID is missing."
        )

        return False

    if backup_task:

        if not backup_task.done():

            logger.info(
                "[BACKUP] Background backup task "
                "already running."
            )

            return True

    # --------------------------------------------------------
    # Verify Telegram channel
    # --------------------------------------------------------

    try:

        chat = await app.get_chat(
            get_backup_channel_id()
        )

        logger.info(
            "[BACKUP] Backup channel verified: %s",
            getattr(
                chat,
                "title",
                "Unknown",
            ),
        )

    except Exception as e:

        logger.exception(
            "[BACKUP] Cannot access backup channel: %s",
            e,
        )

        return False

    # --------------------------------------------------------
    # Create worker
    # --------------------------------------------------------

    backup_task = asyncio.create_task(
        backup_worker(
            app
        )
    )

    logger.info(
        "[BACKUP] Background backup task created."
    )

    return True


# ============================================================
# STOP BACKUP
# ============================================================

async def stop_backup_worker():

    global backup_task
    global backup_running

    if not backup_task:

        return False

    if backup_task.done():

        backup_task = None

        backup_running = False

        return False

    backup_running = False

    backup_task.cancel()

    try:

        await backup_task

    except asyncio.CancelledError:

        pass

    except Exception:

        logger.exception(
            "[BACKUP] Error stopping worker."
        )

    backup_task = None

    return True


# ============================================================
# STATUS TEXT
# ============================================================

def build_status_text():

    if backup_running:

        status = "🟢 RUNNING"

    else:

        status = "🔴 STOPPED"

    channel = (
        get_backup_channel_id()
        or "NOT SET"
    )

    current_db = (
        backup_current_db
        or "-"
    )

    current_file = (
        backup_current_file
        or "Waiting for files..."
    )

    text = (

        "<b>╔══════════════════════════════╗</b>\n"
        "<b>      🚀 DOWNTOWNVILLA BACKUP</b>\n"
        "<b>╚══════════════════════════════╝</b>\n\n"

        f"📡 Status: <b>{status}</b>\n"

        f"📦 Channel: "
        f"<code>{channel}</code>\n\n"

        "<b>🗄️ DATABASES</b>\n"
        "├─ Media\n"
        "├─ Media2\n"
        "└─ Media3\n\n"

        "<b>📊 TRANSFER STATUS</b>\n"

        f"📚 Processed: "
        f"<b>{backup_total_processed}</b>\n"

        f"✅ Uploaded: "
        f"<b>{backup_total_uploaded}</b>\n"

        f"⏭️ Already backed up: "
        f"<b>{backup_total_skipped}</b>\n"

        f"❌ Failed: "
        f"<b>{backup_total_failed}</b>\n\n"

        "<b>📤 CURRENT TRANSFER</b>\n"

        f"🗄️ Database: "
        f"<b>{html.escape(str(current_db))}</b>\n"

        f"📄 File:\n"
        f"<code>{html.escape(str(current_file)[:700])}</code>\n\n"

        f"⏱️ Runtime: "
        f"<b>{get_runtime()}</b>\n"

        f"🕐 Last activity: "
        f"<b>{backup_last_activity or '-'}</b>\n"
    )

    if backup_last_error:

        text += (
            "\n⚠️ <b>LAST ERROR</b>\n"
            f"<code>"
            f"{html.escape(str(backup_last_error)[:1200])}"
            f"</code>\n"
        )

    return text


# ============================================================
# /backup
#
# Manual start command.
# ============================================================

@Client.on_message(
    filters.command(
        "backup"
    )
    & filters.user(
        ADMINS
    )
)
async def backup_command(
    app,
    message,
):

    if not get_backup_channel_id():

        await message.reply_text(
            "<b>❌ BACKUP CHANNEL NOT CONFIGURED</b>\n\n"
            "Add this Render environment variable:\n\n"
            "<code>BACKUP_CHANNEL_ID=-100xxxxxxxxxxxx</code>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    # --------------------------------------------------------
    # Check channel
    # --------------------------------------------------------

    try:

        chat = await app.get_chat(
            get_backup_channel_id()
        )

        channel_name = (
            getattr(
                chat,
                "title",
                None,
            )
            or "Backup Channel"
        )

    except Exception as e:

        await message.reply_text(
            "<b>❌ BACKUP CHANNEL ERROR</b>\n\n"
            f"<code>"
            f"{html.escape(str(e)[:1200])}"
            f"</code>\n\n"
            "Make sure the bot is an administrator "
            "in the backup channel.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    # --------------------------------------------------------
    # Already running
    # --------------------------------------------------------

    if backup_running:

        await message.reply_text(
            "<b>🚀 BACKUP IS ALREADY RUNNING</b>\n\n"

            f"📦 Channel: "
            f"<b>{html.escape(channel_name)}</b>\n"

            f"🆔 <code>"
            f"{get_backup_channel_id()}"
            f"</code>\n\n"

            "🔄 The backup continuously watches "
            "Media, Media2 and Media3.\n\n"

            "📊 Use <code>/backup_status</code> "
            "for live status.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    # --------------------------------------------------------
    # Start
    # --------------------------------------------------------

    started = await start_backup_worker(
        app
    )

    if not started:

        await message.reply_text(
            "<b>❌ BACKUP COULD NOT START</b>\n\n"
            "Check the Render environment variable "
            "and make sure the bot can access the "
            "backup channel.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    await message.reply_text(
        "<b>╔══════════════════════════════╗</b>\n"
        "<b>       🚀 BACKUP STARTED</b>\n"
        "<b>╚══════════════════════════════╝</b>\n\n"

        f"📦 Channel: "
        f"<b>{html.escape(channel_name)}</b>\n"

        f"🆔 <code>"
        f"{get_backup_channel_id()}"
        f"</code>\n\n"

        "<b>🗄️ DATABASES</b>\n"
        "• Media\n"
        "• Media2\n"
        "• Media3\n\n"

        "📤 Existing files will be transferred.\n"
        "🔄 Newly indexed files will also be detected.\n"
        "♻️ Completed files will never be uploaded twice.\n"
        "🔁 Failed files can be retried automatically.\n\n"

        "📊 <code>/backup_status</code>\n"
        "📈 <code>/backup_stats</code>\n"
        "🛑 <code>/backup_stop</code>",
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# /backup_status
# ============================================================

@Client.on_message(
    filters.command(
        "backup_status"
    )
    & filters.user(
        ADMINS
    )
)
async def backup_status_command(
    app,
    message,
):

    await message.reply_text(
        build_status_text(),

        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔄 REFRESH",
                        callback_data=(
                            "downtownvilla_backup_refresh"
                        ),
                    )
                ]
            ]
        ),

        parse_mode=enums.ParseMode.HTML,

        disable_web_page_preview=True,
    )


# ============================================================
# REFRESH STATUS
# ============================================================

@Client.on_callback_query(
    filters.regex(
        "^downtownvilla_backup_refresh$"
    )
)
async def backup_refresh_callback(
    app,
    query,
):

    if query.from_user.id not in ADMINS:

        await query.answer(
            "❌ Admin only.",
            show_alert=True,
        )

        return

    try:

        await query.message.edit_text(
            build_status_text(),

            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 REFRESH",
                            callback_data=(
                                "downtownvilla_backup_refresh"
                            ),
                        )
                    ]
                ]
            ),

            parse_mode=enums.ParseMode.HTML,

            disable_web_page_preview=True,
        )

        await query.answer(
            "🔄 Backup status refreshed."
        )

    except Exception as e:

        # Telegram can return MESSAGE_NOT_MODIFIED
        # when the status has not changed.

        if "MESSAGE_NOT_MODIFIED" in str(
            e
        ):

            await query.answer(
                "ℹ️ No status changes yet."
            )

            return

        logger.exception(
            "[BACKUP] Status refresh error: %s",
            e,
        )

        await query.answer(
            "Unable to refresh.",
            show_alert=True,
        )


# ============================================================
# /backup_stats
# ============================================================

@Client.on_message(
    filters.command(
        "backup_stats"
    )
    & filters.user(
        ADMINS
    )
)
async def backup_stats_command(
    app,
    message,
):

    try:

        completed = (
            await backup_collection.count_documents(
                {
                    "status": "completed",
                }
            )
        )

        failed = (
            await backup_collection.count_documents(
                {
                    "status": "failed",
                }
            )
        )

        uploading = (
            await backup_collection.count_documents(
                {
                    "status": "uploading",
                }
            )
        )

        # ----------------------------------------------------
        # Database counts
        # ----------------------------------------------------

        media_count = (
            await get_database_count(
                db,
                "Media",
            )
        )

        media2_count = (
            await get_database_count(
                db2,
                "Media2",
            )
        )

        media3_count = (
            await get_database_count(
                db3,
                "Media3",
            )
        )

        total_database_files = (
            media_count
            + media2_count
            + media3_count
        )

        text = (

            "<b>╔══════════════════════════════╗</b>\n"
            "<b>       📊 BACKUP STATISTICS</b>\n"
            "<b>╚══════════════════════════════╝</b>\n\n"

            "<b>🗄️ DATABASE</b>\n"

            f"📁 Media: "
            f"<b>{media_count:,}</b>\n"

            f"📁 Media2: "
            f"<b>{media2_count:,}</b>\n"

            f"📁 Media3: "
            f"<b>{media3_count:,}</b>\n"

            f"📚 Total files: "
            f"<b>{total_database_files:,}</b>\n\n"

            "<b>📤 BACKUP</b>\n"

            f"✅ Completed: "
            f"<b>{completed:,}</b>\n"

            f"🔄 Uploading: "
            f"<b>{uploading:,}</b>\n"

            f"❌ Failed: "
            f"<b>{failed:,}</b>\n\n"

            "<b>🚀 CURRENT RUN</b>\n"

            f"📤 Uploaded: "
            f"<b>{backup_total_uploaded:,}</b>\n"

            f"⏭️ Skipped: "
            f"<b>{backup_total_skipped:,}</b>\n"

            f"❌ Failed: "
            f"<b>{backup_total_failed:,}</b>\n"

            f"📚 Processed: "
            f"<b>{backup_total_processed:,}</b>\n"
        )

        await message.reply_text(
            text,
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception as e:

        logger.exception(
            "[BACKUP] Statistics error."
        )

        await message.reply_text(
            "<b>❌ BACKUP STATS ERROR</b>\n\n"
            f"<code>"
            f"{html.escape(str(e)[:1200])}"
            f"</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# ============================================================
# /backup_stop
# ============================================================

@Client.on_message(
    filters.command(
        "backup_stop"
    )
    & filters.user(
        ADMINS
    )
)
async def backup_stop_command(
    app,
    message,
):

    if not backup_running:

        await message.reply_text(
            "🔴 <b>BACKUP IS NOT RUNNING.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    stopped = await stop_backup_worker()

    if stopped:

        await message.reply_text(
            "🛑 <b>DOWNTOWNVILLA BACKUP STOPPED</b>\n\n"

            "✅ Successfully completed files "
            "remain permanently marked as completed.\n\n"

            "🔄 When you start <code>/backup</code> "
            "again, it will continue with the remaining files.\n\n"

            "📡 Newly added files will also be detected.",
            parse_mode=enums.ParseMode.HTML,
        )

    else:

        await message.reply_text(
            "⚠️ Backup was already stopped.",
            parse_mode=enums.ParseMode.HTML,
        )


# ============================================================
# /backup_reset_failed
#
# This does NOT delete completed records.
#
# It simply makes failed files eligible for retry.
# ============================================================

@Client.on_message(
    filters.command(
        "backup_reset_failed"
    )
    & filters.user(
        ADMINS
    )
)
async def backup_reset_failed_command(
    app,
    message,
):

    try:

        result = (
            await backup_collection.update_many(
                {
                    "status": "failed",
                },
                {
                    "$set": {
                        "status": "pending",
                    },
                    "$unset": {
                        "error": "",
                    },
                },
            )
        )

        await message.reply_text(
            "<b>🔄 FAILED BACKUPS RESET</b>\n\n"
            f"Files ready for retry: "
            f"<b>{result.modified_count}</b>\n\n"
            "Start the backup with "
            "<code>/backup</code>.",
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception as e:

        logger.exception(
            "[BACKUP] Failed reset error."
        )

        await message.reply_text(
            "<b>❌ ERROR</b>\n\n"
            f"<code>"
            f"{html.escape(str(e)[:1000])}"
            f"</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# ============================================================
# AUTOMATIC START SUPPORT
# ============================================================
#
# Pyrogram 2.x does NOT provide:
#
#     @Client.on_start()
#
# Therefore we DO NOT use it.
#
# Instead, we use an internal startup watcher.
#
# The first incoming update gives us the real Pyrogram
# Client instance. If BACKUP_AUTO_START=true, the worker
# starts automatically.
#
# This avoids modifying bot.py.
# ============================================================

_auto_start_triggered = False


@Client.on_message(
    filters.all,
    group=-9999,
)
async def downtownvilla_backup_auto_start(
    app,
    message,
):

    global _auto_start_triggered

    if _auto_start_triggered:
        return

    if not BACKUP_AUTO_START:
        return

    if not get_backup_channel_id():
        return

    _auto_start_triggered = True

    try:

        logger.info(
            "[BACKUP] Automatic backup startup "
            "trigger received."
        )

        await asyncio.sleep(
            5
        )

        if not backup_running:

            await start_backup_worker(
                app
            )

            logger.info(
                "[BACKUP] AUTOMATIC BACKUP MONITOR STARTED"
            )

    except Exception:

        _auto_start_triggered = False

        logger.exception(
            "[BACKUP] Automatic startup failed."
        )


# ============================================================
# SAFETY START COMMAND
#
# This command always has the real Pyrogram app.
#
# /backup is the guaranteed way to start manually.
# ============================================================

@Client.on_message(
    filters.command(
        "backup_start"
    )
    & filters.user(
        ADMINS
    )
)
async def backup_start_command(
    app,
    message,
):

    if backup_running:

        await message.reply_text(
            "🟢 <b>BACKUP IS ALREADY RUNNING.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    started = await start_backup_worker(
        app
    )

    if started:

        await message.reply_text(
            "🚀 <b>DOWNTOWNVILLA BACKUP STARTED</b>\n\n"
            "The worker is now watching:\n"
            "• Media\n"
            "• Media2\n"
            "• Media3\n\n"
            "Use <code>/backup_status</code> "
            "to monitor it.",
            parse_mode=enums.ParseMode.HTML,
        )

    else:

        await message.reply_text(
            "❌ <b>BACKUP FAILED TO START.</b>\n\n"
            "Check BACKUP_CHANNEL_ID and channel permissions.",
            parse_mode=enums.ParseMode.HTML,
        )


# ============================================================
# MODULE LOAD LOG
# ============================================================

logger.info(
    "=================================================="
)

logger.info(
    "[BACKUP] DowntownVilla Ultimate Backup System loaded."
)

logger.info(
    "[BACKUP] Media / Media2 / Media3 support enabled."
)

logger.info(
    "[BACKUP] Backup channel: %s",
    get_backup_channel_id(),
)

logger.info(
    "[BACKUP] AUTO_START: %s",
    BACKUP_AUTO_START,
)

logger.info(
    "[BACKUP] Batch size: %s",
    BACKUP_BATCH_SIZE,
)

logger.info(
    "[BACKUP] Check interval: %s seconds",
    BACKUP_CHECK_INTERVAL,
)

logger.info(
    "=================================================="
)


# ============================================================
# END OF DOWNTOWNVILLA BACKUP.PY
# ============================================================
