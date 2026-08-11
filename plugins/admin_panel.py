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
        db, db2, db3,
        Media, Media2, Media3,
    )
except Exception as e:
    logger.exception("Database import failed: %s", e)
    db = db2 = db3 = None
    Media = Media2 = Media3 = None

try:
    from info import COLLECTION_NAME, MULTIPLE_DB
except Exception:
    COLLECTION_NAME = "Telegram_files"
    MULTIPLE_DB = True

# ============================================================
# ADMINS / CONFIG
# ============================================================

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMINS", "").replace(",", " ").split()
    if x.strip().isdigit()
}

try:
    PANEL_UPDATE_SECONDS = max(
        1.0, float(os.getenv("ADMIN_PANEL_UPDATE_SECONDS", "3"))
    )
except Exception:
    PANEL_UPDATE_SECONDS = 3.0

try:
    MAX_LIVE_LOGS = int(os.getenv("ADMIN_MAX_LIVE_LOGS", "100"))
except Exception:
    MAX_LIVE_LOGS = 100

try:
    MAX_TASKS = int(os.getenv("ADMIN_MAX_TASKS", "50"))
except Exception:
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

KNOWN_USERS = set()
DAILY_USERS = Counter()
DAILY_SEARCHES = Counter()
USER_SEARCHES = Counter()
USER_COMMANDS = Counter()
USER_LAST_SEEN = {}
SEARCH_TERMS = Counter()

LIVE_TASKS = {}
LIVE_LOGS = deque(maxlen=MAX_LIVE_LOGS)

# message_id -> panel state
ACTIVE_PANELS = {}

# ============================================================
# HELPERS
# ============================================================

def is_admin(user_id):
    try:
        return user_id is not None and int(user_id) in ADMIN_IDS
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
        units = ["B", "KB", "MB", "GB", "TB"]
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
        percentage = 0 if total <= 0 else current / total * 100
        percentage = max(0, min(100, percentage))
        filled = int(length * percentage / 100)
        return "█" * filled + "░" * (length - filled) + f" {percentage:.1f}%"
    except Exception:
        return "░" * length + " 0%"


def status_icon(status):
    status = str(status).upper()
    if status in ("ONLINE", "RUNNING", "CONNECTED", "ACTIVE"):
        return "🟢"
    if status in ("ERROR", "FAILED", "OFFLINE"):
        return "🔴"
    if status in ("WARNING", "WAITING", "PAUSED"):
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
            message = record.getMessage()
            if len(message) > 350:
                message = message[:350] + "..."
            LIVE_LOGS.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": record.levelname,
                "message": message,
            })
        except Exception:
            pass


try:
    _memory_handler = TelegramMemoryLogHandler()
    _memory_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(_memory_handler)
except Exception:
    pass

# ============================================================
# TRACKING API
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

def start_live_task(task_id, name, task_type="WORK", total=0):
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
            LIVE_TASKS.pop(next(iter(LIVE_TASKS)), None)
    except Exception:
        pass


def update_live_task(task_id, current=None, total=None, speed=None, message=None):
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


def finish_live_task(task_id, status="COMPLETED"):
    try:
        task = LIVE_TASKS.get(str(task_id))
        if task:
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
# DATABASE INFORMATION
# ============================================================

async def database_info(database, model, label):
    result = {
        "label": label,
        "status": "OFFLINE",
        "documents": 0,
        "data_size": 0,
        "index_size": 0,
        "storage_size": 0,
        "collections": 0,
        "name": "",
        "error": "",
    }

    if database is None:
        return result

    try:
        result["name"] = getattr(database, "name", "") or ""
        stats = await database.command("dbStats")

        result["data_size"] = int(stats.get("dataSize", 0) or 0)
        result["index_size"] = int(stats.get("indexSize", 0) or 0)
        result["storage_size"] = int(
            stats.get(
                "storageSize",
                result["data_size"] + result["index_size"],
            ) or 0
        )
        result["collections"] = int(stats.get("collections", 0) or 0)

        if model is not None:
            result["documents"] = await model.count_documents({})
        else:
            coll_stats = await database.command(
                "collStats",
                COLLECTION_NAME,
            )
            result["documents"] = int(coll_stats.get("count", 0) or 0)

        result["status"] = "ONLINE"

    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)[:180]

    return result


async def get_all_database_info():
    tasks = [
        database_info(db, Media, "DATABASE 01"),
    ]

    if MULTIPLE_DB:
        tasks.extend([
            database_info(db2, Media2, "DATABASE 02"),
            database_info(db3, Media3, "DATABASE 03"),
        ])

    try:
        return await asyncio.gather(*tasks)
    except Exception as e:
        logger.exception("Database information error: %s", e)
        return []


# ============================================================
# SERVER INFORMATION
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
        ram_used = ram_total = ram_percent = 0

    try:
        disk = shutil.disk_usage("/")
        disk_used = disk.used
        disk_total = disk.total
        disk_free = disk.free
        disk_percent = disk.used / disk.total * 100 if disk.total else 0
    except Exception:
        disk_used = disk_total = disk_free = disk_percent = 0

    try:
        load = os.getloadavg()
        load_1, load_5, load_15 = load
    except Exception:
        load_1 = load_5 = load_15 = 0

    return {
        "cpu": cpu,
        "ram_used": ram_used,
        "ram_total": ram_total,
        "ram_percent": ram_percent,
        "disk_used": disk_used,
        "disk_total": disk_total,
        "disk_free": disk_free,
        "disk_percent": disk_percent,
        "load_1": load_1,
        "load_5": load_5,
        "load_15": load_15,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }

# ============================================================
# MAIN DASHBOARD
# ============================================================

async def build_dashboard():
    uptime = fmt_duration(time.time() - START_TIME)
    system = system_info()
    databases = await get_all_database_info()

    total_files = sum(x["documents"] for x in databases)
    total_db_storage = sum(x["storage_size"] for x in databases)

    today_searches = DAILY_SEARCHES.get(today_key(), 0)
    today_users = DAILY_USERS.get(today_key(), 0)

    cpu_bar = visual_bar(system["cpu"], 100, 12)
    ram_bar = visual_bar(system["ram_percent"], 100, 12)
    disk_bar = visual_bar(system["disk_percent"], 100, 12)

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

        f"👥 Total Users: <b>{fmt_number(len(KNOWN_USERS))}</b>\n"
        f"🆕 Users Today: <b>{fmt_number(today_users)}</b>\n"
        f"🔎 Total Searches: <b>{fmt_number(STATS['searches'])}</b>\n"
        f"🔍 Searches Today: <b>{fmt_number(today_searches)}</b>\n"
        f"📦 Total Files: <b>{fmt_number(total_files)}</b>\n"
        f"💾 Database Storage: <b>{fmt_bytes(total_db_storage)}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🖥️ <b>SERVER HEALTH</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"⚡ CPU: <b>{system['cpu']:.1f}%</b>\n"
        f"<code>{cpu_bar}</code>\n"
        f"🧠 RAM: <b>{system['ram_percent']:.1f}%</b>\n"
        f"<code>{ram_bar}</code>\n"
        f"💽 Disk: <b>{system['disk_percent']:.1f}%</b>\n"
        f"<code>{disk_bar}</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <b>BOT ACTIVITY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"📥 Indexed: <b>{fmt_number(STATS['files_indexed'])}</b>\n"
        f"⏭️ Skipped: <b>{fmt_number(STATS['files_skipped'])}</b>\n"
        f"📤 Files Sent: <b>{fmt_number(STATS['files_sent'])}</b>\n"
        f"⚠️ Errors: <b>{fmt_number(STATS['errors'])}</b>\n"
        f"⌨️ Commands: <b>{fmt_number(STATS['commands'])}</b>\n"
        f"🚀 Active Tasks: <b>{fmt_number(len(LIVE_TASKS))}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏱️ <b>RUNTIME</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🕐 Uptime: <b>{uptime}</b>\n"
        f"🐍 Python: <b>{system['python']}</b>\n"
    )

    return text[:4000]


# ============================================================
# DATABASE PAGE
# ============================================================

async def build_database_page():
    databases = await get_all_database_info()
    system = system_info()

    total_files = sum(x["documents"] for x in databases)
    total_storage = sum(x["storage_size"] for x in databases)
    total_data = sum(x["data_size"] for x in databases)
    total_indexes = sum(x["index_size"] for x in databases)

    text = (
        "💾 <b>DATABASE CONTROL CENTER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📚 Collection: <code>{html_escape(COLLECTION_NAME)}</code>\n"
        f"🗄️ Databases: <b>{len(databases)}</b>\n\n"
    )

    for item in databases:
        icon = status_icon(item["status"])
        text += (
            f"{icon} <b>{item['label']}</b>\n"
            f"   📦 Files      : <b>{fmt_number(item['documents'])}</b>\n"
            f"   💾 Storage    : <b>{fmt_bytes(item['storage_size'])}</b>\n"
            f"   📄 Data       : <b>{fmt_bytes(item['data_size'])}</b>\n"
            f"   🧩 Indexes    : <b>{fmt_bytes(item['index_size'])}</b>\n"
            f"   🗂️ Collections : <b>{item['collections']}</b>\n"
            f"   🟢 Status     : <b>{item['status']}</b>\n"
        )
        if item["name"]:
            text += f"   🏷️ DB Name    : <code>{html_escape(item['name'])}</code>\n"
        if item["error"]:
            text += f"   ⚠️ Error      : <code>{html_escape(item['error'])}</code>\n"
        text += "\n"

    text += (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>TOTAL DATABASE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Files       : <b>{fmt_number(total_files)}</b>\n"
        f"💾 Storage     : <b>{fmt_bytes(total_storage)}</b>\n"
        f"📄 Data        : <b>{fmt_bytes(total_data)}</b>\n"
        f"🧩 Indexes     : <b>{fmt_bytes(total_indexes)}</b>\n\n"

        "🖥️ <b>SERVER DISK</b>\n"
        f"Used          : <b>{fmt_bytes(system['disk_used'])}</b>\n"
        f"Free          : <b>{fmt_bytes(system['disk_free'])}</b>\n"
        f"Total         : <b>{fmt_bytes(system['disk_total'])}</b>\n"
        f"<code>{visual_bar(system['disk_percent'], 100, 20)}</code>\n"
    )

    return text[:4000]


# ============================================================
# SERVER PAGE
# ============================================================

def build_server_page():
    s = system_info()

    return (
        "🖥️ <b>SERVER LIVE MONITOR</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"⚡ CPU        : <b>{s['cpu']:.1f}%</b>\n"
        f"<code>{visual_bar(s['cpu'], 100, 20)}</code>\n\n"

        f"🧠 RAM        : <b>{s['ram_percent']:.1f}%</b>\n"
        f"   Used       : <b>{fmt_bytes(s['ram_used'])}</b>\n"
        f"   Total      : <b>{fmt_bytes(s['ram_total'])}</b>\n"
        f"<code>{visual_bar(s['ram_percent'], 100, 20)}</code>\n\n"

        f"💽 DISK       : <b>{s['disk_percent']:.1f}%</b>\n"
        f"   Used       : <b>{fmt_bytes(s['disk_used'])}</b>\n"
        f"   Free       : <b>{fmt_bytes(s['disk_free'])}</b>\n"
        f"   Total      : <b>{fmt_bytes(s['disk_total'])}</b>\n"
        f"<code>{visual_bar(s['disk_percent'], 100, 20)}</code>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📈 <b>SYSTEM LOAD</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"1 min : <b>{s['load_1']:.2f}</b>\n"
        f"5 min : <b>{s['load_5']:.2f}</b>\n"
        f"15 min: <b>{s['load_15']:.2f}</b>\n\n"

        f"🐍 Python   : <b>{s['python']}</b>\n"
        f"💻 Platform : <code>{html_escape(s['platform'])}</code>\n"
    )


# ============================================================
# STATISTICS PAGE
# ============================================================

def build_statistics_page():
    top_searches = SEARCH_TERMS.most_common(10)

    text = (
        "📊 <b>BOT STATISTICS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"👥 Users Seen      : <b>{fmt_number(len(KNOWN_USERS))}</b>\n"
        f"🔎 Searches        : <b>{fmt_number(STATS['searches'])}</b>\n"
        f"📥 Files Indexed   : <b>{fmt_number(STATS['files_indexed'])}</b>\n"
        f"⏭️ Files Skipped   : <b>{fmt_number(STATS['files_skipped'])}</b>\n"
        f"📤 Files Sent      : <b>{fmt_number(STATS['files_sent'])}</b>\n"
        f"⚠️ Errors          : <b>{fmt_number(STATS['errors'])}</b>\n"
        f"⌨️ Commands        : <b>{fmt_number(STATS['commands'])}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔎 <b>TOP SEARCH TERMS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    if top_searches:
        for index, (term, count) in enumerate(top_searches, 1):
            text += f"{index}. <code>{html_escape(term[:60])}</code> — <b>{count}</b>\n"
    else:
        text += "💤 No searches recorded yet.\n"

    return text[:4000]


# ============================================================
# TASK PAGE
# ============================================================

def build_tasks_text():
    if not LIVE_TASKS:
        return (
            "🚀 <b>CURRENT WORK</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💤 <b>NO ACTIVE WORK</b>\n\n"
            "The bot is currently waiting.\n"
            "Registered indexing, backup and background tasks\n"
            "will appear here automatically."
        )

    text = (
        "🚀 <b>CURRENT WORK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for task_id, task in list(LIVE_TASKS.items()):
        current = int(task.get("current", 0) or 0)
        total = int(task.get("total", 0) or 0)
        speed = float(task.get("speed", 0) or 0)
        status = task.get("status", "UNKNOWN")
        elapsed = fmt_duration(time.time() - task.get("started", time.time()))

        text += (
            f"{status_icon(status)} <b>{html_escape(task.get('name', task_id))}</b>\n"
            f"🏷️ Type: <b>{html_escape(task.get('type', 'WORK'))}</b>\n"
        )

        if total > 0:
            text += (
                f"📊 <code>{visual_bar(current, total, 20)}</code>\n"
                f"📦 <b>{fmt_number(current)}</b> / <b>{fmt_number(total)}</b>\n"
            )

        if speed > 0:
            text += f"⚡ Speed: <b>{speed:.2f}/sec</b>\n"

        text += f"⏱️ Running: <b>{elapsed}</b>\n"

        if task.get("message"):
            text += f"💬 {html_escape(task['message'])[:140]}\n"

        text += f"🆔 <code>{html_escape(task_id)}</code>\n\n"

    return text[:4000]


# ============================================================
# LOG PAGE
# ============================================================

def build_logs_text():
    if not LIVE_LOGS:
        return (
            "📋 <b>LIVE BOT LOGS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💤 No logs captured yet."
        )

    text = (
        "📋 <b>LIVE BOT LOGS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    for item in list(LIVE_LOGS)[-25:]:
        level = str(item.get("level", "INFO")).upper()
        icon = "🔴" if level == "ERROR" else "🟡" if level == "WARNING" else "🟢"
        message = html_escape(item.get("message", ""))

        text += (
            f"<code>{item.get('time', '')}</code> "
            f"{icon} <b>{level}</b>\n"
            f"{message[:180]}\n\n"
        )

    return text[:4000]


# ============================================================
# KEYBOARDS
# ============================================================

def dashboard_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_refresh"),
            InlineKeyboardButton("💾 Databases", callback_data="admin_db"),
        ],
        [
            InlineKeyboardButton("🚀 Live Tasks", callback_data="admin_tasks"),
            InlineKeyboardButton("📋 Live Logs", callback_data="admin_logs"),
        ],
        [
            InlineKeyboardButton("🖥️ Server", callback_data="admin_server"),
            InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton("🔄 Dashboard", callback_data="admin_dashboard"),
            InlineKeyboardButton("❌ Close", callback_data="admin_close"),
        ],
    ])


def page_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="admin_refresh_page"),
            InlineKeyboardButton("⬅️ Dashboard", callback_data="admin_dashboard"),
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data="admin_close"),
        ],
    ])

# ============================================================
# SAFE EDIT
# ============================================================

async def safe_edit(message, text, reply_markup=None):
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
        if "MESSAGE_NOT_MODIFIED" not in str(e).upper():
            logger.warning("Admin panel edit failed: %s", e)

    except Exception as e:
        logger.warning("Admin panel edit failed: %s", e)

    return False

# ============================================================
# PAGE BUILDER
# ============================================================

async def get_page(page):
    if page == "dashboard":
        return await build_dashboard()
    if page == "db":
        return await build_database_page()
    if page == "server":
        return build_server_page()
    if page == "stats":
        return build_statistics_page()
    if page == "tasks":
        return build_tasks_text()
    if page == "logs":
        return build_logs_text()
    return await build_dashboard()


# ============================================================
# ADMIN COMMANDS
# ============================================================

@Client.on_message(
    filters.command(["admin", "panel", "dashboard"], prefixes="/")
    & filters.private
)
async def admin_panel_command(client, message):
    user_id = message.from_user.id if message.from_user else None

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

    ACTIVE_PANELS[sent.id] = {
        "chat_id": sent.chat.id,
        "message_id": sent.id,
        "last_text": text,
        "page": "dashboard",
    }

    logger.info("Admin dashboard opened by %s", user_id)


# ============================================================
# CALLBACKS
# ============================================================

@Client.on_callback_query(filters.regex("^admin_"))
async def admin_callback(client, query):
    user_id = query.from_user.id if query.from_user else None

    if not is_admin(user_id):
        await query.answer("❌ Access denied", show_alert=True)
        return

    data = query.data

    try:
        if data == "admin_close":
            ACTIVE_PANELS.pop(query.message.id, None)
            await query.message.delete()
            await query.answer("Panel closed")
            return

        if data in (
            "admin_dashboard",
            "admin_refresh",
            "admin_db",
            "admin_server",
            "admin_stats",
            "admin_tasks",
            "admin_logs",
        ):
            page_map = {
                "admin_dashboard": "dashboard",
                "admin_refresh": "dashboard",
                "admin_db": "db",
                "admin_server": "server",
                "admin_stats": "stats",
                "admin_tasks": "tasks",
                "admin_logs": "logs",
            }

            page = page_map[data]
            text = await get_page(page)

            keyboard = (
                dashboard_keyboard()
                if page == "dashboard"
                else page_keyboard()
            )

            await safe_edit(
                query.message,
                text,
                keyboard,
            )

            ACTIVE_PANELS[query.message.id] = {
                "chat_id": query.message.chat.id,
                "message_id": query.message.id,
                "last_text": text,
                "page": page,
            }

            await query.answer("Updated ✓")
            return

        if data == "admin_refresh_page":
            panel = ACTIVE_PANELS.get(query.message.id, {})
            page = panel.get("page", "dashboard")
            text = await get_page(page)

            keyboard = (
                dashboard_keyboard()
                if page == "dashboard"
                else page_keyboard()
            )

            await safe_edit(
                query.message,
                text,
                keyboard,
            )

            panel["last_text"] = text
            await query.answer("Live data refreshed ✓")
            return

    except Exception as e:
        logger.exception("Admin callback error: %s", e)
        try:
            await query.answer("❌ Something went wrong", show_alert=True)
        except Exception:
            pass


# ============================================================
# LIVE PANEL UPDATER
# ============================================================

async def live_panel_updater(client):
    while True:
        try:
            if not ACTIVE_PANELS:
                await asyncio.sleep(PANEL_UPDATE_SECONDS)
                continue

            for panel_id, panel in list(ACTIVE_PANELS.items()):
                try:
                    page = panel.get("page", "dashboard")
                    text = await get_page(page)

                    if text == panel.get("last_text"):
                        continue

                    message = await client.get_messages(
                        panel["chat_id"],
                        panel["message_id"],
                    )

                    if not message:
                        ACTIVE_PANELS.pop(panel_id, None)
                        continue

                    keyboard = (
                        dashboard_keyboard()
                        if page == "dashboard"
                        else page_keyboard()
                    )

                    changed = await safe_edit(
                        message,
                        text,
                        keyboard,
                    )

                    if changed:
                        panel["last_text"] = text

                except Exception as e:
                    logger.debug("Panel update skipped: %s", e)

        except Exception as e:
            logger.exception("Live panel loop error: %s", e)

        await asyncio.sleep(PANEL_UPDATE_SECONDS)


# ============================================================
# STARTUP HOOK
# ============================================================

_admin_updater_started = False


@Client.on_message(
    filters.command("admin_start", prefixes="/") & filters.private
)
async def admin_start_updater(client, message):
    global _admin_updater_started

    user_id = message.from_user.id if message.from_user else None

    if not is_admin(user_id):
        await message.reply_text("❌ Access denied")
        return

    if not _admin_updater_started:
        _admin_updater_started = True
        asyncio.create_task(live_panel_updater(client))

    await message.reply_text(
        "🟢 <b>LIVE ADMIN MONITOR STARTED</b>\n\n"
        "Use /admin to open the control center.",
        parse_mode=enums.ParseMode.HTML,
    )


logger.info("DOWNTOWN VILLA Ultimate Admin Panel loaded")
