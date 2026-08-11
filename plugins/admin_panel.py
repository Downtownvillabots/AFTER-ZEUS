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
# DOWNTOWN VILLA
# ULTIMATE LIVE ADMIN CONTROL CENTER
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# DATABASE IMPORTS
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
    logger.warning("Database import failed: %s", e)

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
    from info import (
        COLLECTION_NAME,
        MULTIPLE_DB,
    )
except Exception:
    COLLECTION_NAME = "Telegram_files"
    MULTIPLE_DB = True


# ============================================================
# ADMINS
# ============================================================

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMINS", "").replace(",", " ").split()
    if x.strip().isdigit()
}


# ============================================================
# PANEL CONFIG
# ============================================================

try:
    PANEL_UPDATE_SECONDS = float(
        os.getenv("ADMIN_PANEL_UPDATE_SECONDS", "3")
    )
except Exception:
    PANEL_UPDATE_SECONDS = 3.0

try:
    MAX_LIVE_LOGS = int(
        os.getenv("ADMIN_MAX_LIVE_LOGS", "100")
    )
except Exception:
    MAX_LIVE_LOGS = 100

try:
    MAX_TASKS = int(
        os.getenv("ADMIN_MAX_TASKS", "50")
    )
except Exception:
    MAX_TASKS = 50


# ============================================================
# START TIME
# ============================================================

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
# USER STATISTICS
# ============================================================

KNOWN_USERS = set()

DAILY_USERS = Counter()
DAILY_SEARCHES = Counter()

USER_SEARCHES = Counter()
USER_COMMANDS = Counter()

USER_LAST_SEEN = {}

SEARCH_TERMS = Counter()


# ============================================================
# LIVE TASKS
# ============================================================

LIVE_TASKS = {}


# ============================================================
# LIVE LOGS
# ============================================================

LIVE_LOGS = deque(maxlen=MAX_LIVE_LOGS)


# ============================================================
# ACTIVE ADMIN PANELS
# ============================================================

ACTIVE_PANELS = {}


# ============================================================
# CLIENT / TASK
# ============================================================

_admin_client = None
_live_task = None


# ============================================================
# HELPERS
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
        seconds = max(0, int(seconds))

        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)

        if days:
            return f"{days}d {hours}h {minutes}m {seconds}s"

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
    return datetime.now().strftime("%d %b %Y • %H:%M:%S")


def visual_bar(current, total, length=18):
    try:
        current = float(current or 0)
        total = float(total or 0)

        if total <= 0:
            percentage = 0
        else:
            percentage = current / total * 100

        percentage = max(0, min(100, percentage))

        filled = int(length * percentage / 100)
        empty = length - filled

        return (
            "█" * filled
            + "░" * empty
            + f" {percentage:.1f}%"
        )

    except Exception:
        return "░" * length + " 0%"


def status_icon(status):
    status = str(status).upper()

    if status in (
        "ONLINE",
        "RUNNING",
        "CONNECTED",
        "ACTIVE",
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
        "PAUSED",
    ):
        return "🟡"

    return "⚪"


def html_escape(text):
    if text is None:
        return ""

    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ============================================================
# LIVE LOG HANDLER
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


# ============================================================
# INSTALL LOG HANDLER
# ============================================================

try:
    _memory_handler = TelegramMemoryLogHandler()
    _memory_handler.setLevel(logging.INFO)

    root_logger = logging.getLogger()

    root_logger.addHandler(_memory_handler)

except Exception as e:
    logger.warning(
        "Could not install memory log handler: %s",
        e,
    )


# ============================================================
# PUBLIC TRACKING FUNCTIONS
# ============================================================

def track_user(user_id):
    try:
        if not user_id:
            return

        user_id = int(user_id)

        if user_id not in KNOWN_USERS:
            KNOWN_USERS.add(user_id)

            STATS["users_seen"] = len(KNOWN_USERS)

            DAILY_USERS[today_key()] += 1

        USER_LAST_SEEN[user_id] = time.time()

    except Exception:
        pass


def track_search(user_id, query=""):
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


def track_command(user_id):
    try:
        track_user(user_id)

        STATS["commands"] += 1

        if user_id:
            USER_COMMANDS[int(user_id)] += 1

    except Exception:
        pass


def track_file_sent():
    try:
        STATS["files_sent"] += 1
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


def track_error():
    try:
        STATS["errors"] += 1
    except Exception:
        pass


# ============================================================
# LIVE TASK API
# ============================================================

def start_live_task(
    task_id,
    name,
    task_type="WORK",
    total=0,
):
    try:
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
            first_id = next(iter(LIVE_TASKS))
            LIVE_TASKS.pop(first_id, None)

    except Exception:
        pass


def update_live_task(
    task_id,
    current=None,
    total=None,
    speed=None,
    message=None,
):
    try:
        task = LIVE_TASKS.get(str(task_id))

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

    except Exception:
        pass


def finish_live_task(
    task_id,
    status="COMPLETED",
):
    try:
        task = LIVE_TASKS.get(str(task_id))

        if not task:
            return

        task["status"] = status
        task["updated"] = time.time()

    except Exception:
        pass


def remove_live_task(task_id):
    try:
        LIVE_TASKS.pop(str(task_id), None)
    except Exception:
        pass


# ============================================================
# DATABASE STATS
# ============================================================

async def database_stats(database, model):

    result = {
        "documents": 0,
        "size": 0,
        "status": "UNKNOWN",
    }

    if database is None:
        result["status"] = "OFFLINE"
        return result

    try:
        collection = database[COLLECTION_NAME]

        stats = await database.command(
            "collStats",
            COLLECTION_NAME,
        )

        result["size"] = stats.get(
            "storageSize",
            stats.get("size", 0),
        )

        if model is not None:
            result["documents"] = await model.count_documents({})
        else:
            result["documents"] = stats.get(
                "count",
                0,
            )

        result["status"] = "ONLINE"

    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)

    return result


async def get_all_database_stats():

    tasks = [
        database_stats(
            db,
            Media,
        )
    ]

    if MULTIPLE_DB:
        tasks.extend(
            [
                database_stats(db2, Media2),
                database_stats(db3, Media3),
            ]
        )

    try:
        return await asyncio.gather(*tasks)

    except Exception:
        return []


# ============================================================
# SYSTEM INFORMATION
# ============================================================

def system_info():

    try:
        cpu = psutil.cpu_percent(interval=0.05)
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

    try:
        load = os.getloadavg()

        load_1 = load[0]
        load_5 = load[1]
        load_15 = load[2]

    except Exception:
        load_1 = 0
        load_5 = 0
        load_15 = 0

    return {
        "cpu": cpu,
        "ram_used": ram_used,
        "ram_total": ram_total,
        "ram_percent": ram_percent,
        "disk_used": disk_used,
        "disk_total": disk_total,
        "disk_percent": disk_percent,
        "load_1": load_1,
        "load_5": load_5,
        "load_15": load_15,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


# ============================================================
# TASK PAGE
# ============================================================

def build_tasks_text():

    if not LIVE_TASKS:
        return (
            "🚀 <b>CURRENT WORK</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💤 <b>NO ACTIVE WORK</b>\n\n"
            "The bot is currently waiting."
        )

    text = (
        "🚀 <b>CURRENT WORK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for task_id, task in list(LIVE_TASKS.items()):

        current = int(task.get("current", 0) or 0)
        total = int(task.get("total", 0) or 0)
        speed = float(task.get("speed", 0) or 0)

        status = task.get(
            "status",
            "UNKNOWN",
        )

        icon = status_icon(status)

        elapsed = fmt_duration(
            time.time()
            - task.get(
                "started",
                time.time(),
            )
        )

        name = html_escape(
            task.get(
                "name",
                task_id,
            )
        )

        task_type = html_escape(
            task.get(
                "type",
                "WORK",
            )
        )

        text += (
            f"{icon} <b>{name}</b>\n"
            f"🏷️ Type: <b>{task_type}</b>\n"
        )

        if total > 0:
            bar = visual_bar(
                current,
                total,
                20,
            )

            text += (
                f"📊 <code>{bar}</code>\n"
                f"📦 <b>{fmt_number(current)}</b>"
                f" / "
                f"<b>{fmt_number(total)}</b>\n"
            )

        if speed > 0:
            text += (
                f"⚡ Speed: "
                f"<b>{speed:.2f}/sec</b>\n"
            )

        text += (
            f"⏱️ Running: "
            f"<b>{elapsed}</b>\n"
        )

        if task.get("message"):
            message = html_escape(
                task["message"]
            )

            text += (
                f"💬 {message[:120]}\n"
            )

        text += (
            f"🆔 <code>{task_id}</code>\n\n"
        )

    return text[:4000]


# ============================================================
# LOG PAGE
# ============================================================

def build_logs_text():

    if not LIVE_LOGS:
        return (
            "📋 <b>LIVE BOT LOGS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💤 No logs captured yet."
        )

    text = (
        "📋 <b>LIVE BOT LOGS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    logs = list(LIVE_LOGS)[-25:]

    for item in logs:

        level = str(
            item.get(
                "level",
                "INFO",
            )
        ).upper()

        if level == "ERROR":
            icon = "🔴"
        elif level == "WARNING":
            icon = "🟡"
        else:
            icon = "🟢"

        message = html_escape(
            item.get(
                "message",
                "",
            )
        )

        text += (
            f"<code>{item.get('time', '')}</code> "
            f"{icon} "
            f"<b>{level}</b>\n"
            f"{message[:180]}\n\n"
        )

    return text[:4000]


# ============================================================
# DASHBOARD
# ============================================================

async def build_dashboard():

    uptime = fmt_duration(
        time.time() - START_TIME
    )

    system = system_info()

    databases = await get_all_database_stats()

    total_files = sum(
        int(
            item.get(
                "documents",
                0,
            )
            or 0
        )
        for item in databases
    )

    total_db_size = sum(
        int(
            item.get(
                "size",
                0,
            )
            or 0
        )
        for item in databases
    )

    today_searches = DAILY_SEARCHES.get(
        today_key(),
        0,
    )

    today_users = DAILY_USERS.get(
        today_key(),
        0,
    )

    cpu_bar = visual_bar(
        system["cpu"],
        100,
        12,
    )

    ram_bar = visual_bar(
        system["ram_percent"],
        100,
        12,
    )

    disk_bar = visual_bar(
        system["disk_percent"],
        100,
        12,
    )

    text = (
        "╔══════════════════════════════╗\n"
        "║ 🏙️ <b>DOWNTOWN VILLA</b>       ║\n"
        "║ 🤖 <b>ADMIN CONTROL CENTER</b> ║\n"
        "╚══════════════════════════════╝\n\n"

        "🟢 <b>BOT ONLINE</b>\n"
        "🔄 <b>LIVE MONITORING ACTIVE</b>\n"
        f"🕒 <code>{human_today()}</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>LIVE OVERVIEW</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"👥 Total Users: "
        f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n"

        f"🆕 Users Today: "
        f"<b>{fmt_number(today_users)}</b>\n"

        f"🔎 Total Searches: "
        f"<b>{fmt_number(STATS['searches'])}</b>\n"

        f"🔍 Searches Today: "
        f"<b>{fmt_number(today_searches)}</b>\n"

        f"📦 Total Files: "
        f"<b>{fmt_number(total_files)}</b>\n"

        f"💾 Database Storage: "
        f"<b>{fmt_bytes(total_db_size)}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🖥️ <b>SERVER HEALTH</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"⚡ CPU: "
        f"<b>{system['cpu']:.1f}%</b>\n"
        f"<code>{cpu_bar}</code>\n"

        f"🧠 RAM: "
        f"<b>{system['ram_percent']:.1f}%</b>\n"
        f"<code>{ram_bar}</code>\n"

        f"💽 Disk: "
        f"<b>{system['disk_percent']:.1f}%</b>\n"
        f"<code>{disk_bar}</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <b>BOT ACTIVITY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"📥 Indexed: "
        f"<b>{fmt_number(STATS['files_indexed'])}</b>\n"

        f"⏭️ Skipped: "
        f"<b>{fmt_number(STATS['files_skipped'])}</b>\n"

        f"📤 Files Sent: "
        f"<b>{fmt_number(STATS['files_sent'])}</b>\n"

        f"⚠️ Errors: "
        f"<b>{fmt_number(STATS['errors'])}</b>\n"

        f"⌨️ Commands: "
        f"<b>{fmt_number(STATS['commands'])}</b>\n"

        f"🚀 Active Tasks: "
        f"<b>{fmt_number(len(LIVE_TASKS))}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏱️ <b>RUNTIME</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🕐 Uptime: <b>{uptime}</b>\n"
        f"🐍 Python: <b>{system['python']}</b>\n"
    )

    return text[:4000]


# ============================================================
# KEYBOARDS
# ============================================================

def dashboard_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data="admin_refresh",
                ),
                InlineKeyboardButton(
                    "🚀 Live Tasks",
                    callback_data="admin_tasks",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📋 Live Logs",
                    callback_data="admin_logs",
                ),
                InlineKeyboardButton(
                    "📊 Dashboard",
                    callback_data="admin_dashboard",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Close",
                    callback_data="admin_close",
                ),
            ],
        ]
    )


def back_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Dashboard",
                    callback_data="admin_dashboard",
                ),
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data="admin_refresh",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Close",
                    callback_data="admin_close",
                ),
            ],
        ]
    )


# ============================================================
# SAFE EDIT
# ============================================================

async def safe_edit(
    message,
    text,
    reply_markup=None,
):

    try:

        if message.text == text:
            return False

        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML,
        )

        return True

    except FloodWait as e:

        await asyncio.sleep(e.value)

    except RPCError as e:

        if "MESSAGE_NOT_MODIFIED" in str(e).upper():
            return False

        logger.warning(
            "Admin panel edit failed: %s",
            e,
        )

    except Exception as e:

        logger.warning(
            "Admin panel edit failed: %s",
            e,
        )

    return False


# ============================================================
# ADMIN COMMAND
# ============================================================

@Client.on_message(
    filters.command(
        ["admin", "panel", "dashboard"],
        prefixes="/",
    )
    & filters.private
)
async def admin_panel_command(
    client,
    message,
):

    user_id = (
        message.from_user.id
        if message.from_user
        else None
    )

    if not is_admin(user_id):

        await message.reply_text(
            "❌ <b>ACCESS DENIED</b>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    track_command(user_id)

    text = await build_dashboard()

    sent = await message.reply_text(
        text,
        reply_markup=dashboard_keyboard(),
        parse_mode=enums.ParseMode.HTML,
    )

    ACTIVE_PANELS[
        sent.id
    ] = {
        "chat_id": sent.chat.id,
        "message_id": sent.id,
        "last_text": text,
        "page": "dashboard",
    }

    logger.info(
        "Admin dashboard opened by %s",
        user_id,
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

@Client.on_callback_query(
    filters.regex("^admin_")
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
            "❌ Access denied",
            show_alert=True,
        )

        return

    data = query.data

    try:

        if data == "admin_dashboard":

            text = await build_dashboard()

            await safe_edit(
                query.message,
                text,
                dashboard_keyboard(),
            )

            await query.answer(
                "Dashboard refreshed",
                show_alert=False,
            )

        elif data == "admin_refresh":

            text = await build_dashboard()

            await safe_edit(
                query.message,
                text,
                dashboard_keyboard(),
            )

            await query.answer(
                "Updated ✓",
                show_alert=False,
            )

        elif data == "admin_tasks":

            text = build_tasks_text()

            await safe_edit(
                query.message,
                text,
                back_keyboard(),
            )

            await query.answer(
                "Live tasks",
                show_alert=False,
            )

        elif data == "admin_logs":

            text = build_logs_text()

            await safe_edit(
                query.message,
                text,
                back_keyboard(),
            )

            await query.answer(
                "Live logs",
                show_alert=False,
            )

        elif data == "admin_close":

            ACTIVE_PANELS.pop(
                query.message.id,
                None,
            )

            await query.message.delete()

            await query.answer(
                "Panel closed",
                show_alert=False,
            )

    except Exception as e:

        logger.exception(
            "Admin callback error: %s",
            e,
        )

        try:
            await query.answer(
                "❌ Something went wrong",
                show_alert=True,
            )
        except Exception:
            pass


# ============================================================
# LIVE PANEL UPDATER
# ============================================================

async def live_panel_updater(client):

    while True:

        try:

            if not ACTIVE_PANELS:
                await asyncio.sleep(
                    PANEL_UPDATE_SECONDS
                )
                continue

            for panel_id, panel in list(
                ACTIVE_PANELS.items()
            ):

                try:

                    page = panel.get(
                        "page",
                        "dashboard",
                    )

                    if page == "tasks":
                        text = build_tasks_text()

                    elif page == "logs":
                        text = build_logs_text()

                    else:
                        text = await build_dashboard()

                    if text == panel.get(
                        "last_text"
                    ):
                        continue

                    message = await client.get_messages(
                        panel["chat_id"],
                        panel["message_id"],
                    )

                    if not message:
                        ACTIVE_PANELS.pop(
                            panel_id,
                            None,
                        )
                        continue

                    keyboard = (
                        dashboard_keyboard()
                        if page == "dashboard"
                        else back_keyboard()
                    )

                    changed = await safe_edit(
                        message,
                        text,
                        keyboard,
                    )

                    if changed:
                        panel[
                            "last_text"
                        ] = text

                except Exception as e:

                    logger.debug(
                        "Panel update skipped: %s",
                        e,
                    )

        except Exception as e:

            logger.exception(
                "Live panel loop error: %s",
                e,
            )

        await asyncio.sleep(
            PANEL_UPDATE_SECONDS
        )


# ============================================================
# STARTUP
# ============================================================

@Client.on_message(
    filters.command(
        "start",
        prefixes="/",
    )
    & filters.private
)
async def admin_panel_start_hook(
    client,
    message,
):

    global _admin_client
    global _live_task

    _admin_client = client

    if _live_task is None:
        _live_task = asyncio.create_task(
            live_panel_updater(client)
        )


logger.info(
    "DOWNTOWN VILLA admin panel loaded"
)
