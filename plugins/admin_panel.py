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

try:
from info import COLLECTION_NAME, MULTIPLE_DB
except Exception:
COLLECTION_NAME = "Telegram_files"
MULTIPLE_DB = True

# ============================================================

# DOWNTOWN VILLA - ULTIMATE LIVE ADMIN PANEL

# ============================================================

logger = logging.getLogger(**name**)

# ============================================================

# ADMIN IDS

# ============================================================

ADMIN_IDS = {
int(x.strip())
for x in os.getenv("ADMINS", "").replace(",", " ").split()
if x.strip().isdigit()
}

# ============================================================

# CONFIG

# ============================================================

PANEL_UPDATE_SECONDS = float(
os.getenv("ADMIN_PANEL_UPDATE_SECONDS", "3")
)

MAX_LIVE_LOGS = int(
os.getenv("ADMIN_MAX_LIVE_LOGS", "80")
)

MAX_TASKS = int(
os.getenv("ADMIN_MAX_TASKS", "50")
)

# ============================================================

# START TIME

# ============================================================

START_TIME = time.time()

# ============================================================

# MEMORY STATISTICS

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

# USER TRACKING

# ============================================================

KNOWN_USERS = set()

DAILY_USERS = Counter()
DAILY_SEARCHES = Counter()

USER_SEARCHES = Counter()
USER_LAST_SEEN = {}

SEARCH_TERMS = Counter()

# ============================================================

# LIVE TASKS

# ============================================================

LIVE_TASKS = {}

# ============================================================

# LIVE LOG BUFFER

# ============================================================

LIVE_LOGS = deque(
maxlen=MAX_LIVE_LOGS
)

# ============================================================

# PANEL STATES

# ============================================================

ACTIVE_PANELS = {}

PANEL_LOCK = asyncio.Lock()

# ============================================================

# LOGGER HANDLER

# ============================================================

class TelegramMemoryLogHandler(logging.Handler):

```
def emit(self, record):

    try:

        now = datetime.now().strftime(
            "%H:%M:%S"
        )

        level = record.levelname

        message = record.getMessage()

        if len(message) > 300:

            message = (
                message[:300]
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
```

try:

```
_memory_handler = TelegramMemoryLogHandler()

_memory_handler.setLevel(
    logging.INFO
)

logging.getLogger().addHandler(
    _memory_handler
)
```

except Exception:
pass

# ============================================================

# BASIC HELPERS

# ============================================================

def is_admin(user_id):

```
return (
    user_id is not None
    and int(user_id) in ADMIN_IDS
)
```

def fmt_number(value):

```
try:
    return f"{int(value):,}"
except Exception:
    return "0"
```

def fmt_bytes(value):

```
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
```

def fmt_duration(seconds):

```
try:

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

    if days:

        return (
            f"{days}d "
            f"{hours}h "
            f"{minutes}m"
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
```

def progress_bar(
current,
total,
length=16,
):

```
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
            percentage,
        ),
    )

    filled = int(
        length
        * percentage
        / 100
    )

    empty = length - filled

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
```

def today_key():

```
return datetime.now().strftime(
    "%Y-%m-%d"
)
```

def human_today():

```
return datetime.now().strftime(
    "%d %b %Y"
)
```

# ============================================================

# PUBLIC TRACKING FUNCTIONS

# ============================================================

#

# Other plugins can import these functions.

#

# Example:

#

# from plugins.admin_panel import track_search

#

# track_search(user_id, "breaking bad")

#

# ============================================================

def track_user(
user_id,
):

```
if not user_id:
    return

try:

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
```

def track_search(
user_id,
query="",
):

```
try:

    track_user(
        user_id
    )

    STATS[
        "searches"
    ] += 1

    key = today_key()

    DAILY_SEARCHES[
        key
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
```

def track_command(
user_id,
):

```
try:

    track_user(
        user_id
    )

    STATS[
        "commands"
    ] += 1

except Exception:
    pass
```

def track_file_sent():

```
STATS[
    "files_sent"
] += 1
```

def track_indexed(
count=1,
):

```
try:

    STATS[
        "files_indexed"
    ] += int(count)

except Exception:
    pass
```

def track_skipped(
count=1,
):

```
try:

    STATS[
        "files_skipped"
    ] += int(count)

except Exception:
    pass
```

def track_error():

```
STATS[
    "errors"
] += 1
```

# ============================================================

# LIVE TASK API

# ============================================================

def start_live_task(
task_id,
name,
task_type="WORK",
total=0,
):

```
LIVE_TASKS[
    str(task_id)
] = {
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

while len(
    LIVE_TASKS
) > MAX_TASKS:

    LIVE_TASKS.pop(
        next(
            iter(
                LIVE_TASKS
            )
        ),
        None,
    )
```

def update_live_task(
task_id,
current=None,
total=None,
speed=None,
message=None,
):

```
task = LIVE_TASKS.get(
    str(task_id)
)

if not task:
    return

if current is not None:

    task[
        "current"
    ] = int(current)

if total is not None:

    task[
        "total"
    ] = int(total)

if speed is not None:

    task[
        "speed"
    ] = float(speed)

if message is not None:

    task[
        "message"
    ] = str(message)

task[
    "updated"
] = time.time()
```

def finish_live_task(
task_id,
status="COMPLETED",
):

```
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
```

def remove_live_task(
task_id,
):

```
LIVE_TASKS.pop(
    str(task_id),
    None,
)
```

# ============================================================

# DATABASE STATS

# ============================================================

async def database_stats(
database,
model,
):

```
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
```

async def get_all_database_stats():

```
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
```

# ============================================================

# SYSTEM INFORMATION

# ============================================================

def system_info():

```
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
```

# ============================================================

# LIVE TASK TEXT

# ============================================================

def build_tasks_text():

```
if not LIVE_TASKS:

    return (
        "🚀 <b>CURRENT WORK</b>\n\n"
        "💤 No active tasks right now."
    )

text = (
    "🚀 <b>CURRENT WORK</b>\n\n"
)

for task_id, task in list(
    LIVE_TASKS.items()
):

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

    if status == "RUNNING":

        icon = "🟢"

    elif status == "COMPLETED":

        icon = "✅"

    elif status == "FAILED":

        icon = "❌"

    else:

        icon = "🟡"

    text += (
        f"{icon} <b>"
        f"{task.get('name', task_id)}"
        f"</b>\n"
    )

    text += (
        f"   🏷 {task.get('type', 'WORK')}\n"
    )

    if total:

        text += (
            f"   {progress_bar(current, total)}\n"
            f"   📦 {fmt_number(current)} / "
            f"{fmt_number(total)}\n"
        )

    if speed:

        text += (
            f"   ⚡ {speed:.1f}/sec\n"
        )

    if task.get("message"):

        text += (
            f"   💬 "
            f"{task['message'][:100]}\n"
        )

    text += (
        f"   ⏱ "
        f"{fmt_duration(time.time() - task.get('started', time.time()))}\n\n"
    )

return text
```

# ============================================================

# LIVE LOG TEXT

# ============================================================

def build_logs_text():

```
if not LIVE_LOGS:

    return (
        "📋 <b>LIVE LOGS</b>\n\n"
        "💤 No logs captured yet."
    )

text = (
    "📋 <b>LIVE LOGS</b>\n\n"
)

logs = list(
    LIVE_LOGS
)[-20:]

for item in logs:

    level = item.get(
        "level",
        "INFO",
    )

    if level == "ERROR":

        icon = "🔴"

    elif level == "WARNING":

        icon = "🟡"

    elif level == "DEBUG":

        icon = "🔵"

    else:

        icon = "🟢"

    message = item.get(
        "message",
        "",
    )

    text += (
        f"<code>{item.get('time', '')}</code> "
        f"{icon} "
        f"{message[:180]}\n"
    )

return text[-3900:]
```

# ============================================================

# OVERVIEW

# ============================================================

async def build_dashboard():

```
uptime = fmt_duration(
    time.time()
    - START_TIME
)

system = system_info()

databases = (
    await get_all_database_stats()
)

total_files = sum(
    x.get(
        "documents",
        0,
    )
    for x in databases
)

total_db_size = sum(
    x.get(
        "size",
        0,
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

text = (
    "╔══════════════════════════╗\n"
    "║   🏙️ <b>DOWNTOWN VILLA</b>   ║\n"
    "║   🤖 <b>ADMIN CONTROL</b>    ║\n"
    "╚══════════════════════════╝\n\n"

    "🟢 <b>SYSTEM ONLINE</b>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "📊 <b>LIVE OVERVIEW</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"

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

    f"💾 Database Size: "
    f"<b>{fmt_bytes(total_db_size)}</b>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚙️ <b>SYSTEM</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    f"⏱ Uptime: "
    f"<b>{uptime}</b>\n"

    f"🧠 RAM: "
    f"<b>{system['ram_percent']:.1f}%</b> "
    f"({fmt_bytes(system['ram_used'])})\n"

    f"⚡ CPU: "
    f"<b>{system['cpu']:.1f}%</b>\n"

    f"💽 Disk: "
    f"<b>{system['disk_percent']:.1f}%</b>\n"

    f"🐍 Python: "
    f"<b>{system['python']}</b>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "🚀 <b>ACTIVITY</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    f"📥 Indexed: "
    f"<b>{fmt_number(STATS['files_indexed'])}</b>\n"

    f"⏭ Skipped: "
    f"<b>{fmt_number(STATS['files_skipped'])}</b>\n"

    f"📤 Sent: "
    f"<b>{fmt_number(STATS['files_sent'])}</b>\n"

    f"⚠️ Errors: "
    f"<b>{fmt_number(STATS['errors'])}</b>\n"

    f"🚀 Active Tasks: "
    f"<b>{len(LIVE_TASKS)}</b>\n\n"

    f"🕒 <i>{human_today()}</i>"
)

return text
```

# ============================================================

# STATISTICS PAGE

# ============================================================

def build_statistics():

```
today = today_key()

text = (
    "📊 <b>DOWNTOWN VILLA STATISTICS</b>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "👥 <b>USERS</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    f"👤 Total tracked: "
    f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n"

    f"🆕 Today: "
    f"<b>{fmt_number(DAILY_USERS.get(today, 0))}</b>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "🔎 <b>SEARCHES</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    f"🔍 Total: "
    f"<b>{fmt_number(STATS['searches'])}</b>\n"

    f"📅 Today: "
    f"<b>{fmt_number(DAILY_SEARCHES.get(today, 0))}</b>\n"

    f"⚡ Last recorded searches: "
    f"<b>{fmt_number(STATS['searches'])}</b>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "📦 <b>FILES</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    f"📥 Indexed: "
    f"<b>{fmt_number(STATS['files_indexed'])}</b>\n"

    f"⏭ Skipped: "
    f"<b>{fmt_number(STATS['files_skipped'])}</b>\n"

    f"📤 Sent: "
    f"<b>{fmt_number(STATS['files_sent'])}</b>\n"

    f"❌ Errors: "
    f"<b>{fmt_number(STATS['errors'])}</b>\n"
)

return text
```

# ============================================================

# TOP SEARCHES

# ============================================================

def build_top_searches():

```
text = (
    "🔥 <b>TOP SEARCHES</b>\n\n"
)

if not SEARCH_TERMS:

    text += (
        "💤 No search data recorded yet."
    )

    return text

for index, (
    query,
    count,
) in enumerate(
    SEARCH_TERMS.most_common(15),
    start=1,
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
        f"{query[:60]} "
        f"— <b>{fmt_number(count)}</b>\n"
    )

return text
```

# ============================================================

# USER STATISTICS

# ============================================================

def build_users():

```
text = (
    "👥 <b>USER ACTIVITY</b>\n\n"

    f"👤 Total tracked: "
    f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "🔥 <b>MOST ACTIVE USERS</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
)

if not USER_SEARCHES:

    text += (
        "💤 No user search data yet."
    )

    return text

for index, (
    user_id,
    count,
) in enumerate(
    USER_SEARCHES.most_common(15),
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

        last_text = "unknown"

    text += (
        f"👤 <b>{index}.</b> "
        f"<code>{user_id}</code>\n"
        f"   🔎 Searches: "
        f"<b>{count}</b>\n"
        f"   🕒 Last seen: "
        f"<b>{last_text}</b>\n\n"
    )

return text
```

# ============================================================

# DATABASE PAGE

# ============================================================

async def build_database_page():

```
databases = (
    await get_all_database_stats()
)

names = [
    "PRIMARY — Media",
    "SECONDARY — Media2",
    "TERTIARY — Media3",
]

text = (
    "💾 <b>DATABASE CONTROL CENTER</b>\n\n"
)

total_files = 0
total_size = 0

for index, data in enumerate(
    databases
):

    name = (
        names[index]
        if index < len(names)
        else f"DATABASE {index + 1}"
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
        f"📦 Files: "
        f"<b>{fmt_number(documents)}</b>\n"
        f"💽 Size: "
        f"<b>{fmt_bytes(size)}</b>\n"
        f"📡 Status: "
        f"<b>{status}</b>\n"
    )

    if data.get("error"):

        text += (
            f"⚠️ {str(data['error'])[:200]}\n"
        )

    text += "\n"

    total_files += documents
    total_size += size

text += (
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "📊 <b>TOTAL</b>\n\n"
    f"📦 Files: "
    f"<b>{fmt_number(total_files)}</b>\n"
    f"💾 Size: "
    f"<b>{fmt_bytes(total_size)}</b>\n"
)

return text
```

# ============================================================

# SYSTEM PAGE

# ============================================================

def build_system():

```
info = system_info()

text = (
    "⚙️ <b>DOWNTOWN VILLA SYSTEM</b>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "🖥️ <b>SERVER</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    f"🐍 Python: "
    f"<b>{info['python']}</b>\n"

    f"💻 Platform: "
    f"<b>{info['platform'][:80]}</b>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "🧠 <b>RESOURCES</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    f"⚡ CPU: "
    f"<b>{info['cpu']:.1f}%</b>\n"

    f"🧠 RAM: "
    f"<b>{info['ram_percent']:.1f}%</b>\n"

    f"   Used: "
    f"<b>{fmt_bytes(info['ram_used'])}</b>\n"

    f"   Total: "
    f"<b>{fmt_bytes(info['ram_total'])}</b>\n\n"

    f"💽 Disk: "
    f"<b>{info['disk_percent']:.1f}%</b>\n"

    f"   Used: "
    f"<b>{fmt_bytes(info['disk_used'])}</b>\n"

    f"   Total: "
    f"<b>{fmt_bytes(info['disk_total'])}</b>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "⏱ <b>RUNTIME</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    f"🚀 Uptime: "
    f"<b>{fmt_duration(time.time() - START_TIME)}</b>\n"

    f"📋 Active tasks: "
    f"<b>{len(LIVE_TASKS)}</b>\n"

    f"👥 Memory users: "
    f"<b>{fmt_number(len(KNOWN_USERS))}</b>\n"
)

return text
```

# ============================================================

# KEYBOARD

# ============================================================

def main_keyboard():

```
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
                "💾 Database",
                callback_data="dv:database",
            ),
        ],
        [
            InlineKeyboardButton(
                "🚀 Live Work",
                callback_data="dv:tasks",
            ),
            InlineKeyboardButton(
                "📋 Live Logs",
                callback_data="dv:logs",
            ),
        ],
        [
            InlineKeyboardButton(
                "⚙️ System",
                callback_data="dv:system",
            ),
            InlineKeyboardButton(
                "🔥 Top Searches",
                callback_data="dv:top",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔄 LIVE DASHBOARD",
                callback_data="dv:live",
            ),
        ],
    ]
)
```

def back_keyboard():

```
return InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(
                "⬅️ Dashboard",
                callback_data="dv:home",
            ),
            InlineKeyboardButton(
                "🔄 Update",
                callback_data="dv:refresh",
            ),
        ]
    ]
)
```

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

```
if not is_admin(
    message.from_user.id
    if message.from_user
    else None
):

    return

track_command(
    message.from_user.id
)

text = await build_dashboard()

sent = await message.reply_text(
    text,
    reply_markup=main_keyboard(),
    parse_mode=enums.ParseMode.HTML,
)

ACTIVE_PANELS[
    message.from_user.id
] = {
    "chat_id": message.chat.id,
    "message_id": sent.id,
    "mode": "home",
    "live": False,
}
```

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

```
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

await query.answer()

try:

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

        ACTIVE_PANELS[
            user_id
        ] = {
            "chat_id": query.message.chat.id,
            "message_id": query.message.id,
            "mode": "home",
            "live": action == "live",
        }

        return

    if action == "stats":

        await query.message.edit_text(
            build_statistics(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        return

    if action == "users":

        await query.message.edit_text(
            build_users(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        return

    if action == "searches":

        await query.message.edit_text(
            (
                "🔎 <b>SEARCH CENTER</b>\n\n"
                f"📊 Total searches: "
                f"<b>{fmt_number(STATS['searches'])}</b>\n"
                f"📅 Today: "
                f"<b>{fmt_number(DAILY_SEARCHES.get(today_key(), 0))}</b>\n\n"
                "Tap <b>🔥 Top Searches</b> "
                "from the dashboard to view "
                "the most searched terms."
            ),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        return

    if action == "database":

        text = await build_database_page()

        await query.message.edit_text(
            text,
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        return

    if action == "tasks":

        await query.message.edit_text(
            build_tasks_text(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        return

    if action == "logs":

        await query.message.edit_text(
            build_logs_text(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        return

    if action == "system":

        await query.message.edit_text(
            build_system(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

        return

    if action == "top":

        await query.message.edit_text(
            build_top_searches(),
            reply_markup=back_keyboard(),
            parse_mode=enums.ParseMode.HTML,
        )

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
        "[ADMIN PANEL] Telegram error"
    )

except Exception:

    logger.exception(
        "[ADMIN PANEL] Callback error"
    )
```

# ============================================================

# AUTOMATIC LIVE DASHBOARD

# ============================================================

async def live_dashboard_loop():

```
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

            if not panel.get(
                "live"
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

            if not chat_id or not message_id:

                continue

            try:

                # ------------------------------------------------
                # Dashboard is intentionally lightweight.
                #
                # We don't query MongoDB every 3 seconds.
                # ------------------------------------------------

                text = await build_dashboard()

                await _admin_client.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=main_keyboard(),
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

            except Exception:

                continue

    except asyncio.CancelledError:

        break

    except Exception:

        logger.exception(
            "[ADMIN PANEL] Live loop error"
        )
```

_admin_client = None
_live_task = None

# ============================================================

# STARTUP HOOK

# ============================================================

@Client.on_message(
filters.command(
"adminpanel_start_internal"
)
)
async def _admin_internal_start(
client,
message,
):

```
return
```

def initialize_admin_panel(
client,
):

```
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
            "Failed to start live dashboard."
        )
```

# ============================================================

# PYROGRAM STARTUP

# ============================================================

@Client.on_message(
filters.command(
"downtownvilla_admin_init"
)
)
async def _init_admin(
client,
message,
):

```
return
```

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
"Live logs: ENABLED"
)

logger.info(
"=================================================="
)
