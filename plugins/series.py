import os
import re
import asyncio
import logging
from collections import defaultdict
from html import escape

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
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
# CONFIGURATION
# ============================================================

# The group where the SERIES system is allowed to work.
#
# Example:
# SERIES_GROUP_ID=-1001234567890
#
SERIES_GROUP_ID = os.getenv(
    "SERIES_GROUP_ID",
    ""
).strip()


# ------------------------------------------------------------
# Caption used when sending season episodes.
#
# You can put anything here.
#
# Example:
#
# SERIES_CAPTION=<b>🎬 {series}</b>\n📺 Season {season}\n🎞 Episode {episode}
#
# Available variables:
#
# {series}
# {season}
# {episode}
# {quality}
# {filename}
# ------------------------------------------------------------

SERIES_CAPTION = os.getenv(
    "SERIES_CAPTION",
    ""
)


# ------------------------------------------------------------
# Poster ON / OFF
#
# SERIES_POSTER=true
# SERIES_POSTER=false
# ------------------------------------------------------------

SERIES_POSTER = os.getenv(
    "SERIES_POSTER",
    "true"
).lower() in (
    "true",
    "1",
    "yes",
    "on",
)


# ------------------------------------------------------------
# Movie group link
#
# If the searched title is identified as a movie,
# the bot will show a button sending the user here.
#
# Example:
#
# MOVIE_GROUP_LINK=https://t.me/YourMovieGroup
# ------------------------------------------------------------

MOVIE_GROUP_LINK = os.getenv(
    "MOVIE_GROUP_LINK",
    ""
).strip()


# ------------------------------------------------------------
# Number of IMDb search results shown.
# ------------------------------------------------------------

SERIES_SEARCH_LIMIT = int(
    os.getenv(
        "SERIES_SEARCH_LIMIT",
        "8",
    )
)


# ------------------------------------------------------------
# Sending delay.
#
# Prevents Telegram FloodWait problems when sending
# a complete season.
# ------------------------------------------------------------

SERIES_SEND_DELAY = float(
    os.getenv(
        "SERIES_SEND_DELAY",
        "0.7",
    )
)


# ------------------------------------------------------------
# Maximum episodes that can be sent in one request.
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
# Search minimum title similarity.
# ------------------------------------------------------------

SERIES_MIN_MATCH = int(
    os.getenv(
        "SERIES_MIN_MATCH",
        "45",
    )
)


# ============================================================
# GROUP ID
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
# IMDB
# ============================================================

try:
    import imdb

    imdb_api = imdb.IMDb()

    IMDB_AVAILABLE = True

    logger.info(
        "[SERIES] IMDbPy loaded successfully."
    )

except Exception as e:

    imdb_api = None

    IMDB_AVAILABLE = False

    logger.warning(
        "[SERIES] IMDbPy unavailable: %s",
        e,
    )


# ============================================================
# MEMORY CACHE
# ============================================================

# IMDb results are cached so repeated searches don't hammer IMDb.

IMDB_CACHE = {}

# Full series details cache.

SERIES_DETAILS_CACHE = {}

# File scan cache.

SERIES_FILE_CACHE = {}


# ============================================================
# HELPERS
# ============================================================


def normalize_title(text):
    """
    Normalize a title for searching.

    Example:

        Game.of.Thrones
        Game-of-Thrones
        Game_of_Thrones

    all become roughly:

        game of thrones
    """

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
    if not text:
        return ""

    text = normalize_title(text)

    return " ".join(
        word.capitalize()
        for word in text.split()
    )


def parse_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def format_rating(rating):
    if rating is None:
        return "N/A"

    try:
        return f"{float(rating):.1f}/10"
    except Exception:
        return "N/A"


def format_bytes(size):
    if not size:
        return "0 B"

    size = float(size)

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB",
    ]

    for unit in units:

        if size < 1024:
            return f"{size:.2f} {unit}"

        size /= 1024

    return f"{size:.2f} PB"


def escape_html(value):
    if value is None:
        return ""

    return escape(
        str(value)
    )


# ============================================================
# FILE NAME PARSER
# ============================================================


def extract_season_episode(filename):
    """
    Detect:

        S01E01
        S1E1
        s01.e01
        S01 E01
        S01-E01
        S01_E01

    Also handles common noisy filenames.
    """

    if not filename:
        return None, None

    name = str(filename)

    # --------------------------------------------------------
    # Standard S01E01 pattern
    # --------------------------------------------------------

    match = re.search(
        r"(?i)\bS(\d{1,2})[\s._\-]*E(\d{1,3})\b",
        name,
    )

    if match:

        season = int(
            match.group(1)
        )

        episode = int(
            match.group(2)
        )

        return season, episode

    # --------------------------------------------------------
    # S01 EP01
    # --------------------------------------------------------

    match = re.search(
        r"(?i)\bS(\d{1,2})[\s._\-]*EP(?:ISODE)?[\s._\-]*(\d{1,3})\b",
        name,
    )

    if match:

        return (
            int(match.group(1)),
            int(match.group(2)),
        )

    # --------------------------------------------------------
    # Season 01 Episode 01
    # --------------------------------------------------------

    match = re.search(
        r"(?i)\bSeason[\s._\-]*(\d{1,2})"
        r"[\s._\-]*(?:Episode|Ep|E)"
        r"[\s._\-]*(\d{1,3})\b",
        name,
    )

    if match:

        return (
            int(match.group(1)),
            int(match.group(2)),
        )

    return None, None


# ============================================================
# QUALITY PARSER
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


def extract_quality(filename):
    if not filename:
        return 0

    filename = str(filename)

    for quality, pattern in QUALITY_PATTERNS:

        if pattern.search(filename):
            return quality

    # --------------------------------------------------------
    # WEB / HD fallback
    # --------------------------------------------------------

    if re.search(
        r"(?i)\bHD\b",
        filename,
    ):
        return 720

    return 0


def quality_text(quality):
    if not quality:
        return "Unknown"

    if quality == 2160:
        return "4K"

    return f"{quality}p"


# ============================================================
# LANGUAGE / SOURCE BONUS
# ============================================================


def file_quality_score(file):
    """
    Select the best duplicate file.

    Priority:

        1. Resolution
        2. File size
        3. Cleaner filename
    """

    filename = str(
        file.get(
            "file_name",
            "",
        )
    )

    quality = extract_quality(
        filename
    )

    size = parse_int(
        file.get(
            "file_size",
            0,
        )
    )

    # Prefer files with a known quality.

    known_quality_bonus = (
        1000000000000
        if quality
        else 0
    )

    return (
        known_quality_bonus
        + quality * 1000000000
        + size
    )


# ============================================================
# TITLE MATCHING
# ============================================================


def title_tokens(text):
    normalized = normalize_title(
        text
    )

    return [
        x
        for x in normalized.split()
        if len(x) >= 2
    ]


def title_match_score(
    query,
    filename,
):
    """
    Robust title matching.

    Example:

        query:
            lost

        filename:
            Lost.S01E01.720p.WEB-DL.mkv

    returns a strong match.
    """

    query_tokens = title_tokens(
        query
    )

    if not query_tokens:
        return 0

    filename_clean = normalize_title(
        filename
    )

    # Remove season/episode information.

    filename_clean = re.sub(
        r"(?i)\bs\d{1,2}\s*e\d{1,3}\b",
        " ",
        filename_clean,
    )

    filename_clean = re.sub(
        r"(?i)\bseason\s*\d{1,2}\b",
        " ",
        filename_clean,
    )

    filename_tokens = set(
        title_tokens(
            filename_clean
        )
    )

    if not filename_tokens:
        return 0

    matched = 0

    for token in query_tokens:

        if token in filename_tokens:

            matched += 1

            continue

        # Prefix matching.

        if any(
            x.startswith(token)
            or token.startswith(x)
            for x in filename_tokens
        ):
            matched += 1

    score = (
        matched
        / len(query_tokens)
        * 100
    )

    return int(score)


# ============================================================
# MONGODB COLLECTIONS
# ============================================================


def get_collections():
    """
    Return the actual MongoDB collections.

    We intentionally bypass umongo here.

    This allows the series plugin to read all existing
    documents directly.
    """

    collections = []

    try:

        collections.append(
            (
                "Media",
                db[COLLECTION_NAME],
            )
        )

    except Exception:
        pass

    if MULTIPLE_DB:

        try:

            collections.append(
                (
                    "Media2",
                    db2[COLLECTION_NAME],
                )
            )

        except Exception:
            pass

        try:

            collections.append(
                (
                    "Media3",
                    db3[COLLECTION_NAME],
                )
            )

        except Exception:
            pass

    return collections


# ============================================================
# READ SERIES FILES
# ============================================================


async def get_all_series_files(
    query,
):
    """
    Find all files matching the requested series.

    Only files containing a recognizable season/episode
    pattern are considered.
    """

    normalized_query = normalize_title(
        query
    )

    if not normalized_query:
        return []

    # --------------------------------------------------------
    # Cache
    # --------------------------------------------------------

    cache_key = normalized_query

    cached = SERIES_FILE_CACHE.get(
        cache_key
    )

    if cached is not None:
        return cached

    all_files = []

    # --------------------------------------------------------
    # Read all DBs
    # --------------------------------------------------------

    for source_db, collection in get_collections():

        try:

            cursor = collection.find(
                {},
                {
                    "_id": 1,
                    "file_name": 1,
                    "file_size": 1,
                    "file_type": 1,
                    "mime_type": 1,
                    "caption": 1,
                    "file_ref": 1,
                    "cover": 1,
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

                if (
                    season is None
                    or episode is None
                ):
                    continue

                score = title_match_score(
                    query,
                    filename,
                )

                if score < SERIES_MIN_MATCH:
                    continue

                document["_source_db"] = (
                    source_db
                )

                document["_season"] = (
                    season
                )

                document["_episode"] = (
                    episode
                )

                document["_quality"] = (
                    extract_quality(
                        filename
                    )
                )

                document["_title_score"] = (
                    score
                )

                all_files.append(
                    document
                )

        except Exception as e:

            logger.exception(
                "[SERIES] Error reading %s: %s",
                source_db,
                e,
            )

    # --------------------------------------------------------
    # Cache
    # --------------------------------------------------------

    SERIES_FILE_CACHE[
        cache_key
    ] = all_files

    return all_files


# ============================================================
# BEST FILES
# ============================================================


def build_episode_quality_map(
    files,
):
    """
    Structure:

        season
          episode
            quality
              best file

    Duplicate files with the same quality are reduced
    to ONE best file.
    """

    result = defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(list)
        )
    )

    for file in files:

        season = file.get(
            "_season"
        )

        episode = file.get(
            "_episode"
        )

        quality = file.get(
            "_quality",
            0,
        )

        if season is None:
            continue

        if episode is None:
            continue

        result[
            season
        ][
            episode
        ][
            quality
        ].append(
            file
        )

    # --------------------------------------------------------
    # Reduce duplicates
    # --------------------------------------------------------

    final = defaultdict(
        lambda: defaultdict(dict)
    )

    for season, episodes in result.items():

        for episode, qualities in episodes.items():

            for quality, files in qualities.items():

                best = max(
                    files,
                    key=file_quality_score,
                )

                final[
                    season
                ][
                    episode
                ][
                    quality
                ] = best

    return final


# ============================================================
# IMDb SEARCH
# ============================================================


async def imdb_search_series(
    query,
):
    """
    Search IMDb without blocking the bot's event loop.
    """

    if not IMDB_AVAILABLE:
        return []

    cache_key = normalize_title(
        query
    )

    if cache_key in IMDB_CACHE:
        return IMDB_CACHE[
            cache_key
        ]

    try:

        results = await asyncio.to_thread(
            imdb_api.search_movie,
            query,
        )

    except Exception as e:

        logger.exception(
            "[SERIES] IMDb search failed: %s",
            e,
        )

        return []

    output = []

    for movie in results[
        :SERIES_SEARCH_LIMIT
    ]:

        kind = movie.get(
            "kind"
        )

        title = movie.get(
            "title"
        )

        if not title:
            continue

        year = movie.get(
            "year"
        )

        imdb_id = movie.get(
            "movieID"
        )

        # ----------------------------------------------------
        # Detect series.
        # ----------------------------------------------------

        is_series = kind in (
            "tv series",
            "tv mini series",
            "tv movie",
            "tv episode",
        )

        # TV episode is not treated as a series result.

        if kind == "tv episode":
            is_series = False

        output.append(
            {
                "id": str(imdb_id)
                if imdb_id
                else "",
                "title": str(title),
                "year": year,
                "kind": kind,
                "is_series": is_series,
            }
        )

    IMDB_CACHE[
        cache_key
    ] = output

    return output


# ============================================================
# IMDb DETAILS
# ============================================================


async def get_imdb_details(
    imdb_id,
):
    if not imdb_id:
        return {}

    if imdb_id in SERIES_DETAILS_CACHE:
        return SERIES_DETAILS_CACHE[
            imdb_id
        ]

    if not IMDB_AVAILABLE:
        return {}

    try:

        movie = await asyncio.to_thread(
            imdb_api.get_movie,
            int(imdb_id),
        )

        # Fetch episodes information.

        try:

            await asyncio.to_thread(
                imdb_api.update,
                movie,
                "episodes",
            )

        except Exception:
            pass

        title = movie.get(
            "title",
            "Unknown",
        )

        rating = movie.get(
            "rating"
        )

        year = movie.get(
            "year"
        )

        genres = movie.get(
            "genres",
            [],
        )

        plot = movie.get(
            "plot",
            [],
        )

        cover_url = movie.get(
            "full-size cover url"
        )

        if not cover_url:
            cover_url = movie.get(
                "cover url"
            )

        # ----------------------------------------------------
        # Seasons / episode count
        # ----------------------------------------------------

        seasons = {}

        episodes = movie.get(
            "episodes"
        )

        if isinstance(
            episodes,
            dict,
        ):

            for season_no, season_data in episodes.items():

                if not isinstance(
                    season_data,
                    dict,
                ):
                    continue

                season_no = parse_int(
                    season_no
                )

                if not season_no:
                    continue

                episode_count = len(
                    season_data
                )

                seasons[
                    season_no
                ] = episode_count

        details = {
            "id": str(imdb_id),
            "title": title,
            "year": year,
            "rating": rating,
            "genres": genres,
            "plot": plot,
            "poster": cover_url,
            "seasons": seasons,
        }

        SERIES_DETAILS_CACHE[
            imdb_id
        ] = details

        return details

    except Exception as e:

        logger.exception(
            "[SERIES] IMDb details failed: %s",
            e,
        )

        return {}


# ============================================================
# FIND MATCHING IMDb SERIES
# ============================================================


async def find_series_candidates(
    query,
):
    results = await imdb_search_series(
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

        score = title_match_score(
            query,
            title,
        )

        # Exact title / token matching.

        if (
            normalize_title(
                query
            )
            == normalize_title(
                title
            )
        ):
            score = 100

        result[
            "match_score"
        ] = score

        candidates.append(
            result
        )

    candidates.sort(
        key=lambda x: (
            x.get(
                "match_score",
                0,
            ),
            x.get(
                "year"
            )
            or 0,
        ),
        reverse=True,
    )

    return candidates


# ============================================================
# CALLBACK DATA
# ============================================================


def cb_series(imdb_id):
    return f"ser:{imdb_id}"


def cb_season(imdb_id, season):
    return f"ses:{imdb_id}:{season}"


def cb_quality(
    imdb_id,
    season,
    quality,
):
    return (
        f"qua:{imdb_id}:"
        f"{season}:"
        f"{quality}"
    )


def cb_back(imdb_id):
    return f"back:{imdb_id}"


# ============================================================
# SERIES SEARCH UI
# ============================================================


def build_series_search_keyboard(
    candidates,
):
    buttons = []

    for item in candidates:

        title = item.get(
            "title",
            "Unknown",
        )

        year = item.get(
            "year"
        )

        label = f"📺 {title}"

        if year:
            label += f" ({year})"

        buttons.append(
            [
                InlineKeyboardButton(
                    text=label[:60],
                    callback_data=cb_series(
                        item.get(
                            "id",
                            "",
                        )
                    ),
                )
            ]
        )

    return InlineKeyboardMarkup(
        buttons
    )


# ============================================================
# SERIES DETAILS TEXT
# ============================================================


def build_series_details_text(
    details,
    file_map,
):
    title = details.get(
        "title",
        "Unknown",
    )

    year = details.get(
        "year"
    )

    rating = format_rating(
        details.get(
            "rating"
        )
    )

    genres = details.get(
        "genres",
        [],
    )

    plot = details.get(
        "plot",
        [],
    )

    imdb_seasons = details.get(
        "seasons",
        {},
    )

    available_seasons = sorted(
        file_map.keys()
    )

    text = (
        "<b>📺 SERIES INFORMATION</b>\n\n"
        f"🎬 <b>{escape_html(title)}</b>"
    )

    if year:
        text += f" ({year})"

    text += "\n\n"

    text += (
        f"⭐ IMDb Rating: <b>{rating}</b>\n"
    )

    text += (
        f"📚 Seasons: "
        f"<b>{len(imdb_seasons) or len(available_seasons)}</b>\n"
    )

    total_episodes = 0

    for season_data in file_map.values():

        total_episodes += len(
            season_data
        )

    if total_episodes:

        text += (
            f"🎞 Indexed Episodes: "
            f"<b>{total_episodes}</b>\n"
        )

    if genres:

        text += (
            f"🎭 Genres: "
            f"<b>{escape_html(', '.join(genres[:5]))}</b>\n"
        )

    if plot:

        plot_text = str(
            plot[0]
        )

        if len(plot_text) > 500:
            plot_text = (
                plot_text[:500]
                + "..."
            )

        text += (
            "\n📝 "
            f"{escape_html(plot_text)}\n"
        )

    text += (
        "\n📦 <b>Available in Bot</b>\n"
    )

    for season in available_seasons:

        episode_count = len(
            file_map[
                season
            ]
        )

        imdb_count = imdb_seasons.get(
            season
        )

        if imdb_count:

            text += (
                f"• Season {season:02d}: "
                f"{episode_count}/{imdb_count} episodes\n"
            )

        else:

            text += (
                f"• Season {season:02d}: "
                f"{episode_count} episodes\n"
            )

    return text


# ============================================================
# SEASON BUTTONS
# ============================================================


def build_season_keyboard(
    imdb_id,
    file_map,
):
    rows = []

    seasons = sorted(
        file_map.keys()
    )

    current_row = []

    for season in seasons:

        episode_count = len(
            file_map[
                season
            ]
        )

        button = InlineKeyboardButton(
            text=(
                f"📺 S{season:02d} "
                f"({episode_count})"
            ),
            callback_data=cb_season(
                imdb_id,
                season,
            ),
        )

        current_row.append(
            button
        )

        if len(current_row) == 2:

            rows.append(
                current_row
            )

            current_row = []

    if current_row:
        rows.append(
            current_row
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 Back",
                callback_data=cb_back(
                    imdb_id
                ),
            )
        ]
    )

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# QUALITY BUTTONS
# ============================================================


def build_quality_keyboard(
    imdb_id,
    season,
    episode_map,
):
    """
    A quality is shown if it exists in at least one episode.

    The callback later sends the best matching file for
    each episode.
    """

    quality_episodes = defaultdict(
        set
    )

    for episode, qualities in episode_map.items():

        for quality in qualities.keys():

            quality_episodes[
                quality
            ].add(
                episode
            )

    quality_order = sorted(
        quality_episodes.keys(),
        reverse=True,
    )

    rows = []

    current = []

    for quality in quality_order:

        episodes = quality_episodes[
            quality
        ]

        if quality:

            text = (
                f"🎞 {quality_text(quality)} "
                f"• {len(episodes)} EP"
            )

        else:

            text = (
                f"🎞 Unknown "
                f"• {len(episodes)} EP"
            )

        current.append(
            InlineKeyboardButton(
                text=text,
                callback_data=cb_quality(
                    imdb_id,
                    season,
                    quality,
                ),
            )
        )

        if len(current) == 2:

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
                    imdb_id
                ),
            )
        ]
    )

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# SEND CAPTION
# ============================================================


def make_caption(
    series,
    season,
    episode,
    quality,
    filename,
):
    if not SERIES_CAPTION:
        return None

    try:

        caption = SERIES_CAPTION.format(
            series=series,
            season=f"{season:02d}",
            episode=f"{episode:02d}",
            quality=quality_text(
                quality
            ),
            filename=filename,
        )

        return caption

    except Exception as e:

        logger.warning(
            "[SERIES] Caption formatting failed: %s",
            e,
        )

        return SERIES_CAPTION


# ============================================================
# SEND COMPLETE SEASON
# ============================================================


async def send_season(
    app,
    chat_id,
    series_title,
    season,
    quality,
    episode_map,
):
    """
    Send one best file per episode.

    Episodes are always sorted numerically.
    """

    episodes = []

    for episode in sorted(
        episode_map.keys()
    ):

        qualities = episode_map[
            episode
        ]

        file = None

        # ----------------------------------------------------
        # Exact selected quality.
        # ----------------------------------------------------

        if quality in qualities:

            file = qualities[
                quality
            ]

        # ----------------------------------------------------
        # Unknown quality.
        # ----------------------------------------------------

        elif quality == 0:

            if qualities:

                # Prefer highest known quality.

                available = sorted(
                    qualities.keys(),
                    reverse=True,
                )

                file = qualities[
                    available[0]
                ]

        if file:

            episodes.append(
                (
                    episode,
                    file,
                )
            )

    if not episodes:

        return 0

    # --------------------------------------------------------
    # Optional safety limit.
    # --------------------------------------------------------

    if SERIES_MAX_EPISODES > 0:

        episodes = episodes[
            :SERIES_MAX_EPISODES
        ]

    sent_count = 0

    for episode, file in episodes:

        file_id = file.get(
            "_id"
        )

        filename = file.get(
            "file_name",
            "Episode",
        )

        if not file_id:
            continue

        caption = make_caption(
            series_title,
            season,
            episode,
            quality,
            filename,
        )

        try:

            logger.info(
                "[SERIES] Sending %s S%02dE%02d %s",
                series_title,
                season,
                episode,
                quality_text(
                    quality
                ),
            )

            await app.send_cached_media(
                chat_id=chat_id,
                file_id=str(
                    file_id
                ),
                caption=caption,
            )

            sent_count += 1

            await asyncio.sleep(
                SERIES_SEND_DELAY
            )

        except FloodWait as e:

            wait_time = int(
                getattr(
                    e,
                    "value",
                    30,
                )
            )

            logger.warning(
                "[SERIES] FloodWait: %s seconds",
                wait_time,
            )

            await asyncio.sleep(
                wait_time + 2
            )

            try:

                await app.send_cached_media(
                    chat_id=chat_id,
                    file_id=str(
                        file_id
                    ),
                    caption=caption,
                )

                sent_count += 1

            except Exception:

                logger.exception(
                    "[SERIES] Retry failed for %s",
                    filename,
                )

        except RPCError as e:

            logger.error(
                "[SERIES] Telegram error "
                "for %s: %s",
                filename,
                e,
            )

        except Exception as e:

            logger.exception(
                "[SERIES] Failed sending %s: %s",
                filename,
                e,
            )

    return sent_count


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
    """
    IMPORTANT:

    This handler ONLY works inside SERIES_GROUP_ID.

    It does not affect other groups.
    """

    # --------------------------------------------------------
    # Group restriction
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

    # --------------------------------------------------------
    # Ignore obvious commands.
    # --------------------------------------------------------

    if query.startswith("/"):
        return

    # --------------------------------------------------------
    # Don't treat raw S01E01 searches as IMDb searches.
    # --------------------------------------------------------

    if re.search(
        r"(?i)\bS\d{1,2}E\d{1,3}\b",
        query,
    ):
        return

    # --------------------------------------------------------
    # Searching message
    # --------------------------------------------------------

    try:

        searching = await message.reply_text(
            "🔎 <b>Searching IMDb...</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:
        searching = None

    # --------------------------------------------------------
    # IMDb search
    # --------------------------------------------------------

    candidates = await find_series_candidates(
        query
    )

    # --------------------------------------------------------
    # If IMDb found no series, check whether the query
    # looks like a movie.
    # --------------------------------------------------------

    if not candidates:

        movie_results = []

        if IMDB_AVAILABLE:

            try:

                movie_results = (
                    await imdb_search_series(
                        query
                    )
                )

            except Exception:
                movie_results = []

        has_movie = any(
            not x.get(
                "is_series"
            )
            for x in movie_results
        )

        if has_movie and MOVIE_GROUP_LINK:

            text = (
                "🎬 <b>Movie detected</b>\n\n"
                f"🔎 <b>{escape_html(query)}</b>\n\n"
                "This looks like a movie rather than a series.\n"
                "Go to our movie group to search for it."
            )

            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="🎬 MOVIE GROUP",
                            url=MOVIE_GROUP_LINK,
                        )
                    ]
                ]
            )

            if searching:

                try:

                    await searching.edit_text(
                        text,
                        reply_markup=markup,
                        parse_mode=enums.ParseMode.HTML,
                    )

                except Exception:
                    pass

            else:

                await message.reply_text(
                    text,
                    reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML,
                )

            return

        # ----------------------------------------------------
        # Maybe the title exists in database even if IMDb
        # doesn't return it.
        # ----------------------------------------------------

        files = await get_all_series_files(
            query
        )

        if not files:

            if searching:

                try:

                    await searching.edit_text(
                        "❌ <b>No series found.</b>\n\n"
                        "Try the series name again.",
                        parse_mode=enums.ParseMode.HTML,
                    )

                except Exception:
                    pass

            return

        # ----------------------------------------------------
        # Database-only fallback.
        # ----------------------------------------------------

        fake_id = (
            "db_"
            + re.sub(
                r"[^a-zA-Z0-9]",
                "",
                query.lower(),
            )[:40]
        )

        SERIES_DETAILS_CACHE[
            fake_id
        ] = {
            "id": fake_id,
            "title": clean_display_title(
                query
            ),
            "year": None,
            "rating": None,
            "genres": [],
            "plot": [],
            "poster": None,
            "seasons": {},
        }

        # We can't put arbitrary database files in IMDb
        # candidate callbacks, so create a temporary search
        # result.

        candidates = [
            {
                "id": fake_id,
                "title": clean_display_title(
                    query
                ),
                "year": None,
                "kind": "tv series",
                "is_series": True,
            }
        ]

    # --------------------------------------------------------
    # Show series choices.
    # --------------------------------------------------------

    text = (
        "📺 <b>Series Search</b>\n\n"
        f"🔎 <b>{escape_html(query)}</b>\n\n"
        "Select the series:"
    )

    markup = build_series_search_keyboard(
        candidates
    )

    if searching:

        try:

            await searching.edit_text(
                text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
            )

        except Exception:
            pass

    else:

        await message.reply_text(
            text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
        )


# ============================================================
# SERIES SELECTION
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
    # --------------------------------------------------------
    # Group restriction
    # --------------------------------------------------------

    if SERIES_CHAT_ID is None:
        await query.answer(
            "Series system is not configured.",
            show_alert=True,
        )
        return

    if (
        query.message
        and query.message.chat.id
        != SERIES_CHAT_ID
    ):
        await query.answer(
            "Series system is not available here.",
            show_alert=True,
        )
        return

    imdb_id = query.data[
        4:
    ]

    await query.answer(
        "📺 Loading series..."
    )

    # --------------------------------------------------------
    # Details
    # --------------------------------------------------------

    details = await get_imdb_details(
        imdb_id
    )

    if not details:

        # Database fallback.

        details = SERIES_DETAILS_CACHE.get(
            imdb_id,
            {},
        )

    title = details.get(
        "title",
        "Series",
    )

    # --------------------------------------------------------
    # Scan files.
    # --------------------------------------------------------

    files = await get_all_series_files(
        title
    )

    file_map = build_episode_quality_map(
        files
    )

    if not file_map:

        await query.message.edit_text(
            "<b>❌ No matching episodes found.</b>\n\n"
            f"Series: <b>{escape_html(title)}</b>\n\n"
            "The series exists in IMDb, but no matching "
            "SxxExx files were found in the database.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    # --------------------------------------------------------
    # Text
    # --------------------------------------------------------

    text = build_series_details_text(
        details,
        file_map,
    )

    # --------------------------------------------------------
    # Poster
    # --------------------------------------------------------

    if (
        SERIES_POSTER
        and details.get(
            "poster"
        )
    ):

        try:

            await query.message.delete()

        except Exception:
            pass

        try:

            await app.send_photo(
                chat_id=query.message.chat.id,
                photo=details[
                    "poster"
                ],
                caption=text,
                reply_markup=build_season_keyboard(
                    imdb_id,
                    file_map,
                ),
            )

            return

        except Exception as e:

            logger.warning(
                "[SERIES] Poster send failed: %s",
                e,
            )

    # --------------------------------------------------------
    # No poster
    # --------------------------------------------------------

    try:

        await query.message.edit_text(
            text,
            reply_markup=build_season_keyboard(
                imdb_id,
                file_map,
            ),
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception as e:

        logger.exception(
            "[SERIES] Details edit failed: %s",
            e,
        )


# ============================================================
# SEASON SELECTION
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
    parts = query.data.split(
        ":"
    )

    if len(parts) != 3:
        await query.answer(
            "Invalid season.",
            show_alert=True,
        )
        return

    imdb_id = parts[1]

    season = parse_int(
        parts[2]
    )

    if season <= 0:

        await query.answer(
            "Invalid season.",
            show_alert=True,
        )

        return

    await query.answer(
        f"📺 Loading Season {season:02d}..."
    )

    details = await get_imdb_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series",
    )

    files = await get_all_series_files(
        title
    )

    file_map = build_episode_quality_map(
        files
    )

    episode_map = file_map.get(
        season,
        {}
    )

    if not episode_map:

        await query.message.edit_text(
            f"❌ <b>Season {season:02d} "
            "has no files.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    # --------------------------------------------------------
    # Quality information
    # --------------------------------------------------------

    quality_episodes = defaultdict(
        set
    )

    for episode, qualities in episode_map.items():

        for quality in qualities:

            quality_episodes[
                quality
            ].add(
                episode
            )

    text = (
        f"📺 <b>{escape_html(title)}</b>\n\n"
        f"🎞 <b>Season {season:02d}</b>\n\n"
        f"📦 Episodes available: "
        f"<b>{len(episode_map)}</b>\n\n"
        "🎯 <b>Select Quality</b>\n"
        "The bot will send the episodes in order."
    )

    await query.message.edit_text(
        text,
        reply_markup=build_quality_keyboard(
            imdb_id,
            season,
            episode_map,
        ),
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# QUALITY SELECTION
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
    parts = query.data.split(
        ":"
    )

    if len(parts) != 4:

        await query.answer(
            "Invalid quality.",
            show_alert=True,
        )

        return

    imdb_id = parts[1]

    season = parse_int(
        parts[2]
    )

    quality = parse_int(
        parts[3]
    )

    await query.answer(
        "🚀 Starting season transfer..."
    )

    details = await get_imdb_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series",
    )

    files = await get_all_series_files(
        title
    )

    file_map = build_episode_quality_map(
        files
    )

    episode_map = file_map.get(
        season,
        {}
    )

    if not episode_map:

        await query.message.reply_text(
            "❌ No episodes found for this season."
        )

        return

    # --------------------------------------------------------
    # Count files before starting.
    # --------------------------------------------------------

    selected_episodes = []

    for episode in sorted(
        episode_map.keys()
    ):

        qualities = episode_map[
            episode
        ]

        if quality in qualities:

            selected_episodes.append(
                episode
            )

        elif quality == 0 and qualities:

            selected_episodes.append(
                episode
            )

    if not selected_episodes:

        await query.message.reply_text(
            "❌ No episodes are available "
            "in this quality."
        )

        return

    # --------------------------------------------------------
    # Start message.
    # --------------------------------------------------------

    start_message = await query.message.reply_text(
        "<b>🚀 SEASON TRANSFER STARTED</b>\n\n"
        f"📺 <b>{escape_html(title)}</b>\n"
        f"🎞 Season: <b>{season:02d}</b>\n"
        f"🎯 Quality: <b>{quality_text(quality)}</b>\n\n"
        f"📦 Episodes: <b>{len(selected_episodes)}</b>\n"
        "⏳ Sending in episode order...",
        parse_mode=enums.ParseMode.HTML,
    )

    # --------------------------------------------------------
    # Send.
    # --------------------------------------------------------

    sent = await send_season(
        app=app,
        chat_id=query.message.chat.id,
        series_title=title,
        season=season,
        quality=quality,
        episode_map=episode_map,
    )

    # --------------------------------------------------------
    # Finish message.
    # --------------------------------------------------------

    try:

        await start_message.edit_text(
            "<b>✅ SEASON TRANSFER COMPLETE</b>\n\n"
            f"📺 <b>{escape_html(title)}</b>\n"
            f"🎞 Season: <b>{season:02d}</b>\n"
            f"🎯 Quality: <b>{quality_text(quality)}</b>\n\n"
            f"📦 Sent: <b>{sent}</b>/"
            f"<b>{len(selected_episodes)}</b>\n\n"
            "🎉 Enjoy!",
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
async def back_callback(
    app,
    query,
):
    imdb_id = query.data[
        5:
    ]

    await query.answer(
        "🔄 Loading..."
    )

    details = await get_imdb_details(
        imdb_id
    )

    if not details:

        await query.message.edit_text(
            "❌ Series information expired. "
            "Please search again."
        )

        return

    title = details.get(
        "title",
        "Series",
    )

    files = await get_all_series_files(
        title
    )

    file_map = build_episode_quality_map(
        files
    )

    if not file_map:

        await query.message.edit_text(
            "❌ No episodes found.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    text = build_series_details_text(
        details,
        file_map,
    )

    # --------------------------------------------------------
    # Poster disabled / avoid sending another poster here.
    #
    # This callback edits the existing message.
    # --------------------------------------------------------

    try:

        await query.message.edit_text(
            text,
            reply_markup=build_season_keyboard(
                imdb_id,
                file_map,
            ),
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:

        pass


# ============================================================
# CACHE CLEARER
# ============================================================


async def clear_series_cache():
    """
    Clears only this plugin's memory cache.

    It does NOT modify MongoDB.
    """

    IMDB_CACHE.clear()

    SERIES_DETAILS_CACHE.clear()

    SERIES_FILE_CACHE.clear()

    logger.info(
        "[SERIES] Memory caches cleared."
    )


# ============================================================
# STARTUP LOG
# ============================================================


logger.info(
    "=================================================="
)

logger.info(
    "[SERIES] DowntownVilla Series System Loaded"
)

logger.info(
    "[SERIES] Group: %s",
    SERIES_CHAT_ID,
)

logger.info(
    "[SERIES] IMDb: %s",
    "AVAILABLE"
    if IMDB_AVAILABLE
    else "UNAVAILABLE",
)

logger.info(
    "[SERIES] Poster: %s",
    SERIES_POSTER,
)

logger.info(
    "[SERIES] Movie group: %s",
    MOVIE_GROUP_LINK
    or "NOT SET",
)

logger.info(
    "=================================================="
)
