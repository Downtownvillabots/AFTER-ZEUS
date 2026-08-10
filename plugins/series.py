```python
# ============================================================
# DOWNTOWNVILLA ULTIMATE SERIES SYSTEM
# ============================================================
#
# FEATURES
# ------------------------------------------------------------
# 1. Series-group-only searching
# 2. IMDb/Cinemagoer spelling correction / suggestions
# 3. Movies are removed from series suggestions
# 4. IMDb details + rating + genres + plot + poster
# 5. "OPEN SERIES" button sends user to bot PM
# 6. /start series_<IMDb_ID> opens series selection in PM
# 7. Language selection
#       eng / mal / hin / tam / tel / kan
# 8. Season selection
#       S01 / S02 / S03 ...
# 9. Quality selection
#       4K / 1440p / 1080p / 720p / 576p / 480p / 360p
# 10. GET FILES button
# 11. MongoDB is searched ONLY after GET FILES
# 12. Searches Media + Media2 + Media3
# 13. Strict title + language + season + quality matching
# 14. Episode extraction:
#       E01 / E02 / E03 / E04...
# 15. One best file per episode
# 16. Sends actual Telegram cached files
# 17. NO STREAM BUTTON
# 18. NO STREAM FUNCTION
# 19. No movie/random files in series result
# 20. IMDb search/detail caching
# 21. Per-user state is RAM only
#
# ============================================================

import os
import re
import asyncio
import logging
from collections import OrderedDict
from difflib import SequenceMatcher
from html import escape
from urllib.parse import quote

from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.errors import (
    FloodWait,
    RPCError,
)

from database.ia_filterdb import (
    Media,
    Media2,
    Media3,
    MULTIPLE_DB,
)

from info import (
    COLLECTION_NAME,
)

logger = logging.getLogger(__name__)


# ============================================================
# ENVIRONMENT
# ============================================================

SERIES_GROUP_ID = os.getenv(
    "SERIES_GROUP_ID",
    "",
).strip()

MOVIE_GROUP_LINK = os.getenv(
    "MOVIE_GROUP_LINK",
    "",
).strip()

SERIES_POSTER = (
    os.getenv(
        "SERIES_POSTER",
        "true",
    ).lower()
    in (
        "true",
        "1",
        "yes",
        "on",
    )
)

SERIES_CAPTION = os.getenv(
    "SERIES_CAPTION",
    "",
)

SERIES_SEND_DELAY = float(
    os.getenv(
        "SERIES_SEND_DELAY",
        "0.15",
    )
)

SERIES_SEARCH_LIMIT = int(
    os.getenv(
        "SERIES_SEARCH_LIMIT",
        "8",
    )
)

SERIES_MAX_EPISODES = int(
    os.getenv(
        "SERIES_MAX_EPISODES",
        "0",
    )
)

SERIES_MAX_SEASON = int(
    os.getenv(
        "SERIES_MAX_SEASON",
        "30",
    )
)


# ============================================================
# GROUP ID
# ============================================================

try:
    SERIES_CHAT_ID = (
        int(SERIES_GROUP_ID)
        if SERIES_GROUP_ID
        else None
    )
except Exception:
    SERIES_CHAT_ID = None
    logger.error(
        "[SERIES] Invalid SERIES_GROUP_ID."
    )


# ============================================================
# IMDb / CINEMAGOER
# ============================================================

try:
    import imdb

    imdb_api = imdb.IMDb()

    IMDB_AVAILABLE = True

    logger.info(
        "[SERIES] IMDb/Cinemagoer loaded."
    )

except Exception as e:
    imdb_api = None
    IMDB_AVAILABLE = False

    logger.warning(
        "[SERIES] IMDb unavailable: %s",
        e,
    )


# ============================================================
# MEMORY CACHE
# ============================================================

# Small LRU-style caches.
#
# We don't want unlimited memory usage.

MAX_IMDB_SEARCH_CACHE = 2000
MAX_IMDB_DETAILS_CACHE = 2000
MAX_USER_STATE = 10000


IMDB_SEARCH_CACHE = OrderedDict()
IMDB_DETAILS_CACHE = OrderedDict()

USER_SERIES_STATE = {}


# ============================================================
# CACHE HELPERS
# ============================================================

def cache_get(
    cache,
    key,
):
    try:
        value = cache.pop(key)
        cache[key] = value
        return value
    except KeyError:
        return None


def cache_put(
    cache,
    key,
    value,
    maximum,
):
    if key in cache:
        cache.pop(key, None)

    cache[key] = value

    while len(cache) > maximum:
        cache.popitem(
            last=False
        )


def cleanup_user_state():

    if len(USER_SERIES_STATE) <= MAX_USER_STATE:
        return

    remove_count = (
        len(USER_SERIES_STATE)
        - MAX_USER_STATE
    )

    for user_id in list(
        USER_SERIES_STATE.keys()
    )[:remove_count]:

        USER_SERIES_STATE.pop(
            user_id,
            None,
        )


# ============================================================
# LANGUAGE SYSTEM
# ============================================================
#
# User sees the normal language.
#
# Database matching uses compact trigger tokens.
#
# English  -> eng
# Malayalam -> mal
# Hindi -> hin
# Tamil -> tam
# Telugu -> tel
# Kannada -> kan
#
# ============================================================

LANGUAGES = {

    "eng": {
        "name": "🇬🇧 English",
        "short": "ENG",
        "triggers": [
            "eng",
            "english",
            "en",
        ],
    },

    "mal": {
        "name": "🇮🇳 Malayalam",
        "short": "MAL",
        "triggers": [
            "mal",
            "malayalam",
        ],
    },

    "hin": {
        "name": "🇮🇳 Hindi",
        "short": "HIN",
        "triggers": [
            "hin",
            "hindi",
        ],
    },

    "tam": {
        "name": "🇮🇳 Tamil",
        "short": "TAM",
        "triggers": [
            "tam",
            "tamil",
        ],
    },

    "tel": {
        "name": "🇮🇳 Telugu",
        "short": "TEL",
        "triggers": [
            "tel",
            "telugu",
        ],
    },

    "kan": {
        "name": "🇮🇳 Kannada",
        "short": "KAN",
        "triggers": [
            "kan",
            "kannada",
        ],
    },
}


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_text(text):

    if not text:
        return ""

    text = str(text)

    text = text.lower()

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


def title_words(text):

    return [
        word
        for word in normalize_text(
            text
        ).split()
        if len(word) >= 2
    ]


def similarity(
    a,
    b,
):

    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0

    return int(
        SequenceMatcher(
            None,
            a,
            b,
        ).ratio()
        * 100
    )


def html_escape(value):

    if value is None:
        return ""

    return escape(
        str(value)
    )


# ============================================================
# QUALITY
# ============================================================

QUALITY_PATTERNS = [

    (
        2160,
        re.compile(
            r"(?i)(?:^|[\s._+\-])"
            r"(?:2160p|4k|uhd)"
            r"(?:$|[\s._+\-])"
        ),
    ),

    (
        1440,
        re.compile(
            r"(?i)(?:^|[\s._+\-])"
            r"1440p"
            r"(?:$|[\s._+\-])"
        ),
    ),

    (
        1080,
        re.compile(
            r"(?i)(?:^|[\s._+\-])"
            r"(?:1080p|1080i)"
            r"(?:$|[\s._+\-])"
        ),
    ),

    (
        720,
        re.compile(
            r"(?i)(?:^|[\s._+\-])"
            r"720p"
            r"(?:$|[\s._+\-])"
        ),
    ),

    (
        576,
        re.compile(
            r"(?i)(?:^|[\s._+\-])"
            r"576p"
            r"(?:$|[\s._+\-])"
        ),
    ),

    (
        480,
        re.compile(
            r"(?i)(?:^|[\s._+\-])"
            r"480p"
            r"(?:$|[\s._+\-])"
        ),
    ),

    (
        360,
        re.compile(
            r"(?i)(?:^|[\s._+\-])"
            r"360p"
            r"(?:$|[\s._+\-])"
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

    return 0


def quality_text(
    quality,
):

    if quality == 2160:
        return "4K"

    if quality:
        return f"{quality}p"

    return "Unknown"


# ============================================================
# LANGUAGE MATCHING
# ============================================================

def language_matches(
    filename,
    language,
):

    if not filename:
        return False

    normalized = normalize_text(
        filename
    )

    words = set(
        normalized.split()
    )

    data = LANGUAGES.get(
        language
    )

    if not data:
        return False

    for trigger in data[
        "triggers"
    ]:

        trigger = normalize_text(
            trigger
        )

        if trigger in words:
            return True

    # Extra protection for compact tags.
    #
    # Example:
    # [ENG]
    # -ENG
    # .ENG
    #

    pattern = (
        r"(?i)(?:^|[^a-z])"
        + "|".join(
            re.escape(
                x
            )
            for x in data[
                "triggers"
            ]
        )
        + r"(?:$|[^a-z])"
    )

    return bool(
        re.search(
            pattern,
            str(filename),
        )
    )


# ============================================================
# SEASON / EPISODE EXTRACTION
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
        r"(?i)(?:^|[^a-z])"
        r"S(\d{1,2})"
        r"[\s._\-]*"
        r"E(\d{1,3})"
        r"(?:$|[^a-z])",
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
    # Season 1 Ep 1
    # --------------------------------------------------------

    match = re.search(
        r"(?i)\bSeason"
        r"[\s._\-]*(\d{1,2})"
        r"[\s._\-]*"
        r"(?:Episode|Ep|E)"
        r"[\s._\-]*"
        r"(\d{1,3})\b",
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


def extract_season(
    filename,
):

    if not filename:
        return None

    match = re.search(
        r"(?i)\bS(\d{1,2})\b",
        str(filename),
    )

    if match:
        return int(
            match.group(1)
        )

    match = re.search(
        r"(?i)\bSeason"
        r"[\s._\-]*(\d{1,2})\b",
        str(filename),
    )

    if match:
        return int(
            match.group(1)
        )

    return None


# ============================================================
# TITLE MATCHING
# ============================================================

def clean_filename_for_title(
    filename,
):

    text = normalize_text(
        filename
    )

    # Remove S01E01.
    text = re.sub(
        r"\bs\d{1,2}\s*e\d{1,3}\b",
        " ",
        text,
    )

    # Remove Season 01 Episode 01.
    text = re.sub(
        r"\bseason\s*\d{1,2}\b",
        " ",
        text,
    )

    text = re.sub(
        r"\b(?:episode|ep)\s*\d{1,3}\b",
        " ",
        text,
    )

    # Remove quality.
    text = re.sub(
        r"\b(?:2160p|1440p|1080p|1080i|720p|576p|480p|360p|4k|uhd)\b",
        " ",
        text,
    )

    # Remove common release tags.
    text = re.sub(
        r"\b(?:"
        r"web[- ]?dl|"
        r"webrip|"
        r"web|"
        r"bluray|"
        r"brrip|"
        r"hdrip|"
        r"hdtv|"
        r"dvdrip|"
        r"x264|"
        r"x265|"
        r"h264|"
        r"h265|"
        r"hevc|"
        r"10bit|"
        r"8bit"
        r")\b",
        " ",
        text,
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def title_matches(
    title,
    filename,
):

    wanted = title_words(
        title
    )

    if not wanted:
        return False

    cleaned = clean_filename_for_title(
        filename
    )

    available = set(
        cleaned.split()
    )

    matched = 0

    for word in wanted:

        if word in available:
            matched += 1
            continue

        # Allow small prefix variation.
        found = False

        for candidate in available:

            if (
                candidate.startswith(
                    word
                )
                or word.startswith(
                    candidate
                )
            ):
                found = True
                break

        if found:
            matched += 1

    score = (
        matched
        / len(wanted)
    ) * 100

    return score >= 60


# ============================================================
# DATABASE LIST
# ============================================================

def get_series_databases():

    databases = [
        (
            "Media",
            Media,
        )
    ]

    if MULTIPLE_DB:

        databases.extend(
            [
                (
                    "Media2",
                    Media2,
                ),
                (
                    "Media3",
                    Media3,
                ),
            ]
        )

    return databases


# ============================================================
# SERVER-SIDE CANDIDATE SEARCH
# ============================================================
#
# IMPORTANT:
#
# We don't use:
#
# collection.find({})
#
# Instead we use the season token to reduce the MongoDB
# candidate set first.
#
# Then strict filtering happens in Python.
#
# ============================================================

async def search_series_candidates(
    title,
    language,
    season,
    quality,
):

    results = []

    season_token = (
        f"S{season:02d}"
    )

    # --------------------------------------------------------
    # Candidate regex.
    #
    # This catches:
    #
    # S01E01
    # S01.E01
    # S01-E01
    # S01_E01
    #
    # MongoDB does the first filtering.
    # --------------------------------------------------------

    season_regex = re.compile(
        rf"(?i)"
        rf"(?:^|[^a-z])"
        rf"S0?{season}"
        rf"[\s._\-]*"
        rf"E\d{{1,3}}"
        rf"(?:$|[^a-z])"
    )

    for db_name, model in get_series_databases():

        try:

            # ------------------------------------------------
            # Main candidate query.
            #
            # We use the existing Document model.
            # ------------------------------------------------

            cursor = model.find(
                {
                    "file_name": {
                        "$regex":
                            season_regex
                    }
                }
            )

            # ------------------------------------------------
            # IMPORTANT:
            #
            # Limit candidates so one bad database cannot
            # explode RAM.
            # ------------------------------------------------

            candidate_count = 0

            async for document in cursor:

                candidate_count += 1

                if (
                    candidate_count
                    > 1500
                ):
                    break

                filename = document.get(
                    "file_name"
                )

                if not filename:
                    continue

                # ------------------------------------------------
                # Season + episode.
                # ------------------------------------------------

                file_season, episode = (
                    extract_season_episode(
                        filename
                    )
                )

                if (
                    file_season
                    != season
                    or episode is None
                ):
                    continue

                # ------------------------------------------------
                # Title.
                # ------------------------------------------------

                if not title_matches(
                    title,
                    filename,
                ):
                    continue

                # ------------------------------------------------
                # Language.
                # ------------------------------------------------

                if not language_matches(
                    filename,
                    language,
                ):
                    continue

                # ------------------------------------------------
                # Quality.
                # ------------------------------------------------

                file_quality = extract_quality(
                    filename
                )

                if (
                    quality
                    and file_quality
                    != quality
                ):
                    continue

                # ------------------------------------------------
                # Save internal metadata.
                # ------------------------------------------------

                document[
                    "_series_db"
                ] = db_name

                document[
                    "_series_season"
                ] = file_season

                document[
                    "_series_episode"
                ] = episode

                document[
                    "_series_quality"
                ] = file_quality

                results.append(
                    document
                )

        except Exception as e:

            logger.exception(
                "[SERIES] %s search failed: %s",
                db_name,
                e,
            )

    return results


# ============================================================
# BEST FILE PER EPISODE
# ============================================================

def episode_file_score(
    document,
):

    quality = int(
        document.get(
            "_series_quality",
            0,
        )
        or 0
    )

    file_size = int(
        document.get(
            "file_size",
            0,
        )
        or 0
    )

    filename = str(
        document.get(
            "file_name",
            "",
        )
    )

    # Quality is the primary score.
    # Size is secondary.
    #
    # This means if the same episode exists in multiple DBs,
    # we select the best matching quality.

    return (
        quality * 10_000_000_000
        + file_size
        + len(filename)
    )


def choose_best_episode_files(
    files,
):

    best = {}

    for document in files:

        episode = document.get(
            "_series_episode"
        )

        if episode is None:
            continue

        old = best.get(
            episode
        )

        if old is None:

            best[
                episode
            ] = document

            continue

        if (
            episode_file_score(
                document
            )
            >
            episode_file_score(
                old
            )
        ):

            best[
                episode
            ] = document

    return dict(
        sorted(
            best.items()
        )
    )


# ============================================================
# IMDb SEARCH
# ============================================================

async def imdb_search(
    query,
):

    if not IMDB_AVAILABLE:
        return []

    clean = normalize_text(
        query
    )

    if not clean:
        return []

    cached = cache_get(
        IMDB_SEARCH_CACHE,
        clean,
    )

    if cached is not None:
        return cached

    try:

        results = await asyncio.to_thread(
            imdb_api.search_movie,
            clean,
        )

    except Exception as e:

        logger.warning(
            "[SERIES] IMDb search failed: %s",
            e,
        )

        return []

    output = []

    for item in results[
        :SERIES_SEARCH_LIMIT * 3
    ]:

        title = item.get(
            "title"
        )

        if not title:
            continue

        kind = str(
            item.get(
                "kind",
                "",
            )
        ).lower()

        # ----------------------------------------------------
        # Strict TV detection.
        # ----------------------------------------------------

        is_series = (
            kind in (
                "tv series",
                "tv mini series",
                "tv mini-series",
                "series",
                "tv show",
                "tv limited series",
            )
            or (
                "tv" in kind
                and "movie" not in kind
            )
        )

        if not is_series:
            continue

        movie_id = getattr(
            item,
            "movieID",
            None,
        )

        if not movie_id:
            movie_id = item.get(
                "movieID"
            )

        if not movie_id:
            continue

        output.append(
            {
                "id": str(
                    movie_id
                ),
                "title": str(
                    title
                ),
                "year": item.get(
                    "year"
                ),
                "kind": kind,
                "score": similarity(
                    query,
                    title,
                ),
            }
        )

    # Exact title first.
    normalized_query = normalize_text(
        query
    )

    output.sort(
        key=lambda x: (
            1
            if normalize_text(
                x["title"]
            )
            == normalized_query
            else 0,
            x.get(
                "score",
                0,
            ),
        ),
        reverse=True,
    )

    output = output[
        :SERIES_SEARCH_LIMIT
    ]

    cache_put(
        IMDB_SEARCH_CACHE,
        clean,
        output,
        MAX_IMDB_SEARCH_CACHE,
    )

    return output


# ============================================================
# IMDb DETAILS
# ============================================================

async def get_series_details(
    imdb_id,
):

    if not imdb_id:
        return {}

    cached = cache_get(
        IMDB_DETAILS_CACHE,
        str(imdb_id),
    )

    if cached is not None:
        return cached

    if not IMDB_AVAILABLE:
        return {}

    try:

        movie = await asyncio.to_thread(
            imdb_api.get_movie,
            str(imdb_id),
        )

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

        cache_put(
            IMDB_DETAILS_CACHE,
            str(imdb_id),
            details,
            MAX_IMDB_DETAILS_CACHE,
        )

        return details

    except Exception as e:

        logger.warning(
            "[SERIES] IMDb details failed: %s",
            e,
        )

        return {}


# ============================================================
# BOT USERNAME / PM LINK
# ============================================================

async def get_bot_username(
    app,
):

    try:

        me = await app.get_me()

        username = getattr(
            me,
            "username",
            None,
        )

        if username:
            return username

    except Exception as e:

        logger.warning(
            "[SERIES] Unable to get bot username: %s",
            e,
        )

    return None


async def build_pm_link(
    app,
    imdb_id,
):

    username = await get_bot_username(
        app
    )

    if not username:
        return None

    return (
        f"https://t.me/"
        f"{username}"
        f"?start=series_{imdb_id}"
    )


# ============================================================
# CALLBACK DATA
# ============================================================

def cb_title(
    imdb_id,
):
    return (
        f"st:{imdb_id}"
    )


def cb_language(
    imdb_id,
    language,
):
    return (
        f"sl:{imdb_id}:"
        f"{language}"
    )


def cb_season(
    imdb_id,
    language,
    season,
):
    return (
        f"ss:{imdb_id}:"
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
        f"sq:{imdb_id}:"
        f"{language}:"
        f"{season}:"
        f"{quality}"
    )


def cb_files(
    imdb_id,
    language,
    season,
    quality,
):
    return (
        f"gf:{imdb_id}:"
        f"{language}:"
        f"{season}:"
        f"{quality}"
    )


# ============================================================
# SEARCH RESULT KEYBOARD
# ============================================================

def build_search_keyboard(
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
                    callback_data=cb_title(
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
# IMDb DETAILS TEXT
# ============================================================

def build_details_text(
    details,
):

    title = details.get(
        "title",
        "Unknown",
    )

    year = details.get(
        "year"
    )

    rating = details.get(
        "rating"
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

    if rating:

        try:

            text += (
                "\n⭐ IMDb: "
                f"<b>{float(rating):.1f}/10</b>"
            )

        except Exception:
            pass

    if genres:

        text += (
            "\n🎭 Genres: "
            f"<b>{html_escape(', '.join(genres[:5]))}</b>"
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
            "\n\n📝 "
            f"{html_escape(plot_text)}"
        )

    return text


# ============================================================
# LANGUAGE KEYBOARD
# ============================================================

def build_language_keyboard(
    imdb_id,
):

    rows = []

    current = []

    for language, data in LANGUAGES.items():

        current.append(
            InlineKeyboardButton(
                text=data[
                    "name"
                ],
                callback_data=cb_language(
                    imdb_id,
                    language,
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

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# SEASON KEYBOARD
# ============================================================

def build_season_keyboard(
    imdb_id,
    language,
):

    rows = []

    current = []

    for season in range(
        1,
        SERIES_MAX_SEASON + 1,
    ):

        current.append(
            InlineKeyboardButton(
                text=f"S{season:02d}",
                callback_data=cb_season(
                    imdb_id,
                    language,
                    season,
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

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# QUALITY KEYBOARD
# ============================================================

def build_quality_keyboard(
    imdb_id,
    language,
    season,
):

    qualities = [
        2160,
        1440,
        1080,
        720,
        576,
        480,
        360,
    ]

    rows = []

    current = []

    for quality in qualities:

        current.append(
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

        if len(current) == 3:

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
# GET FILES BUTTON
# ============================================================

def build_get_files_keyboard(
    imdb_id,
    language,
    season,
    quality,
):

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="🚀 GET FILES",
                    callback_data=cb_files(
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
# SERIES GROUP SEARCH
# ============================================================

@Client.on_message(
    filters.text
    & ~filters.command(
        [
            "start",
            "help",
        ]
    ),
    group=-1000,
)
async def series_group_search(
    app,
    message,
):

    if (
        SERIES_CHAT_ID is None
        or message.chat.id
        != SERIES_CHAT_ID
    ):
        return

    query = (
        message.text or ""
    ).strip()

    if not query:
        return

    if query.startswith("/"):
        return

    try:

        msg = await message.reply_text(
            "🔎 <b>Finding the correct series...</b>",
            parse_mode=enums.ParseMode.HTML,
        )

        candidates = await imdb_search(
            query
        )

        if not candidates:

            buttons = []

            if MOVIE_GROUP_LINK:

                buttons.append(
                    [
                        InlineKeyboardButton(
                            text="🎬 MOVIE GROUP",
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

            await msg.edit_text(
                "❌ <b>No TV series found.</b>\n\n"
                f"🔎 {html_escape(query)}",
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
            )

            return

        text = (
            "📺 <b>SELECT SERIES</b>\n\n"
            f"🔎 Search: <b>{html_escape(query)}</b>\n\n"
            "Select the correct series:"
        )

        await msg.edit_text(
            text,
            reply_markup=build_search_keyboard(
                candidates
            ),
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception as e:

        logger.exception(
            "[SERIES] Group search error: %s",
            e,
        )


# ============================================================
# TITLE SELECTION IN SERIES GROUP
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^st:"
    )
)
async def series_title_callback(
    app,
    query,
):

    if (
        SERIES_CHAT_ID is None
        or query.message.chat.id
        != SERIES_CHAT_ID
    ):

        await query.answer(
            "This button is only available in the series group.",
            show_alert=True,
        )

        return

    imdb_id = query.data[
        3:
    ]

    await query.answer(
        "Loading IMDb..."
    )

    details = await get_series_details(
        imdb_id
    )

    if not details:

        await query.message.edit_text(
            "❌ Unable to load IMDb information."
        )

        return

    title = details.get(
        "title",
        "Series",
    )

    pm_link = await build_pm_link(
        app,
        imdb_id,
    )

    text = build_details_text(
        details
    )

    text += (
        "\n\n"
        "👇 <b>Continue in PM to select "
        "language, season and quality.</b>"
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
                    text="🎬 MOVIE GROUP",
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
    # Poster.
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
                chat_id=SERIES_CHAT_ID,
                photo=details[
                    "poster"
                ],
                caption=text,
                reply_markup=markup,
            )

            return

        except Exception as e:

            logger.warning(
                "[SERIES] Poster send failed: %s",
                e,
            )

    try:

        await query.message.edit_text(
            text,
            reply_markup=markup,
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:
        pass


# ============================================================
# PM /start HANDLER
# ============================================================
#
# Link:
#
# https://t.me/BOT?start=series_0944947
#
# The user opens the bot and presses START.
#
# This handler catches only series deep-link starts.
#
# ============================================================

@Client.on_message(
    filters.private
    & filters.command("start"),
    group=-2000,
)
async def series_start_handler(
    app,
    message,
):

    if not message.command:
        return

    if len(
        message.command
    ) < 2:
        return

    payload = str(
        message.command[1]
    ).strip()

    if not payload.startswith(
        "series_"
    ):
        return

    imdb_id = payload[
        7:
    ]

    if not imdb_id:
        return

    # --------------------------------------------------------
    # Prevent another generic /start handler from processing
    # this same update.
    # --------------------------------------------------------

    try:
        await message.delete()
    except Exception:
        pass

    await show_series_pm_home(
        app,
        message.chat.id,
        imdb_id,
    )

    raise StopPropagation


# ============================================================
# PM HOME
# ============================================================

async def show_series_pm_home(
    app,
    chat_id,
    imdb_id,
):

    details = await get_series_details(
        imdb_id
    )

    if not details:

        await app.send_message(
            chat_id,
            "❌ Unable to load the series information."
        )

        return

    title = details.get(
        "title",
        "Series",
    )

    text = build_details_text(
        details
    )

    text += (
        "\n\n"
        "🌐 <b>Select Language</b>"
    )

    USER_SERIES_STATE[
        chat_id
    ] = {
        "imdb_id": imdb_id,
        "title": title,
    }

    cleanup_user_state()

    if (
        SERIES_POSTER
        and details.get(
            "poster"
        )
    ):

        try:

            await app.send_photo(
                chat_id=chat_id,
                photo=details[
                    "poster"
                ],
                caption=text,
                reply_markup=build_language_keyboard(
                    imdb_id
                ),
            )

            return

        except Exception as e:

            logger.warning(
                "[SERIES] PM poster failed: %s",
                e,
            )

    await app.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=build_language_keyboard(
            imdb_id
        ),
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# LANGUAGE CALLBACK
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^sl:"
    )
)
async def series_language_callback(
    app,
    query,
):

    if not query.message.chat.type == enums.ChatType.PRIVATE:
        await query.answer(
            "Please continue in PM.",
            show_alert=True,
        )
        return

    parts = query.data.split(
        ":"
    )

    if len(parts) != 3:
        return

    imdb_id = parts[1]
    language = parts[2]

    if language not in LANGUAGES:
        return

    state = USER_SERIES_STATE.setdefault(
        query.from_user.id,
        {},
    )

    state[
        "imdb_id"
    ] = imdb_id

    state[
        "language"
    ] = language

    state[
        "language_token"
    ] = language

    details = await get_series_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series",
    )

    await query.answer(
        f"{LANGUAGES[language]['short']} selected"
    )

    text = (
        "📺 <b>"
        f"{html_escape(title)}"
        "</b>\n\n"

        f"🌐 Language: "
        f"<b>{LANGUAGES[language]['name']}</b>\n\n"

        "📚 <b>Select Season</b>"
    )

    try:

        await query.message.edit_text(
            text,
            reply_markup=build_season_keyboard(
                imdb_id,
                language,
            ),
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:

        # This can happen if the previous message was a photo.
        # Send a fresh message instead.

        try:

            await app.send_message(
                query.message.chat.id,
                text,
                reply_markup=build_season_keyboard(
                    imdb_id,
                    language,
                ),
                parse_mode=enums.ParseMode.HTML,
            )

        except Exception:
            pass


# ============================================================
# SEASON CALLBACK
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^ss:"
    )
)
async def series_season_callback(
    app,
    query,
):

    if not query.message.chat.type == enums.ChatType.PRIVATE:
        await query.answer(
            "Please continue in PM.",
            show_alert=True,
        )
        return

    parts = query.data.split(
        ":"
    )

    if len(parts) != 4:
        return

    imdb_id = parts[1]
    language = parts[2]

    try:
        season = int(
            parts[3]
        )
    except Exception:
        return

    if season <= 0:
        return

    if season > SERIES_MAX_SEASON:
        return

    state = USER_SERIES_STATE.setdefault(
        query.from_user.id,
        {},
    )

    state[
        "imdb_id"
    ] = imdb_id

    state[
        "language"
    ] = language

    state[
        "season"
    ] = season

    state[
        "season_token"
    ] = f"S{season:02d}"

    details = await get_series_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series",
    )

    await query.answer(
        f"S{season:02d} selected"
    )

    text = (
        "📺 <b>"
        f"{html_escape(title)}"
        "</b>\n\n"

        f"🌐 Language: "
        f"<b>{LANGUAGES.get(language, {}).get('name', language)}</b>\n"

        f"📚 Season: "
        f"<b>S{season:02d}</b>\n\n"

        "🎯 <b>Select Quality</b>"
    )

    try:

        await query.message.edit_text(
            text,
            reply_markup=build_quality_keyboard(
                imdb_id,
                language,
                season,
            ),
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:

        try:

            await app.send_message(
                query.message.chat.id,
                text,
                reply_markup=build_quality_keyboard(
                    imdb_id,
                    language,
                    season,
                ),
                parse_mode=enums.ParseMode.HTML,
            )

        except Exception:
            pass


# ============================================================
# QUALITY CALLBACK
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^sq:"
    )
)
async def series_quality_callback(
    app,
    query,
):

    if not query.message.chat.type == enums.ChatType.PRIVATE:
        await query.answer(
            "Please continue in PM.",
            show_alert=True,
        )
        return

    parts = query.data.split(
        ":"
    )

    if len(parts) != 5:
        return

    imdb_id = parts[1]
    language = parts[2]

    try:

        season = int(
            parts[3]
        )

        quality = int(
            parts[4]
        )

    except Exception:
        return

    state = USER_SERIES_STATE.setdefault(
        query.from_user.id,
        {},
    )

    state[
        "imdb_id"
    ] = imdb_id

    state[
        "language"
    ] = language

    state[
        "season"
    ] = season

    state[
        "quality"
    ] = quality

    state[
        "language_token"
    ] = language

    state[
        "season_token"
    ] = f"S{season:02d}"

    state[
        "quality_token"
    ] = quality_text(
        quality
    )

    details = await get_series_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series",
    )

    # --------------------------------------------------------
    # This is the compact search representation requested.
    #
    # Example:
    #
    # Game of Thrones eng S01 1080p
    #
    # It is kept only in RAM.
    # --------------------------------------------------------

    search_string = (
        f"{title} "
        f"{language} "
        f"S{season:02d} "
        f"{quality_text(quality)}"
    )

    state[
        "search_string"
    ] = search_string

    await query.answer(
        "Quality selected"
    )

    text = (
        "📺 <b>"
        f"{html_escape(title)}"
        "</b>\n\n"

        f"🌐 Language: "
        f"<b>{LANGUAGES.get(language, {}).get('name', language)}</b>\n"

        f"📚 Season: "
        f"<b>S{season:02d}</b>\n"

        f"🎯 Quality: "
        f"<b>{quality_text(quality)}</b>\n\n"

        "🔎 <b>READY</b>\n\n"

        "<code>"
        f"{html_escape(search_string)}"
        "</code>\n\n"

        "Press <b>GET FILES</b> to search the database."
    )

    try:

        await query.message.edit_text(
            text,
            reply_markup=build_get_files_keyboard(
                imdb_id,
                language,
                season,
                quality,
            ),
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:
        pass


# ============================================================
# GET FILES CALLBACK
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^gf:"
    )
)
async def series_get_files_callback(
    app,
    query,
):

    if not query.message.chat.type == enums.ChatType.PRIVATE:
        await query.answer(
            "Please continue in PM.",
            show_alert=True,
        )
        return

    parts = query.data.split(
        ":"
    )

    if len(parts) != 5:
        return

    imdb_id = parts[1]
    language = parts[2]

    try:

        season = int(
            parts[3]
        )

        quality = int(
            parts[4]
        )

    except Exception:
        return

    if language not in LANGUAGES:
        return

    if season <= 0:
        return

    # --------------------------------------------------------
    # IMDb title.
    # --------------------------------------------------------

    details = await get_series_details(
        imdb_id
    )

    title = details.get(
        "title",
        "",
    )

    if not title:

        await query.answer(
            "Series information unavailable.",
            show_alert=True,
        )

        return

    await query.answer(
        "🔎 Searching files..."
    )

    # --------------------------------------------------------
    # Save state.
    # --------------------------------------------------------

    state = USER_SERIES_STATE.setdefault(
        query.from_user.id,
        {},
    )

    state[
        "imdb_id"
    ] = imdb_id

    state[
        "title"
    ] = title

    state[
        "language"
    ] = language

    state[
        "season"
    ] = season

    state[
        "quality"
    ] = quality

    state[
        "search_string"
    ] = (
        f"{title} "
        f"{language} "
        f"S{season:02d} "
        f"{quality_text(quality)}"
    )

    # --------------------------------------------------------
    # Searching message.
    # --------------------------------------------------------

    try:

        await query.message.edit_text(
            "🔎 <b>SEARCHING FILES...</b>\n\n"

            f"📺 <b>{html_escape(title)}</b>\n"
            f"🌐 {LANGUAGES[language]['short']}\n"
            f"📚 S{season:02d}\n"
            f"🎯 {quality_text(quality)}\n\n"

            "⚡ Searching Media + Media2 + Media3...",
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:
        pass

    # --------------------------------------------------------
    # THIS is the ONLY point where MongoDB is searched.
    # --------------------------------------------------------

    started = asyncio.get_running_loop().time()

    files = await search_series_candidates(
        title=title,
        language=language,
        season=season,
        quality=quality,
    )

    elapsed = (
        asyncio.get_running_loop().time()
        - started
    )

    # --------------------------------------------------------
    # Select one best file per episode.
    # --------------------------------------------------------

    best_files = choose_best_episode_files(
        files
    )

    episode_numbers = sorted(
        best_files.keys()
    )

    if SERIES_MAX_EPISODES > 0:

        episode_numbers = (
            episode_numbers[
                :SERIES_MAX_EPISODES
            ]
        )

    # --------------------------------------------------------
    # No results.
    # --------------------------------------------------------

    if not episode_numbers:

        text = (
            "❌ <b>NO FILES FOUND</b>\n\n"

            f"📺 <b>{html_escape(title)}</b>\n"
            f"🌐 {LANGUAGES[language]['short']}\n"
            f"📚 S{season:02d}\n"
            f"🎯 {quality_text(quality)}\n\n"

            f"⚡ Search time: "
            f"<b>{elapsed:.2f}s</b>\n\n"

            "Try another language or quality."
        )

        try:

            await query.message.edit_text(
                text,
                parse_mode=enums.ParseMode.HTML,
            )

        except Exception:
            pass

        return

    # --------------------------------------------------------
    # Display found episodes.
    # --------------------------------------------------------

    text = (
        "✅ <b>FILES FOUND</b>\n\n"

        f"📺 <b>{html_escape(title)}</b>\n"
        f"🌐 {LANGUAGES[language]['short']}\n"
        f"📚 S{season:02d}\n"
        f"🎯 {quality_text(quality)}\n\n"

        f"📦 Episodes: "
        f"<b>{len(episode_numbers)}</b>\n"

        f"⚡ Search: "
        f"<b>{elapsed:.2f}s</b>\n\n"

        "🎞 <b>EPISODES</b>\n\n"
    )

    for episode in episode_numbers:

        document = best_files[
            episode
        ]

        filename = document.get(
            "file_name",
            f"E{episode:02d}",
        )

        text += (
            f"• <b>E{episode:02d}</b>"
            f" — "
            f"{html_escape(filename[:75])}\n"
        )

    try:

        await query.message.edit_text(
            text,
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:
        pass

    # --------------------------------------------------------
    # SEND ACTUAL FILES.
    #
    # NO STREAMING.
    # --------------------------------------------------------

    sent = 0

    for episode in episode_numbers:

        document = best_files[
            episode
        ]

        # ----------------------------------------------------
        # Your database model stores:
        #
        # file_id = Mongo _id
        # file_ref = separate Telegram reference
        #
        # We prefer file_id.
        # ----------------------------------------------------

        file_id = document.get(
            "file_id"
        )

        file_ref = document.get(
            "file_ref"
        )

        if not file_id:

            logger.warning(
                "[SERIES] Missing file_id "
                "for E%02d | %s",
                episode,
                document.get(
                    "file_name"
                ),
            )

            continue

        filename = document.get(
            "file_name",
            f"E{episode:02d}",
        )

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
                    language=language,
                    filename=filename,
                )

            except Exception:

                caption = SERIES_CAPTION

        try:

            await app.send_cached_media(
                chat_id=query.message.chat.id,
                file_id=str(
                    file_id
                ),
                caption=caption,
            )

            sent += 1

            if SERIES_SEND_DELAY > 0:

                await asyncio.sleep(
                    SERIES_SEND_DELAY
                )

        except FloodWait as e:

            wait_time = int(
                getattr(
                    e,
                    "value",
                    10,
                )
            )

            logger.warning(
                "[SERIES] FloodWait %ss",
                wait_time,
            )

            await asyncio.sleep(
                wait_time + 1
            )

            try:

                await app.send_cached_media(
                    chat_id=query.message.chat.id,
                    file_id=str(
                        file_id
                    ),
                    caption=caption,
                )

                sent += 1

            except Exception as retry_error:

                logger.exception(
                    "[SERIES] Retry failed "
                    "E%02d: %s",
                    episode,
                    retry_error,
                )

        except RPCError as e:

            logger.exception(
                "[SERIES] Telegram error "
                "E%02d: %s",
                episode,
                e,
            )

        except Exception as e:

            logger.exception(
                "[SERIES] Failed sending "
                "E%02d: %s",
                episode,
                e,
            )

    # --------------------------------------------------------
    # Complete.
    # --------------------------------------------------------

    try:

        await query.message.reply_text(
            "✅ <b>SEASON COMPLETE</b>\n\n"

            f"📺 <b>{html_escape(title)}</b>\n"
            f"🌐 {LANGUAGES[language]['short']}\n"
            f"📚 S{season:02d}\n"
            f"🎯 {quality_text(quality)}\n\n"

            f"📦 Sent: "
            f"<b>{sent}/{len(episode_numbers)}</b>\n"

            f"⚡ Search: "
            f"<b>{elapsed:.2f}s</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:
        pass


# ============================================================
# OPTIONAL MEMORY CLEAR
# ============================================================

def clear_series_memory():

    IMDB_SEARCH_CACHE.clear()
    IMDB_DETAILS_CACHE.clear()
    USER_SERIES_STATE.clear()

    logger.info(
        "[SERIES] All series memory caches cleared."
    )


# ============================================================
# STARTUP LOG
# ============================================================

logger.info(
    "=================================================="
)

logger.info(
    "[SERIES] DowntownVilla Ultimate Series System"
)

logger.info(
    "[SERIES] Group ID: %s",
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
    "[SERIES] Multiple DB: %s",
    MULTIPLE_DB,
)

logger.info(
    "[SERIES] Databases: Media / Media2 / Media3"
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
    "[SERIES] MongoDB search occurs ONLY on GET FILES."
)

logger.info(
    "[SERIES] STREAMING: DISABLED"
)

logger.info(
    "=================================================="
)
```
