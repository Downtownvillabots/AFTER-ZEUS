import os
import re
import asyncio
import logging
from collections import defaultdict
from difflib import SequenceMatcher
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
# DOWNTOWNVILLA SERIES SYSTEM
# ============================================================
#
# FLOW:
#
# USER SEARCH
#       ↓
# IMDb suggestion
#       ↓
# SERIES DETAILS
#       ↓
# LANGUAGE
#       ↓
# SEASON
#       ↓
# QUALITY
#       ↓
# GET FILES
#       ↓
# MongoDB targeted search
#       ↓
# E01 / E02 / E03...
#
# IMPORTANT:
#
# MongoDB is NOT scanned during normal search.
#
# ============================================================


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


SERIES_POSTER = os.getenv(
    "SERIES_POSTER",
    "true",
).lower() in (
    "true",
    "1",
    "yes",
    "on",
)


SERIES_CAPTION = os.getenv(
    "SERIES_CAPTION",
    "",
)


SERIES_SEND_DELAY = float(
    os.getenv(
        "SERIES_SEND_DELAY",
        "0.5",
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


# ============================================================
# LANGUAGE TRIGGERS
# ============================================================
#
# IMPORTANT:
#
# These are only search tokens.
#
# User sees:
#
# English
# Malayalam
# Hindi
#
# Internally:
#
# eng
# mal
# hin
#
# ============================================================

LANGUAGES = {
    "eng": {
        "name": "🇬🇧 English",
        "triggers": [
            "eng",
            "english",
            "en",
        ],
    },

    "mal": {
        "name": "🇮🇳 Malayalam",
        "triggers": [
            "mal",
            "malayalam",
        ],
    },

    "hin": {
        "name": "🇮🇳 Hindi",
        "triggers": [
            "hin",
            "hindi",
        ],
    },

    "tam": {
        "name": "🇮🇳 Tamil",
        "triggers": [
            "tam",
            "tamil",
        ],
    },

    "tel": {
        "name": "🇮🇳 Telugu",
        "triggers": [
            "tel",
            "telugu",
        ],
    },

    "kan": {
        "name": "🇮🇳 Kannada",
        "triggers": [
            "kan",
            "kannada",
        ],
    },
}


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
        "[SERIES] Invalid SERIES_GROUP_ID"
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
# MEMORY
# ============================================================

# IMDb title search cache.
#
# This prevents repeated searches for:
#
# got
# GOT
# game of thrones
#
IMDB_SEARCH_CACHE = {}


# IMDb detail cache.
IMDB_DETAILS_CACHE = {}


# Per-user temporary series state.
#
# This is NOT MongoDB.
#
# Example:
#
# {
#     user_id: {
#         "title": "Game of Thrones",
#         "imdb_id": "0944947",
#         "language": "eng",
#         "season": 1,
#         "quality": 1080
#     }
# }
#
USER_SERIES_STATE = {}


# ============================================================
# STATE LIMIT
# ============================================================

MAX_STATE_USERS = 10000


def cleanup_state():

    if len(USER_SERIES_STATE) <= MAX_STATE_USERS:
        return

    # Remove oldest approximately.
    #
    # This is intentionally simple and cheap.
    #
    remove_count = (
        len(USER_SERIES_STATE)
        - MAX_STATE_USERS
    )

    for key in list(
        USER_SERIES_STATE.keys()
    )[:remove_count]:

        USER_SERIES_STATE.pop(
            key,
            None,
        )


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


def similarity(a, b):

    a = normalize_title(a)
    b = normalize_title(b)

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


def escape_html(value):

    if value is None:
        return ""

    return escape(
        str(value)
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

    name = str(
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
        r"(?i)\bS(\d{1,2})"
        r"[\s._\-]*"
        r"E(\d{1,3})\b",
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
    # Season 01 Episode 01
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
# QUALITY
# ============================================================

QUALITY_PATTERNS = [

    (
        2160,
        re.compile(
            r"(?i)\b2160p\b|\b4k\b|\buhd\b"
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
            r"(?i)\b1080p\b|\b1080i\b"
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

def language_tokens(
    language,
):

    data = LANGUAGES.get(
        language
    )

    if not data:
        return []

    return data[
        "triggers"
    ]


def filename_has_language(
    filename,
    language,
):

    if not filename:
        return False

    filename = normalize_title(
        filename
    )

    tokens = language_tokens(
        language
    )

    if not tokens:
        return True

    words = set(
        filename.split()
    )

    for token in tokens:

        if normalize_title(
            token
        ) in words:

            return True

    return False


# ============================================================
# TITLE MATCHING
# ============================================================

def filename_title_match(
    title,
    filename,
):

    title = normalize_title(
        title
    )

    filename = normalize_title(
        filename
    )

    if not title or not filename:
        return False

    # Remove episode information.
    filename = re.sub(
        r"(?i)\bs\d{1,2}\s*e\d{1,3}\b",
        " ",
        filename,
    )

    filename = re.sub(
        r"(?i)\bseason\s*\d{1,2}\b",
        " ",
        filename,
    )

    title_words = [
        x
        for x in title.split()
        if len(x) >= 2
    ]

    filename_words = set(
        filename.split()
    )

    if not title_words:
        return False

    matched = 0

    for word in title_words:

        if word in filename_words:

            matched += 1

            continue

        # Prefix match.
        #
        # Example:
        #
        # game
        # games
        #

        for candidate in filename_words:

            if (
                candidate.startswith(
                    word
                )
                or word.startswith(
                    candidate
                )
            ):

                matched += 1

                break

    score = (
        matched
        / len(title_words)
    ) * 100

    return score >= 60


# ============================================================
# DATABASES
# ============================================================

def get_databases():

    databases = [
        (
            "Media",
            db,
        )
    ]

    if MULTIPLE_DB:

        databases.append(
            (
                "Media2",
                db2,
            )
        )

        databases.append(
            (
                "Media3",
                db3,
            )
        )

    return databases


# ============================================================
# TARGETED MONGODB SEARCH
# ============================================================
#
# THIS IS THE IMPORTANT PERFORMANCE PART.
#
# We DO NOT do:
#
# collection.find({})
#
# We retrieve only documents that contain:
#
# S01E
#
# Then filter them in Python.
#
# Later you should create a MongoDB index on file_name.
#
# ============================================================

async def search_series_files(
    title,
    language,
    season,
    quality,
):

    results = []

    season_text = (
        f"S{season:02d}"
    )

    # --------------------------------------------------------
    # Build regex.
    #
    # This avoids reading every document into Python.
    # --------------------------------------------------------

    season_regex = re.compile(
        rf"(?i){re.escape(season_text)}"
        rf"[\s._\-]*E\d{{1,3}}\b"
    )

    # --------------------------------------------------------
    # Search databases one by one.
    #
    # Media first.
    # Then Media2.
    # Then Media3.
    #
    # --------------------------------------------------------

    for db_name, database in get_databases():

        try:

            collection = database[
                COLLECTION_NAME
            ]

            # ------------------------------------------------
            # MongoDB server-side filter.
            # ------------------------------------------------

            cursor = collection.find(
                {
                    "file_name": {
                        "$regex":
                            season_regex
                    }
                },
                {
                    "_id": 1,
                    "file_name": 1,
                    "file_size": 1,
                    "file_type": 1,
                    "mime_type": 1,
                    "caption": 1,
                    "file_ref": 1,
                    "file_id": 1,
                },
            )

            async for document in cursor:

                filename = document.get(
                    "file_name"
                )

                if not filename:
                    continue

                # --------------------------------------------
                # Season / episode
                # --------------------------------------------

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

                # --------------------------------------------
                # Title
                # --------------------------------------------

                if not filename_title_match(
                    title,
                    filename,
                ):

                    continue

                # --------------------------------------------
                # Language
                # --------------------------------------------

                if not filename_has_language(
                    filename,
                    language,
                ):

                    continue

                # --------------------------------------------
                # Quality
                # --------------------------------------------

                file_quality = (
                    extract_quality(
                        filename
                    )
                )

                if (
                    quality
                    and file_quality
                    != quality
                ):

                    continue

                # --------------------------------------------
                # Save
                # --------------------------------------------

                document[
                    "_db"
                ] = db_name

                document[
                    "_season"
                ] = season

                document[
                    "_episode"
                ] = episode

                document[
                    "_quality"
                ] = file_quality

                results.append(
                    document
                )

        except Exception as e:

            logger.exception(
                "[SERIES] DB %s search failed: %s",
                db_name,
                e,
            )

    return results


# ============================================================
# BEST FILE PER EPISODE
# ============================================================

def file_score(
    document,
):

    quality = parse_int(
        document.get(
            "_quality",
            0,
        )
    )

    size = parse_int(
        document.get(
            "file_size",
            0,
        )
    )

    filename = str(
        document.get(
            "file_name",
            "",
        )
    )

    # Bigger file usually means better encode
    # when resolution is identical.
    #
    # This is only a secondary selection criterion.

    return (
        quality * 10_000_000_000
        + size
        + len(filename)
    )


def choose_best_episode_files(
    files,
):

    best = {}

    for document in files:

        episode = document.get(
            "_episode"
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

        if file_score(
            document
        ) > file_score(
            old
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
    text,
):

    if not IMDB_AVAILABLE:
        return []

    clean = normalize_title(
        text
    )

    if not clean:
        return []

    # --------------------------------------------------------
    # Cache.
    # --------------------------------------------------------

    cached = IMDB_SEARCH_CACHE.get(
        clean
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
        :SERIES_SEARCH_LIMIT
    ]:

        title = item.get(
            "title"
        )

        if not title:
            continue

        kind = str(
            item.get(
                "kind",
                ""
            )
        ).lower()

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # We do not trust only kind.
        #
        # Some IMDb results can have incomplete metadata.
        #
        # ----------------------------------------------------

        is_tv = (
            "tv" in kind
            or kind in (
                "series",
                "tv series",
                "tv mini series",
                "tv movie",
            )
        )

        output.append(
            {
                "id": str(
                    item.movieID
                    if getattr(
                        item,
                        "movieID",
                        None,
                    )
                    else item.get(
                        "movieID",
                        ""
                    )
                ),
                "title": str(
                    title
                ),
                "year": item.get(
                    "year"
                ),
                "kind": kind,
                "is_tv": is_tv,
            }
        )

    IMDB_SEARCH_CACHE[
        clean
    ] = output

    return output


# ============================================================
# SERIES CANDIDATES
# ============================================================

async def get_series_candidates(
    query,
):

    results = await imdb_search(
        query
    )

    candidates = []

    for result in results:

        title = result[
            "title"
        ]

        score = similarity(
            query,
            title,
        )

        # Exact match gets highest priority.

        if normalize_title(
            query
        ) == normalize_title(
            title
        ):

            score = 100

        result[
            "score"
        ] = score

        candidates.append(
            result
        )

    candidates.sort(
        key=lambda x: (
            x.get(
                "is_tv",
                False
            ),
            x.get(
                "score",
                0,
            ),
        ),
        reverse=True,
    )

    return candidates


# ============================================================
# IMDb DETAILS
# ============================================================

async def get_series_details(
    imdb_id,
):

    if not imdb_id:
        return {}

    cached = (
        IMDB_DETAILS_CACHE.get(
            imdb_id
        )
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

        poster = movie.get(
            "full-size cover url"
        )

        if not poster:

            poster = movie.get(
                "cover url"
            )

        details = {
            "id": str(
                imdb_id
            ),
            "title": title,
            "year": year,
            "rating": rating,
            "genres": genres,
            "plot": plot,
            "poster": poster,
        }

        IMDB_DETAILS_CACHE[
            imdb_id
        ] = details

        return details

    except Exception as e:

        logger.warning(
            "[SERIES] IMDb details failed: %s",
            e,
        )

        return {}


# ============================================================
# CALLBACK DATA
# ============================================================

def cb_title(
    imdb_id,
):

    return (
        f"stv:{imdb_id}"
    )


def cb_language(
    imdb_id,
    language,
):

    return (
        f"slg:{imdb_id}:"
        f"{language}"
    )


def cb_season(
    imdb_id,
    language,
    season,
):

    return (
        f"ssn:{imdb_id}:"
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
        f"sql:{imdb_id}:"
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
        f"get:{imdb_id}:"
        f"{language}:"
        f"{season}:"
        f"{quality}"
    )


# ============================================================
# SEARCH BUTTONS
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
# DETAILS TEXT
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
        f"🎬 <b>{escape_html(title)}</b>"
    )

    if year:

        text += (
            f" ({year})"
        )

    text += "\n\n"

    if rating:

        try:

            text += (
                f"⭐ IMDb: "
                f"<b>{float(rating):.1f}/10</b>\n"
            )

        except Exception:

            pass

    if genres:

        text += (
            "🎭 Genres: "
            f"<b>{escape_html(', '.join(genres[:5]))}</b>\n"
        )

    if plot:

        plot_text = str(
            plot[0]
        )

        if len(
            plot_text
        ) > 450:

            plot_text = (
                plot_text[:450]
                + "..."
            )

        text += (
            "\n📝 "
            f"{escape_html(plot_text)}"
        )

    return text


# ============================================================
# LANGUAGE KEYBOARD
# ============================================================

def build_language_keyboard(
    imdb_id,
):

    rows = []

    for language, data in LANGUAGES.items():

        rows.append(
            [
                InlineKeyboardButton(
                    text=data[
                        "name"
                    ],
                    callback_data=cb_language(
                        imdb_id,
                        language,
                    ),
                )
            ]
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

    # Keep this lightweight.
    #
    # We don't query MongoDB here.
    #
    # User can select S01-S30.
    #
    # The actual database search happens only
    # after GET FILES.

    current = []

    for season in range(
        1,
        31,
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
# SERIES SEARCH HANDLER
# ============================================================
#
# ONLY this group.
#
# This handler does NOT query MongoDB.
#
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

    if query.startswith(
        "/"
    ):
        return

    # --------------------------------------------------------
    # SEARCH IMDb ONLY.
    # --------------------------------------------------------

    msg = await message.reply_text(
        "🔎 <b>Searching series...</b>",
        parse_mode=enums.ParseMode.HTML,
    )

    candidates = await get_series_candidates(
        query
    )

    # --------------------------------------------------------
    # Only show TV candidates.
    #
    # Movies are NOT displayed as series.
    # --------------------------------------------------------

    series_candidates = [
        x
        for x in candidates
        if x.get(
            "is_tv",
            False
        )
    ]

    # --------------------------------------------------------
    # No series.
    # --------------------------------------------------------

    if not series_candidates:

        text = (
            "❌ <b>No series found.</b>\n\n"
            f"🔎 {escape_html(query)}"
        )

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

        try:

            await msg.edit_text(
                text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
            )

        except Exception:

            pass

        return

    # --------------------------------------------------------
    # Results.
    # --------------------------------------------------------

    text = (
        "📺 <b>SELECT SERIES</b>\n\n"
        f"🔎 Search: <b>{escape_html(query)}</b>\n\n"
        "Choose the correct series:"
    )

    try:

        await msg.edit_text(
            text,
            reply_markup=build_search_keyboard(
                series_candidates
            ),
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:

        pass


# ============================================================
# TITLE SELECT
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^stv:"
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
            "Series system is not available here.",
            show_alert=True,
        )

        return

    imdb_id = query.data[
        4:
    ]

    await query.answer(
        "📺 Loading..."
    )

    details = await get_series_details(
        imdb_id
    )

    if not details:

        await query.message.edit_text(
            "❌ Unable to load IMDb information.",
            parse_mode=enums.ParseMode.HTML,
        )

        return

    # --------------------------------------------------------
    # Save only in RAM.
    # --------------------------------------------------------

    USER_SERIES_STATE[
        query.from_user.id
    ] = {
        "imdb_id": imdb_id,
        "title": details.get(
            "title",
            "",
        ),
    }

    cleanup_state()

    text = build_details_text(
        details
    )

    text += (
        "\n\n"
        "🌐 <b>Select Language</b>"
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
                chat_id=query.message.chat.id,
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
                "[SERIES] Poster failed: %s",
                e,
            )

    try:

        await query.message.edit_text(
            text,
            reply_markup=build_language_keyboard(
                imdb_id
            ),
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:

        pass


# ============================================================
# LANGUAGE SELECT
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^slg:"
    )
)
async def series_language_callback(
    app,
    query,
):

    parts = query.data.split(
        ":"
    )

    if len(parts) != 3:
        return

    imdb_id = parts[1]
    language = parts[2]

    state = USER_SERIES_STATE.setdefault(
        query.from_user.id,
        {}
    )

    state[
        "imdb_id"
    ] = imdb_id

    state[
        "language"
    ] = language

    # --------------------------------------------------------
    # THIS is the plain text representation.
    #
    # We do not store "English".
    #
    # We store:
    #
    # eng
    #
    # --------------------------------------------------------

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
        f"Language: {language}"
    )

    text = (
        "📺 <b>"
        f"{escape_html(title)}"
        "</b>\n\n"
        f"🌐 Language: <b>{language}</b>\n\n"
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

        pass


# ============================================================
# SEASON SELECT
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^ssn:"
    )
)
async def series_season_callback(
    app,
    query,
):

    parts = query.data.split(
        ":"
    )

    if len(parts) != 4:
        return

    imdb_id = parts[1]
    language = parts[2]
    season = parse_int(
        parts[3]
    )

    if season <= 0:
        return

    state = USER_SERIES_STATE.setdefault(
        query.from_user.id,
        {}
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

    # --------------------------------------------------------
    # Store:
    #
    # S01
    #
    # not:
    #
    # Season 1
    #
    # --------------------------------------------------------

    state[
        "season_token"
    ] = (
        f"S{season:02d}"
    )

    details = await get_series_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series",
    )

    await query.answer(
        f"Season {season}"
    )

    text = (
        "📺 <b>"
        f"{escape_html(title)}"
        "</b>\n\n"
        f"🌐 Language: <b>{language}</b>\n"
        f"📚 Season: <b>S{season:02d}</b>\n\n"
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

        pass


# ============================================================
# QUALITY SELECT
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^sql:"
    )
)
async def series_quality_callback(
    app,
    query,
):

    parts = query.data.split(
        ":"
    )

    if len(parts) != 5:
        return

    imdb_id = parts[1]
    language = parts[2]

    season = parse_int(
        parts[3]
    )

    quality = parse_int(
        parts[4]
    )

    state = USER_SERIES_STATE.setdefault(
        query.from_user.id,
        {}
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

    await query.answer(
        "Quality selected"
    )

    # --------------------------------------------------------
    # The exact compact search representation:
    #
    # Game of Thrones eng S01 1080p
    #
    # This is only created here.
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

    text = (
        "📺 <b>"
        f"{escape_html(title)}"
        "</b>\n\n"

        f"🌐 Language: <b>{language}</b>\n"
        f"📚 Season: <b>S{season:02d}</b>\n"
        f"🎯 Quality: <b>"
        f"{quality_text(quality)}"
        "</b>\n\n"

        "🔎 <b>Ready to search</b>\n\n"

        f"<code>"
        f"{escape_html(search_string)}"
        f"</code>\n\n"

        "Press <b>GET FILES</b>."
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
# GET FILES
# ============================================================
#
# ONLY HERE MongoDB is searched.
#
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^get:"
    )
)
async def series_get_files_callback(
    app,
    query,
):

    parts = query.data.split(
        ":"
    )

    if len(parts) != 5:
        return

    imdb_id = parts[1]
    language = parts[2]

    season = parse_int(
        parts[3]
    )

    quality = parse_int(
        parts[4]
    )

    # --------------------------------------------------------
    # Get title.
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
    # Show searching state.
    # --------------------------------------------------------

    try:

        await query.message.edit_text(
            "🔎 <b>SEARCHING FILES...</b>\n\n"
            f"📺 {escape_html(title)}\n"
            f"🌐 {language}\n"
            f"📚 S{season:02d}\n"
            f"🎯 {quality_text(quality)}\n\n"
            "⏳ Please wait...",
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:

        pass

    # --------------------------------------------------------
    # TARGETED DB SEARCH.
    # --------------------------------------------------------

    files = await search_series_files(
        title=title,
        language=language,
        season=season,
        quality=quality,
    )

    # --------------------------------------------------------
    # Pick one best file per episode.
    # --------------------------------------------------------

    best_files = choose_best_episode_files(
        files
    )

    # --------------------------------------------------------
    # Nothing.
    # --------------------------------------------------------

    if not best_files:

        text = (
            "❌ <b>NO FILES FOUND</b>\n\n"
            f"📺 <b>{escape_html(title)}</b>\n"
            f"🌐 Language: <b>{language}</b>\n"
            f"📚 Season: <b>S{season:02d}</b>\n"
            f"🎯 Quality: <b>{quality_text(quality)}</b>\n\n"
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
    # Episodes.
    # --------------------------------------------------------

    episode_numbers = sorted(
        best_files.keys()
    )

    if (
        SERIES_MAX_EPISODES
        > 0
    ):

        episode_numbers = (
            episode_numbers[
                :SERIES_MAX_EPISODES
            ]
        )

    text = (
        "✅ <b>FILES FOUND</b>\n\n"
        f"📺 <b>{escape_html(title)}</b>\n"
        f"🌐 {language}\n"
        f"📚 S{season:02d}\n"
        f"🎯 {quality_text(quality)}\n\n"
        f"📦 Episodes: <b>{len(episode_numbers)}</b>\n\n"
    )

    # --------------------------------------------------------
    # Show episode list.
    # --------------------------------------------------------

    text += (
        "🎞 <b>AVAILABLE EPISODES</b>\n\n"
    )

    for episode in episode_numbers:

        file = best_files[
            episode
        ]

        filename = file.get(
            "file_name",
            f"E{episode:02d}",
        )

        text += (
            f"• E{episode:02d} "
            f"— "
            f"{escape_html(filename[:70])}\n"
        )

    # --------------------------------------------------------
    # SEND FILES.
    # --------------------------------------------------------

    try:

        await query.message.edit_text(
            text,
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:

        pass

    sent = 0

    for episode in episode_numbers:

        file = best_files[
            episode
        ]

        # ----------------------------------------------------
        # Important:
        #
        # Your DB may store Telegram cached file ID in:
        #
        # file_id
        #
        # or another field.
        #
        # We check the common fields.
        # ----------------------------------------------------

        file_id = (
            file.get(
                "file_id"
            )
            or file.get(
                "file_ref"
            )
        )

        if not file_id:

            logger.warning(
                "[SERIES] Missing file_id for E%02d: %s",
                episode,
                file.get(
                    "file_name"
                ),
            )

            continue

        filename = file.get(
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

            await asyncio.sleep(
                SERIES_SEND_DELAY
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
                    caption=caption,
                )

                sent += 1

            except Exception:

                logger.exception(
                    "[SERIES] Retry failed E%02d",
                    episode,
                )

        except RPCError:

            logger.exception(
                "[SERIES] Telegram error E%02d",
                episode,
            )

        except Exception:

            logger.exception(
                "[SERIES] Failed E%02d",
                episode,
            )

    # --------------------------------------------------------
    # COMPLETE.
    # --------------------------------------------------------

    try:

        await query.message.reply_text(
            "✅ <b>SEASON COMPLETE</b>\n\n"
            f"📺 <b>{escape_html(title)}</b>\n"
            f"📚 Season: <b>S{season:02d}</b>\n"
            f"🎯 Quality: <b>{quality_text(quality)}</b>\n\n"
            f"📦 Sent: <b>{sent}</b>/"
            f"<b>{len(episode_numbers)}</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:

        pass


# ============================================================
# OPTIONAL CACHE CLEAR
# ============================================================

def clear_series_memory():

    IMDB_SEARCH_CACHE.clear()

    IMDB_DETAILS_CACHE.clear()

    USER_SERIES_STATE.clear()

    logger.info(
        "[SERIES] Memory cache cleared."
    )


# ============================================================
# STARTUP
# ============================================================

logger.info(
    "=================================================="
)

logger.info(
    "[SERIES] DowntownVilla Series System Loaded"
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
    "[SERIES] Poster: %s",
    SERIES_POSTER,
)

logger.info(
    "[SERIES] Movie group: %s",
    MOVIE_GROUP_LINK
    or "NOT SET",
)

logger.info(
    "[SERIES] MongoDB is queried ONLY after GET FILES."
)

logger.info(
    "=================================================="
)
