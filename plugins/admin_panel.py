# ============================================================
# DOWNTOWN VILLA - EXTREME LIVE ADMIN CONTROL CENTER
# File: plugins/admin_panel.py
#
# COMMAND:
#     /admin
#
# ENVIRONMENT:
#     ADMINS=123456789 987654321
#
# OPTIONAL:
#     ADMIN_PANEL_UPDATE_SECONDS=3
#     ADMIN_PANEL_LOGS=100
#     ADMIN_PANEL_TASKS=50
#
# IMPORTANT:
# This file is self-contained.
# Replace the ENTIRE old admin_panel.py with this file.
# ============================================================

import os
import time
import asyncio
import logging
import platform
import shutil
import html
from collections import deque, Counter
from datetime import datetime

import psutil

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, RPCError


# ============================================================
# DATABASE IMPORT
# ============================================================

try:
    from database.ia_filterdb import (
        db,
        db2,
        db3,
        Media,
        Media2,
        Media3,
    )
except Exception as e:
    db = None
    db2 = None
    db3 = None
    Media = None
    Media2 = None
    Media3 = None


# ============================================================
# INFO IMPORT
# ============================================================

try:
    from info import (
        COLLECTION_NAME,
        MULTIPLE_DB,
    )
except Exception:
    COLLECTION_NAME = "Telegram_files"
    MULTIPLE_DB = True


# ============================================================
# LOGGER
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# ADMIN CONFIG
# ============================================================

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMINS", "").replace(",", " ").split()
    if x.strip().isdigit()
}


# ============================================================
# PANEL CONFIG
# ============================================================

PANEL_UPDATE_SECONDS = float(
    os.getenv(
        "ADMIN_PANEL_UPDATE_SECONDS",
        "3"
    )
)

MAX_LIVE_LOGS = int(
    os.getenv(
        "ADMIN_PANEL_LOGS",
        "100"
    )
)

MAX_LIVE_TASKS = int(
    os.getenv(
        "ADMIN_PANEL_TASKS",
        "50"
    )
)


# ============================================================
# BOT START TIME
# ============================================================

START_TIME = time.time()


# ============================================================
# GLOBAL STATISTICS
# ============================================================

STATS = {
    "searches": 0,
    "commands": 0,
    "users_seen": 0,
    "files_sent": 0,
    "files_indexed": 0,
    "files_skipped": 0,
    "errors": 0,
    "callbacks": 0,
    "messages": 0,
}


# ============================================================
# USER TRACKING
# ============================================================

KNOWN_USERS = set()

USER_SEARCHES = Counter()

USER_COMMANDS = Counter()

USER_LAST_SEEN = {}

USER_NAMES = {}

USER_FIRST_SEEN = {}

DAILY_USERS = Counter()

DAILY_SEARCHES = Counter()

DAILY_COMMANDS = Counter()

SEARCH_TERMS = Counter()

COMMAND_TERMS = Counter()


# ============================================================
# RECENT ACTIVITY
# ============================================================

RECENT_ACTIVITY = deque(
    maxlen=200
)


# ============================================================
# ERROR BUFFER
# ============================================================

ERROR_BUFFER = deque(
    maxlen=100
)


# ============================================================
# LIVE TASKS
#
# Other plugins can use:
#
# from plugins.admin_panel import start_live_task
# from plugins.admin_panel import update_live_task
# from plugins.admin_panel import finish_live_task
#
# ============================================================

LIVE_TASKS = {}


# ============================================================
# LIVE LOGS
# ============================================================

LIVE_LOGS = deque(
    maxlen=MAX_LIVE_LOGS
)


# ============================================================
# ACTIVE ADMIN PANELS
# ============================================================

ACTIVE_PANELS = {}


# ============================================================
# PANEL LOCK
# ============================================================

PANEL_LOCK = asyncio.Lock()


# ============================================================
# LIVE LOGGER HANDLER
# ============================================================

class TelegramMemoryLogHandler(logging.Handler):

    def emit(self, record):

        try:

            now = datetime.now().strftime(
                "%H:%M:%S"
            )

            level = record.levelname

            message = record.getMessage()

            if len(message) > 500:
                message = message[:500] + "..."

            item = {
                "time": now,
                "level": level,
                "message": message,
            }

            LIVE_LOGS.append(item)

            if level in (
                "ERROR",
                "CRITICAL",
            ):
                ERROR_BUFFER.append(item)

        except Exception:
            pass


# ============================================================
# INSTALL LOG HANDLER
# ============================================================

try:

    _memory_log_handler = TelegramMemoryLogHandler()

    _memory_log_handler.setLevel(
        logging.INFO
    )

    logging.getLogger().addHandler(
        _memory_log_handler
    )

except Exception:
    pass


# ============================================================
# BASIC HELPERS
# ============================================================

def is_admin(user_id):

    try:

        if user_id is None:
            return False

        return int(user_id) in ADMIN_IDS

    except Exception:
        return False


def fmt_number(value):

    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def fmt_float(value):

    try:
        return f"{float(value):,.1f}"
    except Exception:
        return "0.0"


def fmt_bytes(value):

    try:

        value = float(value)

        units = [
            "B",
            "KB",
            "MB",
            "GB",
            "TB",
            "PB",
        ]

        index = 0

        while (
            value >= 1024
            and index < len(units) - 1
        ):

            value /= 1024
            index += 1

        return f"{value:.2f} {units[index]}"

    except Exception:
        return "0 B"


def fmt_duration(seconds):

    try:

        seconds = max(
            0,
            int(seconds)
        )

        days, seconds = divmod(
            seconds,
            86400
        )

        hours, seconds = divmod(
            seconds,
            3600
        )

        minutes, seconds = divmod(
            seconds,
            60
        )

        if days:
            return (
                f"{days}d "
                f"{hours}h "
                f"{minutes}m "
                f"{seconds}s"
            )

        if hours:
            return (
                f"{hours}h "
                f"{minutes}m "
                f"{seconds}s"
            )

        if minutes:
            return (
                f"{minutes}m "
                f"{seconds}s"
            )

        return f"{seconds}s"

    except Exception:
        return "0s"


def now_string():

    return datetime.now().strftime(
        "%d-%m-%Y %H:%M:%S"
    )


def today_key():

    return datetime.now().strftime(
        "%Y-%m-%d"
    )


def safe_html(value):

    return html.escape(
        str(value or "")
    )


def shorten(value, length=100):

    value = str(value or "")

    if len(value) <= length:
        return value

    return value[:length] + "..."


def progress_bar(
    current,
    total,
    length=18,
):

    try:

        current = float(current)
        total = float(total)

        if total <= 0:
            percentage = 0
        else:
            percentage = (
                current / total
            ) * 100

        percentage = max(
            0,
            min(
                100,
                percentage
            )
        )

        filled = int(
            length
            * percentage
            / 100
        )

        empty = (
            length
            - filled
        )

        return (
            "█" * filled
            + "░" * empty
            + f" {percentage:.1f}%"
        )

    except Exception:

        return (
            "░" * length
            + " 0%"
        )


def status_icon(status):

    status = str(
        status or ""
    ).upper()

    if status in (
        "ONLINE",
        "RUNNING",
        "ACTIVE",
        "CONNECTED",
    ):
        return "🟢"

    if status in (
        "ERROR",
        "FAILED",
        "OFFLINE",
    ):
        return "🔴"

    if status in (
        "WARNING",
        "WAITING",
    ):
        return "🟡"

    if status in (
        "COMPLETED",
        "DONE",
    ):
        return "✅"

    return "⚪"


# ============================================================
# ACTIVITY LOGGER
# ============================================================

def add_activity(
    activity_type,
    message,
    user_id=None,
):

    try:

        RECENT_ACTIVITY.append(
            {
                "time": time.time(),
                "type": str(activity_type),
                "message": str(message),
                "user_id": user_id,
            }
        )

    except Exception:
        pass


# ============================================================
# USER TRACKING
# ============================================================

def track_user(
    user_id,
    first_name=None,
    username=None,
):

    if not user_id:
        return

    try:

        user_id = int(user_id)

        current_time = time.time()

        if user_id not in KNOWN_USERS:

            KNOWN_USERS.add(
                user_id
            )

            STATS[
                "users_seen"
            ] = len(
                KNOWN_USERS
            )

            DAILY_USERS[
                today_key()
            ] += 1

            USER_FIRST_SEEN[
                user_id
            ] = current_time

        USER_LAST_SEEN[
            user_id
        ] = current_time

        if first_name or username:

            name = ""

            if first_name:
                name += str(
                    first_name
                )

            if username:

                if name:
                    name += " "

                name += (
                    "@"
                    + str(username)
                )

            USER_NAMES[
                user_id
            ] = name

    except Exception:
        pass


# ============================================================
# TRACK SEARCH
# ============================================================

def track_search(
    user_id,
    query="",
):

    try:

        track_user(
            user_id
        )

        STATS[
            "searches"
        ] += 1

        DAILY_SEARCHES[
            today_key()
        ] += 1

        if user_id:

            USER_SEARCHES[
                int(user_id)
            ] += 1

        clean_query = str(
            query or ""
        ).strip().lower()

        if clean_query:

            SEARCH_TERMS[
                clean_query
            ] += 1

        add_activity(
            "SEARCH",
            clean_query or "Unknown search",
            user_id,
        )

    except Exception:
        pass


# ============================================================
# TRACK COMMAND
# ============================================================

def track_command(
    user_id,
    command=None,
):

    try:

        track_user(
            user_id
        )

        STATS[
            "commands"
        ] += 1

        DAILY_COMMANDS[
            today_key()
        ] += 1

        if user_id:

            USER_COMMANDS[
                int(user_id)
            ] += 1

        if command:

            COMMAND_TERMS[
                str(command)
            ] += 1

        add_activity(
            "COMMAND",
            command or "Unknown command",
            user_id,
        )

    except Exception:
        pass


# ============================================================
# TRACK FILE
# ============================================================

def track_file_sent(
    count=1
):

    try:

        STATS[
            "files_sent"
        ] += int(count)

        add_activity(
            "FILE",
            f"{count} file(s) sent"
        )

    except Exception:
        pass


# ============================================================
# TRACK INDEXING
# ============================================================

def track_indexed(
    count=1
):

    try:

        STATS[
            "files_indexed"
        ] += int(count)

        add_activity(
            "INDEX",
            f"{count} file(s) indexed"
        )

    except Exception:
        pass


# ============================================================
# TRACK SKIPPED
# ============================================================

def track_skipped(
    count=1
):

    try:

        STATS[
            "files_skipped"
        ] += int(count)

        add_activity(
            "INDEX",
            f"{count} file(s) skipped"
        )

    except Exception:
        pass


# ============================================================
# TRACK ERROR
# ============================================================

def track_error(
    message="Unknown error"
):

    try:

        STATS[
            "errors"
        ] += 1

        ERROR_BUFFER.append(
            {
                "time": datetime.now().strftime(
                    "%H:%M:%S"
                ),
                "level": "ERROR",
                "message": str(message),
            }
        )

        add_activity(
            "ERROR",
            str(message)
        )

    except Exception:
        pass


# ============================================================
# LIVE TASK SYSTEM
# ============================================================

def start_live_task(
    task_id,
    name,
    task_type="WORK",
    total=0,
    message="",
):

    try:

        task_id = str(
            task_id
        )

        LIVE_TASKS[
            task_id
        ] = {

            "id": task_id,

            "name": str(
                name
            ),

            "type": str(
                task_type
            ),

            "current": 0,

            "total": int(
                total or 0
            ),

            "status": "RUNNING",

            "started": time.time(),

            "updated": time.time(),

            "speed": 0,

            "message": str(
                message or ""
            ),

            "last_current": 0,

            "last_time": time.time(),

        }

        while len(
            LIVE_TASKS
        ) > MAX_LIVE_TASKS:

            oldest = next(
                iter(
                    LIVE_TASKS
                )
            )

            LIVE_TASKS.pop(
                oldest,
                None
            )

    except Exception:
        pass


# ============================================================
# UPDATE LIVE TASK
# ============================================================

def update_live_task(
    task_id,
    current=None,
    total=None,
    speed=None,
    message=None,
    status=None,
):

    try:

        task = LIVE_TASKS.get(
            str(task_id)
        )

        if not task:
            return

        current_time = time.time()

        if current is not None:

            current = int(
                current
            )

            previous_current = task.get(
                "current",
                0
            )

            previous_time = task.get(
                "last_time",
                current_time
            )

            delta_items = (
                current
                - previous_current
            )

            delta_time = (
                current_time
                - previous_time
            )

            if (
                speed is None
                and delta_time > 0
            ):

                calculated_speed = (
                    delta_items
                    / delta_time
                )

                task[
                    "speed"
                ] = max(
                    0,
                    calculated_speed
                )

            task[
                "current"
            ] = current

            task[
                "last_current"
            ] = current

            task[
                "last_time"
            ] = current_time

        if total is not None:

            task[
                "total"
            ] = int(
                total
            )

        if speed is not None:

            task[
                "speed"
            ] = float(
                speed
            )

        if message is not None:

            task[
                "message"
            ] = str(
                message
            )

        if status is not None:

            task[
                "status"
            ] = str(
                status
            ).upper()

        task[
            "updated"
        ] = current_time

    except Exception:
        pass


# ============================================================
# FINISH LIVE TASK
# ============================================================

def finish_live_task(
    task_id,
    status="COMPLETED",
    message=None,
):

    try:

        task = LIVE_TASKS.get(
            str(task_id)
        )

        if not task:
            return

        task[
            "status"
        ] = str(
            status
        ).upper()

        task[
            "updated"
        ] = time.time()

        if message is not None:

            task[
                "message"
            ] = str(
                message
            )

    except Exception:
        pass


# ============================================================
# REMOVE TASK
# ============================================================

def remove_live_task(
    task_id
):

    try:

        LIVE_TASKS.pop(
            str(task_id),
            None
        )

    except Exception:
        pass


# ============================================================
# DATABASE STATS
# ============================================================

async def database_stats(
    database,
    model,
    name,
):

    result = {

        "name": name,

        "documents": 0,

        "size": 0,

        "indexes": 0,

        "status": "UNKNOWN",

        "error": "",

    }

    if database is None:

        result[
            "status"
        ] = "OFFLINE"

        return result

    try:

        stats = await database.command(
            "collStats",
            COLLECTION_NAME
        )

        result[
            "size"
        ] = stats.get(
            "storageSize",
            stats.get(
                "size",
                0
            )
        )

        result[
            "indexes"
        ] = stats.get(
            "nindexes",
            0
        )

        if model is not None:

            result[
                "documents"
            ] = await model.count_documents(
                {}
            )

        else:

            result[
                "documents"
            ] = stats.get(
                "count",
                0
            )

        result[
            "status"
        ] = "ONLINE"

    except Exception as e:

        result[
            "status"
        ] = "ERROR"

        result[
            "error"
        ] = str(e)

    return result


# ============================================================
# ALL DATABASES
# ============================================================

async def get_all_database_stats():

    databases = [

        (
            db,
            Media,
            "PRIMARY / Media"
        ),

    ]

    if MULTIPLE_DB:

        databases.extend(
            [

                (
                    db2,
                    Media2,
                    "SECONDARY / Media2"
                ),

                (
                    db3,
                    Media3,
                    "TERTIARY / Media3"
                ),

            ]
        )

    try:

        return await asyncio.gather(
            *[
                database_stats(
                    database,
                    model,
                    name
                )
                for database, model, name
                in databases
            ]
        )

    except Exception:

        return []


# ============================================================
# SYSTEM INFORMATION
# ============================================================

def system_info():

    try:

        cpu = psutil.cpu_percent(
            interval=0.05
        )

    except Exception:

        cpu = 0


    try:

        memory = psutil.virtual_memory()

        ram_used = memory.used

        ram_total = memory.total

        ram_percent = memory.percent

    except Exception:

        ram_used = 0

        ram_total = 0

        ram_percent = 0


    try:

        disk = shutil.disk_usage(
            "/"
        )

        disk_used = disk.used

        disk_total = disk.total

        disk_percent = (
            disk.used
            / disk.total
            * 100
            if disk.total
            else 0
        )

    except Exception:

        disk_used = 0

        disk_total = 0

        disk_percent = 0


    try:

        process = psutil.Process()

        process_memory = (
            process.memory_info().rss
        )

        process_cpu = (
            process.cpu_percent(
                interval=0.01
            )
        )

    except Exception:

        process_memory = 0

        process_cpu = 0


    return {

        "cpu": cpu,

        "ram_used": ram_used,

        "ram_total": ram_total,

        "ram_percent": ram_percent,

        "disk_used": disk_used,

        "disk_total": disk_total,

        "disk_percent": disk_percent,

        "process_memory": process_memory,

        "process_cpu": process_cpu,

        "python": platform.python_version(),

        "platform": platform.platform(),

        "machine": platform.machine(),

        "processor": platform.processor(),

    }


# ============================================================
# DATABASE TOTALS
# ============================================================

async def get_database_totals():

    databases = (
        await get_all_database_stats()
    )

    total_files = sum(
        int(
            item.get(
                "documents",
                0
            )
        )
        for item in databases
    )

    total_size = sum(
        int(
            item.get(
                "size",
                0
            )
        )
        for item in databases
    )

    online = sum(
        1
        for item in databases
        if item.get(
            "status"
        ) == "ONLINE"
    )

    return (
        databases,
        total_files,
        total_size,
        online,
    )


# ============================================================
# MAIN DASHBOARD
# ============================================================

async def build_dashboard():

    uptime = fmt_duration(
        time.time()
        - START_TIME
    )

    system = system_info()

    (
        databases,
        total_files,
        total_db_size,
        online_db,
    ) = await get_database_totals()

    today = today_key()

    active_tasks = len(
        [
            task
            for task in LIVE_TASKS.values()
            if task.get(
                "status"
            ) == "RUNNING"
        ]
    )

    text = (

        "╔══════════════════════════════╗\n"
        "║ 🏙️ <b>DOWNTOWN VILLA</b>      ║\n"
        "║ 👑 <b>EXTREME ADMIN CENTER</b> ║\n"
        "╚══════════════════════════════╝\n\n"

        "🟢 <b>BOT STATUS: ONLINE</b>\n"
        f"🕒 <code>{now_string()}</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>LIVE OVERVIEW</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"👥 Total Users Seen: "
        f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n"

        f"🆕 Users Today: "
        f"<b>{fmt_number(DAILY_USERS.get(today, 0))}</b>\n"

        f"🔎 Total Searches: "
        f"<b>{fmt_number(STATS['searches'])}</b>\n"

        f"🔍 Searches Today: "
        f"<b>{fmt_number(DAILY_SEARCHES.get(today, 0))}</b>\n"

        f"📦 Total Indexed Files: "
        f"<b>{fmt_number(total_files)}</b>\n"

        f"💾 Database Storage: "
        f"<b>{fmt_bytes(total_db_size)}</b>\n"

        f"🗄 Databases Online: "
        f"<b>{online_db}/{len(databases)}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>LIVE SERVER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"⚡ CPU: "
        f"<b>{system['cpu']:.1f}%</b>\n"

        f"🧠 RAM: "
        f"<b>{system['ram_percent']:.1f}%</b> "
        f"({fmt_bytes(system['ram_used'])})\n"

        f"💽 Disk: "
        f"<b>{system['disk_percent']:.1f}%</b>\n"

        f"🐍 Python: "
        f"<b>{system['python']}</b>\n"

        f"⏱ Uptime: "
        f"<b>{uptime}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <b>CURRENT WORK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"📥 Indexed: "
        f"<b>{fmt_number(STATS['files_indexed'])}</b>\n"

        f"⏭ Skipped: "
        f"<b>{fmt_number(STATS['files_skipped'])}</b>\n"

        f"📤 Files Sent: "
        f"<b>{fmt_number(STATS['files_sent'])}</b>\n"

        f"⚠️ Errors: "
        f"<b>{fmt_number(STATS['errors'])}</b>\n"

        f"🚀 Active Tasks: "
        f"<b>{active_tasks}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📡 <b>LIVE MODE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🔄 Updates every "
        f"<b>{PANEL_UPDATE_SECONDS:.1f}s</b>\n"

        "🟢 Automatic monitoring enabled\n"

    )

    return text


# ============================================================
# STATISTICS PAGE
# ============================================================

def build_statistics():

    today = today_key()

    text = (

        "📊 <b>DOWNTOWN VILLA — STATISTICS</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👥 <b>USERS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"👤 Total tracked: "
        f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n"

        f"🆕 Today: "
        f"<b>{fmt_number(DAILY_USERS.get(today, 0))}</b>\n"

        f"🔎 Searches made: "
        f"<b>{fmt_number(STATS['searches'])}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔎 <b>SEARCH ACTIVITY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🔍 Total searches: "
        f"<b>{fmt_number(STATS['searches'])}</b>\n"

        f"📅 Today: "
        f"<b>{fmt_number(DAILY_SEARCHES.get(today, 0))}</b>\n"

        f"⚡ Commands: "
        f"<b>{fmt_number(STATS['commands'])}</b>\n"

        f"🔘 Callback actions: "
        f"<b>{fmt_number(STATS['callbacks'])}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📦 <b>FILE ACTIVITY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"📥 Indexed: "
        f"<b>{fmt_number(STATS['files_indexed'])}</b>\n"

        f"⏭ Skipped: "
        f"<b>{fmt_number(STATS['files_skipped'])}</b>\n"

        f"📤 Sent: "
        f"<b>{fmt_number(STATS['files_sent'])}</b>\n"

        f"❌ Errors: "
        f"<b>{fmt_number(STATS['errors'])}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📅 <b>TODAY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"👥 New users: "
        f"<b>{fmt_number(DAILY_USERS.get(today, 0))}</b>\n"

        f"🔎 Searches: "
        f"<b>{fmt_number(DAILY_SEARCHES.get(today, 0))}</b>\n"

        f"⚙️ Commands: "
        f"<b>{fmt_number(DAILY_COMMANDS.get(today, 0))}</b>\n"

    )

    return text


# ============================================================
# TOP SEARCHES
# ============================================================

def build_top_searches():

    text = (
        "🔥 <b>TOP SEARCHES</b>\n\n"
    )

    if not SEARCH_TERMS:

        return (
            text
            + "💤 No searches have been tracked yet."
        )

    for index, (
        term,
        count
    ) in enumerate(
        SEARCH_TERMS.most_common(20),
        start=1
    ):

        if index == 1:
            icon = "🥇"
        elif index == 2:
            icon = "🥈"
        elif index == 3:
            icon = "🥉"
        else:
            icon = "🔹"

        text += (
            f"{icon} <b>{index}.</b> "
            f"<code>{safe_html(shorten(term, 70))}</code>\n"
            f"   🔎 Searches: "
            f"<b>{fmt_number(count)}</b>\n\n"
        )

    return text[:4000]


# ============================================================
# COMMAND STATISTICS
# ============================================================

def build_command_statistics():

    text = (
        "⚙️ <b>COMMAND ACTIVITY</b>\n\n"
    )

    text += (
        f"📊 Total commands: "
        f"<b>{fmt_number(STATS['commands'])}</b>\n\n"
    )

    if not COMMAND_TERMS:

        text += (
            "💤 No command names recorded yet."
        )

        return text

    text += (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 <b>TOP COMMANDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for index, (
        command,
        count
    ) in enumerate(
        COMMAND_TERMS.most_common(20),
        start=1
    ):

        text += (
            f"🔹 <b>{index}.</b> "
            f"<code>{safe_html(command)}</code> "
            f"— <b>{fmt_number(count)}</b>\n"
        )

    return text[:4000]


# ============================================================
# USERS PAGE
# ============================================================

def build_users():

    text = (

        "👥 <b>DOWNTOWN VILLA — USER CENTER</b>\n\n"

        f"👤 Total tracked: "
        f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 <b>MOST ACTIVE USERS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    )

    if not USER_SEARCHES:

        text += (
            "💤 No user search activity recorded."
        )

        return text

    for index, (
        user_id,
        count
    ) in enumerate(
        USER_SEARCHES.most_common(15),
        start=1
    ):

        name = USER_NAMES.get(
            user_id,
            "Unknown"
        )

        last_seen = USER_LAST_SEEN.get(
            user_id
        )

        if last_seen:

            ago = fmt_duration(
                time.time()
                - last_seen
            )

            last_text = (
                f"{ago} ago"
            )

        else:

            last_text = "Unknown"

        text += (

            f"👤 <b>{index}. "
            f"{safe_html(name)}</b>\n"

            f"🆔 <code>{user_id}</code>\n"

            f"🔎 Searches: "
            f"<b>{fmt_number(count)}</b>\n"

            f"🕒 Last seen: "
            f"<b>{last_text}</b>\n\n"

        )

    return text[:4000]


# ============================================================
# RECENT USERS
# ============================================================

def build_recent_users():

    text = (
        "🕒 <b>RECENT USER ACTIVITY</b>\n\n"
    )

    users = sorted(
        USER_LAST_SEEN.items(),
        key=lambda item: item[1],
        reverse=True
    )[:20]

    if not users:

        return (
            text
            + "💤 No users tracked yet."
        )

    for index, (
        user_id,
        last_seen
    ) in enumerate(
        users,
        start=1
    ):

        name = USER_NAMES.get(
            user_id,
            "Unknown"
        )

        text += (

            f"{index}. 👤 "
            f"<b>{safe_html(name)}</b>\n"

            f"   🆔 <code>{user_id}</code>\n"

            f"   🕒 "
            f"{fmt_duration(time.time() - last_seen)} ago\n\n"

        )

    return text[:4000]


# ============================================================
# DATABASE PAGE
# ============================================================

async def build_database_page():

    databases = (
        await get_all_database_stats()
    )

    text = (
        "💾 <b>DATABASE CONTROL CENTER</b>\n\n"
    )

    total_files = 0

    total_size = 0

    for index, data in enumerate(
        databases
    ):

        status = data.get(
            "status",
            "UNKNOWN"
        )

        icon = status_icon(
            status
        )

        documents = data.get(
            "documents",
            0
        )

        size = data.get(
            "size",
            0
        )

        indexes = data.get(
            "indexes",
            0
        )

        text += (

            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

            f"{icon} <b>{safe_html(data.get('name'))}</b>\n\n"

            f"📦 Documents: "
            f"<b>{fmt_number(documents)}</b>\n"

            f"💽 Storage: "
            f"<b>{fmt_bytes(size)}</b>\n"

            f"🗂 Indexes: "
            f"<b>{fmt_number(indexes)}</b>\n"

            f"📡 Status: "
            f"<b>{status}</b>\n"

        )

        if data.get("error"):

            text += (
                f"⚠️ "
                f"<code>{safe_html(shorten(data['error'], 180))}</code>\n"
            )

        text += "\n"

        total_files += documents

        total_size += size

    text += (

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>DATABASE TOTAL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Total files: "
        f"<b>{fmt_number(total_files)}</b>\n"

        f"💾 Total storage: "
        f"<b>{fmt_bytes(total_size)}</b>\n"

    )

    return text[:4000]


# ============================================================
# DATABASE DETAIL
# ============================================================

async def build_database_detail():

    databases = (
        await get_all_database_stats()
    )

    text = (
        "🗄 <b>DATABASE DEEP STATUS</b>\n\n"
    )

    for data in databases:

        text += (

            f"{status_icon(data.get('status'))} "
            f"<b>{safe_html(data.get('name'))}</b>\n"

            f"📦 Documents: "
            f"{fmt_number(data.get('documents', 0))}\n"

            f"💾 Size: "
            f"{fmt_bytes(data.get('size', 0))}\n"

            f"🗂 Indexes: "
            f"{fmt_number(data.get('indexes', 0))}\n"

            f"📡 Status: "
            f"{data.get('status', 'UNKNOWN')}\n\n"

        )

    text += (
        "ℹ️ <i>Database information is read directly "
        "from the connected MongoDB databases.</i>"
    )

    return text[:4000]


# ============================================================
# LIVE TASK PAGE
# ============================================================

def build_tasks_text():

    running = [
        task
        for task in LIVE_TASKS.values()
        if task.get("status") == "RUNNING"
    ]

    completed = [
        task
        for task in LIVE_TASKS.values()
        if task.get("status") != "RUNNING"
    ]

    text = (
        "🚀 <b>CURRENT WORK CENTER</b>\n\n"
    )

    if not LIVE_TASKS:

        return (
            text
            + "💤 <b>NO ACTIVE WORK</b>\n\n"
            "The bot currently has no task registered "
            "with the live task system."
        )

    text += (
        f"🟢 Running: <b>{len(running)}</b>\n"
        f"📋 Other: <b>{len(completed)}</b>\n\n"
    )

    for task_id, task in list(
        LIVE_TASKS.items()
    ):

        status = task.get(
            "status",
            "UNKNOWN"
        )

        icon = status_icon(
            status
        )

        current = task.get(
            "current",
            0
        )

        total = task.get(
            "total",
            0
        )

        speed = task.get(
            "speed",
            0
        )

        started = task.get(
            "started",
            time.time()
        )

        text += (

            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

            f"{icon} <b>{safe_html(task.get('name', task_id))}</b>\n"

            f"🏷 Type: "
            f"<b>{safe_html(task.get('type', 'WORK'))}</b>\n"

            f"📡 Status: "
            f"<b>{safe_html(status)}</b>\n"

        )

        if total:

            text += (

                f"📊 "
                f"{progress_bar(current, total)}\n"

                f"📦 "
                f"{fmt_number(current)} / "
                f"{fmt_number(total)}\n"

            )

        if speed:

            text += (
                f"⚡ Speed: "
                f"<b>{speed:.2f}/sec</b>\n"
            )

        text += (

            f"⏱ Runtime: "
            f"<b>{fmt_duration(time.time() - started)}</b>\n"

        )

        if task.get("message"):

            text += (

                f"💬 "
                f"<code>{safe_html(shorten(task['message'], 180))}</code>\n"

            )

        text += "\n"

    return text[:4000]


# ============================================================
# LIVE LOGS
# ============================================================

def build_logs_text():

    text = (
        "📋 <b>LIVE BOT LOGS</b>\n\n"
    )

    if not LIVE_LOGS:

        return (
            text
            + "💤 No logs captured yet."
        )

    logs = list(
        LIVE_LOGS
    )[-25:]

    for item in logs:

        level = item.get(
            "level",
            "INFO"
        )

        if level in (
            "ERROR",
            "CRITICAL",
        ):

            icon = "🔴"

        elif level == "WARNING":

            icon = "🟡"

        elif level == "DEBUG":

            icon = "🔵"

        else:

            icon = "🟢"

        message = safe_html(
            item.get(
                "message",
                ""
            )
        )

        message = shorten(
            message,
            180
        )

        text += (

            f"<code>{item.get('time', '')}</code> "
            f"{icon} "
            f"<b>{level}</b>\n"

            f"{message}\n\n"

        )

    return text[:4000]


# ============================================================
# ERROR PAGE
# ============================================================

def build_errors():

    text = (
        "🚨 <b>ERROR CENTER</b>\n\n"
    )

    text += (
        f"❌ Total errors: "
        f"<b>{fmt_number(STATS['errors'])}</b>\n\n"
    )

    if not ERROR_BUFFER:

        text += (
            "🟢 <b>NO RECORDED ERRORS</b>\n"
        )

        return text

    text += (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔴 <b>RECENT ERRORS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for item in list(
        ERROR_BUFFER
    )[-15:]:

        text += (

            f"⏰ <code>{item.get('time', '')}</code>\n"

            f"🔴 "
            f"<code>{safe_html(shorten(item.get('message', ''), 250))}</code>\n\n"

        )

    return text[:4000]


# ============================================================
# SYSTEM PAGE
# ============================================================

def build_system():

    info = system_info()

    text = (

        "🖥️ <b>DOWNTOWN VILLA — SYSTEM</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>CPU</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"⚡ CPU usage: "
        f"<b>{info['cpu']:.1f}%</b>\n"

        f"⚙️ Process CPU: "
        f"<b>{info['process_cpu']:.1f}%</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🧠 <b>MEMORY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🧠 RAM usage: "
        f"<b>{info['ram_percent']:.1f}%</b>\n"

        f"📌 Used: "
        f"<b>{fmt_bytes(info['ram_used'])}</b>\n"

        f"📦 Total: "
        f"<b>{fmt_bytes(info['ram_total'])}</b>\n"

        f"🤖 Bot process RAM: "
        f"<b>{fmt_bytes(info['process_memory'])}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💽 <b>DISK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"💽 Usage: "
        f"<b>{info['disk_percent']:.1f}%</b>\n"

        f"📌 Used: "
        f"<b>{fmt_bytes(info['disk_used'])}</b>\n"

        f"📦 Total: "
        f"<b>{fmt_bytes(info['disk_total'])}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🐍 <b>RUNTIME</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🐍 Python: "
        f"<b>{info['python']}</b>\n"

        f"💻 Machine: "
        f"<b>{safe_html(info['machine'])}</b>\n"

        f"🖥 Platform: "
        f"<code>{safe_html(shorten(info['platform'], 100))}</code>\n"

        f"⏱ Uptime: "
        f"<b>{fmt_duration(time.time() - START_TIME)}</b>\n\n"

        f"👥 Memory users: "
        f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n"

        f"🚀 Live tasks: "
        f"<b>{fmt_number(len(LIVE_TASKS))}</b>\n"

    )

    return text[:4000]


# ============================================================
# ACTIVITY PAGE
# ============================================================

def build_activity():

    text = (
        "📡 <b>LIVE ACTIVITY FEED</b>\n\n"
    )

    if not RECENT_ACTIVITY:

        return (
            text
            + "💤 No activity recorded yet."
        )

    for item in list(
        RECENT_ACTIVITY
    )[-25:]:

        activity_type = str(
            item.get(
                "type",
                "ACTIVITY"
            )
        )

        if activity_type == "SEARCH":

            icon = "🔎"

        elif activity_type == "COMMAND":

            icon = "⚙️"

        elif activity_type == "INDEX":

            icon = "📥"

        elif activity_type == "FILE":

            icon = "📤"

        elif activity_type == "ERROR":

            icon = "🔴"

        else:

            icon = "📡"

        age = fmt_duration(
            time.time()
            - item.get(
                "time",
                time.time()
            )
        )

        text += (

            f"{icon} <b>{safe_html(activity_type)}</b> "
            f"<i>{age} ago</i>\n"

            f"   {safe_html(shorten(item.get('message', ''), 200))}\n"

        )

        if item.get("user_id"):

            text += (
                f"   👤 "
                f"<code>{item['user_id']}</code>\n"
            )

        text += "\n"

    return text[:4000]


# ============================================================
# CONFIGURATION PAGE
# ============================================================

def build_configuration():

    admins = ", ".join(
        str(x)
        for x in ADMIN_IDS
    )

    text = (

        "⚙️ <b>ADMIN PANEL CONFIGURATION</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👑 <b>ADMINS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"👥 Admin count: "
        f"<b>{len(ADMIN_IDS)}</b>\n"

        f"🆔 IDs: "
        f"<code>{safe_html(admins or 'NONE')}</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔄 <b>LIVE MONITORING</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"⏱ Update interval: "
        f"<b>{PANEL_UPDATE_SECONDS:.1f}s</b>\n"

        f"📋 Log buffer: "
        f"<b>{MAX_LIVE_LOGS}</b>\n"

        f"🚀 Task buffer: "
        f"<b>{MAX_LIVE_TASKS}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🗄 <b>DATABASE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"📚 Collection: "
        f"<code>{safe_html(COLLECTION_NAME)}</code>\n"

        f"🔗 Multiple DB: "
        f"<b>{MULTIPLE_DB}</b>\n"

    )

    return text


# ============================================================
# HELP / INTEGRATION PAGE
# ============================================================

def build_help():

    return (

        "🛠 <b>ADMIN PANEL INTEGRATION</b>\n\n"

        "This panel can receive live task information "
        "from your other plugins.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <b>START A TASK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<code>start_live_task(\n"
        "    \"indexing\",\n"
        "    \"Channel Indexing\",\n"
        "    \"INDEXING\",\n"
        "    100000\n"
        ")</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>UPDATE TASK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<code>update_live_task(\n"
        "    \"indexing\",\n"
        "    current=5000,\n"
        "    message=\"Processing files\"\n"
        ")</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ <b>FINISH TASK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<code>finish_live_task(\n"
        "    \"indexing\",\n"
        "    \"COMPLETED\"\n"
        ")</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔎 <b>SEARCH TRACKING</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<code>track_search(user_id, query)</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📥 <b>INDEX TRACKING</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        "<code>track_indexed(1)\n"
        "track_skipped(1)</code>\n"

    )


# ============================================================
# MAIN KEYBOARD
# ============================================================

def main_keyboard(
    live=False
):

    live_text = (
        "🟢 LIVE MODE ON"
        if live
        else "🔴 LIVE MODE OFF"
    )

    return InlineKeyboardMarkup(

        [

            [

                InlineKeyboardButton(
                    "📊 Statistics",
                    callback_data="dv:stats"
                ),

                InlineKeyboardButton(
                    "👥 Users",
                    callback_data="dv:users"
                ),

            ],

            [

                InlineKeyboardButton(
                    "🔎 Searches",
                    callback_data="dv:searches"
                ),

                InlineKeyboardButton(
                    "🔥 Top Searches",
                    callback_data="dv:top"
                ),

            ],

            [

                InlineKeyboardButton(
                    "💾 Database",
                    callback_data="dv:database"
                ),

                InlineKeyboardButton(
                    "🗄 DB Deep",
                    callback_data="dv:dbdeep"
                ),

            ],

            [

                InlineKeyboardButton(
                    "🚀 Current Work",
                    callback_data="dv:tasks"
                ),

                InlineKeyboardButton(
                    "📡 Activity",
                    callback_data="dv:activity"
                ),

            ],

            [

                InlineKeyboardButton(
                    "📋 Live Logs",
                    callback_data="dv:logs"
                ),

                InlineKeyboardButton(
                    "🚨 Errors",
                    callback_data="dv:errors"
                ),

            ],

            [

                InlineKeyboardButton(
                    "🖥 System",
                    callback_data="dv:system"
                ),

                InlineKeyboardButton(
                    "⚙️ Commands",
                    callback_data="dv:commands"
                ),

            ],

            [

                InlineKeyboardButton(
                    "👤 Recent Users",
                    callback_data="dv:recentusers"
                ),

                InlineKeyboardButton(
                    "🔧 Configuration",
                    callback_data="dv:config"
                ),

            ],

            [

                InlineKeyboardButton(
                    live_text,
                    callback_data="dv:togglelive"
                ),

                InlineKeyboardButton(
                    "🛠 Help",
                    callback_data="dv:help"
                ),

            ],

            [

                InlineKeyboardButton(
                    "🔄 DASHBOARD",
                    callback_data="dv:home"
                ),

            ],

        ]

    )


# ============================================================
# BACK KEYBOARD
# ============================================================

def back_keyboard():

    return InlineKeyboardMarkup(

        [

            [

                InlineKeyboardButton(
                    "⬅️ Dashboard",
                    callback_data="dv:home"
                ),

                InlineKeyboardButton(
                    "🔄 Update",
                    callback_data="dv:refresh"
                ),

            ]

        ]

    )


# ============================================================
# /ADMIN COMMAND
# ============================================================

@Client.on_message(
    filters.command(
        [
            "admin",
            "adm",
        ]
    )
)
async def admin_command(
    client,
    message,
):

    user = message.from_user

    user_id = (
        user.id
        if user
        else None
    )

    if not is_admin(
        user_id
    ):

        return

    track_command(
        user_id,
        "/admin"
    )

    text = await build_dashboard()

    sent = await message.reply_text(

        text,

        reply_markup=main_keyboard(
            live=False
        ),

        parse_mode=enums.ParseMode.HTML,

        disable_web_page_preview=True,

    )

    ACTIVE_PANELS[
        user_id
    ] = {

        "chat_id": message.chat.id,

        "message_id": sent.id,

        "mode": "home",

        "live": False,

    }


# ============================================================
# CALLBACK ROUTER
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^dv:"
    )
)
async def admin_callback(
    client,
    query,
):

    user_id = (
        query.from_user.id
        if query.from_user
        else None
    )

    if not is_admin(
        user_id
    ):

        await query.answer(
            "⛔ Admin access only.",
            show_alert=True
        )

        return

    STATS[
        "callbacks"
    ] += 1

    action = query.data[
        3:
    ]

    try:

        await query.answer()

    except Exception:
        pass


    # ========================================================
    # HOME
    # ========================================================

    if action in (
        "home",
        "refresh",
    ):

        panel = ACTIVE_PANELS.get(
            user_id,
            {}
        )

        live = panel.get(
            "live",
            False
        )

        text = await build_dashboard()

        try:

            await query.message.edit_text(

                text,

                reply_markup=main_keyboard(
                    live=live
                ),

                parse_mode=enums.ParseMode.HTML,

                disable_web_page_preview=True,

            )

        except Exception:
            pass

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "home",

            "live": live,

        }

        return


    # ========================================================
    # TOGGLE LIVE
    # ========================================================

    if action == "togglelive":

        panel = ACTIVE_PANELS.get(
            user_id,
            {}
        )

        live = not panel.get(
            "live",
            False
        )

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "home",

            "live": live,

        }

        text = await build_dashboard()

        try:

            await query.message.edit_text(

                text,

                reply_markup=main_keyboard(
                    live=live
                ),

                parse_mode=enums.ParseMode.HTML,

                disable_web_page_preview=True,

            )

        except Exception:
            pass

        try:

            await query.answer(
                "🟢 Live monitoring enabled."
                if live
                else
                "🔴 Live monitoring disabled."
            )

        except Exception:
            pass

        return


    # ========================================================
    # STATISTICS
    # ========================================================

    if action == "stats":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "stats",

            "live": True,

        }

        try:

            await query.message.edit_text(

                build_statistics(),

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # USERS
    # ========================================================

    if action == "users":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "users",

            "live": True,

        }

        try:

            await query.message.edit_text(

                build_users(),

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # RECENT USERS
    # ========================================================

    if action == "recentusers":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "recentusers",

            "live": True,

        }

        try:

            await query.message.edit_text(

                build_recent_users(),

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # SEARCHES
    # ========================================================

    if action == "searches":

        text = (

            "🔎 <b>SEARCH CENTER</b>\n\n"

            f"🔍 Total searches: "
            f"<b>{fmt_number(STATS['searches'])}</b>\n"

            f"📅 Today: "
            f"<b>{fmt_number(DAILY_SEARCHES.get(today_key(), 0))}</b>\n\n"

            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

            "🔥 <b>Top search terms</b>\n\n"

        )

        if SEARCH_TERMS:

            for index, (
                term,
                count
            ) in enumerate(
                SEARCH_TERMS.most_common(10),
                start=1
            ):

                text += (

                    f"{index}. "
                    f"<code>{safe_html(shorten(term, 60))}</code> "
                    f"— <b>{count}</b>\n"

                )

        else:

            text += (
                "💤 No searches tracked yet."
            )

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "searches",

            "live": True,

        }

        try:

            await query.message.edit_text(

                text[:4000],

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # TOP SEARCHES
    # ========================================================

    if action == "top":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "top",

            "live": True,

        }

        try:

            await query.message.edit_text(

                build_top_searches(),

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # COMMANDS
    # ========================================================

    if action == "commands":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "commands",

            "live": True,

        }

        try:

            await query.message.edit_text(

                build_command_statistics(),

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # DATABASE
    # ========================================================

    if action == "database":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "database",

            "live": True,

        }

        text = await build_database_page()

        try:

            await query.message.edit_text(

                text,

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # DATABASE DEEP
    # ========================================================

    if action == "dbdeep":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "dbdeep",

            "live": True,

        }

        text = await build_database_detail()

        try:

            await query.message.edit_text(

                text,

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # CURRENT WORK
    # ========================================================

    if action == "tasks":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "tasks",

            "live": True,

        }

        try:

            await query.message.edit_text(

                build_tasks_text(),

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # LOGS
    # ========================================================

    if action == "logs":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "logs",

            "live": True,

        }

        try:

            await query.message.edit_text(

                build_logs_text(),

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # ERRORS
    # ========================================================

    if action == "errors":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "errors",

            "live": True,

        }

        try:

            await query.message.edit_text(

                build_errors(),

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # SYSTEM
    # ========================================================

    if action == "system":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "system",

            "live": True,

        }

        try:

            await query.message.edit_text(

                build_system(),

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # ACTIVITY
    # ========================================================

    if action == "activity":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "activity",

            "live": True,

        }

        try:

            await query.message.edit_text(

                build_activity(),

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # CONFIG
    # ========================================================

    if action == "config":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "config",

            "live": False,

        }

        try:

            await query.message.edit_text(

                build_configuration(),

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


    # ========================================================
    # HELP
    # ========================================================

    if action == "help":

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id": query.message.chat.id,

            "message_id": query.message.id,

            "mode": "help",

            "live": False,

        }

        try:

            await query.message.edit_text(

                build_help(),

                reply_markup=back_keyboard(),

                parse_mode=enums.ParseMode.HTML,

            )

        except Exception:
            pass

        return


# ============================================================
# LIVE PANEL UPDATE LOOP
# ============================================================

async def live_panel_loop():

    while True:

        try:

            await asyncio.sleep(
                PANEL_UPDATE_SECONDS
            )

            if not ACTIVE_PANELS:

                continue

            client = _admin_client

            if client is None:

                continue

            for user_id, panel in list(
                ACTIVE_PANELS.items()
            ):

                try:

                    if not panel.get(
                        "live",
                        False
                    ):
                        continue

                    if not is_admin(
                        user_id
                    ):
                        continue

                    chat_id = panel.get(
                        "chat_id"
                    )

                    message_id = panel.get(
                        "message_id"
                    )

                    mode = panel.get(
                        "mode",
                        "home"
                    )

                    if not chat_id or not message_id:
                        continue


                    # ==========================================
                    # BUILD CORRECT LIVE PAGE
                    # ==========================================

                    if mode == "home":

                        text = await build_dashboard()

                        keyboard = main_keyboard(
                            live=True
                        )

                    elif mode == "stats":

                        text = build_statistics()

                        keyboard = back_keyboard()

                    elif mode == "users":

                        text = build_users()

                        keyboard = back_keyboard()

                    elif mode == "recentusers":

                        text = build_recent_users()

                        keyboard = back_keyboard()

                    elif mode == "searches":

                        text = (

                            "🔎 <b>SEARCH CENTER</b>\n\n"

                            f"🔍 Total: "
                            f"<b>{fmt_number(STATS['searches'])}</b>\n"

                            f"📅 Today: "
                            f"<b>{fmt_number(DAILY_SEARCHES.get(today_key(), 0))}</b>\n\n"

                            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            "🔥 <b>LIVE TOP SEARCHES</b>\n\n"

                        )

                        for index, (
                            term,
                            count
                        ) in enumerate(
                            SEARCH_TERMS.most_common(15),
                            start=1
                        ):

                            text += (

                                f"{index}. "
                                f"<code>{safe_html(shorten(term, 60))}</code> "
                                f"— <b>{count}</b>\n"

                            )

                        keyboard = back_keyboard()

                    elif mode == "top":

                        text = build_top_searches()

                        keyboard = back_keyboard()

                    elif mode == "commands":

                        text = build_command_statistics()

                        keyboard = back_keyboard()

                    elif mode == "database":

                        text = await build_database_page()

                        keyboard = back_keyboard()

                    elif mode == "dbdeep":

                        text = await build_database_detail()

                        keyboard = back_keyboard()

                    elif mode == "tasks":

                        text = build_tasks_text()

                        keyboard = back_keyboard()

                    elif mode == "logs":

                        text = build_logs_text()

                        keyboard = back_keyboard()

                    elif mode == "errors":

                        text = build_errors()

                        keyboard = back_keyboard()

                    elif mode == "system":

                        text = build_system()

                        keyboard = back_keyboard()

                    elif mode == "activity":

                        text = build_activity()

                        keyboard = back_keyboard()

                    else:

                        continue


                    # ==========================================
                    # TELEGRAM EDIT
                    # ==========================================

                    try:

                        await client.edit_message_text(

                            chat_id=chat_id,

                            message_id=message_id,

                            text=text[:4000],

                            reply_markup=keyboard,

                            parse_mode=enums.ParseMode.HTML,

                            disable_web_page_preview=True,

                        )

                    except FloodWait as e:

                        await asyncio.sleep(
                            int(
                                getattr(
                                    e,
                                    "value",
                                    5
                                )
                            )
                        )

                    except RPCError:

                        pass

                    except Exception:

                        pass

                except Exception:

                    continue

        except asyncio.CancelledError:

            break

        except Exception:

            logger.exception(
                "[DOWNTOWN VILLA ADMIN] "
                "Live panel loop error"
            )


# ============================================================
# GLOBAL CLIENT
# ============================================================

_admin_client = None

_live_panel_task = None


# ============================================================
# INITIALIZATION
# ============================================================

def initialize_admin_panel(
    client
):

    global _admin_client
    global _live_panel_task

    _admin_client = client

    if _live_panel_task is None:

        try:

            _live_panel_task = asyncio.create_task(
                live_panel_loop()
            )

            logger.info(
                "[DOWNTOWN VILLA ADMIN] "
                "Extreme live admin panel started."
            )

        except Exception:

            logger.exception(
                "[DOWNTOWN VILLA ADMIN] "
                "Could not start live panel."
            )


# ============================================================
# AUTO INITIALIZATION
#
# The first /admin call also initializes the panel.
# ============================================================

_original_admin_command = admin_command


# ============================================================
# PATCHED ADMIN COMMAND
# ============================================================

@Client.on_message(
    filters.command(
        "adminpanel_init"
    )
)
async def adminpanel_init(
    client,
    message,
):

    global _admin_client

    _admin_client = client

    initialize_admin_panel(
        client
    )

    return


# ============================================================
# SECOND INITIALIZER
#
# This is harmless and only initializes when the command
# below is used. The real panel still works from /admin.
# ============================================================

@Client.on_message(
    filters.command(
        "dvadmininit"
    )
)
async def dvadmininit(
    client,
    message,
):

    user_id = (
        message.from_user.id
        if message.from_user
        else None
    )

    if not is_admin(
        user_id
    ):
        return

    initialize_admin_panel(
        client
    )

    await message.reply_text(
        "🟢 <b>Downtown Villa Admin Monitor Initialized.</b>",
        parse_mode=enums.ParseMode.HTML
    )


# ============================================================
# IMPORTANT:
# Automatically initialize when /admin is opened.
# ============================================================

@Client.on_message(
    filters.command(
        "admin"
    )
)
async def admin_auto_initializer(
    client,
    message,
):

    global _admin_client

    user_id = (
        message.from_user.id
        if message.from_user
        else None
    )

    if not is_admin(
        user_id
    ):
        return

    _admin_client = client

    initialize_admin_panel(
        client
    )


# ============================================================
# STARTUP LOGGING
# ============================================================

logger.info(
    "=================================================="
)

logger.info(
    "[DOWNTOWN VILLA ADMIN] "
    "EXTREME ADMIN CONTROL CENTER LOADED"
)

logger.info(
    "[DOWNTOWN VILLA ADMIN] "
    "Admins configured: %s",
    len(ADMIN_IDS)
)

logger.info(
    "[DOWNTOWN VILLA ADMIN] "
    "Live update interval: %.1fs",
    PANEL_UPDATE_SECONDS
)

logger.info(
    "[DOWNTOWN VILLA ADMIN] "
    "Live logs: ENABLED"
)

logger.info(
    "[DOWNTOWN VILLA ADMIN] "
    "Database monitoring: ENABLED"
)

logger.info(
    "[DOWNTOWN VILLA ADMIN] "
    "Live task monitoring: ENABLED"
)

logger.info(
    "=================================================="
)
