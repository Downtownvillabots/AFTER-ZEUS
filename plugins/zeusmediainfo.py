# Import the Pyrogram Client and filters
from pyrogram import Client, filters

# Import CallbackQuery (used when an inline button is clicked)
from pyrogram.types import CallbackQuery


# ==========================================================
# MediaInfo Callback (Temporary Test)
#
# This function will run ONLY when a button with
# callback_data starting with "mediainfo" is clicked.
#
# Right now it only shows a popup.
# Later it will display the actual MediaInfo.
# ==========================================================
@Client.on_callback_query(filters.regex("^mediainfo"))
async def mediainfo_callback(client: Client, query: CallbackQuery):

    # Show a popup message to the user.
    # This is only for testing.
    await query.answer(
        "🚧 MediaInfo feature is under development.",
        show_alert=True
    )
