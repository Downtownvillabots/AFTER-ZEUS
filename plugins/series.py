import os
import re
import asyncio
import logging

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, RPCError

from database.ia_filterdb import (
    db,
    db2,
    db3,
)

from info import (
    COLLECTION_NAME,
    MULTIPLE_DB,
)

logger = logging.getLogger(__name__)


# ============================================================
# SETTINGS
# ============================================================

SERIES_GROUP_ID = os.getenv(
    "SERIES_GROUP_ID",
    ""
).strip()

try:
    SERIES_CHAT_ID = int(
        SERIES_GROUP_ID
    ) if SERIES_GROUP_ID else None
except Exception:
    SERIES_CHAT_ID = None


# ============================================================
# IMDb
# ============================================================

try:
    import imdb

    imdb_api = imdb.IMDb()

    IMDB_AVAILABLE = True

    logger.info(
        "[SERIES] IMDb loaded successfully."
    )

except Exception as e:

    imdb_api = None

    IMDB_AVAILABLE = False

    logger.error(
        "[SERIES] IMDb failed: %s",
        e
    )


# ============================================================
# CACHE
# ============================================================

IMDB_CACHE = {}

DETAIL_CACHE = {}

USER_STATE = {}


# ============================================================
# HELPERS
# ============================================================

def normalize(text):

    if not text:
        return ""

    text = str(text)

    text = text.lower()

    text = text.replace(
        "_",
        " "
    )

    text = text.replace(
        ".",
        " "
    )

    text = text.replace(
        "-",
        " "
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def html(text):

    if text is None:
        return ""

    from html import escape

    return escape(
        str(text)
    )


# ============================================================
# IMDb SEARCH
# ============================================================

async def search_imdb(
    query
):

    if not IMDB_AVAILABLE:
        return []

    query = normalize(
        query
    )

    if not query:
        return []

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    if query in IMDB_CACHE:

        return IMDB_CACHE[
            query
        ]

    try:

        results = await asyncio.to_thread(
            imdb_api.search_movie,
            query
        )

    except Exception as e:

        logger.exception(
            "[SERIES] IMDb search error: %s",
            e
        )

        return []

    output = []

    for movie in results[:15]:

        try:

            title = movie.get(
                "title"
            )

            if not title:
                continue

            movie_id = getattr(
                movie,
                "movieID",
                None
            )

            if not movie_id:
                continue

            year = movie.get(
                "year"
            )

            kind = str(
                movie.get(
                    "kind",
                    ""
                )
            ).lower()

            # ------------------------------------------------
            # Prefer actual TV content.
            # ------------------------------------------------

            is_series = (
                "tv series" in kind
                or
                "tv mini series" in kind
                or
                kind == "series"
                or
                "series" in kind
            )

            output.append(
                {
                    "id": str(
                        movie_id
                    ),

                    "title": str(
                        title
                    ),

                    "year": year,

                    "kind": kind,

                    "is_series": is_series,
                }
            )

        except Exception:

            continue

    # --------------------------------------------------------
    # SERIES FIRST
    # --------------------------------------------------------

    series = [
        x
        for x in output
        if x.get(
            "is_series"
        )
    ]

    # --------------------------------------------------------
    # If IMDb did not provide kind properly,
    # don't completely fail.
    # --------------------------------------------------------

    if series:

        output = series

    else:

        output = output[:10]

    IMDB_CACHE[
        query
    ] = output

    logger.info(
        "[SERIES] IMDb '%s' -> %s",
        query,
        [
            (
                x["title"],
                x["year"],
                x["kind"]
            )
            for x in output
        ]
    )

    return output


# ============================================================
# IMDb DETAILS
# ============================================================

async def imdb_details(
    imdb_id
):

    if not IMDB_AVAILABLE:
        return {}

    if imdb_id in DETAIL_CACHE:

        return DETAIL_CACHE[
            imdb_id
        ]

    try:

        movie = await asyncio.to_thread(
            imdb_api.get_movie,
            str(imdb_id)
        )

        details = {

            "id": str(
                imdb_id
            ),

            "title": movie.get(
                "title",
                "Unknown"
            ),

            "year": movie.get(
                "year"
            ),

            "kind": str(
                movie.get(
                    "kind",
                    ""
                )
            ),

        }

        DETAIL_CACHE[
            imdb_id
        ] = details

        return details

    except Exception as e:

        logger.exception(
            "[SERIES] IMDb details error: %s",
            e
        )

        return {}


# ============================================================
# DATABASES
# ============================================================

def get_databases():

    databases = [
        db
    ]

    if MULTIPLE_DB:

        databases.append(
            db2
        )

        databases.append(
            db3
        )

    return databases


# ============================================================
# EXTRACT SEASON / EPISODE
# ============================================================

def extract_season_episode(
    filename
):

    if not filename:
        return None, None

    filename = str(
        filename
    )

    # S01E01
    # S1E1
    # S01.E01
    # S01-E01
    # S01_E01

    match = re.search(
        r"(?i)\bS(\d{1,2})"
        r"[\s._-]*"
        r"E(\d{1,3})\b",
        filename
    )

    if match:

        return (
            int(
                match.group(1)
            ),
            int(
                match.group(2)
            )
        )

    # Season 1 Episode 1

    match = re.search(
        r"(?i)\bSeason"
        r"[\s._-]*"
        r"(\d{1,2})"
        r"[\s._-]*"
        r"(?:Episode|Ep|E)"
        r"[\s._-]*"
        r"(\d{1,3})\b",
        filename
    )

    if match:

        return (
            int(
                match.group(1)
            ),
            int(
                match.group(2)
            )
        )

    return None, None


# ============================================================
# SEARCH SERIES FILES
# ============================================================

async def search_series_files(
    title,
    season
):

    all_files = []

    season_pattern = re.compile(
        rf"(?i)\bS{season:02d}"
        rf"[\s._-]*E\d{{1,3}}\b"
    )

    for database in get_databases():

        try:

            collection = database[
                COLLECTION_NAME
            ]

            cursor = collection.find(
                {
                    "file_name": {
                        "$regex":
                            season_pattern
                    }
                },
                {
                    "_id": 1,
                    "file_id": 1,
                    "file_ref": 1,
                    "file_name": 1,
                    "file_size": 1,
                    "file_type": 1,
                    "mime_type": 1,
                    "caption": 1,
                    "cover": 1,
                }
            )

            async for file in cursor:

                filename = file.get(
                    "file_name"
                )

                if not filename:
                    continue

                file_season, episode = (
                    extract_season_episode(
                        filename
                    )
                )

                if file_season != season:
                    continue

                if episode is None:
                    continue

                # ------------------------------------------------
                # Basic title check.
                # ------------------------------------------------

                normalized_title = normalize(
                    title
                )

                normalized_filename = normalize(
                    filename
                )

                title_words = [
                    word
                    for word
                    in normalized_title.split()
                    if len(word) >= 2
                ]

                matched = sum(
                    1
                    for word
                    in title_words
                    if word in normalized_filename
                )

                if title_words:

                    percentage = (
                        matched
                        / len(title_words)
                    ) * 100

                    if percentage < 60:
                        continue

                file[
                    "_episode"
                ] = episode

                file[
                    "_season"
                ] = season

                all_files.append(
                    file
                )

        except Exception as e:

            logger.exception(
                "[SERIES] Database search error: %s",
                e
            )

    # --------------------------------------------------------
    # One file per episode.
    # --------------------------------------------------------

    best = {}

    for file in all_files:

        episode = file.get(
            "_episode"
        )

        if episode is None:
            continue

        if episode not in best:

            best[
                episode
            ] = file

    return dict(
        sorted(
            best.items()
        )
    )


# ============================================================
# SEARCH BUTTONS
# ============================================================

def series_buttons(
    results
):

    rows = []

    for item in results:

        title = item.get(
            "title",
            "Unknown"
        )

        year = item.get(
            "year"
        )

        text = (
            f"📺 {title}"
        )

        if year:

            text += (
                f" ({year})"
            )

        rows.append(
            [
                InlineKeyboardButton(
                    text=text[:64],
                    callback_data=(
                        f"series:{item['id']}"
                    )
                )
            ]
        )

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# SEASON BUTTONS
# ============================================================

def season_buttons(
    imdb_id
):

    rows = []

    current = []

    for season in range(
        1,
        31
    ):

        current.append(
            InlineKeyboardButton(
                text=f"S{season:02d}",
                callback_data=(
                    f"season:"
                    f"{imdb_id}:"
                    f"{season}"
                )
            )
        )

        if len(
            current
        ) == 3:

            rows.append(
                current
            )

            current = []

    if current:

        rows.append(
            current
        )

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# SERIES SEARCH
# ============================================================

@Client.on_message(
    filters.text
    & ~filters.command(
        [
            "start",
            "help",
            "seriesdebug"
        ]
    ),
    group=-100
)
async def series_search(
    app,
    message
):

    # --------------------------------------------------------
    # ONLY SERIES GROUP
    # --------------------------------------------------------

    if SERIES_CHAT_ID is None:

        return

    if message.chat.id != SERIES_CHAT_ID:

        return

    query = (
        message.text or ""
    ).strip()

    if not query:

        return

    if query.startswith(
        "/"
    ):

        return

    msg = await message.reply_text(
        "🔎 <b>Searching series...</b>",
        parse_mode=enums.ParseMode.HTML
    )

    results = await search_imdb(
        query
    )

    if not results:

        await msg.edit_text(
            "❌ <b>No series found.</b>\n\n"
            f"Search: <code>{html(query)}</code>",
            parse_mode=enums.ParseMode.HTML
        )

        return

    # --------------------------------------------------------
    # Keep only likely series.
    # --------------------------------------------------------

    series_results = [
        x
        for x in results
        if x.get(
            "is_series"
        )
    ]

    # Fallback if IMDb did not identify kind.
    if not series_results:

        series_results = results[:5]

    text = (
        "📺 <b>SELECT SERIES</b>\n\n"
        f"🔎 <code>{html(query)}</code>\n\n"
        "Select the correct series:"
    )

    await msg.edit_text(
        text,
        reply_markup=series_buttons(
            series_results[:10]
        ),
        parse_mode=enums.ParseMode.HTML
    )


# ============================================================
# SERIES SELECTED
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^series:"
    )
)
async def series_selected(
    app,
    query
):

    imdb_id = query.data.split(
        ":",
        1
    )[1]

    await query.answer(
        "📺 Series selected"
    )

    details = await imdb_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series"
    )

    year = details.get(
        "year"
    )

    USER_STATE[
        query.from_user.id
    ] = {
        "imdb_id": imdb_id,
        "title": title,
    }

    text = (
        "📺 <b>"
        f"{html(title)}"
        "</b>"
    )

    if year:

        text += (
            f" ({year})"
        )

    text += (
        "\n\n"
        "📚 <b>Select Season</b>"
    )

    await query.message.edit_text(
        text,
        reply_markup=season_buttons(
            imdb_id
        ),
        parse_mode=enums.ParseMode.HTML
    )


# ============================================================
# SEASON SELECTED
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^season:"
    )
)
async def season_selected(
    app,
    query
):

    parts = query.data.split(
        ":"
    )

    if len(parts) != 3:

        return

    imdb_id = parts[1]

    try:

        season = int(
            parts[2]
        )

    except Exception:

        return

    state = USER_STATE.get(
        query.from_user.id,
        {}
    )

    title = state.get(
        "title"
    )

    if not title:

        details = await imdb_details(
            imdb_id
        )

        title = details.get(
            "title",
            "Series"
        )

    await query.answer(
        f"Searching S{season:02d}..."
    )

    try:

        await query.message.edit_text(
            "🔎 <b>SEARCHING EPISODES...</b>\n\n"
            f"📺 <b>{html(title)}</b>\n"
            f"📚 S{season:02d}",
            parse_mode=enums.ParseMode.HTML
        )

    except Exception:

        pass

    # --------------------------------------------------------
    # MongoDB search happens ONLY here.
    # --------------------------------------------------------

    files = await search_series_files(
        title,
        season
    )

    # --------------------------------------------------------
    # No episodes.
    # --------------------------------------------------------

    if not files:

        await query.message.edit_text(
            "❌ <b>NO EPISODES FOUND</b>\n\n"
            f"📺 {html(title)}\n"
            f"📚 S{season:02d}",
            parse_mode=enums.ParseMode.HTML
        )

        return

    # --------------------------------------------------------
    # Episode list.
    # --------------------------------------------------------

    rows = []

    current = []

    for episode, file in files.items():

        current.append(
            InlineKeyboardButton(
                text=f"E{episode:02d}",
                callback_data=(
                    f"episode:"
                    f"{file.get('file_id') or file.get('_id')}"
                )
            )
        )

        if len(
            current
        ) == 4:

            rows.append(
                current
            )

            current = []

    if current:

        rows.append(
            current
        )

    text = (
        "✅ <b>EPISODES FOUND</b>\n\n"
        f"📺 <b>{html(title)}</b>\n"
        f"📚 <b>S{season:02d}</b>\n\n"
        f"Episodes: <b>{len(files)}</b>\n\n"
        "Select an episode:"
    )

    await query.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            rows
        ),
        parse_mode=enums.ParseMode.HTML
    )


# ============================================================
# EPISODE CLICK
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^episode:"
    )
)
async def episode_selected(
    app,
    query
):

    file_id = query.data.split(
        ":",
        1
    )[1]

    await query.answer(
        "Episode selected"
    )

    # --------------------------------------------------------
    # This version does NOT stream automatically.
    #
    # We only confirm the selected episode.
    # --------------------------------------------------------

    await query.message.reply_text(
        "✅ <b>Episode selected.</b>\n\n"
        f"<code>{html(file_id)}</code>",
        parse_mode=enums.ParseMode.HTML
    )


# ============================================================
# STARTUP
# ============================================================

logger.info(
    "=========================================="
)

logger.info(
    "[SERIES] Simple Series System Loaded"
)

logger.info(
    "[SERIES] Group ID: %s",
    SERIES_CHAT_ID
)

logger.info(
    "[SERIES] IMDb: %s",
    "YES"
    if IMDB_AVAILABLE
    else "NO"
)

logger.info(
    "[SERIES] MongoDB search only after season selection."
)

logger.info(
    "=========================================="
)
