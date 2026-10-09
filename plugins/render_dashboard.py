# ============================================================
# RENDER LIVE DASHBOARD — /render
# ============================================================
#
# Live panel showing:
#   • Which account is currently active (auto-detected)
#   • Account email + workspace name
#   • Service name + region + plan
#   • Bandwidth usage vs plan limit
#       (measured by psutil TX, persisted to MongoDB,
#        survives redeploys, resets every month)
#   • CPU / RAM / Disk  (from psutil, real-time)
#   • Network TX / RX rates
#   • Uptime + health
#
# Colors go GREEN → YELLOW → ORANGE → RED as usage grows.
# Multi-account: tries each configured pair in order, uses the
# first one that responds. Remembers the working one.
#
# ============================================================

import os
import time
import asyncio
import logging
from datetime import datetime

import aiohttp

try:
    import psutil
except ImportError:
    psutil = None

from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.errors import FloodWait, RPCError


# ============================================================
# LOGGER
# ============================================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ============================================================
# PERSISTENT BANDWIDTH STORAGE
# ============================================================
#
# We do NOT use Render's metrics API for bandwidth.
# Instead we:
#   1. Read psutil.net_io_counters().bytes_sent on every refresh
#   2. Compute the delta since the last save
#   3. Add the delta to a running monthly total in MongoDB
#   4. On container restart (psutil resets to 0), keep adding
#      from where we left off
#   5. New month → fresh counter
#
# Render counts OUTBOUND (TX) as billable bandwidth.
# RX is incoming, tracked for display only.
#
# ============================================================

try:
    from database.ia_filterdb import db as _main_db
except Exception:
    _main_db = None

_BW_PERSIST_COLLECTION = "Telegram_files_render_bw"
_BW_SAVE_INTERVAL      = 60   # save to Mongo at most once per minute
_BW_STATE              = {"last_save_ts": 0.0}


def _bw_coll():
    if _main_db is None:
        return None
    try:
        return _main_db[_BW_PERSIST_COLLECTION]
    except Exception:
        return None


async def _bw_load(service_id, month):
    """Read stored running total for this service+month."""
    coll = _bw_coll()
    if coll is None:
        return None
    try:
        return await coll.find_one({"_id": f"{service_id}:{month}"})
    except Exception:
        logger.exception("[RENDER] bw_load failed")
        return None


async def _bw_save(service_id, month, tx_bytes, rx_bytes,
                   last_psutil_tx, last_psutil_rx,
                   email=None, plan=None):
    """Write updated running total to Mongo."""
    coll = _bw_coll()
    if coll is None:
        return
    try:
        await coll.update_one(
            {"_id": f"{service_id}:{month}"},
            {
                "$set": {
                    "service_id":     service_id,
                    "month":          month,
                    "tx_bytes":       int(tx_bytes),
                    "rx_bytes":       int(rx_bytes),
                    "last_psutil_tx": int(last_psutil_tx),
                    "last_psutil_rx": int(last_psutil_rx),
                    "email":          email,
                    "plan":           plan,
                    "updated_at":     datetime.utcnow(),
                },
                "$setOnInsert": {
                    "created_at": datetime.utcnow(),
                },
            },
            upsert=True,
        )
    except Exception:
        logger.exception("[RENDER] bw_save failed")


async def get_persistent_tx_rx(service_id, email=None, plan=None):
    """
    Return accumulated {tx_bytes, rx_bytes} for this month.
    Survives redeploys, resets every calendar month.
    """
    if not service_id:
        return {"tx_bytes": 0, "rx_bytes": 0}

    month_key = datetime.utcnow().strftime("%Y-%m")
    now_ts = time.time()

    # Current psutil readings (0 if unavailable)
    cur_tx = 0
    cur_rx = 0
    if psutil:
        try:
            io = psutil.net_io_counters()
            cur_tx = int(io.bytes_sent)
            cur_rx = int(io.bytes_recv)
        except Exception:
            pass

    # Load last stored state
    stored = await _bw_load(service_id, month_key)

    if stored is None:
        stored_tx        = 0
        stored_rx        = 0
        stored_psutil_tx = cur_tx
        stored_psutil_rx = cur_rx
    else:
        stored_tx        = int(stored.get("tx_bytes", 0) or 0)
        stored_rx        = int(stored.get("rx_bytes", 0) or 0)
        stored_psutil_tx = int(stored.get("last_psutil_tx", 0) or 0)
        stored_psutil_rx = int(stored.get("last_psutil_rx", 0) or 0)

    # Compute delta since last save
    # If current psutil counter is LOWER than stored → container
    # restarted (psutil resets). Then delta = current counter
    # (the whole current session's traffic since boot).
    delta_tx = cur_tx if cur_tx < stored_psutil_tx else cur_tx - stored_psutil_tx
    delta_rx = cur_rx if cur_rx < stored_psutil_rx else cur_rx - stored_psutil_rx

    new_tx = stored_tx + delta_tx
    new_rx = stored_rx + delta_rx

    # Throttled save
    if now_ts - _BW_STATE["last_save_ts"] >= _BW_SAVE_INTERVAL:
        await _bw_save(
            service_id, month_key,
            tx_bytes=new_tx,
            rx_bytes=new_rx,
            last_psutil_tx=cur_tx,
            last_psutil_rx=cur_rx,
            email=email,
            plan=plan,
        )
        _BW_STATE["last_save_ts"] = now_ts

    return {"tx_bytes": new_tx, "rx_bytes": new_rx}


# ============================================================
# CONFIG — MULTI-ACCOUNT
# ============================================================

def _split_env(name):
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


_PLAN_LIMITS_GB = {
    "hobby": 5.0,
    "pro": 25.0,
    "scale": 1024.0,
    "free": 100.0,
}

_keys     = _split_env("RENDER_API_KEYS")
_services = _split_env("RENDER_SERVICE_IDS")
_plans    = _split_env("RENDER_PLANS")

# Backward-compat: single vars still work
if not _keys:
    single_key = os.getenv("RENDER_API_KEY", "").strip()
    if single_key:
        _keys = [single_key]

if not _services:
    single_srv = os.getenv("RENDER_SERVICE_ID", "").strip()
    if single_srv:
        _services = [single_srv]

if not _plans:
    single_plan = os.getenv("RENDER_PLAN", "hobby").strip().lower()
    _plans = [single_plan]

# Normalize lengths
_count    = min(len(_keys), len(_services))
_keys     = _keys[:_count]
_services = _services[:_count]

while len(_plans) < _count:
    _plans.append("hobby")
_plans = _plans[:_count]

# Build credential list
RENDER_ACCOUNTS = [
    {
        "api_key":    _keys[i],
        "service_id": _services[i],
        "plan":       _plans[i].lower(),
        "limit_gb":   _PLAN_LIMITS_GB.get(_plans[i].lower(), 5.0),
        # runtime-filled
        "account_name":   None,
        "account_email":  None,
        "workspace_name": None,
        "service_name":   None,
        "service_region": None,
        "service_url":    None,
        "service_type":   None,
    }
    for i in range(_count)
]

# Currently-active account
_ACTIVE_ACCOUNT = {"index": 0}

# Metadata cache — refresh every 30 minutes
_META_CACHE = {"ts": 0.0}


def _active():
    """Return the currently-selected account dict, or None."""
    if not RENDER_ACCOUNTS:
        return None
    i = _ACTIVE_ACCOUNT["index"]
    if 0 <= i < len(RENDER_ACCOUNTS):
        return RENDER_ACCOUNTS[i]
    return RENDER_ACCOUNTS[0]


# Legacy globals for compatibility
RENDER_API_KEY     = _keys[0]    if _keys    else ""
RENDER_SERVICE_ID  = _services[0] if _services else ""
RENDER_PLAN        = _plans[0]   if _plans   else "hobby"
BANDWIDTH_LIMIT_GB = _PLAN_LIMITS_GB.get(RENDER_PLAN, 5.0)

PROCESS_START_TS = time.time()


# ============================================================
# ADMIN HELPERS
# ============================================================

try:
    from info import ADMINS as _ADMINS
except Exception:
    _ADMINS = os.getenv("ADMINS", "").replace(",", " ").split()


def _admin_ids():
    out = set()
    values = _ADMINS
    if isinstance(values, (str, int)):
        values = str(values).replace(",", " ").split()
    if values is None:
        values = []
    for v in values:
        try:
            out.add(int(v))
        except Exception:
            pass
    for v in os.getenv("ADMINS", "").replace(",", " ").split():
        try:
            out.add(int(v))
        except Exception:
            pass
    return out


ADMIN_IDS = _admin_ids()


def is_admin(uid):
    try:
        return int(uid) in ADMIN_IDS
    except Exception:
        return False


# ============================================================
# FORMATTING HELPERS
# ============================================================

def fmt_bytes(n):
    try:
        n = float(n)
    except Exception:
        return "0 B"
    if n <= 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.2f} {units[i]}"


def fmt_duration(sec):
    try:
        sec = max(0, int(sec))
    except Exception:
        return "0s"
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, sec = divmod(sec, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if sec or not parts: parts.append(f"{sec}s")
    return " ".join(parts)


def now_text():
    return datetime.utcnow().strftime("%d %b %Y · %H:%M:%S UTC")


def html_escape(value):
    text = str(value if value is not None else "")
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ============================================================
# USAGE BAR — GREEN → RED as usage grows
# ============================================================

def usage_bar(pct, length=12):
    """
    Usage bar. More usage = redder.
    Green → Yellow → Orange → Red
    """
    try:
        pct = float(pct)
    except Exception:
        pct = 0.0
    pct = max(0.0, min(100.0, pct))
    filled = int(length * pct / 100)

    if pct >= 90:
        fill = "🟥"
    elif pct >= 60:
        fill = "🟧"
    elif pct >= 30:
        fill = "🟨"
    else:
        fill = "🟩"

    empty = "⬛"
    return fill * filled + empty * (length - filled)


def usage_badge(pct):
    try:
        pct = float(pct)
    except Exception:
        pct = 0.0
    if pct >= 90:   return "🔴"
    if pct >= 60:   return "🟠"
    if pct >= 30:   return "🟡"
    return "🟢"


# ============================================================
# CGROUP HELPERS (container-aware CPU / RAM)
# ============================================================

def _read_cgroup_memory():
    try:
        with open("/sys/fs/cgroup/memory.max") as f:
            raw = f.read().strip()
            if raw != "max":
                return int(raw)
    except Exception:
        pass
    try:
        with open("/sys/fs/cgroup/memory/memory.limit_in_bytes") as f:
            val = int(f.read().strip())
            if val < (1 << 60):
                return val
    except Exception:
        pass
    return 0


def _read_cgroup_memory_used():
    try:
        with open("/sys/fs/cgroup/memory.current") as f:
            return int(f.read().strip())
    except Exception:
        pass
    try:
        with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as f:
            return int(f.read().strip())
    except Exception:
        pass
    return 0


def _read_cgroup_cpu_quota():
    try:
        with open("/sys/fs/cgroup/cpu.max") as f:
            parts = f.read().strip().split()
            if len(parts) == 2 and parts[0] != "max":
                quota = int(parts[0])
                period = int(parts[1])
                return max(1, round(quota / period))
    except Exception:
        pass
    return os.cpu_count() or 1


# ============================================================
# PSUTIL COLLECTOR
# ============================================================

_NET_SAMPLE = {"ts": 0.0, "tx": 0, "rx": 0}

if psutil:
    try:
        psutil.cpu_percent(interval=None)
    except Exception:
        pass


def collect_system():
    out = {
        "cpu_pct":     0.0,
        "ram_pct":     0.0,
        "ram_used":    0,
        "ram_total":   0,
        "disk_pct":    0.0,
        "disk_used":   0,
        "disk_total":  0,
        "net_tx":      0,
        "net_rx":      0,
        "net_tx_rate": 0.0,
        "net_rx_rate": 0.0,
        "cpu_count":   1,
    }

    if not psutil:
        return out

    try:
        out["cpu_pct"] = float(psutil.cpu_percent(interval=None))
    except Exception:
        pass

    out["cpu_count"] = _read_cgroup_cpu_quota()

    cg_total = _read_cgroup_memory()
    cg_used  = _read_cgroup_memory_used()

    if cg_total > 0 and cg_used > 0:
        out["ram_total"] = cg_total
        out["ram_used"]  = cg_used
        out["ram_pct"]   = cg_used / cg_total * 100
    else:
        try:
            vm = psutil.virtual_memory()
            out["ram_pct"]   = float(vm.percent)
            out["ram_used"]  = int(vm.used)
            out["ram_total"] = int(vm.total)
        except Exception:
            pass

    try:
        du = psutil.disk_usage("/")
        out["disk_pct"]   = float(du.percent)
        out["disk_used"]  = int(du.used)
        out["disk_total"] = int(du.total)
    except Exception:
        pass

    try:
        io = psutil.net_io_counters()
        out["net_tx"] = int(io.bytes_sent)
        out["net_rx"] = int(io.bytes_recv)

        now_ts = time.time()
        if _NET_SAMPLE["ts"] > 0:
            dt = max(0.001, now_ts - _NET_SAMPLE["ts"])
            out["net_tx_rate"] = (io.bytes_sent - _NET_SAMPLE["tx"]) / dt
            out["net_rx_rate"] = (io.bytes_recv - _NET_SAMPLE["rx"]) / dt

        _NET_SAMPLE["ts"] = now_ts
        _NET_SAMPLE["tx"] = io.bytes_sent
        _NET_SAMPLE["rx"] = io.bytes_recv
    except Exception:
        pass

    return out


# ============================================================
# RENDER API HELPERS (only used for account/service metadata)
# ============================================================

def _render_headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept":        "application/json",
    }


_LAST_API_ERROR = {"msg": None, "ts": 0.0}


async def _render_get_json(url, api_key, timeout=10):
    if not api_key:
        _LAST_API_ERROR["msg"] = "API key is empty"
        _LAST_API_ERROR["ts"] = time.time()
        return None

    if not api_key.startswith("rnd_"):
        _LAST_API_ERROR["msg"] = (
            f"API key does not start with 'rnd_' "
            f"(got: {api_key[:8]}...)"
        )
        _LAST_API_ERROR["ts"] = time.time()
        return None

    try:
        t = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=t) as session:
            async with session.get(
                url, headers=_render_headers(api_key)
            ) as resp:
                if resp.status == 401:
                    _LAST_API_ERROR["msg"] = "401 Unauthorized"
                    _LAST_API_ERROR["ts"] = time.time()
                    return None
                if resp.status == 403:
                    _LAST_API_ERROR["msg"] = "403 Forbidden"
                    _LAST_API_ERROR["ts"] = time.time()
                    return None
                if resp.status == 404:
                    _LAST_API_ERROR["msg"] = "404 Not Found"
                    _LAST_API_ERROR["ts"] = time.time()
                    return None
                if resp.status == 429:
                    _LAST_API_ERROR["msg"] = "429 Rate limited"
                    _LAST_API_ERROR["ts"] = time.time()
                    return None
                if resp.status != 200:
                    body = ""
                    try:
                        body = (await resp.text())[:200]
                    except Exception:
                        pass
                    _LAST_API_ERROR["msg"] = (
                        f"HTTP {resp.status} — {body or 'no body'}"
                    )
                    _LAST_API_ERROR["ts"] = time.time()
                    return None
                return await resp.json()

    except asyncio.TimeoutError:
        _LAST_API_ERROR["msg"] = f"Timeout after {timeout}s"
        _LAST_API_ERROR["ts"] = time.time()
        return None
    except Exception as e:
        _LAST_API_ERROR["msg"] = f"{type(e).__name__}: {e}"
        _LAST_API_ERROR["ts"] = time.time()
        return None


# ============================================================
# ACCOUNT METADATA FETCH
# ============================================================

async def fetch_account_metadata(account, force=False):
    now_ts = time.time()
    cache_age = now_ts - _META_CACHE["ts"]

    if not force and cache_age < 1800 and account.get("account_email"):
        return

    api_key = account["api_key"]
    svc_id  = account["service_id"]

    who = await _render_get_json(
        "https://api.render.com/v1/users", api_key
    )
    if isinstance(who, dict):
        account["account_email"] = who.get("email") or account.get("account_email")
        account["account_name"]  = who.get("name")  or account.get("account_name")

    owners = await _render_get_json(
        "https://api.render.com/v1/owners?limit=20", api_key
    )
    if isinstance(owners, list) and owners:
        entry = owners[0]
        if isinstance(entry, dict):
            ws = entry.get("owner") or entry
            account["workspace_name"] = (
                ws.get("name")
                or ws.get("email")
                or account.get("workspace_name")
            )

    svc = await _render_get_json(
        f"https://api.render.com/v1/services/{svc_id}", api_key
    )
    if isinstance(svc, dict):
        account["service_name"] = svc.get("name")
        account["service_type"] = svc.get("type")
        sd = svc.get("serviceDetails") or {}
        account["service_region"] = (
            sd.get("region")
            or svc.get("region")
            or account.get("service_region")
        )
        account["service_url"] = (
            sd.get("url")
            or svc.get("url")
            or account.get("service_url")
        )

    _META_CACHE["ts"] = now_ts


# ============================================================
# BANDWIDTH FETCH (psutil-based, persisted)
# ============================================================

async def fetch_bandwidth_multi():
    """
    Get bandwidth usage for the currently-active account.
    Uses psutil TX (bytes out) as the billable metric.
    Persists to MongoDB for cross-restart continuity.
    """
    if not RENDER_ACCOUNTS:
        return {
            "ok": False,
            "tx_bytes": 0,
            "rx_bytes": 0,
            "service_id": None,
            "plan": "hobby",
            "limit_gb": 5.0,
            "account_index": -1,
            "account": None,
            "total_accounts": 0,
        }

    idx = _ACTIVE_ACCOUNT["index"]
    if not (0 <= idx < len(RENDER_ACCOUNTS)):
        idx = 0

    acct = RENDER_ACCOUNTS[idx]

    # Fetch metadata once (email, service name)
    try:
        await fetch_account_metadata(acct)
    except Exception:
        pass

    counters = await get_persistent_tx_rx(
        acct["service_id"],
        email=acct.get("account_email"),
        plan=acct.get("plan"),
    )

    return {
        "ok":           True,
        "tx_bytes":     counters["tx_bytes"],
        "rx_bytes":     counters["rx_bytes"],
        "service_id":   acct["service_id"],
        "plan":         acct["plan"],
        "limit_gb":     acct["limit_gb"],
        "account_index": idx,
        "account":      acct,
        "total_accounts": len(RENDER_ACCOUNTS),
    }
# ============================================================
# DASHBOARD BUILDER
# ============================================================

async def build_render_page():
    sys = collect_system()
    bw  = await fetch_bandwidth_multi()

    acct = bw.get("account") or {}

    # ── Bandwidth math (TX = outbound = billable) ────────
    limit_gb = bw["limit_gb"]
    tx_bytes = bw.get("tx_bytes", 0) or 0
    rx_bytes = bw.get("rx_bytes", 0) or 0

    bw_gb    = tx_bytes / (1024 ** 3)
    bw_pct   = (bw_gb / limit_gb * 100) if limit_gb > 0 else 0
    bw_left  = max(0.0, limit_gb - bw_gb)

    if bw_gb > limit_gb:
        over_gb = bw_gb - limit_gb
        over_txt = f"⚠️ <b>${over_gb * 0.15:.2f}</b> est."
    else:
        over_txt = "$0.00 (safe)"

    # ── Account display strings ──────────────────────────
    acct_email = acct.get("account_email")  or "—"
    acct_name  = acct.get("account_name")   or "—"
    ws_name    = acct.get("workspace_name") or "—"
    svc_name   = acct.get("service_name")   or "—"
    svc_region = acct.get("service_region") or "—"
    svc_type   = acct.get("service_type")   or "—"

    total_accounts = bw.get("total_accounts", 0)
    acct_idx       = bw.get("account_index", 0)
    acct_label     = (
        f" · acct {acct_idx + 1}/{total_accounts}"
        if total_accounts > 1 else ""
    )

    uptime = time.time() - PROCESS_START_TS

    cpu_badge  = usage_badge(sys["cpu_pct"])
    ram_badge  = usage_badge(sys["ram_pct"])
    disk_badge = usage_badge(sys["disk_pct"])
    bw_badge   = usage_badge(bw_pct)

    # ── Header ───────────────────────────────────────────
    text = (
        "╭━━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "   🛰️  <b>R E N D E R</b>\n"
        "   ⚡ <b>LIVE DASHBOARD</b> ⚡\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"

        f"🟢 <b>ONLINE</b>{acct_label}\n"
        f"🕒 <code>{now_text()}</code>\n"
        f"⏱ Uptime <code>{fmt_duration(uptime)}</code>\n"
        f"\n"

        # ── ACCOUNT ───────────────────────────────────────
        "╭─〔 👤 <b>ACCOUNT</b> 〕────────╮\n"
        f"│ 📧 <code>{html_escape(acct_email)}</code>\n"
        f"│ 🏷️ <b>{html_escape(acct_name)}</b>\n"
        f"│ 🏢 <code>{html_escape(ws_name)}</code>\n"
        "╰───────────────────────╯\n\n"

        # ── SERVICE ───────────────────────────────────────
        "╭─〔 🚀 <b>SERVICE</b> 〕────────╮\n"
        f"│ 📛 <b>{html_escape(svc_name)}</b>\n"
        f"│ 🆔 <code>{bw['service_id'] or '—'}</code>\n"
        f"│ 🌍 <code>{html_escape(svc_region)}</code>\n"
        f"│ 🧩 <code>{html_escape(svc_type)}</code>\n"
        f"│ 📊 Plan <b>{bw['plan'].upper()}</b>\n"
        "╰───────────────────────╯\n\n"

        # ── BANDWIDTH ─────────────────────────────────────
        "╭─〔 🌐 <b>BANDWIDTH</b> 〕──────╮\n"
    )

    if not bw["ok"]:
        err_msg = _LAST_API_ERROR.get("msg") or "unknown error"
        has_key = bool(_keys and _keys[0])
        has_srv = bool(_services and _services[0])

        text += (
            "│ ⚠️ <b>Unavailable</b>\n"
            f"│ <code>{html_escape(err_msg)[:140]}</code>\n"
            "│\n"
            f"│ 🔑 Key set:  {'✅' if has_key else '❌'}\n"
            f"│ 🆔 Svc set:  {'✅' if has_srv else '❌'}\n"
            f"│ 📊 Limit:    <b>{limit_gb:.2f} GB</b>\n"
        )
    else:
        used_mb  = tx_bytes / (1024 * 1024)
        limit_mb = limit_gb * 1024
        left_mb  = max(0.0, limit_mb - used_mb)
        rx_mb    = rx_bytes / (1024 * 1024)

        if used_mb < 1024:
            used_line  = f"{used_mb:.1f} MB"
            limit_line = f"{limit_mb:.0f} MB"
            left_line  = f"{left_mb:.1f} MB"
        else:
            used_line  = f"{bw_gb:.3f} GB"
            limit_line = f"{limit_gb:.2f} GB"
            left_line  = f"{bw_left:.3f} GB"

        text += (
            f"│ {bw_badge} Used <b>{used_line}</b>\n"
            f"│ 📊 Limit <code>{limit_line}</code>\n"
            f"│ ⏳ Left <b>{left_line}</b>\n"
            f"│\n"
            f"│ {usage_bar(bw_pct, 12)} <b>{bw_pct:.2f}%</b>\n"
            f"│ <i>{used_mb:.1f} MB / {limit_mb:.0f} MB used</i>\n"
            f"│ <i>RX: {rx_mb:.1f} MB (not billed)</i>\n"
            f"│\n"
            f"│ 💸 Overage: {over_txt}\n"
        )

    text += (
        "╰───────────────────────╯\n\n"

        # ── CPU ───────────────────────────────────────────
        "╭─〔 ⚙️ <b>CPU</b> 〕─────────────╮\n"
        f"│ {cpu_badge} Usage <b>{sys['cpu_pct']:.1f}%</b>\n"
        f"│ {usage_bar(sys['cpu_pct'], 12)} <b>{sys['cpu_pct']:.1f}%</b>\n"
        f"│ 🧩 Cores <code>{sys.get('cpu_count', 1)}</code>\n"
        "╰───────────────────────╯\n\n"

        # ── RAM ───────────────────────────────────────────
        "╭─〔 🧠 <b>RAM</b> 〕─────────────╮\n"
        f"│ {ram_badge} Used <b>{fmt_bytes(sys['ram_used'])}</b>\n"
        f"│ 📦 Total <code>{fmt_bytes(sys['ram_total'])}</code>\n"
        f"│ {usage_bar(sys['ram_pct'], 12)} <b>{sys['ram_pct']:.1f}%</b>\n"
        "╰───────────────────────╯\n\n"

        # ── DISK ──────────────────────────────────────────
        "╭─〔 💽 <b>DISK</b> 〕────────────╮\n"
        f"│ {disk_badge} Used <b>{fmt_bytes(sys['disk_used'])}</b>\n"
        f"│ 📦 Total <code>{fmt_bytes(sys['disk_total'])}</code>\n"
        f"│ {usage_bar(sys['disk_pct'], 12)} <b>{sys['disk_pct']:.1f}%</b>\n"
        "╰───────────────────────╯\n\n"

        # ── NETWORK ───────────────────────────────────────
        "╭─〔 🌊 <b>NETWORK</b> 〕─────────╮\n"
        f"│ ⬆️ TX session <code>{fmt_bytes(sys['net_tx'])}</code>\n"
        f"│ ⬇️ RX session <code>{fmt_bytes(sys['net_rx'])}</code>\n"
        f"│ ⚡ TX rate  <code>{fmt_bytes(sys['net_tx_rate'])}/s</code>\n"
        f"│ ⚡ RX rate  <code>{fmt_bytes(sys['net_rx_rate'])}/s</code>\n"
        "╰───────────────────────╯\n\n"

        # ── HEALTH ────────────────────────────────────────
        "╭─〔 🛰️ <b>HEALTH</b> 〕──────────╮\n"
        f"│ {cpu_badge} CPU    {'OK' if sys['cpu_pct'] < 80 else 'HIGH'}\n"
        f"│ {ram_badge} RAM    {'OK' if sys['ram_pct'] < 80 else 'HIGH'}\n"
        f"│ {disk_badge} DISK   {'OK' if sys['disk_pct'] < 80 else 'HIGH'}\n"
        f"│ {bw_badge} BANDW  {'OK' if bw_pct < 80 else 'HIGH'}\n"
        "╰───────────────────────╯"
    )

    if total_accounts > 1:
        text += (
            f"\n\n🔀 <i>{total_accounts} accounts configured — "
            f"currently using #{acct_idx + 1}</i>"
        )

    if not psutil:
        text += (
            "\n\n⚠️ <i>psutil not installed — "
            "CPU/RAM/Disk hidden</i>"
        )

    if _main_db is None:
        text += (
            "\n\n⚠️ <i>MongoDB unavailable — "
            "bandwidth will reset on redeploy</i>"
        )

    return text[:4000]


# ============================================================
# PANEL LOOP — self-healing
# ============================================================

_RENDER_PANEL_LOCK = asyncio.Lock()
_RENDER_PANELS     = {}
_RENDER_LAST_TEXT  = {"t": None}


def _render_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 REFRESH", callback_data="dtv_render_refresh"),
            InlineKeyboardButton("🔀 SWITCH",  callback_data="dtv_render_switch"),
        ],
        [
            InlineKeyboardButton(
                "📊 BILLING PAGE",
                url="https://dashboard.render.com/billing",
            ),
        ],
        [
            InlineKeyboardButton("❌ CLOSE", callback_data="dtv_render_close"),
        ],
    ])


def _is_dead_message(exc):
    s = str(exc).upper()
    return any(m in s for m in (
        "MESSAGE_ID_INVALID",
        "MESSAGEIDINVALID",
        "MESSAGE_NOT_FOUND",
        "MESSAGE TO EDIT NOT FOUND",
        "MESSAGE TO BE EDITED NOT FOUND",
        "MESSAGE_DELETE",
        "PEER_ID_INVALID",
        "CHAT_WRITE_FORBIDDEN",
    ))


async def _edit_panel(message, text, kb=None):
    try:
        await message.edit_text(
            text,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
        return True, False

    except FloodWait as exc:
        await asyncio.sleep(int(getattr(exc, "value", 30)) + 2)
        return False, False

    except RPCError as exc:
        if "MESSAGE_NOT_MODIFIED" in str(exc).upper():
            return True, False
        if _is_dead_message(exc):
            return False, True
        logger.warning("[RENDER] panel edit failed: %s", exc)
        return False, False

    except Exception as exc:
        if _is_dead_message(exc):
            return False, True
        logger.warning("[RENDER] panel edit error: %s", exc)
        return False, False


async def _recreate_panel(client, chat_id):
    if client is None or chat_id is None:
        return None
    async with _RENDER_PANEL_LOCK:
        try:
            text = await build_render_page()
            sent = await client.send_message(
                chat_id=int(chat_id),
                text=text,
                reply_markup=_render_keyboard(),
                disable_web_page_preview=True,
            )
            _RENDER_PANELS[int(sent.id)] = {
                "page": "render",
                "task": None,
                "closed": False,
            }
            logger.info(
                "[RENDER] panel recreated: chat_id=%s msg=%s",
                chat_id, sent.id,
            )
            return sent
        except FloodWait as exc:
            await asyncio.sleep(int(getattr(exc, "value", 30)) + 2)
            return None
        except Exception:
            logger.exception("[RENDER] panel recreation failed")
            return None


async def _render_loop(client, message):
    current_message = message

    while True:
        try:
            text = await build_render_page()

            if text != _RENDER_LAST_TEXT["t"]:
                ok, dead = await _edit_panel(
                    current_message, text, _render_keyboard(),
                )

                if dead:
                    chat_id = None
                    try:
                        chat_id = current_message.chat.id
                    except Exception:
                        pass

                    stale_id = None
                    try:
                        stale_id = current_message.id
                    except Exception:
                        pass

                    new_msg = await _recreate_panel(client, chat_id)
                    if new_msg is None:
                        break

                    if stale_id is not None:
                        _RENDER_PANELS.pop(stale_id, None)

                    _RENDER_PANELS[int(new_msg.id)] = {
                        "page": "render",
                        "task": asyncio.current_task(),
                        "closed": False,
                    }

                    current_message = new_msg
                    continue

                if ok:
                    _RENDER_LAST_TEXT["t"] = text

            await asyncio.sleep(3)

            record = _RENDER_PANELS.get(current_message.id)
            if not record or record.get("closed"):
                break

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("[RENDER] loop error")
            await asyncio.sleep(5)


def _open_panel(client, message):
    _stop_panel(message.id)
    task = asyncio.create_task(_render_loop(client, message))
    _RENDER_PANELS[int(message.id)] = {
        "page":   "render",
        "task":   task,
        "closed": False,
    }
    return task


def _stop_panel(message_id):
    r = _RENDER_PANELS.get(message_id)
    if r:
        r["closed"] = True
        t = r.get("task")
        if t:
            try:
                t.cancel()
            except Exception:
                pass


# ============================================================
# /render COMMAND
# ============================================================

@Client.on_message(filters.command("render"))
async def render_command(client, message):
    user = message.from_user
    if user is None or not is_admin(user.id):
        return

    text = await build_render_page()

    sent = await message.reply_text(
        text,
        reply_markup=_render_keyboard(),
        disable_web_page_preview=True,
    )

    _open_panel(client, sent)


# ============================================================
# CALLBACKS
# ============================================================

@Client.on_callback_query(filters.regex(r"^dtv_render_"))
async def render_callback(client, query):
    user = query.from_user
    if user is None or not is_admin(user.id):
        await query.answer("❌ Access denied", show_alert=True)
        return

    data = str(query.data)

    if data == "dtv_render_refresh":
        await query.answer("🔄 Refreshing")
        _META_CACHE["ts"] = 0.0
        _RENDER_LAST_TEXT["t"] = None

        text = await build_render_page()
        ok, dead = await _edit_panel(
            query.message, text, _render_keyboard(),
        )

        if dead:
            new_msg = await _recreate_panel(client, query.message.chat.id)
            if new_msg:
                _open_panel(client, new_msg)
        else:
            _open_panel(client, query.message)
        return

    if data == "dtv_render_switch":
        if len(RENDER_ACCOUNTS) <= 1:
            await query.answer(
                "Only one account configured", show_alert=True,
            )
            return

        cur = _ACTIVE_ACCOUNT["index"]
        nxt = (cur + 1) % len(RENDER_ACCOUNTS)
        _ACTIVE_ACCOUNT["index"] = nxt

        _META_CACHE["ts"] = 0.0
        _RENDER_LAST_TEXT["t"] = None

        await query.answer(f"🔀 Switched to account #{nxt + 1}")

        text = await build_render_page()
        await _edit_panel(query.message, text, _render_keyboard())
        return

    if data == "dtv_render_close":
        _stop_panel(query.message.id)
        _RENDER_PANELS.pop(query.message.id, None)
        await query.message.delete()
        return


# ============================================================
# STARTUP LOG
# ============================================================

logger.info(
    "[RENDER] dashboard loaded — /render (%d account(s), persist=%s)",
    len(RENDER_ACCOUNTS),
    "on" if _main_db is not None else "off",
)


# ============================================================
# END OF FILE
# ============================================================
