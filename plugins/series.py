import os
import re
import asyncio
import logging
import hashlib
import html
from collections import defaultdict

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.errors import FloodWait, RPCError
from pyrogram import StopPropagation

from database.ia_filterdb import (
    Media,
    Media2,
    Media3,
    get_search_results,
)

logger = logging.getLogger(__name__)


# ============================================================
# ENVIRONMENT CONFIGURATION
# ============================================================

SERIES_ENABLED = os.getenv(
    "SERIES_ENABLED",
    "true"
).lower() in ("true", "1", "yes", "on")


SERIES_GROUP_ID = os.getenv(
    "SERIES_GROUP_ID",
    ""
)


MOVIE_GROUP_LINK = os.getenv(
    "MOVIE_GROUP_LINK",
    ""
)


SERIES_CAPTION = os.getenv(
    "SERIES_CAPTION",
    "📺 <b>{title}</b>\n\n"
    "🎬 Season {season}\n"
    "🎞️ Quality: {quality}\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🤖 Powered by DreamxBotz"
)


# ============================================================
# CONFIG
# ============================================================

# Number of search results to inspect when identifying a series.
SERIES_SEARCH_LIMIT = int(
    os.getenv(
        "SERIES_SEARCH_LIMIT",
        "100"
    )
)


# Delay between sending episodes.
EPISODE_SEND_DELAY = float(
    os.getenv(
        "EPISODE_SEND_DELAY",
        "1"
    )
)


# Maximum episodes allowed in one season.
MAX_EPISODES_PER_SEASON = int(
    os.getenv(
        "MAX_EPISODES_PER_SEASON",
        "100"
    )
)


# ============================================================
# SESSION STORAGE
#
# We don't put the entire title/season/quality inside
# callback_data because Telegram callback_data is limited.
# ============================================================

series_sessions = {}


# ============================================================
# HELPERS
# ============================================================


def get_series_group_id():
    if not SERIES_GROUP_ID:
        return None

    try:
        return int(SERIES_GROUP_ID)
    except Exception:
        logger.error(
            "[SERIES] Invalid SERIES_GROUP_ID: %s",
            SERIES_GROUP_ID
        )
        return None


def clean_text(text):
    if not text:
        return ""

    return re.sub(
        r"[_\-.]+",
        " ",
        str(text)
    ).strip()


def clean_filename(filename):
    if not filename:
        return "Unknown"

    filename = str(filename)

    filename = re.sub(
        r"[_\-.]+",
        " ",
        filename
    )

    filename = re.sub(
        r"\s+",
        " ",
        filename
    )

    return filename.strip()


def get_file_id(file):
    file_id = getattr(
        file,
        "file_id",
        None
    )

    if file_id:
        return str(file_id)

    file_id = getattr(
        file,
        "_id",
        None
    )

    if file_id:
        return str(file_id)

    return None


def get_file_name(file):
    return str(
        getattr(
            file,
            "file_name",
            ""
        )
    )


# ============================================================
# SERIES PARSER
# ============================================================


def parse_episode(filename):
    """
    Detect:

        S01E01
        S01 E01
        s01e01
        S1E1

    Returns:

        {
            "season": 1,
            "episode": 1
        }

    or None.
    """

    if not filename:
        return None

    pattern = re.search(
        r"(?i)\bS(\d{1,2})\s*E(\d{1,3})\b",
        filename
    )

    if not pattern:
        return None

    try:
        season = int(
            pattern.group(1)
        )

        episode = int(
            pattern.group(2)
        )

        return {
            "season": season,
            "episode": episode
        }

    except Exception:
        return None


# ============================================================
# QUALITY DETECTION
# ============================================================


def detect_quality(filename):
    if not filename:
        return "Unknown"


    quality_patterns = [
        (r"(?i)\b2160p\b", "2160p"),
        (r"(?i)\b4k\b", "4K"),
        (r"(?i)\b1440p\b", "1440p"),
        (r"(?i)\b1080p\b", "1080p"),
        (r"(?i)\b720p\b", "720p"),
        (r"(?i)\b576p\b", "576p"),
        (r"(?i)\b480p\b", "480p"),
        (r"(?i)\b360p\b", "360p"),
    ]


    for pattern, quality in quality_patterns:

        if re.search(
            pattern,
            filename
        ):
            return quality


    # Common WEB / BluRay / HDR naming
    extra_patterns = [
        (r"(?i)\bWEB[-_. ]?DL\b", "WEB-DL"),
        (r"(?i)\bWEB[-_. ]?Rip\b", "WEBRip"),
        (r"(?i)\bBluRay\b", "BluRay"),
        (r"(?i)\bBRRip\b", "BRRip"),
        (r"(?i)\bHDTV\b", "HDTV"),
    ]


    for pattern, quality in extra_patterns:

        if re.search(
            pattern,
            filename
        ):
            return quality


    return "Unknown"


# ============================================================
# SERIES TITLE
# ============================================================


def extract_series_title(filename):
    """
    Example:

        Lost S01E01 1080p.mkv

    becomes:

        Lost
    """

    if not filename:
        return "Unknown Series"


    title = re.sub(
        r"(?i)\bS\d{1,2}\s*E\d{1,3}\b",
        "",
        filename
    )


    # Remove quality information
    title = re.sub(
        r"(?i)\b(2160p|1440p|1080p|720p|576p|480p|360p|4K)\b",
        "",
        title
    )


    title = re.sub(
        r"(?i)\b(WEB[-_. ]?DL|WEB[-_. ]?Rip|BluRay|BRRip|HDTV)\b",
        "",
        title
    )


    # Remove common codec tags
    title = re.sub(
        r"(?i)\b(x264|x265|H264|H265|HEVC|AV1|AAC|DDP|DD\+|5\.1|2\.0)\b",
        "",
        title
    )


    title = re.sub(
        r"[_\-.]+",
        " ",
        title
    )


    title = re.sub(
        r"\s+",
        " ",
        title
    )


    return title.strip()


# ============================================================
# NORMALIZE TITLE FOR COMPARISON
# ============================================================


def normalize_title(title):
    if not title:
        return ""

    title = title.lower()

    title = re.sub(
        r"[^a-z0-9]+",
        " ",
        title
    )

    title = re.sub(
        r"\s+",
        " ",
        title
    )

    return title.strip()


# ============================================================
# CREATE SHORT SESSION ID
# ============================================================


def create_session(data):
    raw = (
        f"{data.get('user_id')}:"
        f"{data.get('title')}:"
        f"{data.get('timestamp')}:"
        f"{len(series_sessions)}"
    )

    session_id = hashlib.sha256(
        raw.encode()
    ).hexdigest()[:12]

    series_sessions[session_id] = data

    return session_id


# ============================================================
# SEARCH SERIES FILES
# ============================================================


async def search_series_files(query):
    """
    Search all 3 databases through the existing search system.
    """

    try:

        files, _, _ = await get_search_results(
            chat_id=None,
            query=query,
            max_results=SERIES_SEARCH_LIMIT,
            offset=0,
            filter=False
        )

        return files or []

    except Exception:

        logger.exception(
            "[SERIES] Search error"
        )

        return []


# ============================================================
# BUILD SERIES INFORMATION
# ============================================================


def build_series_data(
    query,
    files
):
    """
    Organize matching files:

        Series
          ├── Season 1
          │     ├── 720p
          │     └── 1080p
          │
          └── Season 2
                ├── 720p
                └── 1080p
    """

    grouped = defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(list)
        )
    )


    detected_title = None


    for file in files:

        filename = get_file_name(
            file
        )

        episode_info = parse_episode(
            filename
        )

        # Only files containing SxxExx
        if not episode_info:
            continue


        title = extract_series_title(
            filename
        )


        if not title:
            continue


        if detected_title is None:
            detected_title = title


        season = episode_info[
            "season"
        ]

        episode = episode_info[
            "episode"
        ]


        quality = detect_quality(
            filename
        )


        grouped[
            normalize_title(title)
        ][
            season
        ][
            quality
        ].append(
            {
                "file": file,
                "episode": episode,
                "filename": filename,
                "quality": quality
            }
        )


    if not grouped:
        return None


    # Try to select the title closest to the user's query.
    normalized_query = normalize_title(
        query
    )


    selected_key = None


    for key in grouped:

        if (
            key == normalized_query
            or normalized_query in key
            or key in normalized_query
        ):
            selected_key = key
            break


    if selected_key is None:

        selected_key = next(
            iter(grouped)
        )


    return {
        "title": detected_title or query,
        "seasons": grouped[
            selected_key
        ]
    }


# ============================================================
# SERIES BUTTON
# ============================================================


def series_button_title(title):
    return (
        "📺 "
        + str(title)[:45]
    )


# ============================================================
# SEND MOVIE GROUP BUTTON
# ============================================================


async def show_movie_group(
    message
):

    if not MOVIE_GROUP_LINK:

        await message.reply_text(
            "🎬 <b>Movie Search</b>\n\n"
            "Please use the movie group to search movies.",
            parse_mode=enums.ParseMode.HTML
        )

        return


    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎬 MOVIE GROUP",
                    url=MOVIE_GROUP_LINK
                )
            ]
        ]
    )


    await message.reply_text(
        "🎬 <b>Looking for a movie?</b>\n\n"
        "Movies are available in our Movie Group.\n"
        "Tap the button below to search there.",
        reply_markup=keyboard,
        parse_mode=enums.ParseMode.HTML
    )


# ============================================================
# MAIN SERIES SEARCH
# ============================================================


@Client.on_message(
    filters.text
    & filters.group,
    group=-100
)
async def series_search_handler(
    client,
    message
):

    if not SERIES_ENABLED:
        return


    series_group_id = get_series_group_id()


    if not series_group_id:
        return


    # --------------------------------------------------------
    # IMPORTANT:
    # SERIES FEATURE ONLY WORKS IN THIS GROUP
    # --------------------------------------------------------

    if message.chat.id != series_group_id:
        return


    query = (
        message.text
        or ""
    ).strip()


    if not query:
        return


    # Ignore commands
    if query.startswith("/"):
        return


    try:

        files = await search_series_files(
            query
        )


        series_data = build_series_data(
            query,
            files
        )


        # ----------------------------------------------------
        # NO SERIES FOUND
        # ----------------------------------------------------

        if not series_data:

            # This is treated as a movie search.
            await show_movie_group(
                message
            )

            raise StopPropagation


        title = series_data[
            "title"
        ]


        # ----------------------------------------------------
        # CREATE SESSION
        # ----------------------------------------------------

        session_id = create_session(
            {
                "user_id": message.from_user.id,
                "title": title,
                "seasons": series_data["seasons"],
                "timestamp": asyncio.get_running_loop().time()
            }
        )


        # ----------------------------------------------------
        # SEASON BUTTONS
        # ----------------------------------------------------

        buttons = []


        seasons = sorted(
            series_data[
                "seasons"
            ].keys()
        )


        row = []


        for season in seasons:

            callback = (
                f"series_season:"
                f"{session_id}:"
                f"{season}"
            )


            row.append(
                InlineKeyboardButton(
                    f"📺 Season {season:02}",
                    callback_data=callback
                )
            )


            if len(row) == 2:

                buttons.append(
                    row
                )

                row = []


        if row:
            buttons.append(
                row
            )


        buttons.append(
            [
                InlineKeyboardButton(
                    "❌ CLOSE",
                    callback_data=(
                        f"series_close:"
                        f"{session_id}"
                    )
                )
            ]
        )


        caption = SERIES_CAPTION.format(
            title=html.escape(
                title
            ),
            season="—",
            quality="—"
        )


        await message.reply_text(
            caption,
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
            parse_mode=enums.ParseMode.HTML
        )


        raise StopPropagation


    except StopPropagation:
        raise


    except Exception:

        logger.exception(
            "[SERIES] Search handler error"
        )


# ============================================================
# SEASON BUTTON
# ============================================================


@Client.on_callback_query(
    filters.regex(
        r"^series_season:"
    )
)
async def season_callback(
    client,
    query
):

    try:

        parts = query.data.split(
            ":"
        )


        if len(parts) != 3:
            await query.answer(
                "Invalid request.",
                show_alert=True
            )
            return


        session_id = parts[1]

        season = int(
            parts[2]
        )


        session = series_sessions.get(
            session_id
        )


        if not session:

            await query.answer(
                "This menu has expired. Search again.",
                show_alert=True
            )

            return


        # Only the user who opened the menu
        # can use it.

        if (
            query.from_user.id
            != session["user_id"]
        ):

            await query.answer(
                "❌ This menu belongs to another user.",
                show_alert=True
            )

            return


        seasons = session[
            "seasons"
        ]


        if season not in seasons:

            await query.answer(
                "Season not found.",
                show_alert=True
            )

            return


        qualities = seasons[
            season
        ]


        buttons = []


        row = []


        # ----------------------------------------------------
        # QUALITY BUTTONS
        # ----------------------------------------------------

        for quality in sorted(
            qualities.keys()
        ):

            episodes = qualities[
                quality
            ]


            callback = (
                f"series_quality:"
                f"{session_id}:"
                f"{season}:"
                f"{hashlib.md5(quality.encode()).hexdigest()[:6]}"
            )


            # Store quality mapping
            session[
                f"quality_{season}_{callback.split(':')[-1]}"
            ] = quality


            row.append(
                InlineKeyboardButton(
                    f"🎞️ {quality} "
                    f"({len(episodes)} EP)",
                    callback_data=callback
                )
            )


            if len(row) == 2:

                buttons.append(
                    row
                )

                row = []


        if row:
            buttons.append(
                row
            )


        buttons.append(
            [
                InlineKeyboardButton(
                    "⬅️ SEASONS",
                    callback_data=(
                        f"series_back:"
                        f"{session_id}"
                    )
                ),
                InlineKeyboardButton(
                    "❌ CLOSE",
                    callback_data=(
                        f"series_close:"
                        f"{session_id}"
                    )
                )
            ]
        )


        caption = SERIES_CAPTION.format(
            title=html.escape(
                session["title"]
            ),
            season=f"{season:02}",
            quality="Choose Quality"
        )


        await query.message.edit_text(
            caption,
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
            parse_mode=enums.ParseMode.HTML
        )


        await query.answer(
            f"Season {season:02}"
        )


    except Exception as e:

        logger.exception(
            "[SERIES] Season callback error: %s",
            e
        )

        await query.answer(
            "❌ Something went wrong.",
            show_alert=True
        )


# ============================================================
# QUALITY BUTTON
# ============================================================


@Client.on_callback_query(
    filters.regex(
        r"^series_quality:"
    )
)
async def quality_callback(
    client,
    query
):

    try:

        parts = query.data.split(
            ":"
        )


        if len(parts) != 4:
            await query.answer(
                "Invalid request.",
                show_alert=True
            )
            return


        session_id = parts[1]

        season = int(
            parts[2]
        )

        quality_hash = parts[3]


        session = series_sessions.get(
            session_id
        )


        if not session:

            await query.answer(
                "This menu has expired. Search again.",
                show_alert=True
            )

            return


        if (
            query.from_user.id
            != session["user_id"]
        ):

            await query.answer(
                "❌ This menu belongs to another user.",
                show_alert=True
            )

            return


        quality_key = (
            f"quality_"
            f"{season}_"
            f"{quality_hash}"
        )


        quality = session.get(
            quality_key
        )


        if not quality:

            await query.answer(
                "Quality not found.",
                show_alert=True
            )

            return


        episodes = (
            session["seasons"]
            .get(season, {})
            .get(quality, [])
        )


        if not episodes:

            await query.answer(
                "No episodes available.",
                show_alert=True
            )

            return


        # ----------------------------------------------------
        # SORT EPISODES
        # ----------------------------------------------------

        episodes = sorted(
            episodes,
            key=lambda x: x["episode"]
        )


        # Remove duplicate episode numbers
        unique_episodes = {}

        for item in episodes:

            unique_episodes[
                item["episode"]
            ] = item


        episodes = list(
            unique_episodes.values()
        )


        if len(episodes) > MAX_EPISODES_PER_SEASON:

            await query.answer(
                "Too many episodes in this season.",
                show_alert=True
            )

            return


        # ----------------------------------------------------
        # SHOW TRANSFER MESSAGE
        # ----------------------------------------------------

        await query.answer(
            f"Sending {len(episodes)} episodes...",
            show_alert=False
        )


        title = session[
            "title"
        ]


        progress = await query.message.reply_text(
            "🚀 <b>SEASON TRANSFER STARTED</b>\n\n"
            f"📺 <b>{html.escape(title)}</b>\n"
            f"🎬 Season <b>{season:02}</b>\n"
            f"🎞️ Quality <b>{html.escape(quality)}</b>\n\n"
            f"📦 Episodes: <b>{len(episodes)}</b>\n"
            f"📤 Sending: <b>1/{len(episodes)}</b>\n\n"
            "⏳ Please wait...",
            parse_mode=enums.ParseMode.HTML
        )


        # ----------------------------------------------------
        # SEND EPISODES IN ORDER
        # ----------------------------------------------------

        sent_count = 0


        for index, item in enumerate(
            episodes,
            start=1
        ):

            file = item[
                "file"
            ]


            file_id = get_file_id(
                file
            )


            if not file_id:
                continue


            filename = get_file_name(
                file
            )


            caption_template = SERIES_CAPTION


            try:

                caption = caption_template.format(
                    title=html.escape(
                        title
                    ),
                    season=f"{season:02}",
                    quality=html.escape(
                        quality
                    )
                )


            except Exception:

                caption = (
                    f"📺 <b>{html.escape(title)}</b>\n"
                    f"🎬 Season {season:02}\n"
                    f"🎞️ {html.escape(quality)}"
                )


            # Add episode information
            caption += (
                f"\n\n"
                f"📌 Episode <b>{item['episode']:02}</b>"
            )


            while True:

                try:

                    await client.send_cached_media(
                        chat_id=query.from_user.id,
                        file_id=file_id,
                        caption=caption
                    )


                    sent_count += 1

                    break


                except FloodWait as e:

                    wait_time = int(
                        getattr(
                            e,
                            "value",
                            30
                        )
                    )

                    logger.warning(
                        "[SERIES] FloodWait: %s seconds",
                        wait_time
                    )

                    await asyncio.sleep(
                        wait_time + 2
                    )


                except RPCError as e:

                    logger.error(
                        "[SERIES] Failed episode %s: %s",
                        item["episode"],
                        e
                    )

                    break


                except Exception as e:

                    logger.exception(
                        "[SERIES] Episode send error: %s",
                        e
                    )

                    break


            # ------------------------------------------------
            # UPDATE PROGRESS
            # ------------------------------------------------

            if (
                index == 1
                or index == len(episodes)
                or index % 3 == 0
            ):

                try:

                    await progress.edit_text(
                        "🚀 <b>SEASON TRANSFER</b>\n\n"
                        f"📺 <b>{html.escape(title)}</b>\n"
                        f"🎬 Season <b>{season:02}</b>\n"
                        f"🎞️ Quality <b>{html.escape(quality)}</b>\n\n"
                        f"📦 Total Episodes: <b>{len(episodes)}</b>\n"
                        f"📤 Sent: <b>{sent_count}</b>\n"
                        f"⏳ Current: <b>{index}/{len(episodes)}</b>\n\n"
                        "⚡ Sending in episode order...",
                        parse_mode=enums.ParseMode.HTML
                    )

                except Exception:
                    pass


            await asyncio.sleep(
                EPISODE_SEND_DELAY
            )


        # ----------------------------------------------------
        # FINISHED
        # ----------------------------------------------------

        try:

            await progress.edit_text(
                "╔══════════════════════════╗\n"
                "       ✅ <b>SEASON COMPLETE</b>\n"
                "╚══════════════════════════╝\n\n"
                f"📺 <b>{html.escape(title)}</b>\n"
                f"🎬 Season <b>{season:02}</b>\n"
                f"🎞️ Quality <b>{html.escape(quality)}</b>\n\n"
                f"📦 Episodes: <b>{len(episodes)}</b>\n"
                f"✅ Sent: <b>{sent_count}</b>\n\n"
                "🎉 All available episodes have been sent.",
                parse_mode=enums.ParseMode.HTML
            )

        except Exception:
            pass


    except Exception as e:

        logger.exception(
            "[SERIES] Quality callback error: %s",
            e
        )

        try:

            await query.answer(
                "❌ Something went wrong.",
                show_alert=True
            )

        except Exception:
            pass


# ============================================================
# BACK TO SEASONS
# ============================================================


@Client.on_callback_query(
    filters.regex(
        r"^series_back:"
    )
)
async def series_back_callback(
    client,
    query
):

    try:

        parts = query.data.split(
            ":"
        )

        session_id = parts[1]


        session = series_sessions.get(
            session_id
        )


        if not session:

            await query.answer(
                "Menu expired.",
                show_alert=True
            )

            return


        if (
            query.from_user.id
            != session["user_id"]
        ):

            await query.answer(
                "❌ This menu belongs to another user.",
                show_alert=True
            )

            return


        buttons = []

        row = []


        seasons = sorted(
            session[
                "seasons"
            ].keys()
        )


        for season in seasons:

            row.append(
                InlineKeyboardButton(
                    f"📺 Season {season:02}",
                    callback_data=(
                        f"series_season:"
                        f"{session_id}:"
                        f"{season}"
                    )
                )
            )


            if len(row) == 2:

                buttons.append(
                    row
                )

                row = []


        if row:
            buttons.append(
                row
            )


        buttons.append(
            [
                InlineKeyboardButton(
                    "❌ CLOSE",
                    callback_data=(
                        f"series_close:"
                        f"{session_id}"
                    )
                )
            ]
        )


        await query.message.edit_text(
            SERIES_CAPTION.format(
                title=html.escape(
                    session["title"]
                ),
                season="—",
                quality="—"
            ),
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
            parse_mode=enums.ParseMode.HTML
        )


        await query.answer(
            "⬅️ Seasons"
        )


    except Exception:

        logger.exception(
            "[SERIES] Back callback error"
        )

        await query.answer(
            "❌ Error.",
            show_alert=True
        )


# ============================================================
# CLOSE
# ============================================================


@Client.on_callback_query(
    filters.regex(
        r"^series_close:"
    )
)
async def series_close_callback(
    client,
    query
):

    try:

        parts = query.data.split(
            ":"
        )

        session_id = parts[1]


        session = series_sessions.get(
            session_id
        )


        if session:

            if (
                query.from_user.id
                != session["user_id"]
            ):

                await query.answer(
                    "❌ This menu belongs to another user.",
                    show_alert=True
                )

                return


        series_sessions.pop(
            session_id,
            None
        )


        try:

            await query.message.delete()

        except Exception:
            pass


        await query.answer(
            "Closed."
        )


    except Exception:

        logger.exception(
            "[SERIES] Close callback error"
        )


# ============================================================
# CLEAN OLD SESSIONS
# ============================================================


async def cleanup_sessions():

    while True:

        try:

            now = asyncio.get_running_loop().time()

            expired = []


            for session_id, session in list(
                series_sessions.items()
            ):

                timestamp = session.get(
                    "timestamp",
                    now
                )


                # 30 minute session lifetime
                if (
                    now - timestamp
                    > 1800
                ):

                    expired.append(
                        session_id
                    )


            for session_id in expired:

                series_sessions.pop(
                    session_id,
                    None
                )


        except Exception:

            logger.exception(
                "[SERIES] Session cleanup error"
            )


        await asyncio.sleep(
            300
        )


# ============================================================
# START SESSION CLEANER
#
# Do NOT use @Client.on_start().
# Pyrogram 2.x does not provide Client.on_start().
# ============================================================


async def series_startup_task():

    await asyncio.sleep(
        10
    )

    logger.info(
        "[SERIES] Series system initialized."
    )

    if SERIES_ENABLED:

        logger.info(
            "[SERIES] Enabled."
        )

        logger.info(
            "[SERIES] Group ID: %s",
            SERIES_GROUP_ID
        )

    else:

        logger.info(
            "[SERIES] Disabled."
        )


    asyncio.create_task(
        cleanup_sessions()
    )


# ============================================================
# END OF SERIES.PY
# ============================================================
