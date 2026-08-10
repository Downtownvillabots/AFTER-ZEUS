import os
import asyncio
import logging
from datetime import datetime

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, RPCError

from database.ia_filterdb import (
    Media,
    Media2,
    Media3,
    db,
)

try:
    from info import ADMINS
except Exception:
    ADMINS = []


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

BACKUP_CHANNEL_ID = os.getenv("BACKUP_CHANNEL_ID")

BACKUP_AUTO_START = os.getenv(
    "BACKUP_AUTO_START",
    "true"
).lower() in ("true", "1", "yes", "on")

BACKUP_CHECK_INTERVAL = int(
    os.getenv("BACKUP_CHECK_INTERVAL", "30")
)

BACKUP_BATCH_SIZE = int(
    os.getenv("BACKUP_BATCH_SIZE", "20")
)

BACKUP_UPLOAD_DELAY = float(
    os.getenv("BACKUP_UPLOAD_DELAY", "1")
)


# ============================================================
# BACKUP TRACKING COLLECTION
#
# IMPORTANT:
# This is a NEW collection.
#
# It does NOT modify:
#     Media
#     Media2
#     Media3
# ============================================================

backup_collection = db["dreamx_backup_status"]


# ============================================================
# GLOBAL STATE
# ============================================================

backup_task = None

backup_running = False

backup_started_at = None
backup_finished_at = None

backup_total_uploaded = 0
backup_total_skipped = 0
backup_total_failed = 0

backup_current_file = None
backup_current_db = None

backup_last_error = None
backup_last_activity = None

backup_lock = asyncio.Lock()

# The actual Pyrogram Client.
#
# IMPORTANT:
# This is NOT the MongoDB client from ia_filterdb.py.
pyrogram_app = None


# ============================================================
# BASIC HELPERS
# ============================================================


def get_channel_id():
    if not BACKUP_CHANNEL_ID:
        return None

    try:
        return int(BACKUP_CHANNEL_ID)
    except Exception:
        logger.error(
            "[BACKUP] Invalid BACKUP_CHANNEL_ID: %s",
            BACKUP_CHANNEL_ID,
        )
        return None


def backup_configured():
    return get_channel_id() is not None


def utc_now():
    return datetime.utcnow()


def utc_text():
    return utc_now().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def format_bytes(size):
    if not size:
        return "0 B"

    size = float(size)

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

    seconds = int(seconds)

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


def get_runtime():
    if not backup_started_at:
        return "Not started"

    elapsed = (
        utc_now() - backup_started_at
    ).total_seconds()

    return format_duration(elapsed)


def get_processed_count():
    return (
        backup_total_uploaded
        + backup_total_skipped
        + backup_total_failed
    )


# ============================================================
# FILE ID HELPER
# ============================================================


def get_database_file_id(file):
    """
    umongo Document exposes file_id because the field is:

        file_id = fields.StrField(attribute="_id")

    This helper safely handles both file_id and _id.
    """

    file_id = getattr(
        file,
        "file_id",
        None,
    )

    if file_id:
        return file_id

    file_id = getattr(
        file,
        "_id",
        None,
    )

    return file_id


# ============================================================
# BACKUP TRACKING
# ============================================================


async def is_backed_up(file_id):
    if not file_id:
        return False

    try:
        result = await backup_collection.find_one(
            {
                "_id": str(file_id),
                "status": "completed",
            }
        )

        return result is not None

    except Exception:
        logger.exception(
            "[BACKUP] Error checking backup status: %s",
            file_id,
        )

        return False


async def mark_completed(
    file_id,
    source_db,
    message_id=None,
):
    try:
        await backup_collection.update_one(
            {
                "_id": str(file_id)
            },
            {
                "$set": {
                    "status": "completed",
                    "source_db": source_db,
                    "backup_channel": get_channel_id(),
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
            "[BACKUP] Failed to save completed status: %s",
            file_id,
        )


async def mark_failed(
    file_id,
    source_db,
    error,
):
    try:
        await backup_collection.update_one(
            {
                "_id": str(file_id)
            },
            {
                "$set": {
                    "status": "failed",
                    "source_db": source_db,
                    "error": str(error)[:2000],
                    "last_failed_at": utc_now(),
                }
            },
            upsert=True,
        )

    except Exception:
        logger.exception(
            "[BACKUP] Failed to save failed status.",
        )


# ============================================================
# GET CAPTION
# ============================================================


def get_caption(file):
    caption = getattr(
        file,
        "caption",
        None,
    )

    if caption:
        return caption

    file_name = getattr(
        file,
        "file_name",
        None,
    )

    if file_name:
        return f"<code>{file_name}</code>"

    return None


# ============================================================
# GET COVER
# ============================================================


def get_cover(file):
    cover = getattr(
        file,
        "cover",
        None,
    )

    if not cover:
        return None

    return cover


# ============================================================
# SEND ONE FILE
# ============================================================


async def backup_one_file(
    app,
    source_db,
    file,
):
    global backup_current_file
    global backup_current_db

    global backup_total_uploaded
    global backup_total_skipped
    global backup_total_failed

    global backup_last_error
    global backup_last_activity

    file_id = get_database_file_id(file)

    if not file_id:
        backup_total_failed += 1

        backup_last_error = (
            "File has no file_id."
        )

        logger.error(
            "[BACKUP] File without file_id in %s",
            source_db,
        )

        return False

    file_id = str(file_id)

    file_name = getattr(
        file,
        "file_name",
        "Unknown file",
    )

    backup_current_file = str(
        file_name
    )

    backup_current_db = source_db

    backup_last_activity = utc_text()

    # --------------------------------------------------------
    # DUPLICATE CHECK
    # --------------------------------------------------------

    if await is_backed_up(file_id):

        backup_total_skipped += 1

        logger.info(
            "[BACKUP] Already backed up: %s",
            file_name,
        )

        return True

    # --------------------------------------------------------
    # CAPTION / COVER
    # --------------------------------------------------------

    caption = get_caption(file)

    cover = get_cover(file)

    # --------------------------------------------------------
    # SEND
    # --------------------------------------------------------

    try:

        logger.info(
            "[BACKUP] Uploading [%s] %s",
            source_db,
            file_name,
        )

        while True:

            try:

                # IMPORTANT:
                #
                # app is the REAL Pyrogram Client.
                #
                # Do NOT use the MongoDB client here.
                #

                send_kwargs = {
                    "chat_id": get_channel_id(),
                    "file_id": file_id,
                }

                if caption:
                    send_kwargs["caption"] = caption

                if cover:
                    send_kwargs["cover"] = cover

                try:

                    sent = await app.send_cached_media(
                        **send_kwargs
                    )

                except TypeError:

                    # Some Pyrogram versions / media types
                    # may reject the cover argument.
                    #
                    # Retry without cover.

                    send_kwargs.pop(
                        "cover",
                        None,
                    )

                    sent = await app.send_cached_media(
                        **send_kwargs
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
                    "[BACKUP] FloodWait %s seconds.",
                    wait_time,
                )

                await asyncio.sleep(
                    wait_time + 2
                )

            except RPCError as e:

                backup_total_failed += 1

                backup_last_error = str(e)

                logger.exception(
                    "[BACKUP] Telegram RPC error "
                    "for %s: %s",
                    file_name,
                    e,
                )

                await mark_failed(
                    file_id,
                    source_db,
                    e,
                )

                return False

            except Exception as e:

                backup_total_failed += 1

                backup_last_error = str(e)

                logger.exception(
                    "[BACKUP] Upload failed: %s",
                    e,
                )

                await mark_failed(
                    file_id,
                    source_db,
                    e,
                )

                return False

        # ----------------------------------------------------
        # MARK SUCCESS
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

        backup_last_activity = utc_text()

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

        backup_last_error = str(e)

        logger.exception(
            "[BACKUP] Unexpected upload error: %s",
            e,
        )

        await mark_failed(
            file_id,
            source_db,
            e,
        )

        return False


# ============================================================
# FIND UNBACKED FILES
# ============================================================


async def scan_model(
    model,
    source_db,
):
    """
    Scan one database collection.

    We intentionally do NOT modify the original document.

    The backup status is kept separately in:
        dreamx_backup_status
    """

    files_to_process = []

    try:

        cursor = (
            model.find({})
            .sort("$natural", 1)
            .limit(BACKUP_BATCH_SIZE)
        )

        files = await cursor.to_list(
            length=BACKUP_BATCH_SIZE
        )

        for file in files:

            file_id = get_database_file_id(
                file
            )

            if not file_id:
                continue

            if await is_backed_up(
                str(file_id)
            ):
                continue

            files_to_process.append(
                file
            )

            if len(files_to_process) >= BACKUP_BATCH_SIZE:
                break

    except Exception:

        logger.exception(
            "[BACKUP] Error scanning %s",
            source_db,
        )

    return files_to_process


# ============================================================
# SCAN ALL THREE DATABASES
# ============================================================


async def get_backup_batch():

    # --------------------------------------------------------
    # PRIMARY
    # --------------------------------------------------------

    files = await scan_model(
        Media,
        "Media",
    )

    if files:
        return [
            ("Media", file)
            for file in files
        ]

    # --------------------------------------------------------
    # SECONDARY
    # --------------------------------------------------------

    files = await scan_model(
        Media2,
        "Media2",
    )

    if files:
        return [
            ("Media2", file)
            for file in files
        ]

    # --------------------------------------------------------
    # TERTIARY
    # --------------------------------------------------------

    files = await scan_model(
        Media3,
        "Media3",
    )

    if files:
        return [
            ("Media3", file)
            for file in files
        ]

    return []


# ============================================================
# BACKUP WORKER
# ============================================================


async def backup_worker(app):

    global backup_running

    global backup_started_at
    global backup_finished_at

    global backup_current_file
    global backup_current_db

    global backup_last_error
    global backup_last_activity

    if not backup_configured():

        logger.warning(
            "[BACKUP] BACKUP_CHANNEL_ID is not configured."
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

        backup_last_error = None

        backup_last_activity = utc_text()

        logger.info(
            "=================================================="
        )

        logger.info(
            "[BACKUP] BACKUP WORKER STARTED"
        )

        logger.info(
            "[BACKUP] Backup channel: %s",
            get_channel_id(),
        )

        logger.info(
            "[BACKUP] Watching Media"
        )

        logger.info(
            "[BACKUP] Watching Media2"
        )

        logger.info(
            "[BACKUP] Watching Media3"
        )

        logger.info(
            "=================================================="
        )

        try:

            while True:

                batch = await get_backup_batch()

                # ------------------------------------------------
                # Nothing currently waiting.
                #
                # IMPORTANT:
                #
                # Do NOT stop the worker.
                #
                # Keep watching for newly added files.
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
                # Process current batch
                # ------------------------------------------------

                for source_db, file in batch:

                    if not backup_running:
                        break

                    await backup_one_file(
                        app,
                        source_db,
                        file,
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

            backup_last_error = str(e)

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
# START WORKER
# ============================================================


async def start_backup_worker(app):

    global backup_task
    global pyrogram_app

    pyrogram_app = app

    if not backup_configured():

        logger.warning(
            "[BACKUP] Cannot start."
        )

        logger.warning(
            "[BACKUP] Add BACKUP_CHANNEL_ID "
            "to Render environment variables."
        )

        return False

    if backup_task:

        if not backup_task.done():

            logger.info(
                "[BACKUP] Worker already active."
            )

            return True

    backup_task = asyncio.create_task(
        backup_worker(app)
    )

    logger.info(
        "[BACKUP] Background worker created."
    )

    return True


# ============================================================
# STOP WORKER
# ============================================================


async def stop_backup_worker():

    global backup_task
    global backup_running

    if not backup_task:
        return False

    if backup_task.done():
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

    return True


# ============================================================
# ADMIN COMMAND: /backup
# ============================================================


@Client.on_message(
    filters.command("backup")
    & filters.user(ADMINS)
)
async def backup_command(
    app,
    message,
):

    if not backup_configured():

        await message.reply_text(
            "<b>❌ BACKUP CHANNEL NOT CONFIGURED</b>\n\n"
            "Add this Render environment variable:\n\n"
            "<code>BACKUP_CHANNEL_ID=-100xxxxxxxxxxxx</code>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    channel_id = get_channel_id()

    # --------------------------------------------------------
    # Test channel access
    # --------------------------------------------------------

    try:

        chat = await app.get_chat(
            channel_id
        )

        channel_name = (
            chat.title
            or "Backup Channel"
        )

    except Exception as e:

        await message.reply_text(
            "<b>❌ BACKUP CHANNEL ERROR</b>\n\n"
            f"<code>{str(e)[:1000]}</code>\n\n"
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
            f"📦 Channel: <b>{channel_name}</b>\n"
            f"🆔 <code>{channel_id}</code>\n\n"
            "The worker is continuously watching "
            "all three databases.\n\n"
            "Use <code>/backup_status</code> "
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
            "<b>❌ BACKUP COULD NOT START.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    await message.reply_text(
        "<b>╔══════════════════════════╗</b>\n"
        "<b>      🚀 BACKUP STARTED</b>\n"
        "<b>╚══════════════════════════╝</b>\n\n"

        f"📦 Channel: <b>{channel_name}</b>\n"
        f"🆔 <code>{channel_id}</code>\n\n"

        "🗄️ <b>Databases:</b>\n"
        "• Media\n"
        "• Media2\n"
        "• Media3\n\n"

        "📤 Existing files will be backed up.\n"
        "🔄 New files will also be detected automatically.\n"
        "♻️ Already backed-up files will not be uploaded again.\n\n"

        "📊 Use <code>/backup_status</code> "
        "to see the transfer status.",
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# ADMIN COMMAND: /backup_status
# ============================================================


def build_status_text():

    if backup_running:

        status = "🟢 RUNNING"

    else:

        status = "🔴 STOPPED"

    processed = get_processed_count()

    current_file = (
        backup_current_file
        or "Waiting for files..."
    )

    current_db = (
        backup_current_db
        or "-"
    )

    text = (
        "<b>╔════════════════════════════╗</b>\n"
        "<b>       🚀 BACKUP STATUS</b>\n"
        "<b>╚════════════════════════════╝</b>\n\n"

        f"📡 Status: <b>{status}</b>\n"
        f"📦 Channel: "
        f"<code>{get_channel_id() or 'NOT SET'}</code>\n\n"

        f"📚 Processed this run: "
        f"<b>{processed}</b>\n\n"

        f"✅ Uploaded: "
        f"<b>{backup_total_uploaded}</b>\n"

        f"⏭️ Already backed up: "
        f"<b>{backup_total_skipped}</b>\n"

        f"❌ Failed: "
        f"<b>{backup_total_failed}</b>\n\n"

        f"🗄️ Current DB: "
        f"<b>{current_db}</b>\n\n"

        f"📄 Current file:\n"
        f"<code>{str(current_file)[:600]}</code>\n\n"

        f"⏱️ Runtime: "
        f"<b>{get_runtime()}</b>\n"

        f"🕐 Last activity: "
        f"<b>{backup_last_activity or '-'}</b>\n"
    )

    if backup_last_error:

        text += (
            "\n⚠️ <b>Last Error:</b>\n"
            f"<code>{str(backup_last_error)[:800]}</code>\n"
        )

    return text


@Client.on_message(
    filters.command("backup_status")
    & filters.user(ADMINS)
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
                        callback_data="backup_refresh",
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
    filters.regex("^backup_refresh$")
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
                            callback_data="backup_refresh",
                        )
                    ]
                ]
            ),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )

        await query.answer(
            "🔄 Status refreshed."
        )

    except Exception as e:

        logger.exception(
            "[BACKUP] Status refresh error: %s",
            e,
        )

        await query.answer(
            "Unable to refresh.",
            show_alert=True,
        )


# ============================================================
# ADMIN COMMAND: /backup_stats
# ============================================================


@Client.on_message(
    filters.command("backup_stats")
    & filters.user(ADMINS)
)
async def backup_stats_command(
    app,
    message,
):

    try:

        completed = (
            await backup_collection.count_documents(
                {
                    "status": "completed"
                }
            )
        )

        failed = (
            await backup_collection.count_documents(
                {
                    "status": "failed"
                }
            )
        )

        text = (
            "<b>📊 BACKUP STATISTICS</b>\n\n"

            f"✅ Total successfully backed up: "
            f"<b>{completed}</b>\n"

            f"❌ Failed records: "
            f"<b>{failed}</b>\n\n"

            f"📤 Uploaded this run: "
            f"<b>{backup_total_uploaded}</b>\n"

            f"⏭️ Skipped this run: "
            f"<b>{backup_total_skipped}</b>\n"

            f"❌ Failed this run: "
            f"<b>{backup_total_failed}</b>\n"
        )

        await message.reply_text(
            text,
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception as e:

        await message.reply_text(
            "<b>❌ ERROR</b>\n\n"
            f"<code>{str(e)[:1000]}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# ============================================================
# ADMIN COMMAND: /backup_stop
# ============================================================


@Client.on_message(
    filters.command("backup_stop")
    & filters.user(ADMINS)
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
            "🛑 <b>BACKUP STOPPED</b>\n\n"
            "Successfully uploaded files remain "
            "marked as completed.\n\n"
            "You can restart it anytime with "
            "<code>/backup</code>.",
            parse_mode=enums.ParseMode.HTML,
        )

    else:

        await message.reply_text(
            "⚠️ Backup was already stopped.",
            parse_mode=enums.ParseMode.HTML,
        )


# ============================================================
# AUTO START
#
# IMPORTANT:
# This uses the REAL Pyrogram Client supplied by
# Pyrogram's on_start callback.
#
# Therefore we NEVER use the MongoDB client for Telegram.
# ============================================================


@Client.on_start()
async def backup_on_start(app):

    global pyrogram_app

    pyrogram_app = app

    logger.info(
        "[BACKUP] Pyrogram client received."
    )

    if not BACKUP_AUTO_START:

        logger.info(
            "[BACKUP] BACKUP_AUTO_START=false"
        )

        return

    if not backup_configured():

        logger.warning(
            "[BACKUP] Automatic backup disabled "
            "because BACKUP_CHANNEL_ID is missing."
        )

        return

    # Give the main bot a few seconds to finish startup.

    await asyncio.sleep(15)

    try:

        # Make sure channel is accessible.

        await app.get_chat(
            get_channel_id()
        )

    except Exception as e:

        logger.exception(
            "[BACKUP] Cannot access backup channel: %s",
            e,
        )

        return

    try:

        await start_backup_worker(
            app
        )

        logger.info(
            "[BACKUP] ========================================"
        )

        logger.info(
            "[BACKUP] AUTOMATIC BACKUP MONITOR STARTED"
        )

        logger.info(
            "[BACKUP] ========================================"
        )

    except Exception:

        logger.exception(
            "[BACKUP] Failed to start automatic backup."
        )


# ============================================================
# END OF BACKUP.PY
# ============================================================
