# ============================================================
# DOWN TOWN VILLA - FAST SERIES SYSTEM
# ============================================================
#
# IMPORTANT:
#
# This version DOES NOT scan Media / Media2 / Media3 directly.
#
# It uses the existing fast database search:
#
#     get_search_results()
#
# exactly like the movie search system.
#
# Flow:
#
# SEARCH
#   ↓
# SERIES NAME
#   ↓
# SEASON
#   ↓
# QUALITY
#   ↓
# EXACT DATABASE SEARCH
#   ↓
# ONE BEST FILE PER EPISODE
#   ↓
# SEND IN ORDER
#
# ============================================================

import os
import re
import asyncio
import logging
import secrets
from collections import defaultdict
from html import escape

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.errors import FloodWait, RPCError

# ------------------------------------------------------------
# EXISTING FAST DATABASE SEARCH
# ------------------------------------------------------------

from database.ia_filterdb import get_search_results


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

SERIES_GROUP_ID = os.getenv(
    "SERIES_GROUP_ID",
    ""
).strip()


SERIES_CAPTION = os.getenv(
    "SERIES_CAPTION",
    ""
)


SERIES_POSTER = os.getenv(
    "SERIES_POSTER",
    "true"
).lower() in (
    "true",
    "1",
    "yes",
    "on",
)


MOVIE_GROUP_LINK = os.getenv(
    "MOVIE_GROUP_LINK",
    ""
).strip()


# ------------------------------------------------------------
# HOW MANY SEARCH RESULTS THE FAST SEARCH CAN RETURN
# ------------------------------------------------------------

SERIES_SEARCH_LIMIT = int(
    os.getenv(
        "SERIES_SEARCH_LIMIT",
        "100",
    )
)


# ------------------------------------------------------------
# SEND DELAY
# ------------------------------------------------------------

SERIES_SEND_DELAY = float(
    os.getenv(
        "SERIES_SEND_DELAY",
        "0.5",
    )
)


# ------------------------------------------------------------
# MAX EPISODES
#
# 0 = unlimited
# ------------------------------------------------------------

SERIES_MAX_EPISODES = int(
    os.getenv(
        "SERIES_MAX_EPISODES",
        "0",
    )
)


# ------------------------------------------------------------
# SEARCH CACHE
#
# Very small memory cache.
#
# This prevents repeated identical searches from hitting
# MongoDB repeatedly.
# ------------------------------------------------------------

SEARCH_CACHE = {}

SEARCH_CACHE_MAX = 100


# ------------------------------------------------------------
# CALLBACK SESSION CACHE
#
# Instead of putting the whole series title into callback_data,
# we store the selected values here.
#
# This avoids Telegram's callback_data size limitation.
# ------------------------------------------------------------

SERIES_SESSIONS = {}

SERIES_SESSION_MAX = 500


# ============================================================
# GROUP
# ============================================================

def get_series_group_id():

    if not SERIES_GROUP_ID:
        return None

    try:
        return int(SERIES_GROUP_ID)

    except Exception:

        logger.error(
            "[SERIES] Invalid SERIES_GROUP_ID: %s",
            SERIES_GROUP_ID,
        )

        return None


SERIES_CHAT_ID = get_series_group_id()


# ============================================================
# IMDb
# ============================================================

try:

    import imdb

    imdb_api = imdb.IMDb()

    IMDB_AVAILABLE = True

    logger.info(
        "[SERIES] IMDbPy loaded."
    )

except Exception as e:

    imdb_api = None

    IMDB_AVAILABLE = False

    logger.warning(
        "[SERIES] IMDb unavailable: %s",
        e,
    )


# ============================================================
# IMDb CACHE
# ============================================================

IMDB_SEARCH_CACHE = {}

IMDB_DETAILS_CACHE = {}


# ============================================================
# BASIC HELPERS
# ============================================================

def normalize_title(text):

    if not text:
        return ""

    text = str(text)

    text = text.replace(
        "_",
        " ",
    )

    text = text.replace(
        ".",
        " ",
    )

    text = text.replace(
        "-",
        " ",
    )

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


def clean_display_title(text):

    normalized = normalize_title(
        text
    )

    return " ".join(
        word.capitalize()
        for word in normalized.split()
    )


def escape_html(text):

    if text is None:
        return ""

    return escape(
        str(text)
    )


def parse_int(
    value,
    default=0,
):

    try:
        return int(value)

    except Exception:

        return default


# ============================================================
# SEASON / EPISODE
# ============================================================

def extract_season_episode(
    filename,
):

    if not filename:
        return None, None

    filename = str(
        filename
    )

    # --------------------------------------------------------
    # S01E01
    # S1E1
    # S01.E01
    # S01-E01
    # S01_E01
    # --------------------------------------------------------

    match = re.search(
        r"(?i)\bS(\d{1,2})[\s._\-]*E(\d{1,3})\b",
        filename,
    )

    if match:

        return (
            int(
                match.group(1)
            ),
            int(
                match.group(2)
            ),
        )

    # --------------------------------------------------------
    # S01 EP01
    # --------------------------------------------------------

    match = re.search(
        r"(?i)\bS(\d{1,2})[\s._\-]*EP(?:ISODE)?[\s._\-]*(\d{1,3})\b",
        filename,
    )

    if match:

        return (
            int(
                match.group(1)
            ),
            int(
                match.group(2)
            ),
        )

    # --------------------------------------------------------
    # Season 01 Episode 01
    # --------------------------------------------------------

    match = re.search(
        r"(?i)\bSeason[\s._\-]*(\d{1,2})"
        r"[\s._\-]*(?:Episode|Ep|E)"
        r"[\s._\-]*(\d{1,3})\b",
        filename,
    )

    if match:

        return (
            int(
                match.group(1)
            ),
            int(
                match.group(2)
            ),
        )

    return None, None


# ============================================================
# QUALITY
# ============================================================

QUALITY_PATTERNS = [

    (
        2160,
        re.compile(
            r"(?i)(?:\b2160p\b|\b4k\b|\buhd\b)"
        ),
    ),

    (
        1440,
        re.compile(
            r"(?i)\b1440p\b"
        ),
    ),

    (
        1080,
        re.compile(
            r"(?i)(?:\b1080p\b|\b1080i\b)"
        ),
    ),

    (
        720,
        re.compile(
            r"(?i)\b720p\b"
        ),
    ),

    (
        576,
        re.compile(
            r"(?i)\b576p\b"
        ),
    ),

    (
        480,
        re.compile(
            r"(?i)\b480p\b"
        ),
    ),

    (
        360,
        re.compile(
            r"(?i)\b360p\b"
        ),
    ),
]


def extract_quality(
    filename,
):

    if not filename:
        return 0

    filename = str(
        filename
    )

    for quality, pattern in QUALITY_PATTERNS:

        if pattern.search(
            filename
        ):

            return quality

    if re.search(
        r"(?i)\bHD\b",
        filename,
    ):

        return 720

    return 0


def quality_text(
    quality,
):

    quality = parse_int(
        quality
    )

    if quality == 2160:
        return "4K"

    if quality:
        return f"{quality}p"

    return "Unknown"


# ============================================================
# FILE QUALITY SCORE
# ============================================================

def file_score(
    file,
):

    filename = str(
        getattr(
            file,
            "file_name",
            "",
        )
        or ""
    )

    quality = extract_quality(
        filename
    )

    size = parse_int(
        getattr(
            file,
            "file_size",
            0,
        )
    )

    return (
        quality * 1_000_000_000
        + size
    )


# ============================================================
# CALLBACK SESSION
# ============================================================

def create_session(
    user_id,
    data,
):

    # --------------------------------------------------------
    # Remove old sessions if cache grows.
    # --------------------------------------------------------

    if len(
        SERIES_SESSIONS
    ) >= SERIES_SESSION_MAX:

        # Remove oldest approximately.
        try:

            SERIES_SESSIONS.pop(
                next(
                    iter(
                        SERIES_SESSIONS
                    )
                )
            )

        except Exception:
            pass

    token = secrets.token_hex(
        5
    )

    SERIES_SESSIONS[
        token
    ] = {
        "user_id": user_id,
        **data,
    }

    return token


def get_session(
    token,
):

    return SERIES_SESSIONS.get(
        token
    )


# ============================================================
# CALLBACKS
# ============================================================

def cb_series(
    token,
):

    return f"ser:{token}"


def cb_season(
    token,
    season,
):

    return (
        f"ses:{token}:"
        f"{season}"
    )


def cb_quality(
    token,
    quality,
):

    return (
        f"qua:{token}:"
        f"{quality}"
    )


def cb_back(
    token,
):

    return f"back:{token}"


# ============================================================
# IMDb SEARCH
# ============================================================

async def imdb_search(
    query,
):

    if not IMDB_AVAILABLE:
        return []

    key = normalize_title(
        query
    )

    if not key:
        return []

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    cached = IMDB_SEARCH_CACHE.get(
        key
    )

    if cached is not None:

        return cached

    # --------------------------------------------------------
    # IMDb runs in a thread.
    #
    # This prevents blocking Pyrogram's event loop.
    # --------------------------------------------------------

    try:

        results = await asyncio.to_thread(
            imdb_api.search_movie,
            query,
        )

    except Exception as e:

        logger.warning(
            "[SERIES] IMDb search failed: %s",
            e,
        )

        return []

    output = []

    for movie in results[:20]:

        title = movie.get(
            "title"
        )

        if not title:
            continue

        kind = movie.get(
            "kind"
        )

        movie_id = movie.get(
            "movieID"
        )

        year = movie.get(
            "year"
        )

        is_series = kind in (
            "tv series",
            "tv mini series",
        )

        output.append(
            {
                "id": str(
                    movie_id
                )
                if movie_id
                else "",
                "title": str(
                    title
                ),
                "year": year,
                "kind": kind,
                "is_series": is_series,
            }
        )

    # --------------------------------------------------------
    # Small cache only.
    # --------------------------------------------------------

    if len(
        IMDB_SEARCH_CACHE
    ) >= 100:

        try:

            IMDB_SEARCH_CACHE.pop(
                next(
                    iter(
                        IMDB_SEARCH_CACHE
                    )
                )
            )

        except Exception:
            pass

    IMDB_SEARCH_CACHE[
        key
    ] = output

    return output


# ============================================================
# IMDb DETAILS
# ============================================================

async def imdb_details(
    imdb_id,
):

    if not imdb_id:
        return {}

    if imdb_id in IMDB_DETAILS_CACHE:

        return IMDB_DETAILS_CACHE[
            imdb_id
        ]

    if not IMDB_AVAILABLE:

        return {}

    try:

        movie = await asyncio.to_thread(
            imdb_api.get_movie,
            int(imdb_id),
        )

    except Exception as e:

        logger.warning(
            "[SERIES] IMDb details failed: %s",
            e,
        )

        return {}

    details = {

        "id": str(
            imdb_id
        ),

        "title": movie.get(
            "title",
            "Unknown",
        ),

        "year": movie.get(
            "year"
        ),

        "rating": movie.get(
            "rating"
        ),

        "genres": movie.get(
            "genres",
            [],
        ),

        "plot": movie.get(
            "plot",
            [],
        ),

        "poster": (
            movie.get(
                "full-size cover url"
            )
            or movie.get(
                "cover url"
            )
        ),
    }

    IMDB_DETAILS_CACHE[
        imdb_id
    ] = details

    return details


# ============================================================
# FAST DATABASE SEARCH
# ============================================================
#
# THIS IS THE MOST IMPORTANT PART.
#
# NO:
#
#     collection.find({})
#
# NO:
#
#     Media scan
#
# NO:
#
#     Media2 scan
#
# NO:
#
#     Media3 scan
#
# We use the same search engine your movie search already uses.
#
# ============================================================

async def fast_search(
    query,
):

    query = str(
        query
    ).strip()

    if not query:

        return []

    cache_key = normalize_title(
        query
    )

    cached = SEARCH_CACHE.get(
        cache_key
    )

    if cached is not None:

        return cached

    try:

        result = await get_search_results(
            chat_id=None,
            query=query,
            max_results=SERIES_SEARCH_LIMIT,
            offset=0,
            filter=False,
        )

        # ----------------------------------------------------
        # get_search_results normally returns:
        #
        #   files, offset, total
        #
        # ----------------------------------------------------

        if isinstance(
            result,
            tuple,
        ):

            files = result[0]

        else:

            files = result

        if files is None:

            files = []

        files = list(
            files
        )

    except Exception as e:

        logger.exception(
            "[SERIES] Fast search failed: %s",
            e,
        )

        return []

    # --------------------------------------------------------
    # Small cache.
    # --------------------------------------------------------

    if len(
        SEARCH_CACHE
    ) >= SEARCH_CACHE_MAX:

        try:

            SEARCH_CACHE.pop(
                next(
                    iter(
                        SEARCH_CACHE
                    )
                )
            )

        except Exception:
            pass

    SEARCH_CACHE[
        cache_key
    ] = files

    return files


# ============================================================
# STRICT SERIES FILTER
# ============================================================

def filter_series_files(
    files,
    wanted_season=None,
    wanted_quality=None,
):

    output = []

    for file in files:

        filename = getattr(
            file,
            "file_name",
            None,
        )

        if not filename:

            filename = getattr(
                file,
                "file_name",
                "",
            )

        if not filename:

            continue

        season, episode = (
            extract_season_episode(
                filename
            )
        )

        if season is None:
            continue

        if episode is None:
            continue

        quality = extract_quality(
            filename
        )

        # ----------------------------------------------------
        # Season
        # ----------------------------------------------------

        if (
            wanted_season is not None
            and season
            != wanted_season
        ):

            continue

        # ----------------------------------------------------
        # Quality
        #
        # quality 0 means unknown quality.
        # We DON'T include unknown quality when user selected
        # an exact quality.
        # ----------------------------------------------------

        if (
            wanted_quality is not None
            and quality
            != wanted_quality
        ):

            continue

        output.append(
            (
                season,
                episode,
                quality,
                file,
            )
        )

    return output


# ============================================================
# ONE BEST FILE PER EPISODE
# ============================================================

def best_episode_files(
    files,
    wanted_season,
    wanted_quality,
):

    grouped = {}

    for season, episode, quality, file in filter_series_files(
        files,
        wanted_season=wanted_season,
        wanted_quality=wanted_quality,
    ):

        # ----------------------------------------------------
        # One file per episode.
        # ----------------------------------------------------

        old = grouped.get(
            episode
        )

        if old is None:

            grouped[
                episode
            ] = file

            continue

        if file_score(
            file
        ) > file_score(
            old
        ):

            grouped[
                episode
            ] = file

    return grouped


# ============================================================
# BUILD SEARCH QUERY
# ============================================================
#
# User's exact requested idea:
#
#     Game of Thrones
#     +
#     S01
#     +
#     720p
#
# becomes:
#
#     Game of Thrones S01 720p
#
# ============================================================

def build_exact_search(
    title,
    season,
    quality,
):

    clean_title = normalize_title(
        title
    )

    query = (
        f"{clean_title} "
        f"S{int(season):02d}"
    )

    if quality:

        query += (
            f" {quality_text(quality)}"
        )

    return query.strip()


# ============================================================
# FIND SERIES FILES
# ============================================================

async def find_series_files(
    title,
    season,
    quality,
):

    # --------------------------------------------------------
    # EXACT QUERY FIRST
    # --------------------------------------------------------

    exact_query = build_exact_search(
        title,
        season,
        quality,
    )

    logger.info(
        "[SERIES] Fast query: %s",
        exact_query,
    )

    files = await fast_search(
        exact_query
    )

    selected = best_episode_files(
        files,
        season,
        quality,
    )

    # --------------------------------------------------------
    # If the database search system is strict and doesn't
    # return enough results, try a slightly simpler query.
    #
    # Still uses get_search_results().
    #
    # NEVER scans MongoDB manually.
    # --------------------------------------------------------

    if not selected:

        fallback_query = (
            f"{normalize_title(title)} "
            f"S{int(season):02d}E"
        )

        logger.info(
            "[SERIES] Fallback query: %s",
            fallback_query,
        )

        files = await fast_search(
            fallback_query
        )

        selected = best_episode_files(
            files,
            season,
            quality,
        )

    return selected


# ============================================================
# DISCOVER AVAILABLE SEASONS
# ============================================================
#
# We don't scan the entire DB.
#
# We perform small searches for the title and extract the
# Sxx information returned by the existing search engine.
#
# ============================================================

async def discover_seasons(
    title,
):

    # --------------------------------------------------------
    # Search title through the EXISTING fast system.
    # --------------------------------------------------------

    files = await fast_search(
        normalize_title(
            title
        )
    )

    seasons = defaultdict(
        lambda: defaultdict(set)
    )

    for file in files:

        filename = getattr(
            file,
            "file_name",
            "",
        )

        if not filename:

            continue

        season, episode = (
            extract_season_episode(
                filename
            )
        )

        if season is None:
            continue

        if episode is None:
            continue

        quality = extract_quality(
            filename
        )

        seasons[
            season
        ][
            episode
        ].add(
            quality
        )

    return seasons


# ============================================================
# SEARCH SERIES CANDIDATES
# ============================================================

async def find_series_candidates(
    query,
):

    results = await imdb_search(
        query
    )

    candidates = []

    for result in results:

        if not result.get(
            "is_series"
        ):

            continue

        title = result.get(
            "title",
            "",
        )

        if not title:

            continue

        candidates.append(
            result
        )

    # --------------------------------------------------------
    # If IMDb returns no series, use the database search
    # system as a fallback.
    # --------------------------------------------------------

    if not candidates:

        files = await fast_search(
            query
        )

        # ----------------------------------------------------
        # Extract possible series titles from matching files.
        #
        # We DON'T try to scan every DB document.
        # Only results returned by the existing search engine
        # are examined.
        # ----------------------------------------------------

        title_candidates = {}

        for file in files:

            filename = getattr(
                file,
                "file_name",
                "",
            )

            if not filename:
                continue

            season, episode = (
                extract_season_episode(
                    filename
                )
            )

            if (
                season is None
                or episode is None
            ):
                continue

            clean_name = re.split(
                r"(?i)\bS\d{1,2}\s*E\d{1,3}\b",
                filename,
                maxsplit=1,
            )[0]

            clean_name = normalize_title(
                clean_name
            )

            if not clean_name:
                continue

            title_candidates[
                clean_name
            ] = True

        for title in list(
            title_candidates.keys()
        )[:8]:

            candidates.append(
                {
                    "id": "",
                    "title": clean_display_title(
                        title
                    ),
                    "year": None,
                    "kind": "tv series",
                    "is_series": True,
                }
            )

    return candidates


# ============================================================
# SEARCH KEYBOARD
# ============================================================

def build_series_keyboard(
    user_id,
    candidates,
):

    rows = []

    for item in candidates:

        title = item.get(
            "title",
            "Unknown",
        )

        year = item.get(
            "year"
        )

        token = create_session(
            user_id,
            {
                "title": title,
                "imdb_id": item.get(
                    "id",
                    "",
                ),
            },
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
                    text=text[:60],
                    callback_data=cb_series(
                        token
                    ),
                )
            ]
        )

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# SERIES SEARCH HANDLER
# ============================================================

@Client.on_message(
    filters.text
    & ~filters.command(
        [
            "start",
            "help",
        ]
    ),
    group=-50,
)
async def series_search_handler(
    app,
    message,
):

    # --------------------------------------------------------
    # ONLY THE SERIES GROUP
    # --------------------------------------------------------

    if SERIES_CHAT_ID is None:
        return

    if message.chat.id != SERIES_CHAT_ID:
        return

    text = (
        message.text or ""
    ).strip()

    if not text:
        return

    if text.startswith(
        "/"
    ):
        return

    # --------------------------------------------------------
    # Don't process direct episode searches here.
    # --------------------------------------------------------

    if re.search(
        r"(?i)\bS\d{1,2}\s*E\d{1,3}\b",
        text,
    ):

        return

    try:

        msg = await message.reply_text(
            "🔎 <b>Searching...</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:

        msg = None

    # --------------------------------------------------------
    # IMDb search is executed in a background thread.
    # --------------------------------------------------------

    candidates = await find_series_candidates(
        text
    )

    # --------------------------------------------------------
    # Movie fallback
    # --------------------------------------------------------

    if not candidates:

        if MOVIE_GROUP_LINK:

            movie_results = []

            try:

                movie_results = await imdb_search(
                    text
                )

            except Exception:

                movie_results = []

            movie_found = any(
                not x.get(
                    "is_series",
                    False,
                )
                for x in movie_results
            )

            if movie_found:

                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                text="🎬 MOVIE GROUP",
                                url=MOVIE_GROUP_LINK,
                            )
                        ]
                    ]
                )

                response = (
                    "🎬 <b>Movie detected</b>\n\n"
                    f"🔎 {escape_html(text)}\n\n"
                    "For movies, search in our movie group."
                )

                if msg:

                    await msg.edit_text(
                        response,
                        reply_markup=keyboard,
                        parse_mode=enums.ParseMode.HTML,
                    )

                return

    # --------------------------------------------------------
    # Nothing found
    # --------------------------------------------------------

    if not candidates:

        if msg:

            await msg.edit_text(
                "❌ <b>No series found.</b>\n\n"
                "Try another name.",
                parse_mode=enums.ParseMode.HTML,
            )

        return

    # --------------------------------------------------------
    # Show results
    # --------------------------------------------------------

    keyboard = build_series_keyboard(
        message.from_user.id,
        candidates,
    )

    response = (
        "📺 <b>SERIES SEARCH</b>\n\n"
        f"🔎 <b>{escape_html(text)}</b>\n\n"
        "Select the series:"
    )

    if msg:

        await msg.edit_text(
            response,
            reply_markup=keyboard,
            parse_mode=enums.ParseMode.HTML,
        )


# ============================================================
# SERIES SELECT
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^ser:"
    )
)
async def series_select_callback(
    app,
    query,
):

    if (
        not query.message
        or query.message.chat.id
        != SERIES_CHAT_ID
    ):

        await query.answer(
            "Series is not available here.",
            show_alert=True,
        )

        return

    token = query.data[
        4:
    ]

    session = get_session(
        token
    )

    if not session:

        await query.answer(
            "This search expired. Search again.",
            show_alert=True,
        )

        return

    await query.answer(
        "📺 Loading..."
    )

    title = session.get(
        "title",
        "Series",
    )

    imdb_id = session.get(
        "imdb_id",
        "",
    )

    # --------------------------------------------------------
    # IMDb details
    # --------------------------------------------------------

    details = {}

    if imdb_id:

        details = await imdb_details(
            imdb_id
        )

    if not details:

        details = {
            "title": title,
            "rating": None,
            "year": None,
            "genres": [],
            "plot": [],
            "poster": None,
        }

    # --------------------------------------------------------
    # Discover seasons using fast DB search.
    # --------------------------------------------------------

    seasons = await discover_seasons(
        title
    )

    if not seasons:

        await query.message.edit_text(
            "<b>❌ No episodes found.</b>\n\n"
            f"📺 {escape_html(title)}\n\n"
            "The series exists, but matching "
            "SxxExx files were not returned by "
            "the database search.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    session[
        "seasons"
    ] = dict(
        seasons
    )

    # --------------------------------------------------------
    # Details
    # --------------------------------------------------------

    rating = details.get(
        "rating"
    )

    if rating:

        rating_text = (
            f"{float(rating):.1f}/10"
        )

    else:

        rating_text = "N/A"

    genres = details.get(
        "genres",
        [],
    )

    plot = details.get(
        "plot",
        [],
    )

    text = (
        "📺 <b>SERIES INFORMATION</b>\n\n"
        f"🎬 <b>{escape_html(title)}</b>\n\n"
        f"⭐ Rating: <b>{rating_text}</b>\n"
        f"📚 Seasons found: <b>{len(seasons)}</b>\n"
    )

    if details.get(
        "year"
    ):

        text += (
            f"📅 Year: <b>{details['year']}</b>\n"
        )

    if genres:

        text += (
            "\n🎭 <b>Genres:</b> "
            f"{escape_html(', '.join(genres[:5]))}\n"
        )

    if plot:

        plot_text = str(
            plot[0]
        )

        if len(
            plot_text
        ) > 400:

            plot_text = (
                plot_text[:400]
                + "..."
            )

        text += (
            "\n📝 "
            f"{escape_html(plot_text)}\n"
        )

    text += (
        "\n📦 <b>Select Season</b>"
    )

    # --------------------------------------------------------
    # Season keyboard
    # --------------------------------------------------------

    rows = []

    current = []

    for season in sorted(
        seasons.keys()
    ):

        episodes = seasons[
            season
        ]

        button = InlineKeyboardButton(
            text=(
                f"S{season:02d} "
                f"({len(episodes)})"
            ),
            callback_data=cb_season(
                token,
                season,
            ),
        )

        current.append(
            button
        )

        if len(
            current
        ) == 2:

            rows.append(
                current
            )

            current = []

    if current:

        rows.append(
            current
        )

    markup = InlineKeyboardMarkup(
        rows
    )

    # --------------------------------------------------------
    # POSTER
    # --------------------------------------------------------

    poster = details.get(
        "poster"
    )

    if (
        SERIES_POSTER
        and poster
    ):

        try:

            await query.message.delete()

        except Exception:

            pass

        try:

            await app.send_photo(
                chat_id=query.message.chat.id,
                photo=poster,
                caption=text,
                reply_markup=markup,
            )

            return

        except Exception as e:

            logger.warning(
                "[SERIES] Poster failed: %s",
                e,
            )

    # --------------------------------------------------------
    # Text mode
    # --------------------------------------------------------

    try:

        await query.message.edit_text(
            text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception as e:

        logger.warning(
            "[SERIES] Edit failed: %s",
            e,
        )


# ============================================================
# SEASON SELECT
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^ses:"
    )
)
async def season_callback(
    app,
    query,
):

    if (
        not query.message
        or query.message.chat.id
        != SERIES_CHAT_ID
    ):

        await query.answer(
            "Series is not available here.",
            show_alert=True,
        )

        return

    parts = query.data.split(
        ":"
    )

    if len(
        parts
    ) != 3:

        return

    token = parts[1]

    season = parse_int(
        parts[2]
    )

    session = get_session(
        token
    )

    if not session:

        await query.answer(
            "Search expired.",
            show_alert=True,
        )

        return

    title = session.get(
        "title",
        "Series",
    )

    session[
        "season"
    ] = season

    await query.answer(
        f"Season {season:02d}"
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # We now search ONLY:
    #
    #     Game of Thrones S01
    #
    # through get_search_results().
    #
    # No DB scan.
    # --------------------------------------------------------

    search_query = build_exact_search(
        title,
        season,
        None,
    )

    files = await fast_search(
        search_query
    )

    filtered = filter_series_files(
        files,
        wanted_season=season,
        wanted_quality=None,
    )

    # --------------------------------------------------------
    # Find qualities
    # --------------------------------------------------------

    qualities = defaultdict(
        set
    )

    for (
        _season,
        episode,
        quality,
        file,
    ) in filtered:

        qualities[
            quality
        ].add(
            episode
        )

    if not qualities:

        await query.message.edit_text(
            f"❌ <b>No files found.</b>\n\n"
            f"📺 {escape_html(title)}\n"
            f"🎞 Season {season:02d}",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    # --------------------------------------------------------
    # QUALITY BUTTONS
    # --------------------------------------------------------

    rows = []

    current = []

    for quality in sorted(
        qualities.keys(),
        reverse=True,
    ):

        episodes = qualities[
            quality
        ]

        quality_name = (
            quality_text(
                quality
            )
        )

        button = InlineKeyboardButton(
            text=(
                f"🎞 {quality_name} "
                f"({len(episodes)} EP)"
            ),
            callback_data=cb_quality(
                token,
                quality,
            ),
        )

        current.append(
            button
        )

        if len(
            current
        ) == 2:

            rows.append(
                current
            )

            current = []

    if current:

        rows.append(
            current
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 Seasons",
                callback_data=cb_back(
                    token
                ),
            )
        ]
    )

    text = (
        "📺 <b>SERIES</b>\n\n"
        f"🎬 <b>{escape_html(title)}</b>\n"
        f"🎞 <b>Season {season:02d}</b>\n\n"
        f"📦 Episodes found: "
        f"<b>{len(set(x[1] for x in filtered))}</b>\n\n"
        "🎯 <b>Select Quality</b>"
    )

    await query.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            rows
        ),
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# QUALITY SELECT
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^qua:"
    )
)
async def quality_callback(
    app,
    query,
):

    if (
        not query.message
        or query.message.chat.id
        != SERIES_CHAT_ID
    ):

        await query.answer(
            "Series is not available here.",
            show_alert=True,
        )

        return

    parts = query.data.split(
        ":"
    )

    if len(
        parts
    ) != 3:

        return

    token = parts[1]

    quality = parse_int(
        parts[2]
    )

    session = get_session(
        token
    )

    if not session:

        await query.answer(
            "Search expired.",
            show_alert=True,
        )

        return

    title = session.get(
        "title",
        "Series",
    )

    season = parse_int(
        session.get(
            "season"
        )
    )

    await query.answer(
        "🔎 Searching files..."
    )

    # --------------------------------------------------------
    # EXACT QUERY
    #
    # Game of Thrones S01 720p
    # --------------------------------------------------------

    search_query = build_exact_search(
        title,
        season,
        quality,
    )

    logger.info(
        "[SERIES] FINAL SEARCH: %s",
        search_query,
    )

    # --------------------------------------------------------
    # FAST SEARCH
    # --------------------------------------------------------

    files = await fast_search(
        search_query
    )

    # --------------------------------------------------------
    # Strict filter
    # --------------------------------------------------------

    selected = best_episode_files(
        files,
        season,
        quality,
    )

    # --------------------------------------------------------
    # If exact quality search returned nothing,
    # perform one fallback search.
    # --------------------------------------------------------

    if not selected:

        fallback_query = (
            f"{normalize_title(title)} "
            f"S{season:02d}E"
        )

        logger.info(
            "[SERIES] FINAL FALLBACK: %s",
            fallback_query,
        )

        fallback_files = await fast_search(
            fallback_query
        )

        selected = best_episode_files(
            fallback_files,
            season,
            quality,
        )

    if not selected:

        await query.message.reply_text(
            "❌ <b>No files found.</b>\n\n"
            f"🔎 {escape_html(search_query)}",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    # --------------------------------------------------------
    # Sort episodes numerically.
    # --------------------------------------------------------

    episodes = sorted(
        selected.keys()
    )

    if SERIES_MAX_EPISODES > 0:

        episodes = episodes[
            :SERIES_MAX_EPISODES
        ]

    # --------------------------------------------------------
    # START MESSAGE
    # --------------------------------------------------------

    status = await query.message.reply_text(
        "<b>🚀 SEASON STARTED</b>\n\n"
        f"📺 <b>{escape_html(title)}</b>\n"
        f"🎞 Season: <b>S{season:02d}</b>\n"
        f"🎯 Quality: <b>{quality_text(quality)}</b>\n\n"
        f"📦 Episodes: <b>{len(episodes)}</b>\n"
        "⏳ Sending in order...",
        parse_mode=enums.ParseMode.HTML,
    )

    sent = 0

    failed = 0

    # ========================================================
    # SEND EPISODES
    # ========================================================

    for episode in episodes:

        file = selected[
            episode
        ]

        filename = getattr(
            file,
            "file_name",
            "Episode",
        )

        file_id = getattr(
            file,
            "file_id",
            None,
        )

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Your existing bot uses:
        #
        #     file.file_id
        #
        # NOT MongoDB _id.
        # ----------------------------------------------------

        if not file_id:

            failed += 1

            continue

        caption = None

        if SERIES_CAPTION:

            try:

                caption = SERIES_CAPTION.format(
                    series=title,
                    season=f"{season:02d}",
                    episode=f"{episode:02d}",
                    quality=quality_text(
                        quality
                    ),
                    filename=filename,
                )

            except Exception:

                caption = SERIES_CAPTION

        try:

            logger.info(
                "[SERIES] Sending "
                "%s S%02dE%02d %s",
                title,
                season,
                episode,
                quality_text(
                    quality
                ),
            )

            # ------------------------------------------------
            # SAME CACHED-MEDIA SYSTEM AS YOUR BOT
            # ------------------------------------------------

            await app.send_cached_media(
                chat_id=query.message.chat.id,
                file_id=file_id,
                caption=caption,
            )

            sent += 1

            # ------------------------------------------------
            # Small delay.
            # ------------------------------------------------

            if SERIES_SEND_DELAY > 0:

                await asyncio.sleep(
                    SERIES_SEND_DELAY
                )

        except FloodWait as e:

            wait = int(
                getattr(
                    e,
                    "value",
                    30,
                )
            )

            logger.warning(
                "[SERIES] FloodWait %s sec",
                wait,
            )

            await asyncio.sleep(
                wait + 2
            )

            try:

                await app.send_cached_media(
                    chat_id=query.message.chat.id,
                    file_id=file_id,
                    caption=caption,
                )

                sent += 1

            except Exception:

                failed += 1

                logger.exception(
                    "[SERIES] Retry failed: %s",
                    filename,
                )

        except RPCError:

            failed += 1

            logger.exception(
                "[SERIES] Telegram error: %s",
                filename,
            )

        except Exception:

            failed += 1

            logger.exception(
                "[SERIES] Failed: %s",
                filename,
            )

    # ========================================================
    # FINISHED
    # ========================================================

    try:

        await status.edit_text(
            "<b>✅ SEASON COMPLETE</b>\n\n"
            f"📺 <b>{escape_html(title)}</b>\n"
            f"🎞 Season: <b>S{season:02d}</b>\n"
            f"🎯 Quality: <b>{quality_text(quality)}</b>\n\n"
            f"📦 Total: <b>{len(episodes)}</b>\n"
            f"✅ Sent: <b>{sent}</b>\n"
            f"❌ Failed: <b>{failed}</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:

        pass


# ============================================================
# BACK TO SERIES
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^back:"
    )
)
async def series_back_callback(
    app,
    query,
):

    if (
        not query.message
        or query.message.chat.id
        != SERIES_CHAT_ID
    ):

        return

    token = query.data[
        5:
    ]

    session = get_session(
        token
    )

    if not session:

        await query.answer(
            "Search expired.",
            show_alert=True,
        )

        return

    title = session.get(
        "title",
        "Series",
    )

    await query.answer(
        "🔄 Loading..."
    )

    seasons = await discover_seasons(
        title
    )

    if not seasons:

        await query.message.edit_text(
            "❌ No seasons found.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    rows = []

    current = []

    for season in sorted(
        seasons.keys()
    ):

        button = InlineKeyboardButton(
            text=(
                f"S{season:02d} "
                f"({len(seasons[season])})"
            ),
            callback_data=cb_season(
                token,
                season,
            ),
        )

        current.append(
            button
        )

        if len(
            current
        ) == 2:

            rows.append(
                current
            )

            current = []

    if current:

        rows.append(
            current
        )

    text = (
        "📺 <b>SERIES</b>\n\n"
        f"🎬 <b>{escape_html(title)}</b>\n\n"
        "📦 <b>Select Season</b>"
    )

    await query.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            rows
        ),
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# CACHE CLEAR
# ============================================================

def clear_series_cache():

    IMDB_SEARCH_CACHE.clear()

    IMDB_DETAILS_CACHE.clear()

    SEARCH_CACHE.clear()

    SERIES_SESSIONS.clear()

    logger.info(
        "[SERIES] All temporary caches cleared."
    )


# ============================================================
# STARTUP
# ============================================================

logger.info(
    "=================================================="
)

logger.info(
    "[SERIES] DowntownVilla FAST SERIES SYSTEM"
)

logger.info(
    "[SERIES] Group: %s",
    SERIES_CHAT_ID,
)

logger.info(
    "[SERIES] IMDb: %s",
    (
        "AVAILABLE"
        if IMDB_AVAILABLE
        else "UNAVAILABLE"
    ),
)

logger.info(
    "[SERIES] Poster: %s",
    SERIES_POSTER,
)

logger.info(
    "[SERIES] Movie group: %s",
    (
        MOVIE_GROUP_LINK
        or "NOT SET"
    ),
)

logger.info(
    "[SERIES] Database search: get_search_results()"
)

logger.info(
    "[SERIES] Direct MongoDB scanning: DISABLED"
)

logger.info(
    "=================================================="
)
