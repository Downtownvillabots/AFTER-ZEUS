# ============================================================
# DOWN TOWN VILLA - SERIES SYSTEM
# ============================================================
#
# FLOW:
#
# SERIES GROUP
#       |
#       | User types:
#       | GOT
#       v
# IMDb SEARCH
#       |
#       | Game of Thrones
#       v
# IMDb DETAILS
#       |
#       | "Game of Thrones" button
#       v
# PRIVATE MESSAGE
#       |
#       | Language
#       v
# English / Malayalam / Hindi
#       |
#       | Season
#       v
# S01 / S02 / S03...
#       |
#       | Quality
#       v
# 1080p / 720p / 480p...
#       |
#       | GET FILES
#       v
# TARGETED MONGODB SEARCH
#       |
#       v
# Episode 1
# Episode 2
# Episode 3
# ...
#
# IMPORTANT:
# MongoDB is NOT scanned during normal title searching.
# MongoDB is searched only after GET FILES.
#
# ============================================================

import os
import re
import time
import asyncio
import logging
from collections import defaultdict
from html import escape
from urllib.parse import quote_plus

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.errors import (
    FloodWait,
    RPCError,
)

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

# ------------------------------------------------------------
# ONLY THIS GROUP USES THE SERIES SYSTEM
# ------------------------------------------------------------

SERIES_GROUP_ID = os.getenv(
    "SERIES_GROUP_ID",
    ""
).strip()


# ------------------------------------------------------------
# BOT USERNAME
#
# Example:
#
# BOT_USERNAME=DowntownVillaBot
#
# Do NOT put @ here.
# ------------------------------------------------------------

BOT_USERNAME = os.getenv(
    "BOT_USERNAME",
    ""
).strip().lstrip("@")


# ------------------------------------------------------------
# MOVIE GROUP
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
# POSTER
#
# SERIES_POSTER=true
# SERIES_POSTER=false
# ------------------------------------------------------------

SERIES_POSTER = os.getenv(
    "SERIES_POSTER",
    "true",
).lower() in (
    "true",
    "1",
    "yes",
    "on",
)


# ------------------------------------------------------------
# CAPTION
#
# Available variables:
#
# {series}
# {language}
# {season}
# {episode}
# {quality}
# {filename}
#
# Example:
#
# SERIES_CAPTION=🎬 {series}
# 📺 Season {season}
# 🎞 Episode {episode}
# 🌐 {language}
# 🎯 {quality}
#
# Empty = use original Telegram caption.
# ------------------------------------------------------------

SERIES_CAPTION = os.getenv(
    "SERIES_CAPTION",
    "",
)


# ------------------------------------------------------------
# IMDb SEARCH RESULT LIMIT
# ------------------------------------------------------------

IMDB_SEARCH_LIMIT = int(
    os.getenv(
        "SERIES_SEARCH_LIMIT",
        "6",
    )
)


# ------------------------------------------------------------
# SEARCH CACHE TTL
#
# IMDb results are cached.
#
# This dramatically reduces repeated requests.
# ------------------------------------------------------------

IMDB_CACHE_TTL = int(
    os.getenv(
        "SERIES_IMDB_CACHE_TTL",
        "1800",
    )
)


# ------------------------------------------------------------
# FILE SEARCH CACHE TTL
#
# Search results after GET FILES can also be cached.
# ------------------------------------------------------------

FILE_CACHE_TTL = int(
    os.getenv(
        "SERIES_FILE_CACHE_TTL",
        "300",
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
# MAX DATABASE RESULTS READ
#
# This prevents accidental huge result loading.
# ------------------------------------------------------------

SERIES_DB_LIMIT = int(
    os.getenv(
        "SERIES_DB_LIMIT",
        "300",
    )
)


# ============================================================
# GROUP ID
# ============================================================

def get_series_group_id():

    if not SERIES_GROUP_ID:
        return None

    try:
        return int(
            SERIES_GROUP_ID
        )

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
        "[SERIES] IMDbPy unavailable: %s",
        e,
    )


# ============================================================
# CACHE
# ============================================================

# {
#   normalized_query: {
#       "time": timestamp,
#       "data": [...]
#   }
# }

IMDB_SEARCH_CACHE = {}


# {
#   imdb_id: {
#       "time": timestamp,
#       "data": {...}
#   }
# }

IMDB_DETAILS_CACHE = {}


# {
#   search_key: {
#       "time": timestamp,
#       "data": [...]
#   }
# }

FILE_SEARCH_CACHE = {}


# ============================================================
# USER SESSION
# ============================================================
#
# Only very small strings are stored.
#
# Example:
#
# {
#   user_id: {
#       "imdb_id": "...",
#       "title": "Game of Thrones",
#       "language": "eng",
#       "season": "S01",
#       "quality": "1080p"
#   }
# }
#
# This is RAM only.
# ============================================================

USER_SERIES_STATE = {}


# ============================================================
# LANGUAGES
# ============================================================

LANGUAGES = {
    "eng": "🇬🇧 English",
    "mal": "🇮🇳 Malayalam",
    "hin": "🇮🇳 Hindi",
}


LANGUAGE_SEARCH_WORDS = {
    "eng": [
        "eng",
        "english",
        "en",
    ],
    "mal": [
        "mal",
        "malayalam",
        "ml",
    ],
    "hin": [
        "hin",
        "hindi",
        "hi",
    ],
}


# ============================================================
# QUALITY
# ============================================================

QUALITY_VALUES = [
    2160,
    1440,
    1080,
    720,
    576,
    480,
    360,
    240,
    180,
]


def quality_text(
    quality,
):

    quality = int(
        quality
    )

    if quality == 2160:
        return "4K"

    return f"{quality}p"


# ============================================================
# NORMALIZE TEXT
# ============================================================

def normalize_title(
    text,
):

    if not text:
        return ""

    text = str(
        text
    ).lower()

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

    return text.strip()


# ============================================================
# DISPLAY TITLE
# ============================================================

def display_title(
    title,
):

    if not title:
        return ""

    title = normalize_title(
        title
    )

    return " ".join(
        word.capitalize()
        for word in title.split()
    )


# ============================================================
# ESCAPE
# ============================================================

def html_escape(
    value,
):

    if value is None:
        return ""

    return escape(
        str(value)
    )


# ============================================================
# PARSE INTEGER
# ============================================================

def to_int(
    value,
    default=0,
):

    try:
        return int(
            value
        )

    except Exception:
        return default


# ============================================================
# FORMAT RATING
# ============================================================

def format_rating(
    rating,
):

    if rating is None:
        return "N/A"

    try:
        return f"{float(rating):.1f}/10"

    except Exception:
        return "N/A"


# ============================================================
# EXTRACT SEASON / EPISODE
# ============================================================

def extract_season_episode(
    filename,
):

    if not filename:
        return None, None

    name = str(
        filename
    )

    # --------------------------------------------------------
    # S01E01
    # S1E1
    # S01.E01
    # S01-E01
    # S01 E01
    # --------------------------------------------------------

    match = re.search(
        r"(?i)(?:^|[\s._\-\[\]()])"
        r"S(\d{1,2})"
        r"[\s._\-]*"
        r"E(\d{1,3})"
        r"(?:$|[\s._\-\[\]()])",
        name,
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
        r"(?i)\bS(\d{1,2})"
        r"[\s._\-]*"
        r"EP(?:ISODE)?"
        r"[\s._\-]*"
        r"(\d{1,3})\b",
        name,
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
    # Season 1 Episode 1
    # --------------------------------------------------------

    match = re.search(
        r"(?i)\bSeason"
        r"[\s._\-]*(\d{1,2})"
        r"[\s._\-]*"
        r"(?:Episode|Ep|E)"
        r"[\s._\-]*"
        r"(\d{1,3})\b",
        name,
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
# EXTRACT QUALITY
# ============================================================

def extract_quality(
    filename,
):

    if not filename:
        return 0

    name = str(
        filename
    )

    for quality in QUALITY_VALUES:

        pattern = rf"(?i)\b{quality}p\b"

        if re.search(
            pattern,
            name,
        ):

            return quality

    if re.search(
        r"(?i)\b4k\b",
        name,
    ):
        return 2160

    if re.search(
        r"(?i)\buhd\b",
        name,
    ):
        return 2160

    if re.search(
        r"(?i)\bhd\b",
        name,
    ):
        return 720

    return 0


# ============================================================
# LANGUAGE DETECTION
# ============================================================

def detect_language(
    filename,
    caption="",
):

    text = (
        f"{filename or ''} "
        f"{caption or ''}"
    )

    text = normalize_title(
        text
    )

    # Malayalam first because it can have
    # different common naming forms.

    if re.search(
        r"(?i)(^|\s)(mal|malayalam|ml)(\s|$)",
        text,
    ):
        return "mal"

    if re.search(
        r"(?i)(^|\s)(hin|hindi|hi)(\s|$)",
        text,
    ):
        return "hin"

    if re.search(
        r"(?i)(^|\s)(eng|english|en)(\s|$)",
        text,
    ):
        return "eng"

    return ""


# ============================================================
# TITLE REGEX
# ============================================================
#
# Converts:
#
# Game of Thrones
#
# into a regex that can match:
#
# Game.of.Thrones
# Game_of_Thrones
# Game-of-Thrones
# Game of Thrones
#
# without loading the entire collection into Python.
# ============================================================

def title_to_regex(
    title,
):

    words = re.findall(
        r"[a-zA-Z0-9]+",
        str(title),
    )

    if not words:
        return ""

    parts = []

    for word in words:

        parts.append(
            re.escape(
                word
            )
        )

    return r"[\W_]+".join(
        parts
    )


# ============================================================
# FILE ID EXTRACTION
# ============================================================
#
# IMPORTANT:
#
# Telegram cached media normally uses "file_id".
#
# We try the common database field names.
# ============================================================

def get_file_id(
    document,
):

    for key in (
        "file_id",
        "media_id",
        "telegram_file_id",
    ):

        value = document.get(
            key
        )

        if value:
            return str(
                value
            )

    return ""


# ============================================================
# FILE SCORE
# ============================================================

def file_score(
    document,
):

    filename = str(
        document.get(
            "file_name",
            "",
        )
    )

    quality = extract_quality(
        filename
    )

    size = to_int(
        document.get(
            "file_size",
            0,
        )
    )

    # Quality dominates.
    # Size is only a tie-breaker.

    return (
        quality * 1_000_000_000
        + min(
            size,
            999_999_999,
        )
    )


# ============================================================
# GET MONGO COLLECTIONS
# ============================================================

def get_collections():

    collections = []

    try:

        collections.append(
            (
                "Media",
                db[
                    COLLECTION_NAME
                ],
            )
        )

    except Exception as e:

        logger.warning(
            "[SERIES] Media collection unavailable: %s",
            e,
        )

    if MULTIPLE_DB:

        try:

            collections.append(
                (
                    "Media2",
                    db2[
                        COLLECTION_NAME
                    ],
                )
            )

        except Exception as e:

            logger.warning(
                "[SERIES] Media2 unavailable: %s",
                e,
            )

        try:

            collections.append(
                (
                    "Media3",
                    db3[
                        COLLECTION_NAME
                    ],
            )
            )

        except Exception as e:

            logger.warning(
                "[SERIES] Media3 unavailable: %s",
                e,
            )

    return collections


# ============================================================
# BUILD TARGETED MONGO QUERY
# ============================================================

def build_file_query(
    title,
    language,
    season,
    quality,
):

    title_regex = title_to_regex(
        title
    )

    season_number = to_int(
        str(
            season
        ).replace(
            "S",
            "",
        )
    )

    # --------------------------------------------------------
    # S01 / S1 forms
    # --------------------------------------------------------

    season_regex = (
        rf"(?i)(?:"
        rf"\bS{season_number:02d}\b"
        rf"|"
        rf"\bS{season_number}\b"
        rf"|"
        rf"\bSeason[\s._\-]*{season_number}\b"
        rf")"
    )

    # --------------------------------------------------------
    # Quality
    # --------------------------------------------------------

    quality_number = to_int(
        str(
            quality
        ).replace(
            "p",
            "",
        )
    )

    quality_regex = (
        rf"(?i)\b"
        rf"{quality_number}"
        rf"p\b"
    )

    # --------------------------------------------------------
    # Language
    # --------------------------------------------------------

    language_words = (
        LANGUAGE_SEARCH_WORDS.get(
            language,
            [],
        )
    )

    language_regex = (
        r"(?i)(?:^|[\s._\-\[\]()])"
        r"(?:"
        + "|".join(
            re.escape(
                word
            )
            for word in language_words
        )
        + r")"
        r"(?:$|[\s._\-\[\]()])"
    )

    # --------------------------------------------------------
    # Search both filename and caption.
    # --------------------------------------------------------

    return {
        "$and": [
            {
                "$or": [
                    {
                        "file_name": {
                            "$regex": title_regex,
                        }
                    },
                    {
                        "caption": {
                            "$regex": title_regex,
                        }
                    },
                ]
            },
            {
                "$or": [
                    {
                        "file_name": {
                            "$regex": season_regex,
                        }
                    },
                    {
                        "caption": {
                            "$regex": season_regex,
                        }
                    },
                ]
            },
            {
                "$or": [
                    {
                        "file_name": {
                            "$regex": quality_regex,
                        }
                    },
                    {
                        "caption": {
                            "$regex": quality_regex,
                        }
                    },
                ]
            },
            {
                "$or": [
                    {
                        "file_name": {
                            "$regex": language_regex,
                        }
                    },
                    {
                        "caption": {
                            "$regex": language_regex,
                        }
                    },
                ]
            },
        ]
    }


# ============================================================
# FAST TARGETED FILE SEARCH
# ============================================================
#
# THIS IS THE ONLY PLACE WHERE DATABASE FILE SEARCH HAPPENS.
#
# We DO NOT:
#
# collection.find({})
#
# We search using the exact selected values:
#
# Game of Thrones
# ENG
# S01
# 1080p
#
# ============================================================

async def search_files(
    title,
    language,
    season,
    quality,
):

    cache_key = (
        normalize_title(
            title
        ),
        language,
        season,
        quality,
    )

    cached = FILE_SEARCH_CACHE.get(
        cache_key
    )

    if cached:

        if (
            time.monotonic()
            - cached["time"]
            < FILE_CACHE_TTL
        ):

            return cached["data"]

    mongo_query = build_file_query(
        title=title,
        language=language,
        season=season,
        quality=quality,
    )

    results = []

    projection = {
        "_id": 1,
        "file_id": 1,
        "media_id": 1,
        "telegram_file_id": 1,
        "file_name": 1,
        "file_size": 1,
        "file_type": 1,
        "mime_type": 1,
        "caption": 1,
        "file_ref": 1,
    }

    # --------------------------------------------------------
    # Media first
    # --------------------------------------------------------

    for source_name, collection in get_collections():

        try:

            cursor = (
                collection
                .find(
                    mongo_query,
                    projection,
                )
                .limit(
                    SERIES_DB_LIMIT
                )
            )

            async for document in cursor:

                filename = str(
                    document.get(
                        "file_name",
                        "",
                    )
                )

                if not filename:
                    continue

                file_id = get_file_id(
                    document
                )

                if not file_id:
                    continue

                season_number, episode = (
                    extract_season_episode(
                        filename
                    )
                )

                if (
                    season_number is None
                    or episode is None
                ):
                    continue

                detected_quality = (
                    extract_quality(
                        filename
                    )
                )

                if (
                    detected_quality
                    != to_int(
                        str(
                            quality
                        ).replace(
                            "p",
                            "",
                        )
                    )
                ):
                    continue

                detected_language = (
                    detect_language(
                        filename,
                        document.get(
                            "caption",
                            "",
                        ),
                    )
                )

                if (
                    detected_language
                    != language
                ):
                    continue

                document[
                    "_source"
                ] = source_name

                document[
                    "_episode"
                ] = episode

                document[
                    "_season"
                ] = season_number

                document[
                    "_quality"
                ] = detected_quality

                results.append(
                    document
                )

        except Exception as e:

            logger.exception(
                "[SERIES] Search error in %s: %s",
                source_name,
                e,
            )

    # --------------------------------------------------------
    # One BEST file per episode
    # --------------------------------------------------------

    best_by_episode = {}

    for document in results:

        episode = document.get(
            "_episode"
        )

        if episode is None:
            continue

        existing = (
            best_by_episode.get(
                episode
            )
        )

        if (
            existing is None
            or file_score(
                document
            )
            > file_score(
                existing
            )
        ):

            best_by_episode[
                episode
            ] = document

    final = [
        best_by_episode[
            episode
        ]
        for episode in sorted(
            best_by_episode.keys()
        )
    ]

    if (
        SERIES_MAX_EPISODES
        > 0
    ):

        final = final[
            :SERIES_MAX_EPISODES
        ]

    FILE_SEARCH_CACHE[
        cache_key
    ] = {
        "time": time.monotonic(),
        "data": final,
    }

    return final


# ============================================================
# IMDb SEARCH
# ============================================================

async def imdb_search(
    text,
):

    if not IMDB_AVAILABLE:
        return []

    normalized = normalize_title(
        text
    )

    if not normalized:
        return []

    cached = (
        IMDB_SEARCH_CACHE.get(
            normalized
        )
    )

    if cached:

        if (
            time.monotonic()
            - cached["time"]
            < IMDB_CACHE_TTL
        ):

            return cached["data"]

    try:

        # IMDbPy is synchronous.
        # Put it in a worker thread so the Telegram
        # event loop does not freeze.

        raw_results = (
            await asyncio.to_thread(
                imdb_api.search_movie,
                text,
            )
        )

    except Exception as e:

        logger.warning(
            "[SERIES] IMDb search failed: %s",
            e,
        )

        return []

    output = []

    for item in raw_results[
        :IMDB_SEARCH_LIMIT
    ]:

        title = item.get(
            "title"
        )

        if not title:
            continue

        kind = item.get(
            "kind",
            "",
        )

        imdb_id = item.get(
            "movieID"
        )

        year = item.get(
            "year"
        )

        # ----------------------------------------------------
        # ONLY SERIES
        # ----------------------------------------------------

        is_series = (
            kind in (
                "tv series",
                "tv mini series",
                "tv special",
            )
        )

        output.append(
            {
                "id": str(
                    imdb_id
                )
                if imdb_id
                else "",
                "title": str(
                    title
                ),
                "year": year,
                "kind": kind,
                "is_series": is_series,
            }
        )

    IMDB_SEARCH_CACHE[
        normalized
    ] = {
        "time": time.monotonic(),
        "data": output,
    }

    return output


# ============================================================
# IMDb DETAILS
# ============================================================

async def imdb_details(
    imdb_id,
):

    if not imdb_id:
        return {}

    cached = (
        IMDB_DETAILS_CACHE.get(
            imdb_id
        )
    )

    if cached:

        if (
            time.monotonic()
            - cached["time"]
            < IMDB_CACHE_TTL
        ):

            return cached["data"]

    if not IMDB_AVAILABLE:
        return {}

    try:

        movie = (
            await asyncio.to_thread(
                imdb_api.get_movie,
                int(
                    imdb_id
                ),
            )
        )

    except Exception as e:

        logger.warning(
            "[SERIES] IMDb details failed: %s",
            e,
        )

        return {}

    genres = movie.get(
        "genres",
        [],
    )

    plot = movie.get(
        "plot",
        [],
    )

    cover = movie.get(
        "full-size cover url"
    )

    if not cover:

        cover = movie.get(
            "cover url"
        )

    # --------------------------------------------------------
    # We intentionally DO NOT call imdb.update(movie, "episodes")
    #
    # That operation is expensive and was one of the causes
    # of the old slow/hanging behaviour.
    #
    # We show seasons based on files later.
    # --------------------------------------------------------

    data = {
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
        "genres": genres,
        "plot": plot,
        "poster": cover,
    }

    IMDB_DETAILS_CACHE[
        imdb_id
    ] = {
        "time": time.monotonic(),
        "data": data,
    }

    return data


# ============================================================
# CALLBACKS
# ============================================================

def cb_series(
    imdb_id,
):

    return (
        f"series:{imdb_id}"
    )


def cb_language(
    imdb_id,
    language,
):

    return (
        f"lang:{imdb_id}:{language}"
    )


def cb_season(
    imdb_id,
    language,
    season,
):

    return (
        f"season:"
        f"{imdb_id}:"
        f"{language}:"
        f"{season}"
    )


def cb_quality(
    imdb_id,
    language,
    season,
    quality,
):

    return (
        f"quality:"
        f"{imdb_id}:"
        f"{language}:"
        f"{season}:"
        f"{quality}"
    )


def cb_get_files(
    imdb_id,
    language,
    season,
    quality,
):

    return (
        f"getfiles:"
        f"{imdb_id}:"
        f"{language}:"
        f"{season}:"
        f"{quality}"
    )


# ============================================================
# SERIES BUTTONS
# ============================================================

def series_buttons(
    results,
):

    rows = []

    for item in results:

        title = item.get(
            "title",
            "Unknown",
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
                    text=text[
                        :64
                    ],
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
        rows
    )


# ============================================================
# LANGUAGE BUTTONS
# ============================================================

def language_buttons(
    imdb_id,
):

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🇬🇧 English",
                    callback_data=cb_language(
                        imdb_id,
                        "eng",
                    ),
                ),
                InlineKeyboardButton(
                    "🇮🇳 Malayalam",
                    callback_data=cb_language(
                        imdb_id,
                        "mal",
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    "🇮🇳 Hindi",
                    callback_data=cb_language(
                        imdb_id,
                        "hin",
                    ),
                ),
            ],
        ]
    )


# ============================================================
# SEASON BUTTONS
# ============================================================

def season_buttons(
    imdb_id,
    language,
    seasons,
):

    rows = []

    row = []

    for season in seasons:

        row.append(
            InlineKeyboardButton(
                text=f"📺 S{season:02d}",
                callback_data=cb_season(
                    imdb_id,
                    language,
                    season,
                ),
            )
        )

        if len(row) == 3:

            rows.append(
                row
            )

            row = []

    if row:
        rows.append(
            row
        )

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# QUALITY BUTTONS
# ============================================================

def quality_buttons(
    imdb_id,
    language,
    season,
):

    rows = []

    row = []

    for quality in (
        2160,
        1080,
        720,
        576,
        480,
        360,
        240,
        180,
    ):

        row.append(
            InlineKeyboardButton(
                text=quality_text(
                    quality
                ),
                callback_data=cb_quality(
                    imdb_id,
                    language,
                    season,
                    quality,
                ),
            )
        )

        if len(row) == 3:

            rows.append(
                row
            )

            row = []

    if row:
        rows.append(
            row
        )

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# GET FILE BUTTON
# ============================================================

def get_files_button(
    imdb_id,
    language,
    season,
    quality,
):

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="📂 GET FILES",
                    callback_data=cb_get_files(
                        imdb_id,
                        language,
                        season,
                        quality,
                    ),
                )
            ]
        ]
    )


# ============================================================
# BUILD IMDb DETAILS
# ============================================================

def details_text(
    details,
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

    text = (
        "📺 <b>SERIES INFORMATION</b>\n\n"
        f"🎬 <b>{html_escape(title)}</b>"
    )

    if year:
        text += (
            f" ({year})"
        )

    text += "\n\n"

    text += (
        f"⭐ IMDb Rating: "
        f"<b>{rating}</b>\n"
    )

    if genres:

        text += (
            "🎭 Genres: "
            f"<b>{html_escape(', '.join(genres[:5]))}</b>\n"
        )

    if plot:

        plot_text = str(
            plot[0]
        )

        if len(
            plot_text
        ) > 450:

            plot_text = (
                plot_text[
                    :450
                ]
                + "..."
            )

        text += (
            "\n📝 "
            f"{html_escape(plot_text)}\n"
        )

    return text


# ============================================================
# DEEP LINK
# ============================================================

def make_pm_link(
    imdb_id,
):

    if not BOT_USERNAME:
        return ""

    payload = (
        f"series_{imdb_id}"
    )

    return (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start="
        f"{quote_plus(payload)}"
    )


# ============================================================
# SERIES SEARCH HANDLER
# ============================================================
#
# ONLY SERIES GROUP.
#
# VERY IMPORTANT:
#
# This handler does not search MongoDB.
# It only asks IMDb for suggestions.
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
async def downtownvilla_series_search(
    client,
    message,
):

    # --------------------------------------------------------
    # Group restriction
    # --------------------------------------------------------

    if (
        SERIES_CHAT_ID is None
        or message.chat.id
        != SERIES_CHAT_ID
    ):
        return

    query_text = (
        message.text or ""
    ).strip()

    if not query_text:
        return

    if query_text.startswith(
        "/"
    ):
        return

    # --------------------------------------------------------
    # Tell other normal handlers to STOP here.
    #
    # This is essential because you said the old normal
    # search was also returning results in the series group.
    #
    # The plugin must be loaded with a suitable group/order
    # so this handler gets the message before the normal
    # search handler.
    # --------------------------------------------------------

    try:

        searching = (
            await message.reply_text(
                "🔎 <b>Finding series...</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        )

    except Exception:

        searching = None

    # --------------------------------------------------------
    # IMDb ONLY
    # --------------------------------------------------------

    results = await imdb_search(
        query_text
    )

    # --------------------------------------------------------
    # Keep ONLY TV SERIES.
    # --------------------------------------------------------

    series_results = [
        item
        for item in results
        if item.get(
            "is_series"
        )
    ]

    # --------------------------------------------------------
    # Movie results are NOT shown as series.
    # --------------------------------------------------------

    if not series_results:

        movie_found = any(
            not item.get(
                "is_series"
            )
            for item in results
        )

        if (
            movie_found
            and MOVIE_GROUP_LINK
        ):

            text = (
                "🎬 <b>Movie detected</b>\n\n"
                f"🔎 {html_escape(query_text)}\n\n"
                "This looks like a movie.\n"
                "Use our movie group to search for it."
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

        else:

            text = (
                "❌ <b>No series found.</b>\n\n"
                "Try another series name."
            )

            markup = None

        if searching:

            try:

                await searching.edit_text(
                    text,
                    reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML,
                )

            except Exception:
                pass

        return

    # --------------------------------------------------------
    # SHOW IMDb suggestions.
    # --------------------------------------------------------

    text = (
        "📺 <b>Series Search</b>\n\n"
        f"🔎 <b>{html_escape(query_text)}</b>\n\n"
        "Select the correct series:"
    )

    markup = series_buttons(
        series_results
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


# ============================================================
# IMDb SERIES SELECTION
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^series:"
    )
)
async def downtownvilla_series_selected(
    client,
    query,
):

    imdb_id = query.data[
        7:
    ]

    if not imdb_id:

        await query.answer(
            "Invalid series.",
            show_alert=True,
        )

        return

    await query.answer(
        "📺 Loading..."
    )

    details = await imdb_details(
        imdb_id
    )

    if not details:

        await query.message.edit_text(
            "❌ Unable to load IMDb information.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    title = details.get(
        "title",
        "Series",
    )

    text = details_text(
        details
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # This button opens the user's PRIVATE chat with the bot.
    # --------------------------------------------------------

    pm_link = make_pm_link(
        imdb_id
    )

    buttons = []

    if pm_link:

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"📺 {title}",
                    url=pm_link,
                )
            ]
        )

    if MOVIE_GROUP_LINK:

        buttons.append(
            [
                InlineKeyboardButton(
                    text="🎬 Movie Group",
                    url=MOVIE_GROUP_LINK,
                )
            ]
        )

    markup = (
        InlineKeyboardMarkup(
            buttons
        )
        if buttons
        else None
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

            await client.send_photo(
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
    # TEXT ONLY
    # --------------------------------------------------------

    try:

        await query.message.edit_text(
            text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception as e:

        logger.warning(
            "[SERIES] Details display failed: %s",
            e,
        )


# ============================================================
# PRIVATE START HANDLER
# ============================================================
#
# /start series_IMDBID
#
# This is where the actual series selection begins.
# ============================================================

@Client.on_message(
    filters.command(
        "start"
    ),
    group=-90,
)
async def downtownvilla_series_start(
    client,
    message,
):

    if not message.from_user:
        return

    if (
        not message.command
        or len(
            message.command
        ) < 2
    ):

        return

    payload = (
        message.command[1]
    )

    if not payload.startswith(
        "series_"
    ):

        return

    imdb_id = payload[
        7:
    ]

    if not imdb_id:
        return

    details = await imdb_details(
        imdb_id
    )

    if not details:

        await message.reply_text(
            "❌ Unable to load series information."
        )

        return

    title = details.get(
        "title",
        "Series",
    )

    # --------------------------------------------------------
    # STORE ONLY SMALL DATA.
    # --------------------------------------------------------

    USER_SERIES_STATE[
        message.from_user.id
    ] = {
        "imdb_id": imdb_id,
        "title": title,
        "language": "",
        "season": "",
        "quality": "",
    }

    text = (
        details_text(
            details
        )
        + "\n\n"
        "🌐 <b>Select Language</b>"
    )

    await message.reply_text(
        text,
        reply_markup=language_buttons(
            imdb_id
        ),
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# LANGUAGE SELECTION
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^lang:"
    )
)
async def downtownvilla_language(
    client,
    query,
):

    parts = query.data.split(
        ":"
    )

    if len(parts) != 3:
        return

    imdb_id = parts[1]

    language = parts[2]

    if language not in LANGUAGES:
        return

    user_id = (
        query.from_user.id
    )

    details = await imdb_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series",
    )

    # --------------------------------------------------------
    # Store PLAIN TEXT values only.
    # --------------------------------------------------------

    USER_SERIES_STATE[
        user_id
    ] = {
        "imdb_id": imdb_id,
        "title": title,
        "language": language,
        "season": "",
        "quality": "",
    }

    await query.answer(
        LANGUAGES[
            language
        ]
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # We DO NOT scan MongoDB here.
    #
    # Seasons are initially generated from IMDb data only
    # when available through lightweight metadata.
    #
    # Since we don't call expensive IMDb episode updates,
    # show a configurable season range.
    #
    # The actual file existence is checked ONLY at GET FILES.
    # --------------------------------------------------------

    seasons = list(
        range(
            1,
            11,
        )
    )

    text = (
        f"📺 <b>{html_escape(title)}</b>\n\n"
        f"🌐 Language: "
        f"<b>{LANGUAGES[language]}</b>\n\n"
        "📚 <b>Select Season</b>"
    )

    await query.message.edit_text(
        text,
        reply_markup=season_buttons(
            imdb_id,
            language,
            seasons,
        ),
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# SEASON SELECTION
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^season:"
    )
)
async def downtownvilla_season(
    client,
    query,
):

    parts = query.data.split(
        ":"
    )

    if len(parts) != 4:
        return

    imdb_id = parts[1]

    language = parts[2]

    season = to_int(
        parts[3]
    )

    if (
        season <= 0
        or language
        not in LANGUAGES
    ):

        await query.answer(
            "Invalid selection.",
            show_alert=True,
        )

        return

    user_id = (
        query.from_user.id
    )

    details = await imdb_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series",
    )

    state = USER_SERIES_STATE.get(
        user_id,
        {},
    )

    state.update(
        {
            "imdb_id": imdb_id,
            "title": title,
            "language": language,
            "season": f"S{season:02d}",
            "quality": "",
        }
    )

    USER_SERIES_STATE[
        user_id
    ] = state

    await query.answer(
        f"Season {season}"
    )

    text = (
        f"📺 <b>{html_escape(title)}</b>\n\n"
        f"🌐 Language: "
        f"<b>{LANGUAGES[language]}</b>\n"
        f"📚 Season: "
        f"<b>S{season:02d}</b>\n\n"
        "🎯 <b>Select Quality</b>"
    )

    await query.message.edit_text(
        text,
        reply_markup=quality_buttons(
            imdb_id,
            language,
            season,
        ),
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# QUALITY SELECTION
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^quality:"
    )
)
async def downtownvilla_quality(
    client,
    query,
):

    parts = query.data.split(
        ":"
    )

    if len(parts) != 5:
        return

    imdb_id = parts[1]

    language = parts[2]

    season = parts[3]

    quality = to_int(
        parts[4]
    )

    if (
        quality <= 0
        or language
        not in LANGUAGES
    ):

        await query.answer(
            "Invalid quality.",
            show_alert=True,
        )

        return

    user_id = (
        query.from_user.id
    )

    details = await imdb_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series",
    )

    # --------------------------------------------------------
    # Store ONLY strings.
    # --------------------------------------------------------

    USER_SERIES_STATE[
        user_id
    ] = {
        "imdb_id": imdb_id,
        "title": title,
        "language": language,
        "season": season,
        "quality": f"{quality}p",
    }

    await query.answer(
        quality_text(
            quality
        )
    )

    text = (
        "📦 <b>FILE SEARCH READY</b>\n\n"
        f"📺 Series: "
        f"<b>{html_escape(title)}</b>\n"
        f"🌐 Language: "
        f"<b>{LANGUAGES[language]}</b>\n"
        f"📚 Season: "
        f"<b>{html_escape(season)}</b>\n"
        f"🎯 Quality: "
        f"<b>{quality_text(quality)}</b>\n\n"
        "The bot will search using exactly these "
        "selected values.\n\n"
        "Press <b>GET FILES</b> to search."
    )

    await query.message.edit_text(
        text,
        reply_markup=get_files_button(
            imdb_id,
            language,
            season,
            quality,
        ),
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# GET FILES
# ============================================================
#
# THIS IS THE ONLY EXPENSIVE PART.
#
# Search:
#
# Game of Thrones
# ENG
# S01
# 1080p
#
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^getfiles:"
    )
)
async def downtownvilla_get_files(
    client,
    query,
):

    parts = query.data.split(
        ":"
    )

    if len(parts) != 5:

        await query.answer(
            "Invalid request.",
            show_alert=True,
        )

        return

    imdb_id = parts[1]

    language = parts[2]

    season = parts[3]

    quality = to_int(
        parts[4]
    )

    details = await imdb_details(
        imdb_id
    )

    if not details:

        await query.answer(
            "Series information unavailable.",
            show_alert=True,
        )

        return

    title = details.get(
        "title",
        "Series",
    )

    await query.answer(
        "🔎 Searching files..."
    )

    # --------------------------------------------------------
    # Show searching message.
    # --------------------------------------------------------

    try:

        await query.message.edit_text(
            "🔎 <b>SEARCHING FILES</b>\n\n"
            f"📺 {html_escape(title)}\n"
            f"🌐 {LANGUAGES.get(language, language)}\n"
            f"📚 {html_escape(season)}\n"
            f"🎯 {quality_text(quality)}\n\n"
            "⏳ Please wait...",
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:
        pass

    # --------------------------------------------------------
    # ACTUAL MONGO SEARCH
    # --------------------------------------------------------

    started = time.monotonic()

    files = await search_files(
        title=title,
        language=language,
        season=season,
        quality=quality,
    )

    elapsed = (
        time.monotonic()
        - started
    )

    # --------------------------------------------------------
    # NO FILES
    # --------------------------------------------------------

    if not files:

        await query.message.edit_text(
            "❌ <b>NO FILES FOUND</b>\n\n"
            f"📺 {html_escape(title)}\n"
            f"🌐 {LANGUAGES.get(language, language)}\n"
            f"📚 {html_escape(season)}\n"
            f"🎯 {quality_text(quality)}\n\n"
            "Try another language, season or quality.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    # --------------------------------------------------------
    # RESULT BUTTONS
    #
    # One result per episode.
    # --------------------------------------------------------

    buttons = []

    for document in files:

        episode = document.get(
            "_episode"
        )

        filename = document.get(
            "file_name",
            f"Episode {episode}",
        )

        file_id = get_file_id(
            document
        )

        if not file_id:
            continue

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # callback contains only file ID.
        # ----------------------------------------------------

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"🎞 E{episode:02d} • "
                        f"{quality_text(quality)} • "
                        f"{str(filename)[:38]}"
                    ),
                    callback_data=(
                        f"file#{file_id}"
                    ),
                )
            ]
        )

    # --------------------------------------------------------
    # RESULT TEXT
    # --------------------------------------------------------

    text = (
        "📺 <b>SERIES FILES</b>\n\n"
        f"🎬 <b>{html_escape(title)}</b>\n"
        f"🌐 {LANGUAGES.get(language, language)}\n"
        f"📚 {html_escape(season)}\n"
        f"🎯 {quality_text(quality)}\n\n"
        f"📦 Episodes found: "
        f"<b>{len(files)}</b>\n"
        f"⚡ Search time: "
        f"<b>{elapsed:.2f}s</b>\n\n"
        "Select an episode:"
    )

    markup = InlineKeyboardMarkup(
        buttons
    )

    try:

        await query.message.edit_text(
            text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception as e:

        logger.warning(
            "[SERIES] Result display failed: %s",
            e,
        )


# ============================================================
# CACHE CLEANER
# ============================================================

async def clear_series_cache():

    IMDB_SEARCH_CACHE.clear()

    IMDB_DETAILS_CACHE.clear()

    FILE_SEARCH_CACHE.clear()

    # Keep active user states.
    # They are tiny strings and are needed while users
    # navigate the buttons.

    logger.info(
        "[SERIES] Caches cleared."
    )


# ============================================================
# OPTIONAL PERIODIC CACHE CLEANER
# ============================================================

async def series_cache_worker():

    while True:

        try:

            now = time.monotonic()

            # ------------------------------------------------
            # IMDb search cache
            # ------------------------------------------------

            expired = []

            for key, value in (
                IMDB_SEARCH_CACHE.items()
            ):

                if (
                    now
                    - value["time"]
                    > IMDB_CACHE_TTL
                ):

                    expired.append(
                        key
                    )

            for key in expired:

                IMDB_SEARCH_CACHE.pop(
                    key,
                    None,
                )

            # ------------------------------------------------
            # IMDb details
            # ------------------------------------------------

            expired = []

            for key, value in (
                IMDB_DETAILS_CACHE.items()
            ):

                if (
                    now
                    - value["time"]
                    > IMDB_CACHE_TTL
                ):

                    expired.append(
                        key
                    )

            for key in expired:

                IMDB_DETAILS_CACHE.pop(
                    key,
                    None,
                )

            # ------------------------------------------------
            # File cache
            # ------------------------------------------------

            expired = []

            for key, value in (
                FILE_SEARCH_CACHE.items()
            ):

                if (
                    now
                    - value["time"]
                    > FILE_CACHE_TTL
                ):

                    expired.append(
                        key
                    )

            for key in expired:

                FILE_SEARCH_CACHE.pop(
                    key,
                    None,
                )

            # ------------------------------------------------
            # User states older than a while can be removed
            # if required. The state is extremely small.
            # ------------------------------------------------

        except Exception:

            logger.exception(
                "[SERIES] Cache worker error."
            )

        await asyncio.sleep(
            600
        )


# ============================================================
# STARTUP
# ============================================================

_series_cache_task = None


async def start_series_system():

    global _series_cache_task

    if _series_cache_task:
        return

    _series_cache_task = (
        asyncio.create_task(
            series_cache_worker()
        )
    )

    logger.info(
        "=================================================="
    )

    logger.info(
        "[DOWNTOWNVILLA SERIES] SYSTEM STARTED"
    )

    logger.info(
        "[DOWNTOWNVILLA SERIES] Group: %s",
        SERIES_CHAT_ID,
    )

    logger.info(
        "[DOWNTOWNVILLA SERIES] IMDb: %s",
        "AVAILABLE"
        if IMDB_AVAILABLE
        else "UNAVAILABLE",
    )

    logger.info(
        "[DOWNTOWNVILLA SERIES] Poster: %s",
        SERIES_POSTER,
    )

    logger.info(
        "[DOWNTOWNVILLA SERIES] Movie group: %s",
        MOVIE_GROUP_LINK
        or "NOT SET",
    )

    logger.info(
        "[DOWNTOWNVILLA SERIES] Bot username: %s",
        BOT_USERNAME
        or "NOT SET",
    )

    logger.info(
        "=================================================="
    )


# ============================================================
# END
# ============================================================
