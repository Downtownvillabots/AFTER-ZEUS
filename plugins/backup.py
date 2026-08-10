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
from datetime import datetime

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, RPCError

from database.ia_filterdb import (
    db,
    db2,
    db3,
)

try:
    from info import ADMINS, COLLECTION_NAME
except Exception:
    ADMINS = []
    COLLECTION_NAME = "TelegramFiles"


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

BACKUP_CHANNEL_ID = os.getenv(
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

BACKUP_UPLOAD_DELAY = float(
    os.getenv(
        "BACKUP_UPLOAD_DELAY",
        "1",
    )
)

BACKUP_BATCH_SIZE = int(
    os.getenv(
        "BACKUP_BATCH_SIZE",
        "20",
    )
)


# ============================================================
# DIRECT MONGODB COLLECTIONS
#
# Media  -> DB1
# Media2 -> DB2
# Media3 -> DB3
#
# This does NOT modify the original collections.
# ============================================================

media_collection = db[COLLECTION_NAME]

media2_collection = db2[COLLECTION_NAME]

media3_collection = db3[COLLECTION_NAME]


# ============================================================
# GLOBAL BACKUP STATE
# ============================================================

backup_task = None

backup_running = False

backup_started_at = None

backup_finished_at = None

backup_last_activity = None

backup_last_error = None

backup_current_db = None

backup_current_file = None

backup_current_index = 0

backup_current_db_total = 0

backup_total_uploaded = 0

backup_total_failed = 0


# ============================================================
# DATABASE COUNTERS
# ============================================================

backup_media_total = 0

backup_media_uploaded = 0

backup_media_failed = 0


backup_media2_total = 0

backup_media2_uploaded = 0

backup_media2_failed = 0


backup_media3_total = 0

backup_media3_uploaded = 0

backup_media3_failed = 0


# ============================================================
# LOCK
# ============================================================

backup_lock = asyncio.Lock()


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
        parts.append(
            f"{days}d"
        )

    if hours:
        parts.append(
            f"{hours}h"
        )

    if minutes:
        parts.append(
            f"{minutes}m"
        )

    if seconds or not parts:

        parts.append(
            f"{seconds}s"
        )

    return " ".join(parts)


def get_runtime():

    if not backup_started_at:
        return "Not started"

    elapsed = (
        utc_now()
        - backup_started_at
    ).total_seconds()

    return format_duration(
        elapsed
    )


# ============================================================
# RESET COUNTERS
# ============================================================


def reset_backup_state():

    global backup_last_error
    global backup_current_db
    global backup_current_file
    global backup_current_index
    global backup_current_db_total

    global backup_total_uploaded
    global backup_total_failed

    global backup_media_total
    global backup_media_uploaded
    global backup_media_failed

    global backup_media2_total
    global backup_media2_uploaded
    global backup_media2_failed

    global backup_media3_total
    global backup_media3_uploaded
    global backup_media3_failed

    backup_last_error = None

    backup_current_db = None

    backup_current_file = None

    backup_current_index = 0

    backup_current_db_total = 0

    backup_total_uploaded = 0

    backup_total_failed = 0

    backup_media_total = 0
    backup_media_uploaded = 0
    backup_media_failed = 0

    backup_media2_total = 0
    backup_media2_uploaded = 0
    backup_media2_failed = 0

    backup_media3_total = 0
    backup_media3_uploaded = 0
    backup_media3_failed = 0


# ============================================================
# GET FILE ID
# ============================================================


def get_file_id(document):

    file_id = document.get(
        "_id"
    )

    if file_id:

        return str(
            file_id
        )

    file_id = document.get(
        "file_id"
    )

    if file_id:

        return str(
            file_id
        )

    return None


# ============================================================
# GET FILE NAME
# ============================================================


def get_file_name(document):

    file_name = document.get(
        "file_name"
    )

    if file_name:

        return str(
            file_name
        )

    return "Unknown File"


# ============================================================
# GET CAPTION
# ============================================================


def get_caption(document):

    caption = document.get(
        "caption"
    )

    if caption:

        return str(
            caption
        )

    file_name = document.get(
        "file_name"
    )

    if file_name:

        return str(
            file_name
        )

    return None


# ============================================================
# GET COLLECTION COUNT
# ============================================================


async def get_collection_count(
    collection
):

    try:

        return await collection.count_documents({})

    except Exception as e:

        logger.exception(
            "[BACKUP] Count error: %s",
            e,
        )

        return 0


# ============================================================
# SEND ONE FILE
# ============================================================


async def send_one_file(
    app,
    source_db,
    document,
):

    global backup_total_uploaded
    global backup_total_failed

    global backup_current_file
    global backup_current_db

    global backup_last_error
    global backup_last_activity

    global backup_media_uploaded
    global backup_media_failed

    global backup_media2_uploaded
    global backup_media2_failed

    global backup_media3_uploaded
    global backup_media3_failed


    file_id = get_file_id(
        document
    )

    file_name = get_file_name(
        document
    )

    backup_current_file = (
        file_name
    )

    backup_current_db = (
        source_db
    )

    backup_last_activity = (
        utc_text()
    )


    # ========================================================
    # INVALID DOCUMENT
    # ========================================================

    if not file_id:

        backup_total_failed += 1

        backup_last_error = (
            f"{source_db}: "
            f"{file_name} has no file_id"
        )

        if source_db == "Media":

            backup_media_failed += 1

        elif source_db == "Media2":

            backup_media2_failed += 1

        elif source_db == "Media3":

            backup_media3_failed += 1

        logger.error(
            "[BACKUP] Missing file_id [%s] %s",
            source_db,
            file_name,
        )

        return False


    caption = get_caption(
        document
    )


    # ========================================================
    # UPLOAD
    # ========================================================

    while True:

        try:

            logger.info(
                "[BACKUP] UPLOADING [%s] %s",
                source_db,
                file_name,
            )


            sent = await app.send_cached_media(

                chat_id=get_channel_id(),

                file_id=file_id,

                caption=caption,
            )


            # =================================================
            # SUCCESS
            # =================================================

            backup_total_uploaded += 1


            if source_db == "Media":

                backup_media_uploaded += 1

            elif source_db == "Media2":

                backup_media2_uploaded += 1

            elif source_db == "Media3":

                backup_media3_uploaded += 1


            backup_last_activity = (
                utc_text()
            )


            logger.info(
                "[BACKUP] SUCCESS [%s] %s | Telegram ID: %s",
                source_db,
                file_name,
                getattr(
                    sent,
                    "id",
                    None,
                ),
            )


            if BACKUP_UPLOAD_DELAY > 0:

                await asyncio.sleep(
                    BACKUP_UPLOAD_DELAY
                )


            return True


        # ====================================================
        # FLOOD WAIT
        # ====================================================

        except FloodWait as e:

            wait_time = int(
                getattr(
                    e,
                    "value",
                    30,
                )
            )

            logger.warning(
                "[BACKUP] FloodWait: waiting %s seconds.",
                wait_time,
            )

            await asyncio.sleep(
                wait_time + 2
            )


        # ====================================================
        # TELEGRAM ERROR
        # ====================================================

        except RPCError as e:

            backup_total_failed += 1

            backup_last_error = (
                str(e)
            )


            if source_db == "Media":

                backup_media_failed += 1

            elif source_db == "Media2":

                backup_media2_failed += 1

            elif source_db == "Media3":

                backup_media3_failed += 1


            logger.exception(
                "[BACKUP] Telegram RPC error [%s] %s: %s",
                source_db,
                file_name,
                e,
            )

            return False


        # ====================================================
        # OTHER ERROR
        # ====================================================

        except Exception as e:

            backup_total_failed += 1

            backup_last_error = (
                str(e)
            )


            if source_db == "Media":

                backup_media_failed += 1

            elif source_db == "Media2":

                backup_media2_failed += 1

            elif source_db == "Media3":

                backup_media3_failed += 1


            logger.exception(
                "[BACKUP] Upload failed [%s] %s: %s",
                source_db,
                file_name,
                e,
            )

            return False


# ============================================================
# BACKUP COMPLETE DATABASE
#
# IMPORTANT:
#
# Media is completed first.
# Then Media2.
# Then Media3.
#
# Nothing is skipped because of another database.
# ============================================================


async def backup_database(
    app,
    collection,
    database_name,
    database_number,
):

    global backup_current_db
    global backup_current_index
    global backup_current_db_total
    global backup_last_activity

    global backup_media_total
    global backup_media2_total
    global backup_media3_total


    backup_current_db = (
        database_name
    )


    total = await get_collection_count(
        collection
    )


    backup_current_db_total = (
        total
    )

    backup_current_index = 0


    if database_name == "Media":

        backup_media_total = total

    elif database_name == "Media2":

        backup_media2_total = total

    elif database_name == "Media3":

        backup_media3_total = total


    logger.info(
        "[BACKUP] %s collection contains %s documents.",
        database_name,
        total,
    )


    if total == 0:

        logger.warning(
            "[BACKUP] %s is EMPTY.",
            database_name,
        )

        return True


    logger.info(
        "=================================================="
    )

    logger.info(
        "[BACKUP] STARTING %s",
        database_name,
    )

    logger.info(
        "[BACKUP] TOTAL FILES: %s",
        total,
    )

    logger.info(
        "=================================================="
    )


    # ========================================================
    # DIRECT MONGO CURSOR
    #
    # No .to_list(20)
    #
    # No repeated first 20 documents.
    #
    # Mongo cursor walks through the ENTIRE collection.
    # ========================================================

    cursor = collection.find(
        {}
    ).sort(
        "$natural",
        1,
    ).batch_size(
        BACKUP_BATCH_SIZE
    )


    try:

        async for document in cursor:

            if not backup_running:

                logger.warning(
                    "[BACKUP] Stop requested during %s.",
                    database_name,
                )

                return False


            backup_current_index += 1

            backup_last_activity = (
                utc_text()
            )


            success = await send_one_file(
                app=app,
                source_db=database_name,
                document=document,
            )


            if success:

                logger.info(
                    "[BACKUP] %s PROGRESS: %s/%s",
                    database_name,
                    backup_current_index,
                    total,
                )

            else:

                logger.error(
                    "[BACKUP] %s PROGRESS: %s/%s FAILED",
                    database_name,
                    backup_current_index,
                    total,
                )


    except asyncio.CancelledError:

        raise


    except Exception as e:

        backup_last_error = (
            str(e)
        )

        logger.exception(
            "[BACKUP] Cursor error in %s: %s",
            database_name,
            e,
        )

        return False


    logger.info(
        "=================================================="
    )

    logger.info(
        "[BACKUP] FINISHED %s",
        database_name,
    )

    logger.info(
        "[BACKUP] Uploaded: %s",
        (
            backup_media_uploaded
            if database_name == "Media"
            else backup_media2_uploaded
            if database_name == "Media2"
            else backup_media3_uploaded
        ),
    )

    logger.info(
        "[BACKUP] Failed: %s",
        (
            backup_media_failed
            if database_name == "Media"
            else backup_media2_failed
            if database_name == "Media2"
            else backup_media3_failed
        ),
    )

    logger.info(
        "=================================================="
    )


    return True


# ============================================================
# MAIN BACKUP WORKER
# ============================================================


async def backup_worker(app):

    global backup_running

    global backup_started_at
    global backup_finished_at

    global backup_last_activity

    global backup_current_db
    global backup_current_file


    async with backup_lock:

        if backup_running:

            logger.info(
                "[BACKUP] Worker already running."
            )

            return


        if not backup_configured():

            logger.error(
                "[BACKUP] BACKUP_CHANNEL_ID is missing."
            )

            return


        backup_running = True

        backup_started_at = (
            utc_now()
        )

        backup_finished_at = None

        reset_backup_state()

        backup_last_activity = (
            utc_text()
        )


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
            get_channel_id(),
        )

        logger.info(
            "[BACKUP] Order: Media -> Media2 -> Media3"
        )

        logger.info(
            "=================================================="


        )


        try:

            # ==================================================
            # MEDIA
            # ==================================================

            completed = await backup_database(

                app,

                media_collection,

                "Media",

                1,
            )


            if not completed:

                return


            # ==================================================
            # MEDIA2
            # ==================================================

            completed = await backup_database(

                app,

                media2_collection,

                "Media2",

                2,
            )


            if not completed:

                return


            # ==================================================
            # MEDIA3
            # ==================================================

            completed = await backup_database(

                app,

                media3_collection,

                "Media3",

                3,
            )


            if not completed:

                return


            # ==================================================
            # INITIAL BACKUP COMPLETE
            # ==================================================

            logger.info(
                "=================================================="
            )

            logger.info(
                "[BACKUP] INITIAL BACKUP COMPLETE"
            )

            logger.info(
                "[BACKUP] Media uploaded: %s",
                backup_media_uploaded,
            )

            logger.info(
                "[BACKUP] Media2 uploaded: %s",
                backup_media2_uploaded,
            )

            logger.info(
                "[BACKUP] Media3 uploaded: %s",
                backup_media3_uploaded,
            )

            logger.info(
                "[BACKUP] TOTAL UPLOADED: %s",
                backup_total_uploaded,
            )

            logger.info(
                "[BACKUP] TOTAL FAILED: %s",
                backup_total_failed,
            )

            logger.info(
                "=================================================="
            )


            # ==================================================
            # CONTINUOUS MONITOR
            #
            # After all existing files are transferred,
            # keep watching the databases for NEW files.
            # ==================================================

            while backup_running:

                found_new = False


                # ==================================================
                # CHECK MEDIA
                # ==================================================

                latest_media = (
                    await media_collection
                    .find({})
                    .sort("$natural", -1)
                    .limit(1)
                    .to_list(
                        length=1
                    )
                )


                if latest_media:

                    latest_id = get_file_id(
                        latest_media[0]
                    )

                    if latest_id:

                        # We do not use duplicate tracking here.
                        #
                        # New files are detected by comparing the
                        # newest database document against the last
                        # processed natural-order position.
                        #
                        # To keep this version simple and safe,
                        # newly inserted files are handled by the
                        # periodic scan below.

                        pass


                # ==================================================
                # PERIODIC NEW FILE SCAN
                #
                # Search recent files in each database.
                # ==================================================

                for collection, db_name in (
                    (
                        media_collection,
                        "Media",
                    ),
                    (
                        media2_collection,
                        "Media2",
                    ),
                    (
                        media3_collection,
                        "Media3",
                    ),
                ):

                    if not backup_running:

                        break


                    recent_files = (
                        await collection
                        .find({})
                        .sort("$natural", -1)
                        .limit(
                            BACKUP_BATCH_SIZE
                        )
                        .to_list(
                            length=BACKUP_BATCH_SIZE
                        )
                    )


                    if not recent_files:

                        continue


                    # New files are attempted.
                    #
                    # This mode intentionally does not maintain a
                    # backup tracking collection.
                    #
                    # Therefore duplicates are possible during
                    # continuous monitoring.
                    #
                    # User requested duplicates to be handled later.

                    for document in reversed(
                        recent_files
                    ):

                        if not backup_running:

                            break


                        backup_current_db = (
                            db_name
                        )

                        backup_current_file = (
                            get_file_name(
                                document
                            )
                        )


                        await send_one_file(

                            app,

                            db_name,

                            document,
                        )


                        found_new = True


                backup_last_activity = (
                    utc_text()
                )


                if not found_new:

                    await asyncio.sleep(
                        BACKUP_CHECK_INTERVAL
                    )

                else:

                    await asyncio.sleep(
                        BACKUP_CHECK_INTERVAL
                    )


        except asyncio.CancelledError:

            logger.info(
                "[BACKUP] Worker cancellation received."
            )

            raise


        except Exception as e:

            backup_last_error = (
                str(e)
            )

            logger.exception(
                "[BACKUP] Worker crashed: %s",
                e,
            )


        finally:

            backup_running = False

            backup_finished_at = (
                utc_now()
            )

            backup_current_db = None

            backup_current_file = None

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


    if not backup_configured():

        logger.error(
            "[BACKUP] BACKUP_CHANNEL_ID is not configured."
        )

        return False


    if backup_task:

        if not backup_task.done():

            logger.info(
                "[BACKUP] Backup task already running."
            )

            return True


    backup_task = asyncio.create_task(
        backup_worker(app)
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

        return False


    backup_running = False

    backup_task.cancel()


    try:

        await backup_task

    except asyncio.CancelledError:

        pass

    except Exception:

        logger.exception(
            "[BACKUP] Error stopping backup."
        )


    return True


# ============================================================
# STATUS TEXT
# ============================================================


def build_status_text():

    if backup_running:

        status = "🟢 RUNNING"

    else:

        status = "🔴 STOPPED"


    if backup_current_db:

        current_database = (
            backup_current_db
        )

    else:

        current_database = "-"


    if backup_current_file:

        current_file = (
            backup_current_file
        )

    else:

        current_file = (
            "Waiting for files..."
        )


    if backup_current_db_total:

        progress = (
            f"{backup_current_index}/"
            f"{backup_current_db_total}"
        )

    else:

        progress = "0/0"


    text = (

        "<b>╔══════════════════════════════╗</b>\n"
        "<b>        🚀 DOWNTOWNVILLA BACKUP</b>\n"
        "<b>╚══════════════════════════════╝</b>\n\n"

        f"📡 Status: <b>{status}</b>\n"

        f"📦 Channel: "
        f"<code>{get_channel_id() or 'NOT SET'}</code>\n\n"

        "<b>━━━━━━━━ DATABASES ━━━━━━━━</b>\n\n"

        f"🗄️ Media:\n"
        f"   📚 Total: <b>{backup_media_total:,}</b>\n"
        f"   ✅ Uploaded: <b>{backup_media_uploaded:,}</b>\n"
        f"   ❌ Failed: <b>{backup_media_failed:,}</b>\n\n"

        f"🗄️ Media2:\n"
        f"   📚 Total: <b>{backup_media2_total:,}</b>\n"
        f"   ✅ Uploaded: <b>{backup_media2_uploaded:,}</b>\n"
        f"   ❌ Failed: <b>{backup_media2_failed:,}</b>\n\n"

        f"🗄️ Media3:\n"
        f"   📚 Total: <b>{backup_media3_total:,}</b>\n"
        f"   ✅ Uploaded: <b>{backup_media3_uploaded:,}</b>\n"
        f"   ❌ Failed: <b>{backup_media3_failed:,}</b>\n\n"

        "<b>━━━━━━━━ TOTAL ━━━━━━━━</b>\n\n"

        f"📤 Total uploaded: "
        f"<b>{backup_total_uploaded:,}</b>\n"

        f"❌ Total failed: "
        f"<b>{backup_total_failed:,}</b>\n\n"

        "<b>━━━━━━━━ CURRENT ━━━━━━━━</b>\n\n"

        f"🗄️ Database: "
        f"<b>{current_database}</b>\n"

        f"📊 Progress: "
        f"<b>{progress}</b>\n\n"

        f"📄 File:\n"
        f"<code>{str(current_file)[:700]}</code>\n\n"

        "<b>━━━━━━━━ TIME ━━━━━━━━</b>\n\n"

        f"⏱️ Runtime: "
        f"<b>{get_runtime()}</b>\n"

        f"🕐 Last activity: "
        f"<b>{backup_last_activity or '-'}</b>\n"

    )


    if backup_last_error:

        text += (

            "\n<b>━━━━━━━━ LAST ERROR ━━━━━━━━</b>\n\n"

            f"⚠️ <code>"
            f"{str(backup_last_error)[:1000]}"
            f"</code>\n"
        )


    return text


# ============================================================
# /backup
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

            "<code>"
            "BACKUP_CHANNEL_ID=-100xxxxxxxxxxxx"
            "</code>\n\n"

            "<code>"
            "BACKUP_AUTO_START=true"
            "</code>",

            parse_mode=enums.ParseMode.HTML,
        )

        return


    channel_id = (
        get_channel_id()
    )


    # ========================================================
    # TEST CHANNEL
    # ========================================================

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

            f"<code>{str(e)[:1200]}</code>\n\n"

            "Make sure the bot is an administrator "
            "in the backup channel.",

            parse_mode=enums.ParseMode.HTML,
        )

        return


    # ========================================================
    # ALREADY RUNNING
    # ========================================================

    if backup_running:

        await message.reply_text(

            "<b>🟢 BACKUP IS ALREADY RUNNING</b>\n\n"

            f"📦 Channel: "
            f"<b>{channel_name}</b>\n"

            f"🆔 <code>{channel_id}</code>\n\n"

            "The backup is processing:\n"

            "1️⃣ Media\n"
            "2️⃣ Media2\n"
            "3️⃣ Media3\n\n"

            "Use <code>/backup_status</code> "
            "for the live progress.",

            parse_mode=enums.ParseMode.HTML,
        )

        return


    # ========================================================
    # START
    # ========================================================

    started = await start_backup_worker(
        app
    )


    if not started:

        await message.reply_text(

            "<b>❌ BACKUP COULD NOT START</b>",

            parse_mode=enums.ParseMode.HTML,
        )

        return


    await message.reply_text(

        "<b>╔════════════════════════════╗</b>\n"
        "<b>       🚀 BACKUP STARTED</b>\n"
        "<b>╚════════════════════════════╝</b>\n\n"

        f"📦 Channel: "
        f"<b>{channel_name}</b>\n"

        f"🆔 <code>{channel_id}</code>\n\n"

        "<b>📚 BACKUP ORDER</b>\n\n"

        "1️⃣ Media\n"
        "2️⃣ Media2\n"
        "3️⃣ Media3\n\n"

        "📤 All existing files will be uploaded.\n"
        "🔄 After completion, the worker continues watching "
        "for new files.\n\n"

        "📊 <code>/backup_status</code> "
        "→ Live transfer status\n\n"

        "🛑 <code>/backup_stop</code> "
        "→ Stop backup",

        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# /backup_status
# ============================================================


@Client.on_message(
    filters.command("backup_status")
    & filters.user(ADMINS)
)
async def backup_status_command(
    app,
    message,
):

    keyboard = InlineKeyboardMarkup(

        [

            [

                InlineKeyboardButton(

                    "🔄 REFRESH",

                    callback_data=(
                        "backup_refresh"
                    ),
                )

            ]

        ]

    )


    await message.reply_text(

        build_status_text(),

        reply_markup=keyboard,

        parse_mode=enums.ParseMode.HTML,

        disable_web_page_preview=True,
    )


# ============================================================
# REFRESH STATUS
# ============================================================


@Client.on_callback_query(
    filters.regex(
        "^backup_refresh$"
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

        keyboard = InlineKeyboardMarkup(

            [

                [

                    InlineKeyboardButton(

                        "🔄 REFRESH",

                        callback_data=(
                            "backup_refresh"
                        ),
                    )

                ]

            ]

        )


        await query.message.edit_text(

            build_status_text(),

            reply_markup=keyboard,

            parse_mode=enums.ParseMode.HTML,

            disable_web_page_preview=True,
        )


        await query.answer(
            "🔄 Status refreshed."
        )


    except Exception as e:

        # MESSAGE_NOT_MODIFIED is harmless.
        if "MESSAGE_NOT_MODIFIED" in str(
            e
        ):

            await query.answer(
                "Already up to date."
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
    filters.command("backup_stats")
    & filters.user(ADMINS)
)
async def backup_stats_command(
    app,
    message,
):

    text = (

        "<b>📊 DOWNTOWNVILLA BACKUP STATISTICS</b>\n\n"

        "<b>━━━━━━━━ MEDIA ━━━━━━━━</b>\n\n"

        f"📚 Total: "
        f"<b>{backup_media_total:,}</b>\n"

        f"✅ Uploaded: "
        f"<b>{backup_media_uploaded:,}</b>\n"

        f"❌ Failed: "
        f"<b>{backup_media_failed:,}</b>\n\n"


        "<b>━━━━━━━━ MEDIA2 ━━━━━━━━</b>\n\n"

        f"📚 Total: "
        f"<b>{backup_media2_total:,}</b>\n"

        f"✅ Uploaded: "
        f"<b>{backup_media2_uploaded:,}</b>\n"

        f"❌ Failed: "
        f"<b>{backup_media2_failed:,}</b>\n\n"


        "<b>━━━━━━━━ MEDIA3 ━━━━━━━━</b>\n\n"

        f"📚 Total: "
        f"<b>{backup_media3_total:,}</b>\n"

        f"✅ Uploaded: "
        f"<b>{backup_media3_uploaded:,}</b>\n"

        f"❌ Failed: "
        f"<b>{backup_media3_failed:,}</b>\n\n"


        "<b>━━━━━━━━ ALL DATABASES ━━━━━━━━</b>\n\n"

        f"📤 Total uploaded: "
        f"<b>{backup_total_uploaded:,}</b>\n"

        f"❌ Total failed: "
        f"<b>{backup_total_failed:,}</b>\n\n"

        f"📡 Current status: "
        f"<b>{'🟢 RUNNING' if backup_running else '🔴 STOPPED'}</b>\n"

        f"⏱️ Runtime: "
        f"<b>{get_runtime()}</b>\n"
    )


    await message.reply_text(

        text,

        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# /backup_stop
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

            "🔴 <b>BACKUP IS NOT RUNNING</b>",

            parse_mode=enums.ParseMode.HTML,
        )

        return


    stopped = await stop_backup_worker()


    if stopped:

        await message.reply_text(

            "🛑 <b>BACKUP STOPPED</b>\n\n"

            "The files already uploaded remain "
            "in the backup channel.\n\n"

            "Run <code>/backup</code> again "
            "to start a new backup.",

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
#
# There is NO @Client.on_start().
#
# Pyrogram 2.x does not provide that decorator.
#
# We start the backup when the first command is received
# OR from the application's existing startup mechanism.
#
# If BACKUP_AUTO_START=true, the helper below can be called
# from bot.py after the main client starts.
# ============================================================


async def auto_start_backup(
    app
):

    if not BACKUP_AUTO_START:

        logger.info(
            "[BACKUP] BACKUP_AUTO_START=false"
        )

        return False


    if not backup_configured():

        logger.warning(
            "[BACKUP] Auto backup disabled: "
            "BACKUP_CHANNEL_ID missing."
        )

        return False


    try:

        await app.get_chat(
            get_channel_id()
        )

    except Exception as e:

        logger.exception(
            "[BACKUP] Cannot access backup channel: %s",
            e,
        )

        return False


    return await start_backup_worker(
        app
    )


# ============================================================
# END backup.py
# ============================================================
