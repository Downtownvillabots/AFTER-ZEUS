import os
import asyncio
import logging

from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, RPCError

from database.ia_filterdb import Media, Media2, Media3

logger = logging.getLogger(__name__)

# ============================================================
# BACKUP CHANNEL
# Render Environment Variable:
# BACKUP_CHANNEL_ID=-100xxxxxxxxxxxx
# ============================================================

BACKUP_CHANNEL_ID = os.getenv("BACKUP_CHANNEL_ID")

if BACKUP_CHANNEL_ID:
    try:
        BACKUP_CHANNEL_ID = int(BACKUP_CHANNEL_ID)
    except ValueError:
        BACKUP_CHANNEL_ID = None


# ============================================================
# ADMIN IDS
# Use your existing ADMINS from info.py
# ============================================================

from info import ADMINS


# ============================================================
# BACKUP SETTINGS
# ============================================================

BACKUP_BATCH_SIZE = 50

# Small delay between files to reduce Telegram flood limits.
BACKUP_DELAY = 0.3

# ============================================================
# GLOBAL BACKUP STATE
# ============================================================

backup_running = False


# ============================================================
# GET ALL FILES FROM ALL 3 DATABASES
# ============================================================

async def get_all_backup_files():
    """
    Get every indexed file from:
        DB1 -> Media
        DB2 -> Media2
        DB3 -> Media3

    DB1 first, then DB2, then DB3.
    """

    collections = [Media]

    # Media2 / Media3 are available because your
    # ia_filterdb.py already supports 3 databases.

    collections.append(Media2)
    collections.append(Media3)

    for collection in collections:
        try:
            cursor = collection.find({}).sort("$natural", 1)

            async for file in cursor:
                yield file

        except Exception:
            logger.exception(
                "Error reading backup collection: %s",
                collection.__name__,
            )


# ============================================================
# BUILD CAPTION
# ============================================================

def build_backup_caption(file):
    """
    Preserve the existing indexed caption.

    If there is no caption, use the original filename.
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

async def backup_one_file(bot, file):
    file_id = getattr(file, "file_id", None)

    if not file_id:
        return False, "missing_file_id"

    caption = build_backup_caption(file)

    try:
        await bot.send_cached_media(
            chat_id=BACKUP_CHANNEL_ID,
            file_id=file_id,
            caption=caption,
            parse_mode=enums.ParseMode.HTML,
        )

        return True, None

    except FloodWait as e:
        logger.warning(
            "FloodWait received: sleeping %s seconds",
            e.value,
        )

        await asyncio.sleep(e.value + 2)

        try:
            await bot.send_cached_media(
                chat_id=BACKUP_CHANNEL_ID,
                file_id=file_id,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
            )

            return True, None

        except Exception as retry_error:
            logger.exception(
                "Retry failed for %s: %s",
                file_id,
                retry_error,
            )

            return False, str(retry_error)

    except RPCError as e:
        logger.error(
            "Telegram error for %s: %s",
            file_id,
            e,
        )

        return False, str(e)

    except Exception as e:
        logger.exception(
            "Backup failed for %s",
            file_id,
        )

        return False, str(e)


# ============================================================
# /backup
# ============================================================

@Client.on_message(
    filters.command("backup") & filters.user(ADMINS)
)
async def start_backup(bot, message):

    global backup_running

    if backup_running:
        return await message.reply_text(
            "⚠️ <b>A backup is already running.</b>\n\n"
            "Please wait until it finishes."
        )

    if not BACKUP_CHANNEL_ID:
        return await message.reply_text(
            "❌ <b>BACKUP_CHANNEL_ID is not configured.</b>\n\n"
            "Add it to Render Environment Variables."
        )

    backup_running = True

    status = await message.reply_text(
        "🚀 <b>BACKUP STARTED</b>\n\n"
        "📦 Reading files from DB1...\n"
        "⏳ Please wait..."
    )

    total = 0
    success = 0
    failed = 0

    try:

        async for file in get_all_backup_files():

            total += 1

            ok, error = await backup_one_file(
                bot,
                file,
            )

            if ok:
                success += 1
            else:
                failed += 1

                logger.error(
                    "Backup failed: %s | %s",
                    getattr(file, "file_name", "Unknown"),
                    error,
                )

            # Update progress every 50 files
            if total % BACKUP_BATCH_SIZE == 0:

                try:
                    await status.edit_text(
                        "🚀 <b>BACKUP RUNNING</b>\n\n"
                        f"📦 Processed: <code>{total}</code>\n"
                        f"✅ Success: <code>{success}</code>\n"
                        f"❌ Failed: <code>{failed}</code>\n\n"
                        "🗄️ DB1 + DB2 + DB3"
                    )
                except Exception:
                    pass

            await asyncio.sleep(BACKUP_DELAY)

        await status.edit_text(
            "✅ <b>BACKUP COMPLETED</b>\n\n"
            f"📦 Total processed: <code>{total}</code>\n"
            f"✅ Successfully backed up: <code>{success}</code>\n"
            f"❌ Failed: <code>{failed}</code>\n\n"
            "🗄️ DB1 + DB2 + DB3\n"
            "🎯 Backup channel updated."
        )

    except Exception as e:

        logger.exception(
            "FULL BACKUP ERROR"
        )

        try:
            await status.edit_text(
                "❌ <b>BACKUP STOPPED</b>\n\n"
                f"📦 Processed: <code>{total}</code>\n"
                f"✅ Success: <code>{success}</code>\n"
                f"❌ Failed: <code>{failed}</code>\n\n"
                f"<code>{str(e)[:1000]}</code>"
            )
        except Exception:
            pass

    finally:
        backup_running = False


# ============================================================
# /backup_status
# ============================================================

@Client.on_message(
    filters.command("backup_status") & filters.user(ADMINS)
)
async def backup_status(bot, message):

    if backup_running:
        await message.reply_text(
            "🟢 <b>Backup is currently running.</b>"
        )
    else:
        await message.reply_text(
            "⚪ <b>No backup is currently running.</b>"
        )
