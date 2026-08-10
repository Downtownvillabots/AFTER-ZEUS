```python
import os
import re
import asyncio
import logging
from collections import defaultdict
from difflib import SequenceMatcher
from html import escape

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, RPCError

from database.ia_filterdb import (
    Media,
    Media2,
    Media3,
)

from info import (
    COLLECTION_NAME,
    MULTIPLE_DB,
)

logger = logging.getLogger(__name__)


# ============================================================
# CONFIG
# ============================================================

SERIES_GROUP_ID = os.getenv(
    "SERIES_GROUP_ID",
    "",
).strip()

try:
    SERIES_CHAT_ID = int(SERIES_GROUP_ID) if SERIES_GROUP_ID else None
except Exception:
    SERIES_CHAT_ID = None


# Maximum series suggestions shown
MAX_SERIES_RESULTS = 10

# Maximum seasons shown
MAX_SEASONS = 30

# Maximum episodes shown
MAX_EPISODES = 200


# ============================================================
# USER STATE
# ============================================================

USER_STATE = {}


# ============================================================
# BASIC HELPERS
# ============================================================

def normalize(text):
    """
    Convert filename/title into a simple searchable form.
    """

    if not text:
        return ""

    text = str(text)

    text = text.replace("_", " ")
    text = text.replace(".", " ")
    text = text.replace("-", " ")

    text = re.sub(
        r"[\[\]{}()#+=|\\/,:;!?@#$%^&*~`'\"<>]",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip().lower()


def similarity(a, b):
    a = normalize(a)
    b = normalize(b)

    if not a or not b:
        return 0

    return int(
        SequenceMatcher(
            None,
            a,
            b,
        ).ratio() * 100
    )


def html(text):
    return escape(str(text or ""))


# ============================================================
# DATABASES
# ============================================================

def get_collections():

    collections = [
        Media,
    ]

    if MULTIPLE_DB:
        collections.extend(
            [
                Media2,
                Media3,
            ]
        )

    return collections


# ============================================================
# SEASON / EPISODE EXTRACTION
# ============================================================

def extract_season_episode(filename):

    if not filename:
        return None, None

    filename = str(filename)

    # S01E01
    # S1E1
    # S01.E01
    # S01-E01
    # S01_E01

    match = re.search(
        r"(?i)\bS(\d{1,2})"
        r"[\s._\-]*"
        r"E(\d{1,3})\b",
        filename,
    )

    if match:
        return (
            int(match.group(1)),
            int(match.group(2)),
        )

    # Season 1 Episode 1
    # Season 01 Ep 01

    match = re.search(
        r"(?i)\bSeason"
        r"[\s._\-]*(\d{1,2})"
        r"[\s._\-]*"
        r"(?:Episode|Ep|E)"
        r"[\s._\-]*(\d{1,3})\b",
        filename,
    )

    if match:
        return (
            int(match.group(1)),
            int(match.group(2)),
        )

    return None, None


# ============================================================
# EXTRACT SERIES TITLE
# ============================================================

def extract_series_title(filename):

    if not filename:
        return ""

    title = str(filename)

    # --------------------------------------------------------
    # Remove extension
    # --------------------------------------------------------

    title = re.sub(
        r"(?i)\.(mkv|mp4|avi|mov|webm|m4v|ts)$",
        "",
        title,
    )

    # --------------------------------------------------------
    # Remove S01E01 and everything after it
    #
    # Example:
    #
    # Game of Thrones S01E01 1080p.mkv
    #
    # becomes:
    #
    # Game of Thrones
    # --------------------------------------------------------

    match = re.search(
        r"(?i)\bS\d{1,2}"
        r"[\s._\-]*E\d{1,3}\b",
        title,
    )

    if match:
        title = title[:match.start()]

    else:

        match = re.search(
            r"(?i)\bSeason"
            r"[\s._\-]*\d{1,2}\b",
            title,
        )

        if match:
            title = title[:match.start()]

    # --------------------------------------------------------
    # Clean separators
    # --------------------------------------------------------

    title = title.replace(
        "_",
        " ",
    )

    title = title.replace(
        ".",
        " ",
    )

    title = title.replace(
        "-",
        " ",
    )

    title = re.sub(
        r"\s+",
        " ",
        title,
    )

    return title.strip()


# ============================================================
# REMOVE COMMON QUALITY / TECH TOKENS
# ============================================================

def clean_series_title(title):

    if not title:
        return ""

    words = title.split()

    remove_words = {
        "480p",
        "576p",
        "720p",
        "1080p",
        "1080i",
        "1440p",
        "2160p",
        "4k",
        "uhd",
        "hdr",
        "hevc",
        "x265",
        "x264",
        "h264",
        "h265",
        "bluray",
        "blu-ray",
        "web",
        "webrip",
        "web-dl",
        "webdl",
        "nf",
        "amzn",
        "amazon",
        "hdtv",
        "proper",
        "repack",
        "dual",
        "audio",
        "multi",
        "dubbed",
        "eng",
        "english",
        "mal",
        "malayalam",
        "hin",
        "hindi",
        "tam",
        "tamil",
        "tel",
        "telugu",
        "kan",
        "kannada",
    }

    cleaned = []

    for word in words:

        if normalize(word) in remove_words:
            continue

        cleaned.append(word)

    return " ".join(cleaned).strip()


# ============================================================
# QUERY MATCH
# ============================================================

def title_matches(query, title):

    query = normalize(query)
    title = normalize(title)

    if not query or not title:
        return False

    # Exact
    if query == title:
        return True

    # Query contained inside title
    if query in title:
        return True

    # Title contained inside query
    if title in query:
        return True

    query_words = [
        x for x in query.split()
        if len(x) >= 2
    ]

    title_words = set(
        title.split()
    )

    if not query_words:
        return False

    matched = 0

    for word in query_words:

        if word in title_words:
            matched += 1
            continue

        # Small spelling tolerance
        for candidate in title_words:

            if (
                SequenceMatcher(
                    None,
                    word,
                    candidate,
                ).ratio()
                >= 0.82
            ):
                matched += 1
                break

    score = (
        matched / len(query_words)
    ) * 100

    return score >= 70


# ============================================================
# GET EXISTING FILES
# ============================================================

async def get_existing_series_files():

    all_files = []

    for model in get_collections():

        try:

            cursor = model.find(
                {},
                {
                    "file_id": 1,
                    "file_ref": 1,
                    "file_name": 1,
                    "file_size": 1,
                    "file_type": 1,
                    "mime_type": 1,
                    "caption": 1,
                },
            )

            async for document in cursor:

                filename = document.get(
                    "file_name"
                )

                if not filename:
                    continue

                season, episode = (
                    extract_season_episode(
                        filename
                    )
                )

                # Only files that look like
                # series episodes.
                if season is None or episode is None:
                    continue

                document["_season"] = season
                document["_episode"] = episode

                title = extract_series_title(
                    filename
                )

                title = clean_series_title(
                    title
                )

                if not title:
                    continue

                document["_series_title"] = title

                all_files.append(
                    document
                )

        except Exception as e:

            logger.exception(
                "[SERIES] Database read failed: %s",
                e,
            )

    return all_files


# ============================================================
# FIND SERIES
# ============================================================

async def find_series(query):

    files = await get_existing_series_files()

    if not files:
        return []

    grouped = {}

    for file in files:

        title = file.get(
            "_series_title",
            "",
        )

        if not title:
            continue

        if not title_matches(
            query,
            title,
        ):
            continue

        key = normalize(title)

        if key not in grouped:

            grouped[key] = {
                "title": title,
                "seasons": set(),
                "episodes": 0,
            }

        grouped[key][
            "seasons"
        ].add(
            file["_season"]
        )

        grouped[key][
            "episodes"
        ] += 1

    results = list(
        grouped.values()
    )

    # Best match first
    results.sort(
        key=lambda x: (
            similarity(
                query,
                x["title"],
            ),
            x["episodes"],
        ),
        reverse=True,
    )

    return results[
        :MAX_SERIES_RESULTS
    ]


# ============================================================
# SERIES BUTTONS
# ============================================================

def series_buttons(results):

    rows = []

    for index, item in enumerate(results):

        title = item["title"]

        seasons = len(
            item["seasons"]
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"📺 {title[:45]}"
                        f"  •  {seasons} Seasons"
                    ),
                    callback_data=(
                        f"SERIES:{index}"
                    ),
                )
            ]
        )

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# TEMPORARY SEARCH RESULTS
# ============================================================

SEARCH_RESULTS = {}


# ============================================================
# GROUP SEARCH
# ============================================================

@Client.on_message(
    filters.text
    & ~filters.command(
        [
            "start",
            "help",
        ]
    ),
    group=-100,
)
async def series_search(
    app,
    message,
):

    if SERIES_CHAT_ID is None:
        return

    if message.chat.id != SERIES_CHAT_ID:
        return

    query = (
        message.text or ""
    ).strip()

    if not query:
        return

    if query.startswith("/"):
        return

    logger.info(
        "[SERIES] Search: %s",
        query,
    )

    msg = await message.reply_text(
        "🔎 <b>Searching your series files...</b>",
        parse_mode=enums.ParseMode.HTML,
    )

    results = await find_series(
        query
    )

    if not results:

        await msg.edit_text(
            "❌ <b>No series found.</b>\n\n"
            f"Search: <code>{html(query)}</code>\n\n"
            "The series must already exist in "
            "your database with filenames such as:\n"
            "<code>Game of Thrones S01E01 1080p.mkv</code>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    # Save only for this message
    SEARCH_RESULTS[
        message.id
    ] = results

    text = (
        "📺 <b>SELECT SERIES</b>\n\n"
        f"🔎 Search: <b>{html(query)}</b>\n\n"
        "Choose the series:"
    )

    await msg.edit_text(
        text,
        reply_markup=series_buttons(
            results
        ),
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# SELECT SERIES
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^SERIES:\d+$"
    )
)
async def select_series(
    app,
    query,
):

    await query.answer()

    # --------------------------------------------------------
    # Find the nearest stored result set.
    # --------------------------------------------------------

    results = None

    for key, value in reversed(
        list(
            SEARCH_RESULTS.items()
        )
    ):

        if value:
            results = value
            break

    if not results:
        await query.answer(
            "Search expired. Search again.",
            show_alert=True,
        )
        return

    index = int(
        query.data.split(
            ":"
        )[1]
    )

    if index >= len(results):
        return

    selected = results[
        index
    ]

    title = selected[
        "title"
    ]

    # --------------------------------------------------------
    # Save user's selection
    # --------------------------------------------------------

    USER_STATE[
        query.from_user.id
    ] = {
        "title": title,
    }

    # --------------------------------------------------------
    # Find seasons again
    # --------------------------------------------------------

    files = await get_existing_series_files()

    seasons = set()

    for file in files:

        file_title = file.get(
            "_series_title",
            "",
        )

        if normalize(
            file_title
        ) != normalize(
            title
        ):
            continue

        seasons.add(
            file["_season"]
        )

    seasons = sorted(
        seasons
    )

    if not seasons:

        await query.message.edit_text(
            "❌ No seasons found.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    rows = []
    current = []

    for season in seasons[
        :MAX_SEASONS
    ]:

        current.append(
            InlineKeyboardButton(
                text=f"S{season:02d}",
                callback_data=(
                    f"SEASON:"
                    f"{query.from_user.id}:"
                    f"{season}"
                ),
            )
        )

        if len(current) == 4:

            rows.append(
                current
            )

            current = []

    if current:
        rows.append(
            current
        )

    text = (
        "📺 <b>"
        f"{html(title)}"
        "</b>\n\n"
        "📚 <b>SELECT SEASON</b>\n\n"
        "Only seasons that actually exist "
        "in your database are shown."
    )

    await query.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            rows
        ),
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# SELECT SEASON
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^SEASON:\d+:\d+$"
    )
)
async def select_season(
    app,
    query,
):

    await query.answer()

    parts = query.data.split(
        ":"
    )

    user_id = int(
        parts[1]
    )

    season = int(
        parts[2]
    )

    if user_id != query.from_user.id:
        await query.answer(
            "This selection belongs to another user.",
            show_alert=True,
        )
        return

    state = USER_STATE.get(
        user_id
    )

    if not state:
        await query.answer(
            "Search expired. Search again.",
            show_alert=True,
        )
        return

    title = state[
        "title"
    ]

    # --------------------------------------------------------
    # Find episodes
    # --------------------------------------------------------

    files = await get_existing_series_files()

    episodes = {}

    for file in files:

        file_title = file.get(
            "_series_title",
            "",
        )

        if normalize(
            file_title
        ) != normalize(
            title
        ):
            continue

        if file.get(
            "_season"
        ) != season:
            continue

        episode = file.get(
            "_episode"
        )

        if episode is None:
            continue

        # Keep first file for episode.
        if episode not in episodes:
            episodes[
                episode
            ] = file

    episode_numbers = sorted(
        episodes.keys()
    )

    if not episode_numbers:

        await query.message.edit_text(
            "❌ <b>No episodes found.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    episode_numbers = episode_numbers[
        :MAX_EPISODES
    ]

    # --------------------------------------------------------
    # Store selected season
    # --------------------------------------------------------

    state[
        "season"
    ] = season

    # --------------------------------------------------------
    # Episode buttons
    # --------------------------------------------------------

    rows = []
    current = []

    for episode in episode_numbers:

        current.append(
            InlineKeyboardButton(
                text=f"E{episode:02d}",
                callback_data=(
                    f"EP:"
                    f"{user_id}:"
                    f"{season}:"
                    f"{episode}"
                ),
            )
        )

        if len(current) == 5:

            rows.append(
                current
            )

            current = []

    if current:
        rows.append(
            current
        )

    text = (
        "📺 <b>"
        f"{html(title)}"
        "</b>\n\n"
        f"📚 Season: <b>S{season:02d}</b>\n"
        f"🎞 Episodes: <b>{len(episode_numbers)}</b>\n\n"
        "Select an episode:"
    )

    await query.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            rows
        ),
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# EPISODE SELECT
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^EP:\d+:\d+:\d+$"
    )
)
async def select_episode(
    app,
    query,
):

    await query.answer()

    parts = query.data.split(
        ":"
    )

    user_id = int(
        parts[1]
    )

    season = int(
        parts[2]
    )

    episode = int(
        parts[3]
    )

    if user_id != query.from_user.id:
        await query.answer(
            "This selection belongs to another user.",
            show_alert=True,
        )
        return

    state = USER_STATE.get(
        user_id
    )

    if not state:
        await query.answer(
            "Search expired.",
            show_alert=True,
        )
        return

    title = state[
        "title"
    ]

    # --------------------------------------------------------
    # Search only selected episode.
    # --------------------------------------------------------

    files = await get_existing_series_files()

    selected_file = None

    for file in files:

        file_title = file.get(
            "_series_title",
            "",
        )

        if normalize(
            file_title
        ) != normalize(
            title
        ):
            continue

        if file.get(
            "_season"
        ) != season:
            continue

        if file.get(
            "_episode"
        ) != episode:
            continue

        selected_file = file
        break

    if not selected_file:

        await query.message.edit_text(
            "❌ File not found.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    file_id = (
        selected_file.get(
            "file_id"
        )
        or selected_file.get(
            "file_ref"
        )
    )

    if not file_id:

        await query.message.edit_text(
            "❌ File ID is missing from database.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    filename = selected_file.get(
        "file_name",
        f"E{episode:02d}",
    )

    await query.message.edit_text(
        "📤 <b>Sending file...</b>\n\n"
        f"📺 {html(title)}\n"
        f"📚 S{season:02d}\n"
        f"🎞 E{episode:02d}",
        parse_mode=enums.ParseMode.HTML,
    )

    try:

        await app.send_cached_media(
            chat_id=query.message.chat.id,
            file_id=str(
                file_id
            ),
            caption=(
                f"📺 {title}\n"
                f"S{season:02d} E{episode:02d}\n\n"
                f"{filename}"
            ),
        )

    except FloodWait as e:

        wait = int(
            getattr(
                e,
                "value",
                10,
            )
        )

        await asyncio.sleep(
            wait + 2
        )

        try:

            await app.send_cached_media(
                chat_id=query.message.chat.id,
                file_id=str(
                    file_id
                ),
            )

        except Exception:

            logger.exception(
                "[SERIES] Retry failed."
            )

    except RPCError:

        logger.exception(
            "[SERIES] Telegram error."
        )

    except Exception:

        logger.exception(
            "[SERIES] Failed sending episode."
        )


# ============================================================
# STARTUP LOG
# ============================================================

logger.info(
    "=================================================="
)

logger.info(
    "[SERIES] Simple DB Series System Loaded"
)

logger.info(
    "[SERIES] Group ID: %s",
    SERIES_CHAT_ID,
)

logger.info(
    "[SERIES] IMDb/Cinemagoer: DISABLED"
)

logger.info(
    "[SERIES] Language selection: DISABLED"
)

logger.info(
    "[SERIES] Quality selection: DISABLED"
)

logger.info(
    "[SERIES] Streaming: DISABLED"
)

logger.info(
    "[SERIES] Source: Media / Media2 / Media3"
)

logger.info(
    "=================================================="
)
```
