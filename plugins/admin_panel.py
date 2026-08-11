import os
import time
import asyncio
import logging
import platform
import shutil
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
except Exception:
    db = None
    db2 = None
    db3 = None

    Media = None
    Media2 = None
    Media3 = None


# ============================================================
# BOT CONFIG
# ============================================================

try:
    from info import COLLECTION_NAME, MULTIPLE_DB
except Exception:
    COLLECTION_NAME = "Telegram_files"
    MULTIPLE_DB = True


# ============================================================
# LOGGER
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# DOWNTOWN VILLA
# ULTIMATE LIVE ADMIN CONTROL CENTER
# ============================================================


# ============================================================
# ADMINS
#
# Render Environment:
#
# ADMINS=123456789 987654321
#
# OR
#
# ADMINS=123456789,987654321
# ============================================================

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMINS", "").replace(",", " ").split()
    if x.strip().isdigit()
}


# ============================================================
# SETTINGS
# ============================================================

PANEL_UPDATE_SECONDS = 3

MAX_LIVE_LOGS = 100

MAX_TASKS = 50

START_TIME = time.time()


# ============================================================
# GLOBAL STATISTICS
# ============================================================

STATS = {
    "searches": 0,
    "users_seen": 0,
    "files_sent": 0,
    "files_indexed": 0,
    "files_skipped": 0,
    "errors": 0,
    "commands": 0,
}


# ============================================================
# USER DATA
# ============================================================

KNOWN_USERS = set()

DAILY_USERS = Counter()

DAILY_SEARCHES = Counter()

USER_SEARCHES = Counter()

USER_LAST_SEEN = {}

SEARCH_TERMS = Counter()


# ============================================================
# LIVE TASKS
#
# Example:
#
# start_live_task(
#     "indexing",
#     "Telegram Indexing",
#     "INDEX",
#     100000
# )
#
# update_live_task(
#     "indexing",
#     current=50000,
#     message="Processing files..."
# )
#
# finish_live_task(
#     "indexing",
#     "COMPLETED"
# )
# ============================================================

LIVE_TASKS = {}


# ============================================================
# LIVE LOG BUFFER
# ============================================================

LIVE_LOGS = deque(maxlen=MAX_LIVE_LOGS)


# ============================================================
# ACTIVE ADMIN PANELS
# ============================================================

ACTIVE_PANELS = {}


# ============================================================
# DATABASE CACHE
# ============================================================

DB_CACHE = {
    "time": 0,
    "data": [],
}


# ============================================================
# LOG HANDLER
# ============================================================

class TelegramMemoryLogHandler(logging.Handler):

    def emit(self, record):

        try:
            now = datetime.now().strftime("%H:%M:%S")

            level = record.levelname

            message = record.getMessage()

            if len(message) > 350:
                message = message[:350] + "..."

            LIVE_LOGS.append(
                {
                    "time": now,
                    "level": level,
                    "message": message,
                }
            )

        except Exception:
            pass


try:
    memory_handler = TelegramMemoryLogHandler()

    memory_handler.setLevel(logging.INFO)

    logging.getLogger().addHandler(memory_handler)

except Exception:
    pass


# ============================================================
# BASIC HELPERS
# ============================================================

def is_admin(user_id):

    try:
        return (
            user_id is not None
            and int(user_id) in ADMIN_IDS
        )
    except Exception:
        return False


def fmt_number(value):

    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def fmt_bytes(value):

    try:

        value = float(value)

        units = [
            "B",
            "KB",
            "MB",
            "GB",
            "TB",
        ]

        index = 0

        while value >= 1024 and index < len(units) - 1:
            value /= 1024
            index += 1

        return f"{value:.1f} {units[index]}"

    except Exception:
        return "0 B"


def fmt_duration(seconds):

    try:

        seconds = int(seconds)

        days, seconds = divmod(seconds, 86400)

        hours, seconds = divmod(seconds, 3600)

        minutes, seconds = divmod(seconds, 60)

        if days:
            return f"{days}d {hours}h {minutes}m"

        if hours:
            return f"{hours}h {minutes}m {seconds}s"

        if minutes:
            return f"{minutes}m {seconds}s"

        return f"{seconds}s"

    except Exception:
        return "0s"


def today_key():

    return datetime.now().strftime("%Y-%m-%d")


def human_today():

    return datetime.now().strftime("%d %b %Y")


# ============================================================
# VISUAL BAR
# ============================================================

def visual_bar(
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
            percentage = (current / total) * 100

        percentage = max(
            0,
            min(100, percentage),
        )

        filled = int(
            length * percentage / 100
        )

        empty = length - filled

        return (
            "🟩" * filled
            + "⬜" * empty
            + f" {percentage:.1f}%"
        )

    except Exception:

        return "⬜" * length + " 0%"


def usage_bar(
    percentage,
    length=15,
):

    try:

        percentage = float(percentage)

        percentage = max(
            0,
            min(100, percentage),
        )

        filled = int(
            length * percentage / 100
        )

        empty = length - filled

        if percentage >= 90:
            icon = "🔴"
        elif percentage >= 70:
            icon = "🟠"
        else:
            icon = "🟢"

        return (
            icon
            + "🟦" * filled
            + "⬜" * empty
            + f" {percentage:.1f}%"
        )

    except Exception:

        return "⚪⬜" * length + " 0%"


# ============================================================
# USER TRACKING
# ============================================================

def track_user(user_id):

    if not user_id:
        return

    try:

        user_id = int(user_id)

        if user_id not in KNOWN_USERS:

            KNOWN_USERS.add(user_id)

            STATS["users_seen"] = len(KNOWN_USERS)

            DAILY_USERS[today_key()] += 1

        USER_LAST_SEEN[user_id] = time.time()

    except Exception:
        pass


# ============================================================
# SEARCH TRACKING
# ============================================================

def track_search(
    user_id,
    query="",
):

    try:

        track_user(user_id)

        STATS["searches"] += 1

        DAILY_SEARCHES[today_key()] += 1

        if user_id:

            USER_SEARCHES[int(user_id)] += 1

        if query:

            clean = str(query).strip().lower()

            if clean:

                SEARCH_TERMS[clean] += 1

    except Exception:
        pass


# ============================================================
# COMMAND TRACKING
# ============================================================

def track_command(user_id):

    try:

        track_user(user_id)

        STATS["commands"] += 1

    except Exception:
        pass


# ============================================================
# FILE TRACKING
# ============================================================

def track_file_sent(count=1):

    try:
        STATS["files_sent"] += int(count)
    except Exception:
        pass


def track_indexed(count=1):

    try:
        STATS["files_indexed"] += int(count)
    except Exception:
        pass


def track_skipped(count=1):

    try:
        STATS["files_skipped"] += int(count)
    except Exception:
        pass


def track_error(count=1):

    try:
        STATS["errors"] += int(count)
    except Exception:
        pass


# ============================================================
# LIVE TASK FUNCTIONS
# ============================================================

def start_live_task(
    task_id,
    name,
    task_type="WORK",
    total=0,
):

    task_id = str(task_id)

    LIVE_TASKS[task_id] = {
        "name": str(name),
        "type": str(task_type),
        "current": 0,
        "total": int(total or 0),
        "status": "RUNNING",
        "started": time.time(),
        "updated": time.time(),
        "speed": 0,
        "message": "",
    }

    while len(LIVE_TASKS) > MAX_TASKS:

        first_key = next(
            iter(LIVE_TASKS)
        )

        LIVE_TASKS.pop(
            first_key,
            None,
        )


def update_live_task(
    task_id,
    current=None,
    total=None,
    speed=None,
    message=None,
):

    task = LIVE_TASKS.get(
        str(task_id)
    )

    if not task:
        return

    if current is not None:
        task["current"] = int(current)

    if total is not None:
        task["total"] = int(total)

    if speed is not None:
        task["speed"] = float(speed)

    if message is not None:
        task["message"] = str(message)

    task["updated"] = time.time()


def finish_live_task(
    task_id,
    status="COMPLETED",
):

    task = LIVE_TASKS.get(
        str(task_id)
    )

    if not task:
        return

    task["status"] = status

    task["updated"] = time.time()


def remove_live_task(task_id):

    LIVE_TASKS.pop(
        str(task_id),
        None,
    )


# ============================================================
# DATABASE INFORMATION
# ============================================================

async def database_stats(
    database,
    model,
):

    result = {
        "documents": 0,
        "size": 0,
        "status": "OFFLINE",
    }

    if database is None:

        return result

    try:

        stats = await database.command(
            "collStats",
            COLLECTION_NAME,
        )

        result["documents"] = int(
            stats.get("count", 0)
        )

        result["size"] = int(
            stats.get(
                "storageSize",
                stats.get("size", 0),
            )
        )

        result["status"] = "ONLINE"

    except Exception as e:

        result["status"] = "ERROR"

        result["error"] = str(e)

        try:

            if model is not None:

                result["documents"] = await model.count_documents({})

        except Exception:
            pass

    return result


async def get_all_database_stats():

    global DB_CACHE

    now = time.time()

    # Don't hammer Mongo every single edit.
    # Dashboard itself still updates every 3 seconds.
    if now - DB_CACHE["time"] < 5:
        return DB_CACHE["data"]

    databases = [
        (
            "PRIMARY",
            db,
            Media,
        )
    ]

    if MULTIPLE_DB:

        databases.extend(
            [
                (
                    "SECONDARY",
                    db2,
                    Media2,
                ),
                (
                    "TERTIARY",
                    db3,
                    Media3,
                ),
            ]
        )

    tasks = [
        database_stats(database, model)
        for _, database, model in databases
    ]

    try:

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

    except Exception:

        return DB_CACHE["data"]

    final = []

    for index, result in enumerate(results):

        if isinstance(result, Exception):

            result = {
                "documents": 0,
                "size": 0,
                "status": "ERROR",
                "error": str(result),
            }

        name = databases[index][0]

        result["name"] = name

        final.append(result)

    DB_CACHE["time"] = now

    DB_CACHE["data"] = final

    return final


# ============================================================
# SYSTEM INFORMATION
# ============================================================

def system_info():

    try:
        cpu = psutil.cpu_percent(
            interval=None
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

        disk = shutil.disk_usage("/")

        disk_used = disk.used

        disk_total = disk.total

        disk_percent = (
            disk.used / disk.total * 100
            if disk.total
            else 0
        )

    except Exception:

        disk_used = 0

        disk_total = 0

        disk_percent = 0

    return {
        "cpu": cpu,
        "ram_used": ram_used,
        "ram_total": ram_total,
        "ram_percent": ram_percent,
        "disk_used": disk_used,
        "disk_total": disk_total,
        "disk_percent": disk_percent,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


# ============================================================
# DATABASE VISUAL
# ============================================================

def database_visual(
    name,
    data,
):

    documents = data.get(
        "documents",
        0,
    )

    size = data.get(
        "size",
        0,
    )

    status = data.get(
        "status",
        "UNKNOWN",
    )

    if status == "ONLINE":
        status_icon = "🟢"
    elif status == "ERROR":
        status_icon = "🔴"
    else:
        status_icon = "⚪"

    return (
        f"{status_icon} <b>{name} DATABASE</b>\n"
        f"📦 Files: <b>{fmt_number(documents)}</b>\n"
        f"💾 Storage: <b>{fmt_bytes(size)}</b>\n"
        f"📡 Status: <b>{status}</b>\n"
    )


# ============================================================
# MAIN DASHBOARD
# ============================================================

async def build_dashboard():

    system = system_info()

    databases = await get_all_database_stats()

    total_files = sum(
        item.get("documents", 0)
        for item in databases
    )

    total_size = sum(
        item.get("size", 0)
        for item in databases
    )

    today = today_key()

    uptime = fmt_duration(
        time.time() - START_TIME
    )

    active_tasks = len(LIVE_TASKS)

    dashboard = (
        "╔══════════════════════════════╗\n"
        "║ 🏙️ <b>DOWNTOWN VILLA</b>        ║\n"
        "║ 👑 <b>ULTIMATE ADMIN CENTER</b> ║\n"
        "╚══════════════════════════════╝\n\n"

        "🟢 <b>BOT STATUS: ONLINE</b>\n"
        "⚡ <b>LIVE MONITORING: ACTIVE</b>\n"
        "🔄 <b>AUTO UPDATE: 3 SECONDS</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>LIVE OVERVIEW</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"👥 Total Users: "
        f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n"

        f"🆕 Users Today: "
        f"<b>{fmt_number(DAILY_USERS.get(today, 0))}</b>\n"

        f"🔎 Total Searches: "
        f"<b>{fmt_number(STATS['searches'])}</b>\n"

        f"🔍 Searches Today: "
        f"<b>{fmt_number(DAILY_SEARCHES.get(today, 0))}</b>\n"

        f"📦 Total Files: "
        f"<b>{fmt_number(total_files)}</b>\n"

        f"💾 Total Storage: "
        f"<b>{fmt_bytes(total_size)}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🖥️ <b>SERVER HEALTH</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"⚡ CPU\n"
        f"{usage_bar(system['cpu'])}\n\n"

        f"🧠 RAM\n"
        f"{usage_bar(system['ram_percent'])}\n"
        f"   {fmt_bytes(system['ram_used'])} / "
        f"{fmt_bytes(system['ram_total'])}\n\n"

        f"💽 DISK\n"
        f"{usage_bar(system['disk_percent'])}\n"
        f"   {fmt_bytes(system['disk_used'])} / "
        f"{fmt_bytes(system['disk_total'])}\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💾 <b>DATABASE STORAGE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for data in databases:

        name = data.get(
            "name",
            "DATABASE",
        )

        documents = data.get(
            "documents",
            0,
        )

        size = data.get(
            "size",
            0,
        )

        status = data.get(
            "status",
            "UNKNOWN",
        )

        if status == "ONLINE":
            icon = "🟢"
        elif status == "ERROR":
            icon = "🔴"
        else:
            icon = "⚪"

        dashboard += (
            f"{icon} <b>{name}</b>\n"
            f"   📦 {fmt_number(documents)} files\n"
            f"   💾 {fmt_bytes(size)}\n"
            f"   {visual_bar(documents, total_files or 1, 12)}\n\n"
        )

    dashboard += (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <b>CURRENT ACTIVITY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"📥 Indexed: "
        f"<b>{fmt_number(STATS['files_indexed'])}</b>\n"

        f"⏭ Skipped: "
        f"<b>{fmt_number(STATS['files_skipped'])}</b>\n"

        f"📤 Files Sent: "
        f"<b>{fmt_number(STATS['files_sent'])}</b>\n"

        f"⚠️ Errors: "
        f"<b>{fmt_number(STATS['errors'])}</b>\n"

        f"⚙️ Commands: "
        f"<b>{fmt_number(STATS['commands'])}</b>\n"

        f"🔥 Active Tasks: "
        f"<b>{active_tasks}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏱️ <b>RUNTIME</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🚀 Uptime: <b>{uptime}</b>\n"
        f"🐍 Python: <b>{system['python']}</b>\n"
        f"📅 Date: <b>{human_today()}</b>\n"
        f"🕐 Updated: "
        f"<b>{datetime.now().strftime('%H:%M:%S')}</b>\n"
    )

    return dashboard


# ============================================================
# CURRENT WORK PAGE
# ============================================================

def build_tasks_page():

    if not LIVE_TASKS:

        return (
            "🚀 <b>CURRENT WORK CENTER</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💤 <b>NO ACTIVE WORK</b>\n\n"
            "The bot is currently waiting for work."
        )

    text = (
        "🚀 <b>CURRENT WORK CENTER</b>\n\n"
        "🔴 LIVE TASK MONITOR\n\n"
    )

    for task_id, task in list(
        LIVE_TASKS.items()
    ):

        name = task.get(
            "name",
            task_id,
        )

        task_type = task.get(
            "type",
            "WORK",
        )

        current = task.get(
            "current",
            0,
        )

        total = task.get(
            "total",
            0,
        )

        speed = task.get(
            "speed",
            0,
        )

        status = task.get(
            "status",
            "UNKNOWN",
        )

        started = task.get(
            "started",
            time.time(),
        )

        if status == "RUNNING":
            icon = "🟢"
        elif status == "COMPLETED":
            icon = "✅"
        elif status == "FAILED":
            icon = "🔴"
        else:
            icon = "🟡"

        text += (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{icon} <b>{name}</b>\n\n"
            f"🏷 Type: <b>{task_type}</b>\n"
        )

        if total > 0:

            text += (
                f"📊 Progress\n"
                f"{visual_bar(current, total, 16)}\n"
                f"📦 {fmt_number(current)} / "
                f"{fmt_number(total)}\n"
            )

        if speed > 0:

            text += (
                f"⚡ Speed: "
                f"<b>{speed:.2f}/sec</b>\n"
            )

        text += (
            f"⏱ Running: "
            f"<b>{fmt_duration(time.time() - started)}</b>\n"
        )

        if task.get("message"):

            text += (
                f"💬 {task['message'][:200]}\n"
            )

        text += "\n"

    return text[:4000]


# ============================================================
# LIVE LOG PAGE
# ============================================================

def build_logs_page():

    if not LIVE_LOGS:

        return (
            "📋 <b>LIVE BOT LOGS</b>\n\n"
            "💤 No logs captured yet."
        )

    text = (
        "📋 <b>LIVE BOT LOGS</b>\n"
        "🔴 Real-time memory log\n\n"
    )

    for item in list(LIVE_LOGS)[-25:]:

        level = item.get(
            "level",
            "INFO",
        )

        if level == "ERROR":
            icon = "🔴"
        elif level == "WARNING":
            icon = "🟠"
        elif level == "DEBUG":
            icon = "🔵"
        else:
            icon = "🟢"

        message = item.get(
            "message",
            "",
        )

        message = (
            message
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

        text += (
            f"<code>{item.get('time', '')}</code> "
            f"{icon} "
            f"{message[:180]}\n"
        )

    return text[:4000]


# ============================================================
# SEARCH PAGE
# ============================================================

def build_search_page():

    today = today_key()

    text = (
        "🔎 <b>SEARCH ANALYTICS</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>SEARCH ACTIVITY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🔍 Total Searches: "
        f"<b>{fmt_number(STATS['searches'])}</b>\n"

        f"📅 Today: "
        f"<b>{fmt_number(DAILY_SEARCHES.get(today, 0))}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 <b>TOP SEARCHES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if not SEARCH_TERMS:

        text += "💤 No search terms recorded yet."

    else:

        for index, (
            term,
            count,
        ) in enumerate(
            SEARCH_TERMS.most_common(15),
            1,
        ):

            if index == 1:
                medal = "🥇"
            elif index == 2:
                medal = "🥈"
            elif index == 3:
                medal = "🥉"
            else:
                medal = "🔹"

            text += (
                f"{medal} <b>{index}.</b> "
                f"{term[:60]} "
                f"— <b>{fmt_number(count)}</b>\n"
            )

    return text[:4000]


# ============================================================
# USERS PAGE
# ============================================================

def build_users_page():

    text = (
        "👥 <b>USER CONTROL CENTER</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>USER OVERVIEW</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"👤 Total Tracked: "
        f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n"

        f"🆕 Today: "
        f"<b>{fmt_number(DAILY_USERS.get(today_key(), 0))}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 <b>MOST ACTIVE USERS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if not USER_SEARCHES:

        text += "💤 No user activity recorded."

        return text

    for index, (
        user_id,
        searches,
    ) in enumerate(
        USER_SEARCHES.most_common(15),
        1,
    ):

        last_seen = USER_LAST_SEEN.get(
            user_id
        )

        if last_seen:

            last = fmt_duration(
                time.time() - last_seen
            )

            last_text = f"{last} ago"

        else:

            last_text = "Unknown"

        text += (
            f"👤 <b>{index}.</b> "
            f"<code>{user_id}</code>\n"
            f"   🔎 Searches: "
            f"<b>{searches}</b>\n"
            f"   🕐 Last seen: "
            f"<b>{last_text}</b>\n\n"
        )

    return text[:4000]


# ============================================================
# DATABASE PAGE
# ============================================================

async def build_database_page():

    databases = await get_all_database_stats()

    text = (
        "💾 <b>DATABASE CONTROL CENTER</b>\n\n"
    )

    total_files = 0

    total_size = 0

    for data in databases:

        name = data.get(
            "name",
            "DATABASE",
        )

        documents = data.get(
            "documents",
            0,
        )

        size = data.get(
            "size",
            0,
        )

        status = data.get(
            "status",
            "UNKNOWN",
        )

        if status == "ONLINE":
            icon = "🟢"
        elif status == "ERROR":
            icon = "🔴"
        else:
            icon = "⚪"

        text += (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{icon} <b>{name}</b>\n\n"

            f"📦 Documents: "
            f"<b>{fmt_number(documents)}</b>\n"

            f"💾 Storage: "
            f"<b>{fmt_bytes(size)}</b>\n"

            f"📡 Connection: "
            f"<b>{status}</b>\n\n"

            f"📊 File Share\n"
            f"{visual_bar(documents, max(1, sum("
            f"x.get('documents', 0) for x in databases"
            f")), 16)}\n\n"
        )

        total_files += documents

        total_size += size

    text += (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🏆 <b>DATABASE TOTAL</b>\n\n"

        f"📦 Total Files: "
        f"<b>{fmt_number(total_files)}</b>\n"

        f"💾 Total Storage: "
        f"<b>{fmt_bytes(total_size)}</b>\n"
    )

    return text


# ============================================================
# SYSTEM PAGE
# ============================================================

def build_system_page():

    system = system_info()

    uptime = fmt_duration(
        time.time() - START_TIME
    )

    return (
        "⚙️ <b>SYSTEM CONTROL CENTER</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🖥️ <b>SERVER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🐍 Python: "
        f"<b>{system['python']}</b>\n"

        f"💻 Platform: "
        f"<b>{system['platform'][:100]}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>CPU</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"{usage_bar(system['cpu'], 20)}\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🧠 <b>MEMORY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"{usage_bar(system['ram_percent'], 20)}\n"

        f"Used: <b>{fmt_bytes(system['ram_used'])}</b>\n"
        f"Total: <b>{fmt_bytes(system['ram_total'])}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💽 <b>DISK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"{usage_bar(system['disk_percent'], 20)}\n"

        f"Used: <b>{fmt_bytes(system['disk_used'])}</b>\n"
        f"Total: <b>{fmt_bytes(system['disk_total'])}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏱️ <b>RUNTIME</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🚀 Uptime: <b>{uptime}</b>\n"
        f"⚙️ Active Tasks: <b>{len(LIVE_TASKS)}</b>\n"
        f"👥 Tracked Users: "
        f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n"
        f"🔎 Searches: "
        f"<b>{fmt_number(STATS['searches'])}</b>\n"
    )


# ============================================================
# MAIN KEYBOARD
# ============================================================

def main_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📊 OVERVIEW",
                    callback_data="dv:home",
                ),
                InlineKeyboardButton(
                    "👥 USERS",
                    callback_data="dv:users",
                ),
            ],

            [
                InlineKeyboardButton(
                    "🔎 SEARCHES",
                    callback_data="dv:searches",
                ),
                InlineKeyboardButton(
                    "🔥 TOP SEARCHES",
                    callback_data="dv:top",
                ),
            ],

            [
                InlineKeyboardButton(
                    "💾 DATABASE",
                    callback_data="dv:database",
                ),
                InlineKeyboardButton(
                    "🚀 CURRENT WORK",
                    callback_data="dv:tasks",
                ),
            ],

            [
                InlineKeyboardButton(
                    "📋 LIVE LOGS",
                    callback_data="dv:logs",
                ),
                InlineKeyboardButton(
                    "⚙️ SYSTEM",
                    callback_data="dv:system",
                ),
            ],

            [
                InlineKeyboardButton(
                    "🔴 LIVE MODE",
                    callback_data="dv:live",
                ),
            ],

            [
                InlineKeyboardButton(
                    "🔄 REFRESH NOW",
                    callback_data="dv:refresh",
                ),
            ],
        ]
    )


# ============================================================
# BACK BUTTON
# ============================================================

def back_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ DASHBOARD",
                    callback_data="dv:home",
                ),
                InlineKeyboardButton(
                    "🔄 UPDATE",
                    callback_data="dv:refresh",
                ),
            ]
        ]
    )


# ============================================================
# ADMIN COMMAND
# ============================================================

@Client.on_message(
    filters.command(
        ["admin", "adm"]
    )
)
async def admin_command(
    client,
    message,
):

    user_id = (
        message.from_user.id
        if message.from_user
        else None
    )

    if not is_admin(user_id):

        return

    track_command(user_id)

    text = await build_dashboard()

    sent = await message.reply_text(
        text,
        reply_markup=main_keyboard(),
        parse_mode=enums.ParseMode.HTML,
    )

    ACTIVE_PANELS[user_id] = {
        "chat_id": message.chat.id,
        "message_id": sent.id,
        "mode": "home",
        "live": True,
    }

    # Start the live updater only when
    # an admin actually opens the panel.

    ensure_live_loop(client)


# ============================================================
# CALLBACK HANDLER
# ============================================================

@Client.on_callback_query(
    filters.regex(r"^dv:")
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

    if not is_admin(user_id):

        await query.answer(
            "⛔ Admin access only.",
            show_alert=True,
        )

        return

    await query.answer()

    action = query.data[3:]

    try:

        ensure_live_loop(client)

        if action in (
            "home",
            "refresh",
            "live",
        ):

            text = await build_dashboard()

            await query.message.edit_text(
                text,
                reply_markup=main_keyboard(),
                parse_mode=enums.ParseMode.HTML,
            )

            ACTIVE_PANELS[user_id] = {
                "chat_id": query.message.chat.id,
                "message_id": query.message.id,
                "mode": "home",
                "live": True,
            }

            return

        if action == "stats":

            text = await build_dashboard()

            await query.message.edit_text(
                text,
                reply_markup=back_keyboard(),
                parse_mode=enums.ParseMode.HTML,
            )

            ACTIVE_PANELS[user_id]["mode"] = "stats"

            return

        if action == "users":

            await query.message.edit_text(
                build_users_page(),
                reply_markup=back_keyboard(),
                parse_mode=enums.ParseMode.HTML,
            )

            ACTIVE_PANELS[user_id]["mode"] = "users"

            return

        if action == "searches":

            await query.message.edit_text(
                build_search_page(),
                reply_markup=back_keyboard(),
                parse_mode=enums.ParseMode.HTML,
            )

            ACTIVE_PANELS[user_id]["mode"] = "searches"

            return

        if action == "top":

            await query.message.edit_text(
                build_search_page(),
                reply_markup=back_keyboard(),
                parse_mode=enums.ParseMode.HTML,
            )

            ACTIVE_PANELS[user_id]["mode"] = "top"

            return

        if action == "database":

            await query.message.edit_text(
                await build_database_page(),
                reply_markup=back_keyboard(),
                parse_mode=enums.ParseMode.HTML,
            )

            ACTIVE_PANELS[user_id]["mode"] = "database"

            return

        if action == "tasks":

            await query.message.edit_text(
                build_tasks_page(),
                reply_markup=back_keyboard(),
                parse_mode=enums.ParseMode.HTML,
            )

            ACTIVE_PANELS[user_id]["mode"] = "tasks"

            return

        if action == "logs":

            await query.message.edit_text(
                build_logs_page(),
                reply_markup=back_keyboard(),
                parse_mode=enums.ParseMode.HTML,
            )

            ACTIVE_PANELS[user_id]["mode"] = "logs"

            return

        if action == "system":

            await query.message.edit_text(
                build_system_page(),
                reply_markup=back_keyboard(),
                parse_mode=enums.ParseMode.HTML,
            )

            ACTIVE_PANELS[user_id]["mode"] = "system"

            return

    except FloodWait as e:

        await asyncio.sleep(
            int(
                getattr(
                    e,
                    "value",
                    5,
                )
            )
        )

    except RPCError:

        logger.exception(
            "[DOWNTOWN VILLA ADMIN] Telegram error"
        )

    except Exception:

        logger.exception(
            "[DOWNTOWN VILLA ADMIN] Callback error"
        )


# ============================================================
# LIVE PANEL UPDATER
# ============================================================

_live_task = None


def ensure_live_loop(client):

    global _live_task

    if _live_task is not None:

        if not _live_task.done():

            return

    try:

        _live_task = asyncio.create_task(
            live_dashboard_loop(client)
        )

        logger.info(
            "[DOWNTOWN VILLA ADMIN] "
            "Live dashboard updater started."
        )

    except Exception:

        logger.exception(
            "[DOWNTOWN VILLA ADMIN] "
            "Unable to start live updater."
        )


async def live_dashboard_loop(client):

    while True:

        try:

            await asyncio.sleep(
                PANEL_UPDATE_SECONDS
            )

            if not ACTIVE_PANELS:

                continue

            for user_id, panel in list(
                ACTIVE_PANELS.items()
            ):

                if not is_admin(user_id):

                    continue

                if not panel.get(
                    "live",
                    True,
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
                    "home",
                )

                if not chat_id or not message_id:

                    continue

                try:

                    if mode == "home":

                        text = await build_dashboard()

                        keyboard = main_keyboard()

                    elif mode == "users":

                        text = build_users_page()

                        keyboard = back_keyboard()

                    elif mode in (
                        "searches",
                        "top",
                    ):

                        text = build_search_page()

                        keyboard = back_keyboard()

                    elif mode == "database":

                        text = await build_database_page()

                        keyboard = back_keyboard()

                    elif mode == "tasks":

                        text = build_tasks_page()

                        keyboard = back_keyboard()

                    elif mode == "logs":

                        text = build_logs_page()

                        keyboard = back_keyboard()

                    elif mode == "system":

                        text = build_system_page()

                        keyboard = back_keyboard()

                    else:

                        text = await build_dashboard()

                        keyboard = main_keyboard()

                    await client.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode=enums.ParseMode.HTML,
                    )

                except FloodWait as e:

                    await asyncio.sleep(
                        int(
                            getattr(
                                e,
                                "value",
                                5,
                            )
                        )
                    )

                except Exception as e:

                    # Message may have been deleted.
                    # Remove stale panel.

                    error_text = str(e).lower()

                    if (
                        "message to edit not found"
                        in error_text
                        or "message not modified"
                        in error_text
                        or "message_id_invalid"
                        in error_text
                    ):

                        ACTIVE_PANELS.pop(
                            user_id,
                            None,
                        )

        except asyncio.CancelledError:

            break

        except Exception:

            logger.exception(
                "[DOWNTOWN VILLA ADMIN] "
                "Live loop crashed."
            )

            await asyncio.sleep(5)


# ============================================================
# ADMIN PANEL STARTUP LOG
# ============================================================

logger.info(
    "=================================================="
)

logger.info(
    "[DOWNTOWN VILLA ADMIN] "
    "Ultimate Admin Panel Loaded"
)

logger.info(
    "[DOWNTOWN VILLA ADMIN] "
    "Admins configured: %s",
    len(ADMIN_IDS),
)

logger.info(
    "[DOWNTOWN VILLA ADMIN] "
    "Live updates: 3 seconds"
)

logger.info(
    "[DOWNTOWN VILLA ADMIN] "
    "Database monitoring: ENABLED"
)

logger.info(
    "[DOWNTOWN VILLA ADMIN] "
    "Live logs: ENABLED"
)

logger.info(
    "[DOWNTOWN VILLA ADMIN] "
    "Current work monitor: ENABLED"
)

logger.info(
    "=================================================="
)
