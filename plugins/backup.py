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
    client,
)

logger = logging.getLogger(__name__)

# ============================================================
# BACKUP CONFIGURATION
# ============================================================

BACKUP_CHANNEL_ID = os.environ.get("BACKUP_CHANNEL_ID")

# How often the worker checks MongoDB for new files.
BACKUP_CHECK_INTERVAL = int(
    os.environ.get("BACKUP_CHECK_INTERVAL", "30")
)

# Number of files processed in one database scan.
BACKUP_BATCH_SIZE = int(
    os.environ.get("BACKUP_BATCH_SIZE", "20")
)

# Small delay between uploads.
BACKUP_UPLOAD_DELAY = float(
    os.environ.get("BACKUP_UPLOAD_DELAY", "1.0")
)

# Automatically start backup when bot starts.
BACKUP_AUTO_START = os.environ.get(
    "BACKUP_AUTO_START",
    "true"
).lower() in ("true", "1", "yes", "on")

# ============================================================
# INTERNAL STATE
# ============================================================

backup_task = None
backup_running = False
backup_requested = False

backup_started_at = None
backup_finished_at = None

backup_total_scanned = 0
backup_total_uploaded = 0
backup_total_skipped = 0
backup_total_failed = 0

backup_current_file = None
backup_current_db = None

backup_last_error = None
backup_last_activity = None

backup_lock = asyncio.Lock()

# ============================================================
# DATABASE USED FOR BACKUP TRACKING
# ============================================================

# We create ONE separate collection only for tracking which
# files have already been successfully backed up.
#
# This does NOT modify Media / Media2 / Media3.
#
# Collection:
#     dreamx_backup_status
#
# Only successful backup records are stored here.

backup_collection = db["dreamx_backup_status"]

# ============================================================
# HELPERS
# ============================================================


def now_text():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


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
    ]

    for unit in units:
        if size < 1024:
            return f"{size:.2f} {unit}"

        size /= 1024

    return f"{size:.2f} PB"


def format_duration(seconds):
    if not seconds:
        return "0s"

    seconds = int(seconds)

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


def get_runtime():
    if not backup_started_at:
        return "Not started"

    elapsed = datetime.utcnow() - backup_started_at
    return format_duration(elapsed.total_seconds())


def get_backup_progress():
    processed = (
        backup_total_uploaded
        + backup_total_skipped
        + backup_total_failed
    )

    if backup_total_scanned <= 0:
        return 0

    return min(
        100,
        (processed / backup_total_scanned) * 100
    )


def is_configured():
    return bool(BACKUP_CHANNEL_ID)


def get_channel_id():
    if not BACKUP_CHANNEL_ID:
        return None

    try:
        return int(BACKUP_CHANNEL_ID)
    except Exception:
        return None


async def safe_edit(message, text, reply_markup=None):
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        pass


# ============================================================
# BACKUP TRACKING
# ============================================================


async def is_already_backed_up(file_id):
    try:
        result = await backup_collection.find_one(
            {
                "_id": file_id,
                "status": "completed",
            }
        )

        return result is not None

    except Exception:
        logger.exception(
            "Error checking backup status for %s",
            file_id,
        )
        return False


async def mark_backed_up(
    file_id,
    source_db,
    message_id=None,
):
    try:
        await backup_collection.update_one(
            {"_id": file_id},
            {
                "$set": {
                    "status": "completed",
                    "source_db": source_db,
                    "backup_channel": get_channel_id(),
                    "backup_message_id": message_id,
                    "backed_up_at": datetime.utcnow(),
                }
            },
            upsert=True,
        )

    except Exception:
        logger.exception(
            "Failed to save backup tracking for %s",
            file_id,
        )


async def mark_failed(file_id, error):
    try:
        await backup_collection.update_one(
            {"_id": file_id},
            {
                "$set": {
                    "status": "failed",
                    "error": str(error),
                    "last_failed_at": datetime.utcnow(),
                }
            },
            upsert=True,
        )

    except Exception:
        logger.exception(
            "Failed to save failed backup status",
        )


# ============================================================
# GET FILES FROM ALL THREE DATABASES
# ============================================================


async def get_next_files():
    """
    Returns files from:

        Media
        Media2
        Media3

    Only files which have not successfully been backed up
    are returned.
    """

    results = []

    databases = [
        ("Media", Media),
        ("Media2", Media2),
        ("Media3", Media3),
    ]

    for source_name, model in databases:

        try:
            cursor = (
                model.find(
                    {
                        "_id": {
                            "$nin": await get_completed_ids()
                        }
                    }
                )
                .sort("$natural", 1)
                .limit(BACKUP_BATCH_SIZE)
            )

            files = await cursor.to_list(
                length=BACKUP_BATCH_SIZE
            )

            for file in files:
                results.append(
                    (
                        source_name,
                        file,
                    )
                )

                if len(results) >= BACKUP_BATCH_SIZE:
                    return results

        except Exception:
            logger.exception(
                "Error reading %s",
                source_name,
            )

    return results


async def get_completed_ids():
    """
    Gets already completed IDs.

    This is intentionally limited so the worker doesn't load
    an unlimited amount of data into RAM.
    """

    try:
        cursor = backup_collection.find(
            {"status": "completed"},
            {"_id": 1},
        ).limit(50000)

        docs = await cursor.to_list(length=50000)

        return [
            doc["_id"]
            for doc in docs
            if "_id" in doc
        ]

    except Exception:
        logger.exception(
            "Error loading backup IDs"
        )
        return []


# ============================================================
# FILE CAPTION
# ============================================================


def get_file_caption(file):
    """
    Preserve the caption already stored in MongoDB.

    If no caption exists, use the original filename.
    """

    caption = getattr(file, "caption", None)
    file_name = getattr(file, "file_name", None)

    if caption:
        return caption

    if file_name:
        return f"<code>{file_name}</code>"

    return None


# ============================================================
# SEND ONE FILE
# ============================================================


async def backup_single_file(
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

    channel_id = get_channel_id()

    if not channel_id:
        raise RuntimeError(
            "BACKUP_CHANNEL_ID is not configured."
        )

    file_id = getattr(file, "file_id", None)

    if not file_id:
        file_id = getattr(file, "_id", None)

    if not file_id:
        backup_total_failed += 1
        return False

    file_name = getattr(
        file,
        "file_name",
        "Unknown file",
    )

    backup_current_file = file_name
    backup_current_db = source_db
    backup_last_activity = now_text()

    # --------------------------------------------------------
    # Check duplicate
    # --------------------------------------------------------

    if await is_already_backed_up(file_id):
        backup_total_skipped += 1
        return True

    # --------------------------------------------------------
    # Caption
    # --------------------------------------------------------

    caption = get_file_caption(file)

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

                sent = await client.send_cached_media(
                    chat_id=channel_id,
                    file_id=file_id,
                    caption=caption,
                )

                break

            except FloodWait as e:

                wait_time = int(e.value)

                logger.warning(
                    "[BACKUP] FloodWait: sleeping %s seconds",
                    wait_time,
                )

                await asyncio.sleep(
                    wait_time + 2
                )

            except RPCError as e:

                logger.exception(
                    "[BACKUP] Telegram RPC error: %s",
                    e,
                )

                backup_total_failed += 1
                backup_last_error = str(e)

                await mark_failed(
                    file_id,
                    e,
                )

                return False

            except Exception as e:

                logger.exception(
                    "[BACKUP] Upload failed: %s",
                    e,
                )

                backup_total_failed += 1
                backup_last_error = str(e)

                await mark_failed(
                    file_id,
                    e,
                )

                return False

        # ----------------------------------------------------
        # Mark successful
        # ----------------------------------------------------

        await mark_backed_up(
            file_id=file_id,
            source_db=source_db,
            message_id=getattr(
                sent,
                "id",
                None,
            ),
        )

        backup_total_uploaded += 1
        backup_last_activity = now_text()

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
            "[BACKUP] Unexpected error: %s",
            e,
        )

        await mark_failed(
            file_id,
            e,
        )

        return False


# ============================================================
# MAIN BACKUP LOOP
# ============================================================


async def backup_worker():
    global backup_running
    global backup_started_at
    global backup_finished_at
    global backup_current_file
    global backup_current_db
    global backup_last_error
    global backup_last_activity

    if not is_configured():

        logger.warning(
            "[BACKUP] BACKUP_CHANNEL_ID is not configured."
        )

        return

    if backup_running:
        logger.info(
            "[BACKUP] Worker already running."
        )
        return

    async with backup_lock:

        if backup_running:
            return

        backup_running = True
        backup_started_at = datetime.utcnow()
        backup_finished_at = None
        backup_last_error = None
        backup_last_activity = now_text()

        logger.info(
            "=================================================="
        )

        logger.info(
            "[BACKUP] BACKUP WORKER STARTED"
        )

        logger.info(
            "[BACKUP] Channel: %s",
            get_channel_id(),
        )

        logger.info(
            "[BACKUP] Automatic monitoring enabled."
        )

        logger.info(
            "=================================================="
        )

        try:

            while True:

                files = await get_next_files()

                # ------------------------------------------------
                # No files currently waiting.
                #
                # DO NOT STOP.
                #
                # Keep checking so newly added files are backed up.
                # ------------------------------------------------

                if not files:

                    backup_current_file = None
                    backup_current_db = None

                    backup_last_activity = now_text()

                    await asyncio.sleep(
                        BACKUP_CHECK_INTERVAL
                    )

                    continue

                # ------------------------------------------------
                # Process files
                # ------------------------------------------------

                for source_db, file in files:

                    await backup_single_file(
                        source_db,
                        file,
                    )

                    await asyncio.sleep(
                        0.2
                    )

        except asyncio.CancelledError:

            logger.info(
                "[BACKUP] Worker cancelled."
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
            backup_finished_at = datetime.utcnow()

            backup_current_file = None
            backup_current_db = None

            logger.info(
                "[BACKUP] Worker stopped."
            )


# ============================================================
# START WORKER
# ============================================================


async def start_backup_worker():
    global backup_task

    if not is_configured():

        logger.warning(
            "[BACKUP] Cannot start."
        )

        logger.warning(
            "[BACKUP] Set BACKUP_CHANNEL_ID in Render."
        )

        return

    if backup_task:

        if not backup_task.done():

            return

    backup_task = asyncio.create_task(
        backup_worker()
    )

    logger.info(
        "[BACKUP] Background task created."
    )


# ============================================================
# BOT STARTUP
# ============================================================


@Client.on_message(
    filters.command("backup")
    & filters.user([])
)
async def backup_command(bot, message):
    """
    This handler is replaced below with an admin-aware
    command because ADMINS is loaded separately.
    """
    pass


# ============================================================
# ADMIN COMMANDS
# ============================================================

try:

    from info import ADMINS

except Exception:

    ADMINS = []


@Client.on_message(
    filters.command("backup")
    & filters.user(ADMINS)
)
async def backup_start_command(bot, message):

    global backup_task

    if not is_configured():

        return await message.reply_text(
            "<b>❌ BACKUP CHANNEL IS NOT CONFIGURED</b>\n\n"
            "Add this Render environment variable:\n\n"
            "<code>BACKUP_CHANNEL_ID=-100xxxxxxxxxx</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    channel_id = get_channel_id()

    try:

        chat = await bot.get_chat(
            channel_id
        )

        channel_name = (
            chat.title
            or "Backup Channel"
        )

    except Exception as e:

        return await message.reply_text(
            "<b>❌ BACKUP CHANNEL ERROR</b>\n\n"
            f"<code>{e}</code>\n\n"
            "Make sure the bot is an admin in the "
            "backup channel.",
            parse_mode=enums.ParseMode.HTML,
        )

    if backup_running:

        return await message.reply_text(
            "<b>🚀 BACKUP IS ALREADY RUNNING</b>\n\n"
            f"📦 Channel: <b>{channel_name}</b>\n"
            f"🆔 <code>{channel_id}</code>\n\n"
            "Use <code>/backup_status</code> "
            "to see the live transfer status.",
            parse_mode=enums.ParseMode.HTML,
        )

    await start_backup_worker()

    await message.reply_text(
        "<b>🚀 BACKUP STARTED</b>\n\n"
        f"📦 <b>{channel_name}</b>\n"
        f"🆔 <code>{channel_id}</code>\n\n"
        "📚 Scanning Media DB...\n"
        "📚 Scanning Media2 DB...\n"
        "📚 Scanning Media3 DB...\n\n"
        "♻️ The worker will continue running "
        "automatically and will also pick up "
        "new files added later.\n\n"
        "Use <code>/backup_status</code> "
        "for live status.",
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# STATUS
# ============================================================


@Client.on_message(
    filters.command("backup_status")
    & filters.user(ADMINS)
)
async def backup_status_command(bot, message):

    if not is_configured():

        return await message.reply_text(
            "<b>❌ BACKUP NOT CONFIGURED</b>\n\n"
            "Render variable missing:\n"
            "<code>BACKUP_CHANNEL_ID</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    status = (
        "🟢 RUNNING"
        if backup_running
        else "🔴 NOT RUNNING"
    )

    progress = get_backup_progress()

    bar_length = 20

    filled = int(
        (progress / 100)
        * bar_length
    )

    progress_bar = (
        "█" * filled
        + "░" * (bar_length - filled)
    )

    processed = (
        backup_total_uploaded
        + backup_total_skipped
        + backup_total_failed
    )

    current_file = (
        backup_current_file
        if backup_current_file
        else "Waiting for files..."
    )

    current_db = (
        backup_current_db
        if backup_current_db
        else "-"
    )

    text = (
        "<b>╔══════════════════════════╗</b>\n"
        "<b>      🚀 BACKUP STATUS</b>\n"
        "<b>╚══════════════════════════╝</b>\n\n"

        f"📡 Status: <b>{status}</b>\n"
        f"📦 Channel: <code>{get_channel_id()}</code>\n\n"

        f"📊 Progress: <b>{progress:.2f}%</b>\n"
        f"<code>[{progress_bar}]</code>\n\n"

        f"📚 Processed: <b>{processed}</b>\n"
        f"✅ Uploaded: <b>{backup_total_uploaded}</b>\n"
        f"⏭️ Skipped: <b>{backup_total_skipped}</b>\n"
        f"❌ Failed: <b>{backup_total_failed}</b>\n\n"

        f"🗄️ Current DB: <b>{current_db}</b>\n"
        f"📄 Current file:\n"
        f"<code>{current_file[:500]}</code>\n\n"

        f"⏱️ Runtime: <b>{get_runtime()}</b>\n"
        f"🕐 Last activity: <b>{backup_last_activity or '-'}</b>\n"
    )

    if backup_last_error:

        text += (
            "\n⚠️ <b>Last Error:</b>\n"
            f"<code>{str(backup_last_error)[:500]}</code>\n"
        )

    buttons = [
        [
            InlineKeyboardButton(
                "🔄 REFRESH",
                callback_data="backup_status_refresh",
            )
        ],
    ]

    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons
        ),
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ============================================================
# STATUS REFRESH BUTTON
# ============================================================


@Client.on_callback_query(
    filters.regex("^backup_status_refresh$")
)
async def backup_status_refresh(bot, query):

    try:

        if query.from_user.id not in ADMINS:

            return await query.answer(
                "❌ Admin only.",
                show_alert=True,
            )

        if not is_configured():

            return await query.answer(
                "Backup channel is not configured.",
                show_alert=True,
            )

        status = (
            "🟢 RUNNING"
            if backup_running
            else "🔴 NOT RUNNING"
        )

        progress = get_backup_progress()

        bar_length = 20

        filled = int(
            (progress / 100)
            * bar_length
        )

        progress_bar = (
            "█" * filled
            + "░" * (bar_length - filled)
        )

        processed = (
            backup_total_uploaded
            + backup_total_skipped
            + backup_total_failed
        )

        current_file = (
            backup_current_file
            if backup_current_file
            else "Waiting for files..."
        )

        current_db = (
            backup_current_db
            if backup_current_db
            else "-"
        )

        text = (
            "<b>╔══════════════════════════╗</b>\n"
            "<b>      🚀 BACKUP STATUS</b>\n"
            "<b>╚══════════════════════════╝</b>\n\n"

            f"📡 Status: <b>{status}</b>\n"
            f"📦 Channel: <code>{get_channel_id()}</code>\n\n"

            f"📊 Progress: <b>{progress:.2f}%</b>\n"
            f"<code>[{progress_bar}]</code>\n\n"

            f"📚 Processed: <b>{processed}</b>\n"
            f"✅ Uploaded: <b>{backup_total_uploaded}</b>\n"
            f"⏭️ Skipped: <b>{backup_total_skipped}</b>\n"
            f"❌ Failed: <b>{backup_total_failed}</b>\n\n"

            f"🗄️ Current DB: <b>{current_db}</b>\n"
            f"📄 Current file:\n"
            f"<code>{current_file[:500]}</code>\n\n"

            f"⏱️ Runtime: <b>{get_runtime()}</b>\n"
            f"🕐 Last activity: <b>{backup_last_activity or '-'}</b>\n"
        )

        if backup_last_error:

            text += (
                "\n⚠️ <b>Last Error:</b>\n"
                f"<code>{str(backup_last_error)[:500]}</code>\n"
            )

        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 REFRESH",
                            callback_data="backup_status_refresh",
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
            "Status refresh failed: %s",
            e,
        )

        await query.answer(
            "Refresh failed.",
            show_alert=True,
        )


# ============================================================
# STOP BACKUP
# ============================================================


@Client.on_message(
    filters.command("backup_stop")
    & filters.user(ADMINS)
)
async def backup_stop_command(bot, message):

    global backup_task

    if not backup_task:

        return await message.reply_text(
            "🔴 <b>BACKUP IS NOT RUNNING.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    if backup_task.done():

        return await message.reply_text(
            "🔴 <b>BACKUP IS NOT RUNNING.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    backup_task.cancel()

    await message.reply_text(
        "🛑 <b>BACKUP STOP REQUESTED</b>\n\n"
        "The worker has been stopped.\n"
        "Your successfully uploaded files remain "
        "marked as completed.",
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# BACKUP STATISTICS
# ============================================================


@Client.on_message(
    filters.command("backup_stats")
    & filters.user(ADMINS)
)
async def backup_stats_command(bot, message):

    try:

        completed = await backup_collection.count_documents(
            {
                "status": "completed"
            }
        )

        failed = await backup_collection.count_documents(
            {
                "status": "failed"
            }
        )

        text = (
            "<b>📊 BACKUP DATABASE STATISTICS</b>\n\n"
            f"✅ Successfully backed up: <b>{completed}</b>\n"
            f"❌ Failed records: <b>{failed}</b>\n\n"
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
            f"<b>❌ ERROR</b>\n\n"
            f"<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# ============================================================
# AUTO START
# ============================================================


async def _auto_backup_start():

    # Give the rest of the bot a little time to initialize.
    await asyncio.sleep(15)

    if not BACKUP_AUTO_START:

        logger.info(
            "[BACKUP] Automatic startup disabled."
        )

        return

    if not is_configured():

        logger.warning(
            "[BACKUP] Automatic startup skipped."
        )

        logger.warning(
            "[BACKUP] Set BACKUP_CHANNEL_ID in Render."
        )

        return

    try:

        await start_backup_worker()

        logger.info(
            "[BACKUP] Automatic backup monitoring started."
        )

    except Exception:

        logger.exception(
            "[BACKUP] Failed to auto-start backup."
        )


# ============================================================
# START AUTO WORKER WHEN PLUGIN LOADS
# ============================================================

try:

    asyncio.get_running_loop()

    asyncio.create_task(
        _auto_backup_start()
    )

except RuntimeError:

    # If the plugin is imported before the event loop is
    # available, the manual /backup command can start it.
    logger.info(
        "[BACKUP] Event loop not ready during import."
    )

# ============================================================
# END
# ============================================================
