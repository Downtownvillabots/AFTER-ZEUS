```python
# ============================================================
# DOWNTOWNVILLA - SERIES SEARCH SYSTEM
# ============================================================
#
# FLOW
#
# SERIES GROUP
#       ↓
# User searches:
#       GOT
#       ↓
# IMDb / Cinemagoer
#       ↓
# Game of Thrones
#       ↓
# IMDb details + poster
#       ↓
# "GET GAME OF THRONES"
#       ↓
# PM deep-link
#       ↓
# /start series_<imdb_id>
#       ↓
# IMDb details
#       ↓
# LANGUAGE
#       ↓
# eng / mal / hin / tam / tel / kan
#       ↓
# SEASON
#       ↓
# S01
#       ↓
# QUALITY
#       ↓
# 1080p
#       ↓
# GET FILES
#       ↓
# TARGETED SEARCH
#       ↓
# E01 E02 E03 E04...
#
# IMPORTANT:
#
# MongoDB is NOT searched while typing/searching IMDb.
#
# MongoDB is searched ONLY after GET FILES.
#
# NO STREAMING BUTTON.
#
# ============================================================

import os
import re
import asyncio
import logging
from collections import OrderedDict
from difflib import SequenceMatcher
from html import escape

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
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

SERIES_SEND_DELAY = float(
    os.getenv(
        "SERIES_SEND_DELAY",
        "0.3",
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
        "[SERIES] Invalid SERIES_GROUP_ID: %s",
        SERIES_GROUP_ID,
    )


# ============================================================
# IMDb / CINEMAGOER
# ============================================================

try:
    import imdb

    imdb_api = imdb.IMDb()

    IMDB_AVAILABLE = True

    logger.info(
        "[SERIES] Cinemagoer / IMDb loaded."
    )

except Exception as e:
    imdb_api = None
    IMDB_AVAILABLE = False

    logger.warning(
        "[SERIES] IMDb unavailable: %s",
        e,
    )


# ============================================================
# LANGUAGES
# ============================================================

LANGUAGES = {
    "eng": {
        "name": "🇬🇧 English",
        "triggers": (
            "eng",
            "english",
            "en",
        ),
    },

    "mal": {
        "name": "🇮🇳 Malayalam",
        "triggers": (
            "mal",
            "malayalam",
        ),
    },

    "hin": {
        "name": "🇮🇳 Hindi",
        "triggers": (
            "hin",
            "hindi",
        ),
    },

    "tam": {
        "name": "🇮🇳 Tamil",
        "triggers": (
            "tam",
            "tamil",
        ),
    },

    "tel": {
        "name": "🇮🇳 Telugu",
        "triggers": (
            "tel",
            "telugu",
        ),
    },

    "kan": {
        "name": "🇮🇳 Kannada",
        "triggers": (
            "kan",
            "kannada",
        ),
    },
}


# ============================================================
# QUALITY
# ============================================================

QUALITIES = (
    2160,
    1440,
    1080,
    720,
    576,
    480,
    360,
)


def quality_text(value):
    value = int(value or 0)

    if value == 2160:
        return "4K"

    if value:
        return f"{value}p"

    return "Unknown"


# ============================================================
# MEMORY CACHE
# ============================================================

IMDB_SEARCH_CACHE = OrderedDict()

IMDB_DETAILS_CACHE = OrderedDict()

USER_SERIES_STATE = {}

MAX_CACHE_ITEMS = 5000

MAX_STATE_USERS = 10000


def cache_set(cache, key, value):
    cache[key] = value
    cache.move_to_end(key)

    while len(cache) > MAX_CACHE_ITEMS:
        cache.popitem(last=False)


def cleanup_state():
    while len(USER_SERIES_STATE) > MAX_STATE_USERS:
        USER_SERIES_STATE.pop(
            next(iter(USER_SERIES_STATE)),
            None,
        )


# ============================================================
# NORMALIZATION
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
        r"[\[\]{}()#+=|\\/:;!?@#$%^&*~`'\"<>]",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip().lower()


def title_similarity(a, b):
    a = normalize_title(a)
    b = normalize_title(b)

    if not a or not b:
        return 0

    if a == b:
        return 100

    return int(
        SequenceMatcher(
            None,
            a,
            b,
        ).ratio()
        * 100
    )


def html(value):
    return escape(
        str(value or "")
    )


# ============================================================
# IMDb RESULT TYPE DETECTION
# ============================================================

def is_series_result(item):
    """
    Cinemagoer 'kind' is not always consistent.

    We therefore accept the common TV/series kinds.
    """

    kind = str(
        item.get(
            "kind",
            "",
        )
        or ""
    ).lower()

    accepted = (
        "tv series",
        "tv mini series",
        "tv mini-series",
        "tv episode",
        "tv movie",
        "series",
        "mini series",
        "mini-series",
    )

    if kind in accepted:
        return True

    if "tv series" in kind:
        return True

    if "mini series" in kind:
        return True

    if "mini-series" in kind:
        return True

    return False


# ============================================================
# IMDb SEARCH
# ============================================================

async def search_imdb_series(query):
    """
    Search IMDb/Cinemagoer.

    This function is fully contained in this file.
    No external get_series_candidates() is required.
    """

    if not IMDB_AVAILABLE:
        return []

    clean_query = normalize_title(
        query
    )

    if not clean_query:
        return []

    cached = IMDB_SEARCH_CACHE.get(
        clean_query
    )

    if cached is not None:
        return cached

    try:
        raw_results = await asyncio.to_thread(
            imdb_api.search_movie,
            clean_query,
        )

    except Exception as e:
        logger.exception(
            "[SERIES] IMDb search failed: %s",
            e,
        )
        return []

    results = []

    for item in raw_results[
        :30
    ]:

        title = item.get(
            "title"
        )

        if not title:
            continue

        movie_id = (
            getattr(
                item,
                "movieID",
                None,
            )
            or item.get(
                "movieID"
            )
        )

        if not movie_id:
            continue

        result = {
            "id": str(
                movie_id
            ),
            "title": str(
                title
            ),
            "year": item.get(
                "year"
            ),
            "kind": str(
                item.get(
                    "kind",
                    "",
                )
                or ""
            ),
        }

        result[
            "is_series"
        ] = is_series_result(
            item
        )

        result[
            "score"
        ] = title_similarity(
            query,
            title,
        )

        results.append(
            result
        )

    # --------------------------------------------------------
    # Put series first.
    # --------------------------------------------------------

    results.sort(
        key=lambda x: (
            x.get(
                "is_series",
                False,
            ),
            x.get(
                "score",
                0,
            ),
        ),
        reverse=True,
    )

    # --------------------------------------------------------
    # We ONLY return series here.
    # --------------------------------------------------------

    series_results = [
        item
        for item in results
        if item.get(
            "is_series",
            False,
        )
    ]

    # --------------------------------------------------------
    # Fallback:
    #
    # Cinemagoer sometimes returns incomplete "kind".
    #
    # If there are no detected series, inspect details for
    # the strongest few results.
    # --------------------------------------------------------

    if not series_results:

        for item in results[
            :8
        ]:

            try:
                details = await get_imdb_details(
                    item["id"]
                )

                if looks_like_series_details(
                    details
                ):
                    item[
                        "is_series"
                    ] = True

                    series_results.append(
                        item
                    )

            except Exception:
                continue

    series_results.sort(
        key=lambda x: x.get(
            "score",
            0,
        ),
        reverse=True,
    )

    series_results = series_results[
        :SERIES_SEARCH_LIMIT
    ]

    cache_set(
        IMDB_SEARCH_CACHE,
        clean_query,
        series_results,
    )

    return series_results


# ============================================================
# IMDb DETAIL TYPE CHECK
# ============================================================

def looks_like_series_details(details):
    if not details:
        return False

    kind = str(
        details.get(
            "kind",
            "",
        )
        or ""
    ).lower()

    if (
        "series" in kind
        or "tv" in kind
        or "mini" in kind
    ):
        return True

    # Cinemagoer sometimes provides series/episodes data.
    if details.get(
        "episodes"
    ):
        return True

    return False


# ============================================================
# IMDb DETAILS
# ============================================================

async def get_imdb_details(
    imdb_id,
):

    imdb_id = str(
        imdb_id or ""
    )

    if not imdb_id:
        return {}

    cached = IMDB_DETAILS_CACHE.get(
        imdb_id
    )

    if cached is not None:
        return cached

    if not IMDB_AVAILABLE:
        return {}

    try:
        movie = await asyncio.to_thread(
            imdb_api.get_movie,
            imdb_id,
        )

    except Exception as e:
        logger.exception(
            "[SERIES] IMDb details failed for %s: %s",
            imdb_id,
            e,
        )
        return {}

    title = movie.get(
        "title"
    )

    if not title:
        return {}

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

    kind = str(
        movie.get(
            "kind",
            "",
        )
        or ""
    )

    details = {
        "id": imdb_id,
        "title": str(
            title
        ),
        "year": year,
        "rating": rating,
        "genres": genres,
        "plot": plot,
        "poster": poster,
        "kind": kind,
    }

    cache_set(
        IMDB_DETAILS_CACHE,
        imdb_id,
        details,
    )

    return details


# ============================================================
# CALLBACK DATA
# ============================================================

def callback_series_title(imdb_id):
    return (
        f"ser_title:{imdb_id}"
    )


def callback_open_pm(imdb_id):
    return (
        f"ser_pm:{imdb_id}"
    )


def callback_language(
    imdb_id,
    language,
):
    return (
        f"ser_lang:{imdb_id}:{language}"
    )


def callback_season(
    imdb_id,
    language,
    season,
):
    return (
        f"ser_season:"
        f"{imdb_id}:"
        f"{language}:"
        f"{season}"
    )


def callback_quality(
    imdb_id,
    language,
    season,
    quality,
):
    return (
        f"ser_quality:"
        f"{imdb_id}:"
        f"{language}:"
        f"{season}:"
        f"{quality}"
    )


def callback_get_files(
    imdb_id,
    language,
    season,
    quality,
):
    return (
        f"ser_get:"
        f"{imdb_id}:"
        f"{language}:"
        f"{season}:"
        f"{quality}"
    )


# ============================================================
# SEARCH RESULT KEYBOARD
# ============================================================

def build_series_results_keyboard(
    results
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

        button_text = (
            f"📺 {title}"
        )

        if year:
            button_text += (
                f" ({year})"
            )

        rows.append(
            [
                InlineKeyboardButton(
                    text=button_text[
                        :64
                    ],
                    callback_data=callback_series_title(
                        item[
                            "id"
                        ]
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
        f"🎬 <b>{html(title)}</b>"
    )

    if year:
        text += (
            f" ({year})"
        )

    text += "\n"

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
            f"<b>{html(', '.join(genres[:5]))}</b>\n"
        )

    if plot:
        plot_text = str(
            plot[0]
        )

        if len(plot_text) > 450:
            plot_text = (
                plot_text[:450]
                + "..."
            )

        text += (
            "\n📝 "
            f"{html(plot_text)}"
        )

    return text


# ============================================================
# PM LINK
# ============================================================

async def get_bot_username(
    app
):

    try:
        me = await app.get_me()

        return (
            me.username
            if me and me.username
            else None
        )

    except Exception as e:
        logger.warning(
            "[SERIES] Cannot get bot username: %s",
            e,
        )

        return None


async def make_pm_url(
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
# LANGUAGE KEYBOARD
# ============================================================

def build_language_keyboard(
    imdb_id
):

    rows = []

    for language, data in LANGUAGES.items():

        rows.append(
            [
                InlineKeyboardButton(
                    text=data[
                        "name"
                    ],
                    callback_data=callback_language(
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

    current_row = []

    for season in range(
        1,
        31,
    ):

        current_row.append(
            InlineKeyboardButton(
                text=f"S{season:02d}",
                callback_data=callback_season(
                    imdb_id,
                    language,
                    season,
                ),
            )
        )

        if len(
            current_row
        ) == 3:

            rows.append(
                current_row
            )

            current_row = []

    if current_row:
        rows.append(
            current_row
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

    rows = []

    current_row = []

    for quality in QUALITIES:

        current_row.append(
            InlineKeyboardButton(
                text=quality_text(
                    quality
                ),
                callback_data=callback_quality(
                    imdb_id,
                    language,
                    season,
                    quality,
                ),
            )
        )

        if len(
            current_row
        ) == 2:

            rows.append(
                current_row
            )

            current_row = []

    if current_row:
        rows.append(
            current_row
        )

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# GET FILES KEYBOARD
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
                    callback_data=callback_get_files(
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
    group=-100,
)
async def series_group_search(
    app,
    message,
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

    if query.startswith("/"):
        return

    searching_message = await message.reply_text(
        "🔎 <b>Finding the series...</b>",
        parse_mode=enums.ParseMode.HTML,
    )

    # --------------------------------------------------------
    # IMDb ONLY.
    #
    # NO MONGODB HERE.
    # --------------------------------------------------------

    results = await search_imdb_series(
        query
    )

    # --------------------------------------------------------
    # NO SERIES
    # --------------------------------------------------------

    if not results:

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
            await searching_message.edit_text(
                (
                    "❌ <b>No series found.</b>\n\n"
                    f"🔎 Search: <b>{html(query)}</b>\n\n"
                    "Try the full series name."
                ),
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

        return

    # --------------------------------------------------------
    # SHOW IMDb SUGGESTIONS
    # --------------------------------------------------------

    text = (
        "📺 <b>SELECT YOUR SERIES</b>\n\n"
        f"🔎 Search: <b>{html(query)}</b>\n\n"
        "Choose the correct IMDb result:"
    )

    try:
        await searching_message.edit_text(
            text,
            reply_markup=build_series_results_keyboard(
                results
            ),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass


# ============================================================
# TITLE SELECTED
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^ser_title:"
    )
)
async def series_title_selected(
    app,
    query,
):

    if (
        SERIES_CHAT_ID is None
        or not query.message
        or query.message.chat.id
        != SERIES_CHAT_ID
    ):
        await query.answer(
            "This is not the series group.",
            show_alert=True,
        )
        return

    imdb_id = query.data.split(
        ":",
        1,
    )[1]

    await query.answer(
        "📺 Loading IMDb..."
    )

    details = await get_imdb_details(
        imdb_id
    )

    if not details:
        await query.message.edit_text(
            "❌ Could not load IMDb details."
        )
        return

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
        "👇 <b>Continue to the bot to select "
        "language, season and quality.</b>"
    )

    pm_url = await make_pm_url(
        app,
        imdb_id,
    )

    buttons = []

    if pm_url:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        "📺 GET "
                        f"{details.get('title', 'SERIES')}"
                    )[:64],
                    url=pm_url,
                )
            ]
        )

    # Movie group only as optional fallback.
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
    # POSTER
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
                parse_mode=enums.ParseMode.HTML,
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
# PM START
# ============================================================
#
# User presses:
#
# GET GAME OF THRONES
#
# Telegram opens:
#
# /start series_0944947
#
# This handler restores the series flow.
#
# ============================================================

@Client.on_message(
    filters.command("start")
    & filters.private,
    group=-100,
)
async def series_pm_start(
    app,
    message,
):

    if not message.command:
        return

    if len(
        message.command
    ) < 2:
        return

    payload = (
        message.command[1]
        or ""
    ).strip()

    if not payload.startswith(
        "series_"
    ):
        return

    imdb_id = payload[
        len("series_"):
    ]

    if not imdb_id:
        return

    details = await get_imdb_details(
        imdb_id
    )

    if not details:
        await message.reply_text(
            "❌ Could not load the series."
        )
        return

    USER_SERIES_STATE[
        message.from_user.id
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
    # Poster in PM.
    # --------------------------------------------------------

    if (
        SERIES_POSTER
        and details.get(
            "poster"
        )
    ):

        try:
            await app.send_photo(
                chat_id=message.chat.id,
                photo=details[
                    "poster"
                ],
                caption=text,
                reply_markup=build_language_keyboard(
                    imdb_id
                ),
                parse_mode=enums.ParseMode.HTML,
            )

            return

        except Exception as e:
            logger.warning(
                "[SERIES] PM poster failed: %s",
                e,
            )

    await message.reply_text(
        text,
        reply_markup=build_language_keyboard(
            imdb_id
        ),
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# LANGUAGE SELECT
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^ser_lang:"
    )
)
async def series_language_selected(
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

    if language not in LANGUAGES:
        await query.answer(
            "Invalid language.",
            show_alert=True,
        )
        return

    details = await get_imdb_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series",
    )

    # --------------------------------------------------------
    # STORE COMPACT VALUE ONLY.
    #
    # English -> eng
    # Malayalam -> mal
    # Hindi -> hin
    #
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
        "language_token"
    ] = language

    await query.answer(
        f"Selected {language}"
    )

    text = (
        "📺 <b>"
        f"{html(title)}"
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
        r"^ser_season:"
    )
)
async def series_season_selected(
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

    try:
        season = int(
            parts[3]
        )
    except Exception:
        return

    if season < 1 or season > 30:
        await query.answer(
            "Invalid season.",
            show_alert=True,
        )
        return

    details = await get_imdb_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series",
    )

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

    # --------------------------------------------------------
    # Compact storage:
    #
    # S01
    # --------------------------------------------------------

    state[
        "season_token"
    ] = (
        f"S{season:02d}"
    )

    await query.answer(
        f"S{season:02d}"
    )

    text = (
        "📺 <b>"
        f"{html(title)}"
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
        r"^ser_quality:"
    )
)
async def series_quality_selected(
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

    try:
        season = int(
            parts[3]
        )

        quality = int(
            parts[4]
        )

    except Exception:
        return

    if quality not in QUALITIES:
        await query.answer(
            "Invalid quality.",
            show_alert=True,
        )
        return

    details = await get_imdb_details(
        imdb_id
    )

    title = details.get(
        "title",
        "Series",
    )

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
        "quality_token"
    ] = quality_text(
        quality
    )

    # --------------------------------------------------------
    # THIS IS THE FINAL COMPACT SEARCH.
    #
    # Example:
    #
    # Game of Thrones eng S01 1080p
    #
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
        f"{html(title)}"
        "</b>\n\n"

        f"🌐 Language: <b>{language}</b>\n"
        f"📚 Season: <b>S{season:02d}</b>\n"
        f"🎯 Quality: <b>"
        f"{quality_text(quality)}"
        "</b>\n\n"

        "🔎 <b>READY</b>\n\n"

        f"<code>"
        f"{html(search_string)}"
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
# DATABASE HELPERS
# ============================================================

def get_series_models():

    models = [
        Media,
    ]

    if MULTIPLE_DB:
        models.extend(
            [
                Media2,
                Media3,
            ]
        )

    return models


# ============================================================
# FILE NAME HELPERS
# ============================================================

def extract_season_episode(
    filename,
):

    if not filename:
        return None, None

    name = str(
        filename
    )

    # S01E01
    # S1E1
    # S01.E01
    # S01-E01
    # S01_E01

    match = re.search(
        r"(?i)\bS"
        r"(\d{1,2})"
        r"[\s._\-]*"
        r"E"
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

    # Season 01 Episode 01

    match = re.search(
        r"(?i)\bSeason"
        r"[\s._\-]*"
        r"(\d{1,2})"
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
# QUALITY EXTRACTION
# ============================================================

def extract_quality(
    filename,
):

    if not filename:
        return 0

    name = str(
        filename
    )

    patterns = (
        (
            2160,
            r"(?i)(?:\b2160p\b|\b4k\b|\buhd\b)",
        ),
        (
            1440,
            r"(?i)\b1440p\b",
        ),
        (
            1080,
            r"(?i)\b1080p\b|\b1080i\b",
        ),
        (
            720,
            r"(?i)\b720p\b",
        ),
        (
            576,
            r"(?i)\b576p\b",
        ),
        (
            480,
            r"(?i)\b480p\b",
        ),
        (
            360,
            r"(?i)\b360p\b",
        ),
    )

    for quality, pattern in patterns:

        if re.search(
            pattern,
            name,
        ):
            return quality

    return 0


# ============================================================
# LANGUAGE MATCHING
# ============================================================

def filename_has_language(
    filename,
    language,
):

    if not filename:
        return False

    normalized = normalize_title(
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

        if normalize_title(
            trigger
        ) in words:
            return True

    return False


# ============================================================
# TITLE MATCHING
# ============================================================

def filename_has_title(
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

    # Remove S01E01.
    filename = re.sub(
        r"(?i)\bs\d{1,2}\s*e\d{1,3}\b",
        " ",
        filename,
    )

    # Remove Season 01.
    filename = re.sub(
        r"(?i)\bseason\s*\d{1,2}\b",
        " ",
        filename,
    )

    title_words = [
        word
        for word in title.split()
        if len(word) >= 2
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

        # Prefix fallback.
        for candidate in filename_words:

            if (
                candidate.startswith(word)
                or word.startswith(candidate)
            ):
                matched += 1
                break

    percentage = (
        matched
        / len(title_words)
    ) * 100

    return percentage >= 70


# ============================================================
# TARGETED SERIES DATABASE SEARCH
# ============================================================
#
# IMPORTANT:
#
# We do not call your normal get_search_results().
#
# That function is designed for normal movie/file search
# and returns only the configured max buttons.
#
# Series needs ALL episodes.
#
# Therefore we query the three collections directly.
#
# ============================================================

async def search_series_database(
    title,
    language,
    season,
    quality,
):

    season_token = (
        f"S{season:02d}"
    )

    # --------------------------------------------------------
    # Server-side MongoDB regex.
    #
    # This makes MongoDB look only for S01E...
    #
    # It does NOT download the whole collection.
    # --------------------------------------------------------

    season_pattern = (
        rf"(?i){re.escape(season_token)}"
        rf"[\s._\-]*E\d{{1,3}}\b"
    )

    mongo_filter = {
        "file_name": {
            "$regex": season_pattern,
        }
    }

    models = get_series_models()

    async def query_model(
        model
    ):

        found = []

        try:

            cursor = model.find(
                mongo_filter
            )

            async for document in cursor:

                filename = document.get(
                    "file_name"
                )

                if not filename:
                    continue

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
                # TITLE
                # ------------------------------------------------

                if not filename_has_title(
                    title,
                    filename,
                ):
                    continue

                # ------------------------------------------------
                # LANGUAGE
                # ------------------------------------------------

                if not filename_has_language(
                    filename,
                    language,
                ):
                    continue

                # ------------------------------------------------
                # QUALITY
                # ------------------------------------------------

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

                document[
                    "_series_episode"
                ] = episode

                document[
                    "_series_quality"
                ] = file_quality

                found.append(
                    document
                )

        except Exception as e:

            logger.exception(
                "[SERIES] Database query failed: %s",
                e,
            )

        return found

    # --------------------------------------------------------
    # ALL 3 DATABASES CONCURRENTLY
    # --------------------------------------------------------

    results = await asyncio.gather(
        *[
            query_model(model)
            for model in models
        ],
        return_exceptions=True,
    )

    combined = []

    for result in results:

        if isinstance(
            result,
            Exception,
        ):
            logger.exception(
                "[SERIES] DB task failed: %s",
                result,
            )
            continue

        combined.extend(
            result
        )

    return combined


# ============================================================
# FILE SCORING
# ============================================================

def file_score(
    document
):

    quality = int(
        document.get(
            "_series_quality",
            0,
        )
        or 0
    )

    size = int(
        document.get(
            "file_size",
            0,
        )
        or 0
    )

    return (
        quality * 10_000_000_000
        + size
    )


# ============================================================
# ONE BEST FILE PER EPISODE
# ============================================================

def select_episode_files(
    documents
):

    best = {}

    for document in documents:

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
            best.items(),
            key=lambda x: x[0],
        )
    )


# ============================================================
# GET FILES
# ============================================================

@Client.on_callback_query(
    filters.regex(
        r"^ser_get:"
    )
)
async def series_get_files(
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

    try:
        season = int(
            parts[3]
        )

        quality = int(
            parts[4]
        )

    except Exception:
        await query.answer(
            "Invalid selection.",
            show_alert=True,
        )
        return

    details = await get_imdb_details(
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
    # THIS is the ONLY place where MongoDB is searched.
    # --------------------------------------------------------

    try:
        await query.message.edit_text(
            (
                "🔎 <b>SEARCHING FILES...</b>\n\n"
                f"📺 <b>{html(title)}</b>\n"
                f"🌐 {language}\n"
                f"📚 S{season:02d}\n"
                f"🎯 {quality_text(quality)}\n\n"
                "⏳ Searching databases..."
            ),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass

    documents = await search_series_database(
        title=title,
        language=language,
        season=season,
        quality=quality,
    )

    episode_files = select_episode_files(
        documents
    )

    # --------------------------------------------------------
    # LIMIT IF CONFIGURED
    # --------------------------------------------------------

    episode_numbers = list(
        episode_files.keys()
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

    # --------------------------------------------------------
    # NO FILES
    # --------------------------------------------------------

    if not episode_numbers:

        try:
            await query.message.edit_text(
                (
                    "❌ <b>NO FILES FOUND</b>\n\n"
                    f"📺 <b>{html(title)}</b>\n"
                    f"🌐 Language: <b>{language}</b>\n"
                    f"📚 Season: <b>S{season:02d}</b>\n"
                    f"🎯 Quality: <b>{quality_text(quality)}</b>\n\n"
                    "Try another language or quality."
                ),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

        return

    # --------------------------------------------------------
    # SHOW FOUND EPISODES
    # --------------------------------------------------------

    episode_text = "\n".join(
        f"• E{episode:02d}"
        for episode in episode_numbers
    )

    try:
        await query.message.edit_text(
            (
                "✅ <b>FILES FOUND</b>\n\n"
                f"📺 <b>{html(title)}</b>\n"
                f"🌐 {language}\n"
                f"📚 S{season:02d}\n"
                f"🎯 {quality_text(quality)}\n\n"
                f"📦 Episodes: <b>{len(episode_numbers)}</b>\n\n"
                f"{episode_text}\n\n"
                "📤 Sending files..."
            ),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass

    # --------------------------------------------------------
    # SEND FILES
    # --------------------------------------------------------

    sent = 0

    for episode in episode_numbers:

        document = episode_files[
            episode
        ]

        # ----------------------------------------------------
        # YOUR Media models use:
        #
        # file_id = fields.StrField(attribute="_id")
        #
        # So the Mongo document normally has "_id".
        #
        # We check both.
        # ----------------------------------------------------

        file_id = (
            document.get(
                "file_id"
            )
            or document.get(
                "_id"
            )
        )

        file_ref = document.get(
            "file_ref"
        )

        if not file_id:

            logger.warning(
                "[SERIES] Missing file_id for E%02d",
                episode,
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

        # ----------------------------------------------------
        # SEND CACHED TELEGRAM MEDIA
        #
        # NO STREAM BUTTON.
        # ----------------------------------------------------

        try:

            await app.send_cached_media(
                chat_id=query.message.chat.id,
                file_id=str(
                    file_id
                ),
                caption=caption,
            )

            sent += 1

        except FloodWait as e:

            wait_time = int(
                getattr(
                    e,
                    "value",
                    10,
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
                "[SERIES] Failed sending E%02d",
                episode,
            )

        # ----------------------------------------------------
        # Small delay.
        # ----------------------------------------------------

        if SERIES_SEND_DELAY > 0:
            await asyncio.sleep(
                SERIES_SEND_DELAY
            )

    # --------------------------------------------------------
    # COMPLETE
    # --------------------------------------------------------

    try:

        await query.message.reply_text(
            (
                "✅ <b>SEASON COMPLETE</b>\n\n"
                f"📺 <b>{html(title)}</b>\n"
                f"🌐 Language: <b>{language}</b>\n"
                f"📚 Season: <b>S{season:02d}</b>\n"
                f"🎯 Quality: <b>{quality_text(quality)}</b>\n\n"
                f"📦 Sent: <b>{sent}</b>/"
                f"<b>{len(episode_numbers)}</b>"
            ),
            parse_mode=enums.ParseMode.HTML,
        )

    except Exception:
        pass


# ============================================================
# OPTIONAL DEBUG COMMAND
# ============================================================
#
# Use:
#
# /seriesdebug
#
# ONLY inside the series group.
#
# This lets you immediately verify whether IMDb is working.
#
# ============================================================

@Client.on_message(
    filters.command(
        "seriesdebug"
    ),
    group=-99,
)
async def series_debug(
    app,
    message,
):

    if (
        SERIES_CHAT_ID is None
        or message.chat.id
        != SERIES_CHAT_ID
    ):
        return

    text = (
        "📺 <b>SERIES SYSTEM</b>\n\n"
        f"Group ID: <code>{SERIES_CHAT_ID}</code>\n"
        f"IMDb: <b>{'ON' if IMDB_AVAILABLE else 'OFF'}</b>\n"
        f"Multiple DB: <b>{MULTIPLE_DB}</b>\n"
        f"Collection: <code>{html(COLLECTION_NAME)}</code>\n"
        f"Media: <b>ON</b>\n"
        f"Media2: <b>{'ON' if MULTIPLE_DB else 'OFF'}</b>\n"
        f"Media3: <b>{'ON' if MULTIPLE_DB else 'OFF'}</b>\n"
        f"Streaming: <b>OFF</b>"
    )

    await message.reply_text(
        text,
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# CACHE CLEAR
# ============================================================

def clear_series_cache():

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
    "[SERIES] Multiple DB: %s",
    MULTIPLE_DB,
)

logger.info(
    "[SERIES] Collection: %s",
    COLLECTION_NAME,
)

logger.info(
    "[SERIES] Streaming buttons: DISABLED"
)

logger.info(
    "[SERIES] MongoDB search: ONLY AFTER GET FILES"
)

logger.info(
    "=================================================="
)
```
