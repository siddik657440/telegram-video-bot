import os
import sqlite3
import asyncio
import logging

from fastapi import FastAPI
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# আপনার Admin ID
ADMIN_IDS = {
    5632554230
}

DB_NAME = "videos.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

app = FastAPI()


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL,
            user_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

    logger.info("Database initialized")


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    user_id = update.effective_user.id

    if is_admin(user_id):
        text = (
            "🤖 Bot is online!\n\n"
            "Admin Commands:\n"
            "/start - Start bot\n"
            "/stats - Video statistics\n"
            "/delvideo ID - Delete video\n\n"
            "📹 Send a video to save it."
        )
    else:
        text = (
            "👋 Welcome!\n\n"
            "🤖 Bot is working."
        )

    await update.message.reply_text(text)


# =========================================================
# ADD VIDEO
# =========================================================

async def addvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    user_id = update.effective_user.id

    # Only admin can add videos
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ You are not authorized to add videos."
        )
        return

    if not update.message or not update.message.video:
        await update.message.reply_text(
            "❌ Please send a video."
        )
        return

    video = update.message.video
    file_id = video.file_id

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO videos (file_id, user_id)
        VALUES (?, ?)
        """,
        (file_id, user_id)
    )

    video_id = cur.lastrowid

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Video saved successfully!\n\n"
        f"🆔 Video ID: `{video_id}`",
        parse_mode="Markdown"
    )


# =========================================================
# DELETE VIDEO
# =========================================================

async def delvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ You are not authorized."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "/delvideo ID\n\n"
            "Example:\n"
            "/delvideo 5"
        )
        return

    try:
        video_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid video ID."
        )
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM videos WHERE id = ?",
        (video_id,)
    )

    video = cur.fetchone()

    if not video:
        conn.close()

        await update.message.reply_text(
            "❌ Video not found."
        )
        return

    cur.execute(
        "DELETE FROM videos WHERE id = ?",
        (video_id,)
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"🗑 Video `{video_id}` deleted successfully.",
        parse_mode="Markdown"
    )


# =========================================================
# STATS
# =========================================================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ You are not authorized."
        )
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) AS total FROM videos"
    )

    result = cur.fetchone()

    total = result["total"]

    conn.close()

    await update.message.reply_text(
        f"📊 Bot Statistics\n\n"
        f"🎬 Total Videos: {total}"
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Exception while handling update:",
        exc_info=context.error
    )


# =========================================================
# BOT
# =========================================================

async def run_bot():

    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("BOT_TOKEN is not configured.")
        return

    bot_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    bot_app.add_handler(
        CommandHandler("start", start)
    )

    bot_app.add_handler(
        CommandHandler("addvideo", addvideo)
    )

    bot_app.add_handler(
        CommandHandler("delvideo", delvideo)
    )

    bot_app.add_handler(
        CommandHandler("stats", stats)
    )

    # Any video
    bot_app.add_handler(
        MessageHandler(
            filters.VIDEO,
            addvideo
        )
    )

    # Error handler
    bot_app.add_error_handler(error_handler)

    logger.info("Initializing Telegram bot...")

    await bot_app.initialize()

    await bot_app.start()

    # IMPORTANT:
    # No 70 second delay here.
    await bot_app.updater.start_polling()

    logger.info("Telegram bot polling started.")

    try:
        while True:
            await asyncio.sleep(3600)

    except asyncio.CancelledError:
        logger.info("Bot stopping...")

    finally:
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()


# =========================================================
# FASTAPI HEALTH CHECK
# =========================================================

@app.get("/")
async def home():
    return {
        "status": "online",
        "bot": "running"
    }


@app.get("/health")
async def health():
    return {
        "status": "ok"
    }


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    import threading
    import uvicorn

    # Initialize database
    init_db()

    # Run Telegram bot in background thread
    bot_thread = threading.Thread(
        target=lambda: asyncio.run(run_bot()),
        daemon=True
    )

    bot_thread.start()

    # Run FastAPI server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000))
    )