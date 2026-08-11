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

logger = logging.getLogger(**name**)

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
except Exception:
db = None
db2 = None
db3 = None

#
Media = None
Media2 = None
Media3 = None
#

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

# RENDER ENVIRONMENT

# ADMINS=123456789 987654321

# ============================================================

ADMIN_IDS = {
int(x.strip())
for x in os.getenv("ADMINS", "").replace(",", " ").split()
if x.strip().isdigit()
}

# ============================================================

# PANEL CONFIGURATION

# ============================================================

PANEL_UPDATE_SECONDS = float(
os.getenv(
"ADMIN_PANEL_UPDATE_SECONDS",
"3",
)
)

MAX_LIVE_LOGS = int(
os.getenv(
"ADMIN_MAX_LIVE_LOGS",
"100",
)
)

MAX_TASKS = int(
os.getenv(
"ADMIN_MAX_TASKS",
"50",
)
)

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

LIVE_LOGS = deque(
maxlen=MAX_LIVE_LOGS
)

# ============================================================

# ACTIVE ADMIN PANELS

# ============================================================

ACTIVE_PANELS = {}

# ============================================================

# CLIENT

# ============================================================

_admin_client = None
_live_task = None

# ============================================================

# LOCK

# ============================================================

PANEL_LOCK = asyncio.Lock()

# ============================================================

# LIVE LOG HANDLER

# ============================================================

class TelegramMemoryLogHandler(logging.Handler):

#
def emit(self, record):

    try:
        now = datetime.now().strftime(
            "%H:%M:%S"
        )

        level = record.levelname

        message = record.getMessage()

        if len(message) > 350:
            message = (
                message[:350]
                + "..."
            )

        LIVE_LOGS.append(
            {
                "time": now,
                "level": level,
                "message": message,
            }
        )

    except Exception:
        pass
#

# ============================================================

# INSTALL LOG HANDLER

# ============================================================

try:

#
_memory_handler = TelegramMemoryLogHandler()

_memory_handler.setLevel(
    logging.INFO
)

logging.getLogger().addHandler(
    _memory_handler
)
#

except Exception:
pass

# ============================================================

# BASIC HELPERS

# ============================================================

def is_admin(user_id):

#
try:

    if user_id is None:
        return False

    return int(user_id) in ADMIN_IDS

except Exception:
    return False
#

def fmt_number(value):

#
try:
    return f"{int(value):,}"

except Exception:
    return "0"
#

def fmt_bytes(value):

#
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

    while (
        value >= 1024
        and index < len(units) - 1
    ):

        value /= 1024

        index += 1

    return (
        f"{value:.1f} "
        f"{units[index]}"
    )

except Exception:
    return "0 B"
#

def fmt_duration(seconds):

#
try:

    seconds = max(
        0,
        int(seconds),
    )

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
#

def today_key():

#
return datetime.now().strftime(
    "%Y-%m-%d"
)
#

def human_today():

#
return datetime.now().strftime(
    "%d %b %Y • %H:%M:%S"
)
#

def visual_bar(
current,
total,
length=18,
):

#
try:

    current = float(current or 0)
    total = float(total or 0)

    if total <= 0:
        percentage = 0
    else:
        percentage = (
            current
            / total
            * 100
        )

    percentage = max(
        0,
        min(
            100,
            percentage,
        ),
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
#

def status_icon(status):

#
status = str(
    status
).upper()

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
#

# ============================================================

# PUBLIC TRACKING FUNCTIONS

# ============================================================

def track_user(user_id):

#
try:

    if not user_id:
        return

    user_id = int(
        user_id
    )

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

    USER_LAST_SEEN[
        user_id
    ] = time.time()

except Exception:
    pass
#

def track_search(
user_id,
query="",
):

#
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

    if query:

        clean = (
            str(query)
            .strip()
            .lower()
        )

        if clean:

            SEARCH_TERMS[
                clean
            ] += 1

except Exception:
    pass
#

def track_command(
user_id,
):

#
try:

    track_user(
        user_id
    )

    STATS[
        "commands"
    ] += 1

    if user_id:

        USER_COMMANDS[
            int(user_id)
        ] += 1

except Exception:
    pass
#

def track_file_sent():

#
try:

    STATS[
        "files_sent"
    ] += 1

except Exception:
    pass
#

def track_indexed(
count=1,
):

#
try:

    STATS[
        "files_indexed"
    ] += int(count)

except Exception:
    pass
#

def track_skipped(
count=1,
):

#
try:

    STATS[
        "files_skipped"
    ] += int(count)

except Exception:
    pass
#

def track_error():

#
try:

    STATS[
        "errors"
    ] += 1

except Exception:
    pass
#

# ============================================================

# LIVE TASK API

# ============================================================

def start_live_task(
task_id,
name,
task_type="WORK",
total=0,
):

#
try:

    LIVE_TASKS[
        str(task_id)
    ] = {

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

        "message": "",

    }

    while (
        len(LIVE_TASKS)
        > MAX_TASKS
    ):

        first_id = next(
            iter(
                LIVE_TASKS
            )
        )

        LIVE_TASKS.pop(
            first_id,
            None,
        )

except Exception:
    pass
#

def update_live_task(
task_id,
current=None,
total=None,
speed=None,
message=None,
):

#
try:

    task = LIVE_TASKS.get(
        str(task_id)
    )

    if not task:
        return

    if current is not None:

        task[
            "current"
        ] = int(
            current
        )

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

    task[
        "updated"
    ] = time.time()

except Exception:
    pass
#

def finish_live_task(
task_id,
status="COMPLETED",
):

#
try:

    task = LIVE_TASKS.get(
        str(task_id)
    )

    if not task:
        return

    task[
        "status"
    ] = status

    task[
        "updated"
    ] = time.time()

except Exception:
    pass
#

def remove_live_task(
task_id,
):

#
try:

    LIVE_TASKS.pop(
        str(task_id),
        None,
    )

except Exception:
    pass
#

# ============================================================

# DATABASE STATS

# ============================================================

async def database_stats(
database,
model,
):

#
result = {
    "documents": 0,
    "size": 0,
    "status": "UNKNOWN",
}

if database is None:

    result[
        "status"
    ] = "OFFLINE"

    return result

try:

    collection = database[
        COLLECTION_NAME
    ]

    stats = await database.command(
        "collStats",
        COLLECTION_NAME,
    )

    result[
        "size"
    ] = stats.get(
        "storageSize",
        stats.get(
            "size",
            0,
        ),
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
            0,
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
#

async def get_all_database_stats():

#
tasks = [
    database_stats(
        db,
        Media,
    )
]

if MULTIPLE_DB:

    tasks.extend(
        [
            database_stats(
                db2,
                Media2,
            ),
            database_stats(
                db3,
                Media3,
            ),
        ]
    )

try:

    return await asyncio.gather(
        *tasks
    )

except Exception:

    return []
#

# ============================================================

# SYSTEM INFORMATION

# ============================================================

def system_info():

#
try:

    cpu = psutil.cpu_percent(
        interval=0.1
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
#

# ============================================================

# LIVE TASK PAGE

# ============================================================

def build_tasks_text():

#
if not LIVE_TASKS:

    return (
        "🚀 <b>CURRENT WORK</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💤 <b>NO ACTIVE WORK</b>\n\n"
        "The bot is currently waiting.\n"
        "Any registered indexing, backup,\n"
        "database or background task will\n"
        "appear here automatically."
    )


text = (
    "🚀 <b>CURRENT WORK</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
)


for task_id, task in list(
    LIVE_TASKS.items()
):

    current = int(
        task.get(
            "current",
            0,
        )
        or 0
    )

    total = int(
        task.get(
            "total",
            0,
        )
        or 0
    )

    speed = float(
        task.get(
            "speed",
            0,
        )
        or 0
    )

    status = task.get(
        "status",
        "UNKNOWN",
    )

    icon = status_icon(
        status
    )

    elapsed = fmt_duration(
        time.time()
        - task.get(
            "started",
            time.time(),
        )
    )

    text += (
        f"{icon} <b>"
        f"{task.get('name', task_id)}"
        f"</b>\n"
    )

    text += (
        f"🏷️ Type: "
        f"<b>{task.get('type', 'WORK')}</b>\n"
    )

    if total > 0:

        bar = visual_bar(
            current,
            total,
            20,
        )

        text += (
            f"📊 <code>{bar}</code>\n"
            f"📦 "
            f"<b>{fmt_number(current)}</b>"
            f" / "
            f"<b>{fmt_number(total)}</b>\n"
        )

    else:

        text += (
            "📊 <code>████████░░░░░░░░░░░░</code>\n"
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

        text += (
            f"💬 "
            f"{str(task['message'])[:120]}\n"
        )

    text += (
        f"🆔 <code>{task_id}</code>\n\n"
    )


return text[:4000]
#

# ============================================================

# LIVE LOG PAGE

# ============================================================

def build_logs_text():

#
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


logs = list(
    LIVE_LOGS
)[-28:]


for item in logs:

    level = str(
        item.get(
            "level",
            "INFO",
        )
    ).upper()

    icon = status_icon(
        "ERROR"
        if level == "ERROR"
        else (
            "WARNING"
            if level == "WARNING"
            else "ONLINE"
        )
    )

    message = str(
        item.get(
            "message",
            "",
        )
    )

    message = (
        message
        .replace(
            "<",
            "&lt;",
        )
        .replace(
            ">",
            "&gt;",
        )
    )

    text += (
        f"<code>"
        f"{item.get('time', '')}"
        f"</code> "
        f"{icon} "
        f"<b>{level}</b>\n"
        f"{message[:180]}\n\n"
    )


return text[:4000]
#

# ============================================================

# DASHBOARD

# ============================================================

async def build_dashboard():

#
uptime = fmt_duration(
    time.time()
    - START_TIME
)

system = system_info()

databases = (
    await get_all_database_stats()
)

total_files = sum(
    int(
        x.get(
            "documents",
            0,
        )
        or 0
    )
    for x in databases
)

total_db_size = sum(
    int(
        x.get(
            "size",
            0,
        )
        or 0
    )
    for x in databases
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
    f"🐍 Python: <b>{system['python']}</b>\n\n"

    "💡 <i>Tap LIVE DASHBOARD for continuous\n"
    "automatic updates every "
    f"{PANEL_UPDATE_SECONDS:.0f} seconds.</i>"
)

return text[:4000]
#

# ============================================================

# STATISTICS PAGE

# ============================================================

def build_statistics():

#
today = today_key()

text = (
    "📊 <b>DOWNTOWN VILLA STATISTICS</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    "👥 <b>USERS</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"

    f"👤 Total: "
    f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n"

    f"🆕 Today: "
    f"<b>{fmt_number(DAILY_USERS.get(today, 0))}</b>\n\n"

    "🔎 <b>SEARCH ACTIVITY</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"

    f"🔍 Total Searches: "
    f"<b>{fmt_number(STATS['searches'])}</b>\n"

    f"📅 Today's Searches: "
    f"<b>{fmt_number(DAILY_SEARCHES.get(today, 0))}</b>\n\n"

    "📦 <b>FILE ACTIVITY</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"

    f"📥 Indexed: "
    f"<b>{fmt_number(STATS['files_indexed'])}</b>\n"

    f"⏭️ Skipped: "
    f"<b>{fmt_number(STATS['files_skipped'])}</b>\n"

    f"📤 Sent: "
    f"<b>{fmt_number(STATS['files_sent'])}</b>\n"

    f"⚠️ Errors: "
    f"<b>{fmt_number(STATS['errors'])}</b>\n\n"

    "⌨️ <b>COMMAND ACTIVITY</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"

    f"⌨️ Commands: "
    f"<b>{fmt_number(STATS['commands'])}</b>\n"

    f"🚀 Active Tasks: "
    f"<b>{fmt_number(len(LIVE_TASKS))}</b>\n"
)

return text
#

# ============================================================

# TOP SEARCHES

# ============================================================

def build_top_searches():

#
text = (
    "🔥 <b>TOP SEARCHES</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
)

if not SEARCH_TERMS:

    return (
        text
        + "💤 No search data recorded yet."
    )

medals = [
    "🥇",
    "🥈",
    "🥉",
]

for index, (
    query,
    count,
) in enumerate(
    SEARCH_TERMS.most_common(20),
    start=1,
):

    if index <= 3:
        icon = medals[
            index - 1
        ]
    else:
        icon = "🔹"

    safe_query = (
        str(query)
        .replace(
            "<",
            "&lt;",
        )
        .replace(
            ">",
            "&gt;",
        )
    )

    text += (
        f"{icon} <b>{index}.</b> "
        f"{safe_query[:70]}\n"
        f"   🔎 Searches: "
        f"<b>{fmt_number(count)}</b>\n\n"
    )

return text[:4000]
#

# ============================================================

# USERS PAGE

# ============================================================

def build_users():

#
text = (
    "👥 <b>USER ACTIVITY CENTER</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    f"👤 Total Tracked: "
    f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n"

    f"🔎 Total Searches: "
    f"<b>{fmt_number(STATS['searches'])}</b>\n\n"

    "🔥 <b>MOST ACTIVE USERS</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
)

if not USER_SEARCHES:

    text += (
        "💤 No user search activity yet."
    )

    return text


for index, (
    user_id,
    count,
) in enumerate(
    USER_SEARCHES.most_common(20),
    start=1,
):

    last_seen = USER_LAST_SEEN.get(
        user_id
    )

    if last_seen:

        last = fmt_duration(
            time.time()
            - last_seen
        )

        last_text = (
            f"{last} ago"
        )

    else:

        last_text = "Unknown"


    text += (
        f"👤 <b>{index}.</b> "
        f"<code>{user_id}</code>\n"
        f"   🔎 Searches: "
        f"<b>{fmt_number(count)}</b>\n"
        f"   🕒 Last Seen: "
        f"<b>{last_text}</b>\n\n"
    )


return text[:4000]
#

# ============================================================

# DATABASE PAGE

# ============================================================

async def build_database_page():

#
databases = (
    await get_all_database_stats()
)

names = [
    "PRIMARY • Media",
    "SECONDARY • Media2",
    "TERTIARY • Media3",
]

total_files = sum(
    int(
        data.get(
            "documents",
            0,
        )
        or 0
    )
    for data in databases
)

total_size = sum(
    int(
        data.get(
            "size",
            0,
        )
        or 0
    )
    for data in databases
)

text = (
    "💾 <b>DATABASE COMMAND CENTER</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🔄 <b>LIVE DATABASE MONITOR</b>\n\n"
)


for index, data in enumerate(
    databases
):

    if index < len(names):
        name = names[index]
    else:
        name = (
            f"DATABASE {index + 1}"
        )


    documents = int(
        data.get(
            "documents",
            0,
        )
        or 0
    )

    size = int(
        data.get(
            "size",
            0,
        )
        or 0
    )

    status = str(
        data.get(
            "status",
            "UNKNOWN",
        )
    )


    if total_files > 0:

        percentage = (
            documents
            / total_files
            * 100
        )

    else:

        percentage = 0


    bar_length = 20

    filled = int(
        bar_length
        * percentage
        / 100
    )

    filled = max(
        0,
        min(
            bar_length,
            filled,
        ),
    )

    bar = (
        "█" * filled
        + "░" * (
            bar_length
            - filled
        )
    )


    text += (
        f"{status_icon(status)} "
        f"<b>{name}</b>\n"
        "┌──────────────────────────────\n"
        f"│ 📦 Files: "
        f"<b>{fmt_number(documents)}</b>\n"
        f"│ 💽 Storage: "
        f"<b>{fmt_bytes(size)}</b>\n"
        f"│ 📊 Share: "
        f"<b>{percentage:.1f}%</b>\n"
        f"│ <code>{bar}</code>\n"
        f"│ 📡 Status: "
        f"<b>{status}</b>\n"
    )


    if data.get("error"):

        error_text = str(
            data.get(
                "error"
            )
        )

        error_text = (
            error_text
            .replace(
                "<",
                "&lt;",
            )
            .replace(
                ">",
                "&gt;",
            )
        )

        text += (
            f"│ ⚠️ "
            f"<code>{error_text[:150]}</code>\n"
        )


    text += (
        "└──────────────────────────────\n\n"
    )


text += (
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "📊 <b>DATABASE TOTAL</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    f"📦 Total Files: "
    f"<b>{fmt_number(total_files)}</b>\n"

    f"💾 Total Storage: "
    f"<b>{fmt_bytes(total_size)}</b>\n"

    f"🗃️ Databases: "
    f"<b>{len(databases)}</b>\n"

    f"🔄 Multiple DB: "
    f"<b>{'ENABLED' if MULTIPLE_DB else 'DISABLED'}</b>\n"
)


return text[:4000]
#

# ============================================================

# SYSTEM PAGE

# ============================================================

def build_system():

#
info = system_info()

cpu_bar = visual_bar(
    info["cpu"],
    100,
    18,
)

ram_bar = visual_bar(
    info["ram_percent"],
    100,
    18,
)

disk_bar = visual_bar(
    info["disk_percent"],
    100,
    18,
)

text = (
    "⚙️ <b>DOWNTOWN VILLA SYSTEM</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    "🖥️ <b>SERVER</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"

    f"🐍 Python: "
    f"<b>{info['python']}</b>\n"

    f"💻 Platform: "
    f"<b>{info['platform'][:90]}</b>\n\n"

    "⚡ <b>CPU</b>\n"
    f"<code>{cpu_bar}</code>\n"
    f"Usage: <b>{info['cpu']:.1f}%</b>\n\n"

    "🧠 <b>RAM</b>\n"
    f"<code>{ram_bar}</code>\n"
    f"Usage: <b>{info['ram_percent']:.1f}%</b>\n"
    f"Used: <b>{fmt_bytes(info['ram_used'])}</b>\n"
    f"Total: <b>{fmt_bytes(info['ram_total'])}</b>\n\n"

    "💽 <b>DISK</b>\n"
    f"<code>{disk_bar}</code>\n"
    f"Usage: <b>{info['disk_percent']:.1f}%</b>\n"
    f"Used: <b>{fmt_bytes(info['disk_used'])}</b>\n"
    f"Total: <b>{fmt_bytes(info['disk_total'])}</b>\n\n"

    "📈 <b>LOAD AVERAGE</b>\n"
    f"1m: <b>{info['load_1']:.2f}</b>\n"
    f"5m: <b>{info['load_5']:.2f}</b>\n"
    f"15m: <b>{info['load_15']:.2f}</b>\n\n"

    "⏱️ <b>RUNTIME</b>\n"
    f"🚀 Uptime: "
    f"<b>{fmt_duration(time.time() - START_TIME)}</b>\n"
    f"🚀 Active Tasks: "
    f"<b>{fmt_number(len(LIVE_TASKS))}</b>\n"
    f"👥 Memory Users: "
    f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n"
)

return text[:4000]
#

# ============================================================

# SEARCH PAGE

# ============================================================

def build_search_page():

#
today = today_key()

text = (
    "🔎 <b>SEARCH COMMAND CENTER</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    "📊 <b>SEARCH OVERVIEW</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"

    f"🔍 Total Searches: "
    f"<b>{fmt_number(STATS['searches'])}</b>\n"

    f"📅 Today: "
    f"<b>{fmt_number(DAILY_SEARCHES.get(today, 0))}</b>\n"

    f"👥 Users Searching: "
    f"<b>{fmt_number(len(USER_SEARCHES))}</b>\n"

    f"🔥 Unique Search Terms: "
    f"<b>{fmt_number(len(SEARCH_TERMS))}</b>\n\n"

    "🔥 <b>TOP 10</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
)


if not SEARCH_TERMS:

    text += (
        "💤 No search terms recorded."
    )

    return text


for index, (
    query,
    count,
) in enumerate(
    SEARCH_TERMS.most_common(10),
    start=1,
):

    safe_query = (
        str(query)
        .replace(
            "<",
            "&lt;",
        )
        .replace(
            ">",
            "&gt;",
        )
    )

    text += (
        f"🔹 <b>{index}.</b> "
        f"{safe_query[:60]} "
        f"— <b>{fmt_number(count)}</b>\n"
    )


return text[:4000]
#

# ============================================================

# MAIN KEYBOARD

# ============================================================

def main_keyboard():

#
return InlineKeyboardMarkup(
    [

        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="dv:stats",
            ),

            InlineKeyboardButton(
                "👥 Users",
                callback_data="dv:users",
            ),
        ],

        [
            InlineKeyboardButton(
                "🔎 Searches",
                callback_data="dv:searches",
            ),

            InlineKeyboardButton(
                "🔥 Top Searches",
                callback_data="dv:top",
            ),
        ],

        [
            InlineKeyboardButton(
                "💾 Database",
                callback_data="dv:database",
            ),

            InlineKeyboardButton(
                "🚀 Live Work",
                callback_data="dv:tasks",
            ),
        ],

        [
            InlineKeyboardButton(
                "📋 Live Logs",
                callback_data="dv:logs",
            ),

            InlineKeyboardButton(
                "⚙️ System",
                callback_data="dv:system",
            ),
        ],

        [
            InlineKeyboardButton(
                "🔴 LIVE DASHBOARD",
                callback_data="dv:live",
            ),
        ],

    ]
)
#

# ============================================================

# BACK KEYBOARD

# ============================================================

def back_keyboard():

#
return InlineKeyboardMarkup(
    [

        [
            InlineKeyboardButton(
                "⬅️ Dashboard",
                callback_data="dv:home",
            ),

            InlineKeyboardButton(
                "🔄 Refresh",
                callback_data="dv:refresh",
            ),
        ],

    ]
)
#

# ============================================================

# LIVE BACK KEYBOARD

# ============================================================

def live_keyboard():

#
return InlineKeyboardMarkup(
    [

        [
            InlineKeyboardButton(
                "⏹️ Stop Live",
                callback_data="dv:home",
            ),

            InlineKeyboardButton(
                "🔄 Update Now",
                callback_data="dv:live_refresh",
            ),
        ],

        [
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="dv:stats",
            ),

            InlineKeyboardButton(
                "🚀 Current Work",
                callback_data="dv:tasks",
            ),
        ],

        [
            InlineKeyboardButton(
                "📋 Live Logs",
                callback_data="dv:logs",
            ),

            InlineKeyboardButton(
                "💾 Database",
                callback_data="dv:database",
            ),
        ],

    ]
)
#

# ============================================================

# /ADMIN

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

#
user_id = (
    message.from_user.id
    if message.from_user
    else None
)

if not is_admin(
    user_id
):
    return


track_command(
    user_id
)


try:

    text = await build_dashboard()

    sent = await message.reply_text(
        text,
        reply_markup=main_keyboard(),
        parse_mode=enums.ParseMode.HTML,
    )

    ACTIVE_PANELS[
        user_id
    ] = {

        "chat_id": message.chat.id,

        "message_id": sent.id,

        "mode": "home",

        "live": False,

    }

except Exception:

    logger.exception(
        "[DOWNTOWN VILLA ADMIN] "
        "Failed to open panel."
    )
#

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

#
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
        show_alert=True,
    )

    return


action = query.data[
    3:
]


try:

    await query.answer()

except Exception:
    pass


try:

    if action in (
        "home",
        "refresh",
    ):

        text = await build_dashboard()

        await query.message.edit_text(
            text,
            reply_markup=main_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id":
                query.message.chat.id,

            "message_id":
                query.message.id,

            "mode":
                "home",

            "live":
                False,

        }

        return


    if action in (
        "live",
        "live_refresh",
    ):

        text = await build_dashboard()

        await query.message.edit_text(
            text,
            reply_markup=live_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id":
                query.message.chat.id,

            "message_id":
                query.message.id,

            "mode":
                "live",

            "live":
                True,

        }

        return


    if action == "stats":

        await query.message.edit_text(
            build_statistics(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id":
                query.message.chat.id,

            "message_id":
                query.message.id,

            "mode":
                "stats",

            "live":
                False,

        }

        return


    if action == "users":

        await query.message.edit_text(
            build_users(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id":
                query.message.chat.id,

            "message_id":
                query.message.id,

            "mode":
                "users",

            "live":
                False,

        }

        return


    if action == "searches":

        await query.message.edit_text(
            build_search_page(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id":
                query.message.chat.id,

            "message_id":
                query.message.id,

            "mode":
                "searches",

            "live":
                False,

        }

        return


    if action == "database":

        text = await build_database_page()

        await query.message.edit_text(
            text,
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id":
                query.message.chat.id,

            "message_id":
                query.message.id,

            "mode":
                "database",

            "live":
                False,

        }

        return


    if action == "tasks":

        await query.message.edit_text(
            build_tasks_text(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id":
                query.message.chat.id,

            "message_id":
                query.message.id,

            "mode":
                "tasks",

            "live":
                False,

        }

        return


    if action == "logs":

        await query.message.edit_text(
            build_logs_text(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id":
                query.message.chat.id,

            "message_id":
                query.message.id,

            "mode":
                "logs",

            "live":
                False,

        }

        return


    if action == "system":

        await query.message.edit_text(
            build_system(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id":
                query.message.chat.id,

            "message_id":
                query.message.id,

            "mode":
                "system",

            "live":
                False,

        }

        return


    if action == "top":

        await query.message.edit_text(
            build_top_searches(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        ACTIVE_PANELS[
            user_id
        ] = {

            "chat_id":
                query.message.chat.id,

            "message_id":
                query.message.id,

            "mode":
                "top",

            "live":
                False,

        }

        return


except FloodWait as e:

    try:

        await asyncio.sleep(
            int(
                getattr(
                    e,
                    "value",
                    5,
                )
            )
        )

    except Exception:
        pass


except RPCError:

    logger.exception(
        "[DOWNTOWN VILLA ADMIN] "
        "Telegram callback error."
    )


except Exception:

    logger.exception(
        "[DOWNTOWN VILLA ADMIN] "
        "Callback error."
    )
#

# ============================================================

# AUTOMATIC LIVE PANEL

# ============================================================

async def live_dashboard_loop():

#
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

            try:

                if not panel.get(
                    "live",
                    False,
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


                if not chat_id:
                    continue

                if not message_id:
                    continue


                text = await build_dashboard()


                await _admin_client.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=live_keyboard(),
                    parse_mode=enums.ParseMode.HTML,
                )


            except FloodWait as e:

                try:

                    await asyncio.sleep(
                        int(
                            getattr(
                                e,
                                "value",
                                5,
                            )
                        )
                    )

                except Exception:
                    pass


            except RPCError:

                continue


            except Exception:

                continue


    except asyncio.CancelledError:

        break


    except Exception:

        logger.exception(
            "[DOWNTOWN VILLA ADMIN] "
            "Live dashboard loop error."
        )
#

# ============================================================

# INITIALIZE PANEL

# ============================================================

def initialize_admin_panel(
client,
):

#
global _admin_client
global _live_task


_admin_client = client


if _live_task is None:

    try:

        _live_task = asyncio.create_task(
            live_dashboard_loop()
        )

        logger.info(
            "[DOWNTOWN VILLA ADMIN] "
            "Live dashboard started."
        )

    except Exception:

        logger.exception(
            "[DOWNTOWN VILLA ADMIN] "
            "Unable to start live dashboard."
        )
#

# ============================================================

# OPTIONAL INTERNAL INITIALIZER

# ============================================================

@Client.on_message(
filters.command(
"downtownvilla_admin_init"
)
)
async def downtownvilla_admin_init(
client,
message,
):

#
return
#

# ============================================================

# STARTUP LOGGING

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
"Live update interval: %.1fs",
PANEL_UPDATE_SECONDS,
)

logger.info(
"[DOWNTOWN VILLA ADMIN] "
"Database monitoring: ENABLED"
)

logger.info(
"[DOWNTOWN VILLA ADMIN] "
"Media / Media2 / Media3 monitoring: ENABLED"
)

logger.info(
"[DOWNTOWN VILLA ADMIN] "
"Live logs: ENABLED"
)

logger.info(
"[DOWNTOWN VILLA ADMIN] "
"Live work monitoring: ENABLED"
)

logger.info(
"=================================================="
)
