# ============================================================
# DOWNTOWNVILLA ULTIMATE LIVE ADMIN PANEL
# ============================================================
#
# File:
#     plugins/admin_panel.py
#
# Commands:
#
#     /panel
#     /panelclose
#
# The panel automatically updates.
#
# ============================================================

import os
import time
import asyncio
import logging
import platform
from datetime import datetime

import psutil

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
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
    MULTIPLE_DB,
    COLLECTION_NAME,
)

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

# ------------------------------------------------------------
# PUT YOUR TELEGRAM USER ID HERE
# ------------------------------------------------------------

ADMIN_IDS = set(map(int, os.getenv("ADMINS", "").split()))


# ------------------------------------------------------------
# PANEL UPDATE INTERVAL
# ------------------------------------------------------------

PANEL_UPDATE_SECONDS = 3


# ------------------------------------------------------------
# ACTIVITY LOG LIMIT
# ------------------------------------------------------------

MAX_ACTIVITY_LOG = 30


# ------------------------------------------------------------
# ERROR LOG LIMIT
# ------------------------------------------------------------

MAX_ERROR_LOG = 20


# ============================================================
# START TIME
# ============================================================

BOT_START_TIME = time.time()


# ============================================================
# GLOBAL LIVE STATE
# ============================================================

LIVE_STATE = {

    # --------------------------------------------------------
    # Current operation
    # --------------------------------------------------------

    "current_task": "Idle",

    "current_task_type": "system",

    "current_task_progress": 0,

    "current_task_total": 0,

    "current_task_status": "idle",

    "current_task_started": None,

    "current_task_details": "",

    # --------------------------------------------------------
    # Counters
    # --------------------------------------------------------

    "searches": 0,

    "searches_success": 0,

    "searches_failed": 0,

    "files_saved": 0,

    "files_failed": 0,

    "files_skipped": 0,

    "files_sent": 0,

    "errors": 0,

    "floodwaits": 0,

    # --------------------------------------------------------
    # Last operations
    # --------------------------------------------------------

    "last_search": "None",

    "last_search_time": 0,

    "last_saved_file": "None",

    "last_sent_file": "None",

    "last_error": "None",

    "last_activity": "System started",

    "last_activity_time": time.time(),
}


# ============================================================
# ACTIVITY HISTORY
# ============================================================

ACTIVITY_LOG = []

ERROR_LOG = []


# ============================================================
# PANEL USERS
# ============================================================

PANEL_MESSAGES = {}


# ============================================================
# LOCK
# ============================================================

STATE_LOCK = asyncio.Lock()


# ============================================================
# TIME HELPERS
# ============================================================

def now_string():

    return datetime.now().strftime(
        "%d-%m-%Y %H:%M:%S"
    )


def short_time():

    return datetime.now().strftime(
        "%H:%M:%S"
    )


def format_duration(seconds):

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

    parts.append(
        f"{seconds}s"
    )

    return " ".join(parts)


# ============================================================
# MEMORY HELPERS
# ============================================================

def add_activity(
    message,
    level="INFO",
):

    timestamp = short_time()

    entry = (
        f"{timestamp} "
        f"{level} "
        f"{message}"
    )

    ACTIVITY_LOG.insert(
        0,
        entry,
    )

    if len(
        ACTIVITY_LOG
    ) > MAX_ACTIVITY_LOG:

        del ACTIVITY_LOG[
            MAX_ACTIVITY_LOG:
        ]

    LIVE_STATE[
        "last_activity"
    ] = message

    LIVE_STATE[
        "last_activity_time"
    ] = time.time()


def add_error(
    message,
):

    timestamp = short_time()

    entry = (
        f"{timestamp} ❌ "
        f"{message}"
    )

    ERROR_LOG.insert(
        0,
        entry,
    )

    if len(
        ERROR_LOG
    ) > MAX_ERROR_LOG:

        del ERROR_LOG[
            MAX_ERROR_LOG:
        ]

    LIVE_STATE[
        "errors"
    ] += 1

    LIVE_STATE[
        "last_error"
    ] = message

    add_activity(
        message,
        "ERROR",
    )


# ============================================================
# EXTERNAL STATE API
# ============================================================
#
# Later your backup/index/search code can simply call:
#
# start_live_task(...)
# update_live_task(...)
# finish_live_task(...)
#
# We will connect these to your real functions later.
#
# ============================================================

def start_live_task(
    name,
    task_type="other",
    total=0,
    details="",
):

    LIVE_STATE[
        "current_task"
    ] = name

    LIVE_STATE[
        "current_task_type"
    ] = task_type

    LIVE_STATE[
        "current_task_progress"
    ] = 0

    LIVE_STATE[
        "current_task_total"
    ] = total

    LIVE_STATE[
        "current_task_status"
    ] = "running"

    LIVE_STATE[
        "current_task_started"
    ] = time.time()

    LIVE_STATE[
        "current_task_details"
    ] = details

    add_activity(
        f"START {name}",
        "START",
    )


def update_live_task(
    progress=None,
    total=None,
    details=None,
    name=None,
):

    if progress is not None:

        LIVE_STATE[
            "current_task_progress"
        ] = progress

    if total is not None:

        LIVE_STATE[
            "current_task_total"
        ] = total

    if details is not None:

        LIVE_STATE[
            "current_task_details"
        ] = details

    if name is not None:

        LIVE_STATE[
            "current_task"
        ] = name

    LIVE_STATE[
        "current_task_status"
    ] = "running"


def finish_live_task(
    message=None,
):

    old_task = LIVE_STATE[
        "current_task"
    ]

    LIVE_STATE[
        "current_task_status"
    ] = "idle"

    LIVE_STATE[
        "current_task"
    ] = "Idle"

    LIVE_STATE[
        "current_task_type"
    ] = "system"

    LIVE_STATE[
        "current_task_progress"
    ] = 0

    LIVE_STATE[
        "current_task_total"
    ] = 0

    LIVE_STATE[
        "current_task_started"
    ] = None

    LIVE_STATE[
        "current_task_details"
    ] = ""

    add_activity(
        message
        or f"FINISHED {old_task}",
        "DONE",
    )


# ============================================================
# SEARCH TRACKING
# ============================================================

def track_search(
    query,
    duration=0,
    success=True,
):

    LIVE_STATE[
        "searches"
    ] += 1

    if success:

        LIVE_STATE[
            "searches_success"
        ] += 1

    else:

        LIVE_STATE[
            "searches_failed"
        ] += 1

    LIVE_STATE[
        "last_search"
    ] = str(
        query
    )[:100]

    LIVE_STATE[
        "last_search_time"
    ] = duration

    add_activity(
        f"SEARCH: {str(query)[:70]} "
        f"({duration:.2f}s)",
        "SEARCH",
    )


# ============================================================
# FILE TRACKING
# ============================================================

def track_file_saved(
    filename,
    database="Unknown",
):

    LIVE_STATE[
        "files_saved"
    ] += 1

    LIVE_STATE[
        "last_saved_file"
    ] = str(
        filename
    )[:100]

    add_activity(
        f"SAVED: {str(filename)[:60]} "
        f"→ {database}",
        "FILE",
    )


def track_file_skipped(
    filename,
):

    LIVE_STATE[
        "files_skipped"
    ] += 1

    add_activity(
        f"SKIPPED: {str(filename)[:60]}",
        "SKIP",
    )


def track_file_failed(
    filename,
):

    LIVE_STATE[
        "files_failed"
    ] += 1

    add_error(
        f"FILE FAILED: "
        f"{str(filename)[:80]}"
    )


def track_file_sent(
    filename,
):

    LIVE_STATE[
        "files_sent"
    ] += 1

    LIVE_STATE[
        "last_sent_file"
    ] = str(
        filename
    )[:100]

    add_activity(
        f"SENT: {str(filename)[:60]}",
        "SEND",
    )


# ============================================================
# SYSTEM INFORMATION
# ============================================================

def get_process():

    try:

        return psutil.Process(
            os.getpid()
        )

    except Exception:

        return None


def get_cpu():

    try:

        return psutil.cpu_percent(
            interval=None
        )

    except Exception:

        return 0


def get_memory():

    try:

        process = get_process()

        if process:

            process_mb = (
                process.memory_info().rss
                / 1024
                / 1024
            )

        else:

            process_mb = 0

        system = psutil.virtual_memory()

        return (
            process_mb,
            system.percent,
            system.total
            / 1024
            / 1024
            / 1024,
        )

    except Exception:

        return 0, 0, 0


def get_disk():

    try:

        disk = psutil.disk_usage(
            "/"
        )

        return (
            disk.used
            / 1024
            / 1024
            / 1024,

            disk.total
            / 1024
            / 1024
            / 1024,

            disk.percent,
        )

    except Exception:

        return 0, 0, 0


def get_task_count():

    try:

        return len(
            asyncio.all_tasks()
        )

    except Exception:

        return 0


# ============================================================
# DATABASE STATISTICS
# ============================================================

async def database_info(
    document_model,
    database,
    name,
):

    result = {
        "name": name,
        "count": 0,
        "size": 0,
        "indexes": 0,
        "status": "OFFLINE",
        "error": "",
    }

    try:

        collection = database[
            COLLECTION_NAME
        ]

        count = await collection.count_documents(
            {}
        )

        stats = await database.command(
            "collStats",
            COLLECTION_NAME,
        )

        size = (
            stats.get(
                "storageSize",
                0,
            )
            / 1024
            / 1024
        )

        indexes = (
            stats.get(
                "totalIndexSize",
                0,
            )
            / 1024
            / 1024
        )

        result[
            "count"
        ] = count

        result[
            "size"
        ] = size

        result[
            "indexes"
        ] = indexes

        result[
            "status"
        ] = "ONLINE"

    except Exception as e:

        result[
            "error"
        ] = str(e)[:100]

    return result


async def get_all_database_info():

    tasks = [
        database_info(
            Media,
            db,
            "Media",
        )
    ]

    if MULTIPLE_DB:

        tasks.append(
            database_info(
                Media2,
                db2,
                "Media2",
            )
        )

        tasks.append(
            database_info(
                Media3,
                db3,
                "Media3",
            )
        )

    try:

        return await asyncio.gather(
            *tasks
        )

    except Exception as e:

        logger.exception(
            "Database statistics failed: %s",
            e,
        )

        return []


# ============================================================
# DATABASE BAR
# ============================================================

def progress_bar(
    value,
    maximum=407,
    length=14,
):

    try:

        percentage = (
            float(value)
            / float(maximum)
        )

    except Exception:

        percentage = 0

    percentage = max(
        0,
        min(
            percentage,
            1,
        ),
    )

    filled = int(
        percentage * length
    )

    empty = (
        length - filled
    )

    return (
        "█" * filled
        + "░" * empty
    )


# ============================================================
# CURRENT TASK DISPLAY
# ============================================================

def current_task_text():

    status = LIVE_STATE[
        "current_task_status"
    ]

    if status != "running":

        return (
            "🟢 <b>Current Work:</b> "
            "Idle"
        )

    name = LIVE_STATE[
        "current_task"
    ]

    task_type = LIVE_STATE[
        "current_task_type"
    ]

    progress = LIVE_STATE[
        "current_task_progress"
    ]

    total = LIVE_STATE[
        "current_task_total"
    ]

    details = LIVE_STATE[
        "current_task_details"
    ]

    if total:

        try:

            percent = (
                progress
                / total
                * 100
            )

        except Exception:

            percent = 0

        bar = progress_bar(
            percent,
            100,
            18,
        )

        progress_text = (
            f"{progress:,}/"
            f"{total:,} "
            f"({percent:.1f}%)"
        )

    else:

        bar = (
            "🔄 "
            "WORKING..."
        )

        progress_text = (
            "In progress"
        )

    text = (
        "🔴 <b>CURRENT WORK</b>\n\n"
        f"⚙️ Task: <b>{name}</b>\n"
        f"🏷 Type: <b>{task_type}</b>\n"
        f"📊 {bar}\n"
        f"📈 {progress_text}\n"
    )

    if details:

        text += (
            f"📝 {details[:200]}\n"
        )

    if LIVE_STATE[
        "current_task_started"
    ]:

        elapsed = (
            time.time()
            - LIVE_STATE[
                "current_task_started"
            ]
        )

        text += (
            f"⏱ Running: "
            f"<b>{format_duration(elapsed)}</b>\n"
        )

    return text


# ============================================================
# PANEL TEXT
# ============================================================

async def build_panel():

    uptime = (
        time.time()
        - BOT_START_TIME
    )

    cpu = get_cpu()

    process_mb, ram_percent, ram_total = (
        get_memory()
    )

    disk_used, disk_total, disk_percent = (
        get_disk()
    )

    task_count = get_task_count()

    databases = (
        await get_all_database_info()
    )

    text = (
        "🤖 <b>DOWNTOWNVILLA "
        "LIVE ADMIN PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    # ========================================================
    # SYSTEM
    # ========================================================

    text += (
        "🖥 <b>SYSTEM</b>\n\n"
        f"🟢 Status: <b>ONLINE</b>\n"
        f"⏱ Uptime: <b>"
        f"{format_duration(uptime)}</b>\n"
        f"🕐 Time: <b>{now_string()}</b>\n"
        f"🐍 Python: <b>"
        f"{platform.python_version()}</b>\n"
        f"💻 CPU: <b>{cpu:.1f}%</b>\n"
        f"🧠 Process RAM: <b>"
        f"{process_mb:.1f} MB</b>\n"
        f"🧠 System RAM: <b>"
        f"{ram_percent:.1f}%</b>\n"
        f"💾 Disk: <b>"
        f"{disk_used:.1f}/"
        f"{disk_total:.1f} GB "
        f"({disk_percent:.1f}%)</b>\n"
        f"⚙️ Async Tasks: <b>"
        f"{task_count}</b>\n"
        f"🆔 PID: <b>{os.getpid()}</b>\n\n"
    )

    # ========================================================
    # CURRENT WORK
    # ========================================================

    text += (
        current_task_text()
        + "\n"
    )

    # ========================================================
    # COUNTERS
    # ========================================================

    text += (
        "📊 <b>ACTIVITY</b>\n\n"
        f"🔎 Searches: <b>"
        f"{LIVE_STATE['searches']:,}</b>\n"
        f"✅ Successful searches: <b>"
        f"{LIVE_STATE['searches_success']:,}</b>\n"
        f"❌ Failed searches: <b>"
        f"{LIVE_STATE['searches_failed']:,}</b>\n"
        f"📁 Files saved: <b>"
        f"{LIVE_STATE['files_saved']:,}</b>\n"
        f"⏭ Files skipped: <b>"
        f"{LIVE_STATE['files_skipped']:,}</b>\n"
        f"📤 Files sent: <b>"
        f"{LIVE_STATE['files_sent']:,}</b>\n"
        f"⚠️ Errors: <b>"
        f"{LIVE_STATE['errors']:,}</b>\n"
        f"🚦 FloodWaits: <b>"
        f"{LIVE_STATE['floodwaits']:,}</b>\n\n"
    )

    # ========================================================
    # DATABASES
    # ========================================================

    text += (
        "🗄 <b>DATABASES</b>\n\n"
    )

    for database in databases:

        name = database[
            "name"
        ]

        count = database[
            "count"
        ]

        size = database[
            "size"
        ]

        indexes = database[
            "indexes"
        ]

        status = database[
            "status"
        ]

        if status == "ONLINE":

            status_icon = "🟢"

        else:

            status_icon = "🔴"

        text += (
            f"{status_icon} "
            f"<b>{name}</b>\n"
            f"   📦 Documents: "
            f"<b>{count:,}</b>\n"
            f"   💾 Data: "
            f"<b>{size:.2f} MB</b>\n"
            f"   📑 Indexes: "
            f"<b>{indexes:.2f} MB</b>\n"
        )

        if status != "ONLINE":

            text += (
                f"   ⚠️ "
                f"{database['error']}\n"
            )

        text += "\n"

    # ========================================================
    # LAST ACTIVITY
    # ========================================================

    text += (
        "📡 <b>LAST ACTIVITY</b>\n\n"
        f"📝 "
        f"{LIVE_STATE['last_activity'][:150]}\n"
    )

    if LIVE_STATE[
        "last_search"
    ] != "None":

        text += (
            f"🔎 Last search: "
            f"<code>"
            f"{LIVE_STATE['last_search'][:80]}"
            f"</code>\n"
            f"⚡ Search time: "
            f"<b>"
            f"{LIVE_STATE['last_search_time']:.3f}s"
            f"</b>\n"
        )

    if LIVE_STATE[
        "last_saved_file"
    ] != "None":

        text += (
            f"📁 Last saved: "
            f"{LIVE_STATE['last_saved_file'][:80]}\n"
        )

    # ========================================================
    # RECENT ACTIVITY
    # ========================================================

    text += (
        "\n📜 <b>RECENT ACTIVITY</b>\n\n"
    )

    if ACTIVITY_LOG:

        for entry in ACTIVITY_LOG[:8]:

            text += (
                f"<code>"
                f"{entry[:150]}"
                f"</code>\n"
            )

    else:

        text += (
            "No activity yet.\n"
        )

    # ========================================================
    # RECENT ERRORS
    # ========================================================

    if ERROR_LOG:

        text += (
            "\n🚨 <b>RECENT ERRORS</b>\n\n"
        )

        for entry in ERROR_LOG[:5]:

            text += (
                f"<code>"
                f"{entry[:150]}"
                f"</code>\n"
            )

    # ========================================================
    # FOOTER
    # ========================================================

    text += (
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🔄 Auto update: "
        f"<b>{PANEL_UPDATE_SECONDS}s</b>\n"
        f"🕐 Updated: "
        f"<b>{short_time()}</b>"
    )

    return text


# ============================================================
# PANEL KEYBOARD
# ============================================================

def panel_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📊 SYSTEM",
                    callback_data="admin_system",
                ),
                InlineKeyboardButton(
                    "🗄 DATABASE",
                    callback_data="admin_database",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📜 ACTIVITY",
                    callback_data="admin_activity",
                ),
                InlineKeyboardButton(
                    "🚨 ERRORS",
                    callback_data="admin_errors",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ CLOSE",
                    callback_data="admin_close",
                ),
            ],
        ]
    )


# ============================================================
# ADMIN CHECK
# ============================================================

def is_admin(
    user_id,
):

    return int(
        user_id
    ) in ADMIN_IDS


# ============================================================
# LIVE PANEL UPDATE LOOP
# ============================================================

async def panel_updater(
    app,
    chat_id,
    message_id,
):

    while True:

        try:

            await asyncio.sleep(
                PANEL_UPDATE_SECONDS
            )

            if (
                chat_id
                not in PANEL_MESSAGES
            ):

                return

            current_message_id = (
                PANEL_MESSAGES[
                    chat_id
                ]
            )

            if (
                current_message_id
                != message_id
            ):

                return

            text = await build_panel()

            try:

                await app.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=panel_keyboard(),
                    parse_mode=enums.ParseMode.HTML,
                )

            except Exception as e:

                error_text = str(
                    e
                ).lower()

                # Message was not modified.
                if (
                    "not modified"
                    in error_text
                ):

                    continue

                # Message deleted.
                if (
                    "message to edit"
                    in error_text
                    or "message not found"
                    in error_text
                ):

                    PANEL_MESSAGES.pop(
                        chat_id,
                        None,
                    )

                    return

                logger.warning(
                    "[ADMIN PANEL] Update failed: %s",
                    e,
                )

        except asyncio.CancelledError:

            return

        except Exception:

            logger.exception(
                "[ADMIN PANEL] Updater crashed."
            )

            await asyncio.sleep(
                PANEL_UPDATE_SECONDS
            )


# ============================================================
# /PANEL
# ============================================================

@Client.on_message(
    filters.command(
        "panel"
    )
)
async def admin_panel_command(
    app,
    message,
):

    if not is_admin(
        message.from_user.id
    ):

        return

    try:

        text = await build_panel()

        msg = await message.reply_text(
            text,
            reply_markup=panel_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        # ----------------------------------------------------
        # If an old panel exists in this chat,
        # replace its ID.
        # ----------------------------------------------------

        old_message = PANEL_MESSAGES.get(
            message.chat.id
        )

        PANEL_MESSAGES[
            message.chat.id
        ] = msg.id

        add_activity(
            f"Admin panel opened by "
            f"{message.from_user.id}",
            "ADMIN",
        )

        # ----------------------------------------------------
        # Start live updater.
        # ----------------------------------------------------

        asyncio.create_task(
            panel_updater(
                app,
                message.chat.id,
                msg.id,
            )
        )

    except Exception:

        logger.exception(
            "[ADMIN PANEL] Failed to open panel."
        )


# ============================================================
# /PANELCLOSE
# ============================================================

@Client.on_message(
    filters.command(
        "panelclose"
    )
)
async def admin_panel_close_command(
    app,
    message,
):

    if not is_admin(
        message.from_user.id
    ):

        return

    PANEL_MESSAGES.pop(
        message.chat.id,
        None,
    )

    try:

        await message.reply_text(
            "✅ Live admin panel stopped."
        )

    except Exception:

        pass


# ============================================================
# CALLBACK HANDLER
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^admin_"
    )
)
async def admin_panel_callback(
    app,
    query,
):

    if not is_admin(
        query.from_user.id
    ):

        await query.answer(
            "⛔ Access denied.",
            show_alert=True,
        )

        return

    if (
        query.data
        == "admin_close"
    ):

        PANEL_MESSAGES.pop(
            query.message.chat.id,
            None,
        )

        await query.answer(
            "Panel stopped."
        )

        try:

            await query.message.delete()

        except Exception:

            pass

        return

    if (
        query.data
        == "admin_system"
    ):

        await query.answer(
            "System information is shown in the live panel."
        )

        return

    if (
        query.data
        == "admin_database"
    ):

        await query.answer(
            "Database information is shown in the live panel."
        )

        return

    if (
        query.data
        == "admin_activity"
    ):

        await query.answer(
            "Activity is shown in the live panel."
        )

        return

    if (
        query.data
        == "admin_errors"
    ):

        await query.answer(
            "Errors are shown in the live panel."
        )

        return


# ============================================================
# STARTUP
# ============================================================

add_activity(
    "Admin panel system loaded.",
    "SYSTEM",
)

logger.info(
    "=================================================="
)

logger.info(
    "[ADMIN PANEL] Ultimate Live Admin Panel Loaded"
)

logger.info(
    "[ADMIN PANEL] Update interval: %ss",
    PANEL_UPDATE_SECONDS,
)

logger.info(
    "[ADMIN PANEL] Admin IDs: %s",
    ADMIN_IDS,
)

logger.info(
    "=================================================="
)
